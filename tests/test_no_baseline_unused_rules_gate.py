"""``unused_rules: block`` gates ``compare --no-baseline`` (ADR-067 P1).

Bug class: the audit's ``disposition_audit.unused_rules_review`` reported
``gate_contribution: 1`` for a stale suppression while the audit's own exit
code -- folded from ``_no_baseline_exit_axes``, which had no such axis --
stayed ``0``. A single-build declared-vs-exported gate whose exceptions file
must not rot silently passed with a stale exception, on the scalar audit and
on the N-library audit alike.

Exercised over the whole small domain ``{allow, warn, block} x {rule used,
rule stale}`` against the documented rule (``docs/reference/exit-codes.md``:
exit ``1`` iff ``block`` and a rule is unused), plus the set-only case the
fold exists for: a rule one member uses and another member leaves unused is
not stale for the set (a per-member ``max`` would flag it).

Needs gcc and castxml, so it carries the ``integration`` marker.
"""

from __future__ import annotations

import itertools
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="ELF fixture"),
]

_HEADER = "#pragma once\nint a_fn(int);\nint b_fn(int);\n"


def _lib(tmp_path: Path, name: str, source: str) -> Path:
    gcc = shutil.which("gcc")
    if gcc is None or shutil.which("castxml") is None:
        pytest.skip("gcc/castxml not found")
    src = tmp_path / f"{name}.c"
    src.write_text(source, encoding="utf-8")
    out = tmp_path / "libs" / f"lib{name}.so"
    out.parent.mkdir(exist_ok=True)
    proc = subprocess.run(
        [gcc, "-shared", "-fPIC", "-o", str(out), str(src)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        pytest.skip(f"gcc failed: {proc.stderr[:200]}")
    return out


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    inc = tmp_path / "inc"
    inc.mkdir()
    (inc / "api.h").write_text(_HEADER, encoding="utf-8")
    # libA declares-and-exports a_fn, exports an undeclared a_extra, and
    # does not export the declared b_fn.
    lib_a = _lib(
        tmp_path,
        "a",
        "int a_fn(int x){return x;}\nint a_extra(int x){return x+1;}\n",
    )
    return lib_a, inc


def _files(tmp_path: Path, mode: str, symbol: str) -> tuple[Path, Path]:
    policy = tmp_path / "policy.yml"
    policy.write_text(f"unused_rules: {mode}\n", encoding="utf-8")
    sup = tmp_path / "sup.yml"
    sup.write_text(
        "version: 1\nsuppressions:\n"
        f"  - symbol: {symbol}\n"
        "    change_kind: exported_not_public\n"
        "    reason: internal export, tracked in TICKET-1\n",
        encoding="utf-8",
    )
    return policy, sup


def _run(tmp_path: Path, operand: Path, inc: Path, policy: Path, sup: Path):
    out = tmp_path / "report.json"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            "--no-baseline",
            str(operand),
            "-H",
            str(inc),
            "--policy",
            str(policy),
            "--suppress",
            str(sup),
            "-o",
            f"json={out}",
        ],
    )
    return result.exit_code, json.loads(out.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("mode", "stale"),
    list(itertools.product(("allow", "warn", "block"), (False, True))),
)
def test_scalar_audit_exit_follows_the_unused_rules_mode(
    tmp_path: Path, mode: str, stale: bool
) -> None:
    lib_a, inc = _fixture(tmp_path)
    policy, sup = _files(tmp_path, mode, "no_such_export" if stale else "a_extra")
    code, report = _run(tmp_path, lib_a, inc, policy, sup)
    expected = 1 if (mode == "block" and stale) else 0
    assert code == expected
    assert report["exit_axes"]["unused_rules"] == expected
    assert report["exit_code"] == expected
    # The declared-but-not-exported finding is reported either way.
    assert "b_fn" in {f.get("symbol") for f in report["findings"]}


@pytest.mark.parametrize("stale", [False, True])
def test_set_audit_folds_unused_rules_over_members(tmp_path: Path, stale: bool) -> None:
    lib_a, inc = _fixture(tmp_path)
    # libB exports b_fn only: it can evaluate the a_extra rule and leaves it
    # unused. The set must still treat the rule as used, since libA uses it.
    _lib(tmp_path, "b", "int b_fn(int x){return x;}\n")
    policy, sup = _files(tmp_path, "block", "no_such_export" if stale else "a_extra")
    code, report = _run(tmp_path, lib_a.parent, inc, policy, sup)
    assert report["exit_axes"]["unused_rules"] == (1 if stale else 0)
    assert code == (1 if stale else 0)
