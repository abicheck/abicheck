"""castxml header path: a callback's signature reaches the public compare workflow.

castxml spelled every unnamed function type ``FunctionType``, so a callback
parameter, typedef, struct field or return whose signature changed compared
``NO_CHANGE`` through headers (known gap 2026-10-15). Each case builds two
gcc ELF libraries *without* debug info, so the header AST is the only
evidence, and runs ``python -m abicheck compare -H``. The oracle is the
expected finding kind on the named entity, read from the JSON report.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests._abicheck_subprocess import abicheck_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="gcc emits ELF on Linux only"),
    pytest.mark.skipif(
        not (shutil.which("castxml") and shutil.which("gcc")),
        reason="needs castxml and gcc",
    ),
]

# (case id, old header, new header, definitions, expected (kind, symbol) or None)
_CASES = [
    (
        "inline_param_arity",
        "void set_cb(void (*cb)(int));",
        "void set_cb(void (*cb)(int, void *));",
        "void set_cb(CB_DECL) {}",
        ("func_params_changed", "set_cb"),
    ),
    (
        "inline_param_type",
        "void set_cb(void (*cb)(int));",
        "void set_cb(void (*cb)(long));",
        "void set_cb(CB_DECL) {}",
        ("func_params_changed", "set_cb"),
    ),
    (
        "inline_param_return",
        "void set_cb(int (*cb)(int));",
        "void set_cb(double (*cb)(int));",
        "void set_cb(CB_DECL) {}",
        ("func_params_changed", "set_cb"),
    ),
    (
        "inline_param_variadic",
        "void set_cb(int (*cb)(int));",
        "void set_cb(int (*cb)(int, ...));",
        "void set_cb(CB_DECL) {}",
        ("func_params_changed", "set_cb"),
    ),
    (
        "pointer_to_fp",
        "void set_cb(void (**cb)(int));",
        "void set_cb(void (**cb)(int, int));",
        "void set_cb(CB_DECL) {}",
        ("func_params_changed", "set_cb"),
    ),
    (
        "typedef_fp",
        "typedef int (*cb_t)(int);\nint set_cb(cb_t cb);",
        "typedef int (*cb_t)(int, int);\nint set_cb(cb_t cb);",
        "int set_cb(cb_t cb) { return 0; }",
        ("typedef_base_changed", "cb_t"),
    ),
    (
        "struct_field_fp",
        "struct ops { int (*fn)(int); };\nvoid set_ops(struct ops *o);",
        "struct ops { int (*fn)(int, int); };\nvoid set_ops(struct ops *o);",
        "void set_ops(struct ops *o) {}",
        ("type_field_type_changed", "ops"),
    ),
    (
        "returned_fp",
        "typedef int handler_fn(int);\nhandler_fn *get_cb(void);",
        "typedef int handler_fn(int, int);\nhandler_fn *get_cb(void);",
        "handler_fn *get_cb(void) { return 0; }",
        ("typedef_base_changed", "handler_fn"),
    ),
    (
        "identical_callback_negative_control",
        "typedef int (*cb_t)(int);\nvoid set_cb(void (*cb)(int, void *), cb_t t);",
        "typedef int (*cb_t)(int);\nvoid set_cb(void (*cb)(int, void *), cb_t t);",
        "void set_cb(void (*cb)(int, void *), cb_t t) {}",
        None,
    ),
]


def _param_decl(header: str) -> str:
    start = header.index("set_cb(") + len("set_cb(")
    return header[start : header.rindex(")")]


def _build(tmp: Path, name: str, header: str, defs: str) -> tuple[Path, Path]:
    tmp.mkdir(parents=True, exist_ok=True)
    h = tmp / f"{name}.h"
    h.write_text(header + "\n")
    if "CB_DECL" in defs:
        defs = defs.replace("CB_DECL", _param_decl(header))
    src = tmp / f"{name}.c"
    src.write_text(f'#include "{name}.h"\n{defs}\n')
    so = tmp / f"lib{name}.so"
    built = subprocess.run(
        ["gcc", "-shared", "-fPIC", "-w", "-o", str(so), str(src)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert built.returncode == 0, built.stderr
    return so, h


@pytest.mark.parametrize(
    ("old_h", "new_h", "defs", "expected"),
    [pytest.param(*c[1:], id=c[0]) for c in _CASES],
)
def test_callback_signature_change_is_reported_through_headers(
    tmp_path: Path,
    old_h: str,
    new_h: str,
    defs: str,
    expected: tuple[str, str] | None,
) -> None:
    old_so, old_hp = _build(tmp_path / "old", "api", old_h, defs)
    new_so, new_hp = _build(tmp_path / "new", "api", new_h, defs)
    report = tmp_path / "report.json"
    proc = subprocess.run(
        abicheck_command("compare",
            str(old_so),
            str(new_so),
            "-H",
            f"old={old_hp}",
            "-H",
            f"new={new_hp}",
            "-o",
            f"json={report}",
        ),
        capture_output=True,
        text=True,
        timeout=300,
    )
    doc = json.loads(report.read_text())
    found = {(c["kind"], c.get("symbol")) for c in doc["changes"]}
    if expected is None:
        assert found == set(), found
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return
    assert expected in found, (found, proc.stdout)
    assert proc.returncode == 4, proc.stdout + proc.stderr
    # The finding carries the full signatures, not castxml's opaque tag.
    change = next(c for c in doc["changes"] if (c["kind"], c.get("symbol")) == expected)
    assert "FunctionType" not in f"{change.get('old_value')}{change.get('new_value')}"
