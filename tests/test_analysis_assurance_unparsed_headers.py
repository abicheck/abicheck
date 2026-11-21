"""A dropped public header lowers ``analysis_assurance.status``.

SVS scan report (known-gaps ``five-svs-scan-report-findings``): a multi-header
L2 parse that dropped a header it could not compile still read ``complete``,
because neither frontend's drop record (clang's ``header_parse_excluded``,
castxml's ``unparseable_headers_excluded``) contributed to the rollup. The
declarations only that header provides were never observed, so the run may
not claim a clean header surface (AGENTS.md: weaker evidence narrows
conclusions).
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
from itertools import product

import pytest
from click.testing import CliRunner

from abicheck.checker import compare
from abicheck.cli import main
from abicheck.model import AbiSnapshot

_KEYS = ("header_parse_excluded", "unparseable_headers_excluded")


def _snap(version: str, toolchain: dict[str, str] | None) -> AbiSnapshot:
    return dataclasses.replace(
        AbiSnapshot(library="l", version=version), ast_toolchain=toolchain or {}
    )


def _toolchain(key: str | None, dropped: list[str]) -> dict[str, str] | None:
    return {key: json.dumps(dropped)} if key is not None else None


# Exhaustive small domain: which record (none / clang / castxml) on each side,
# with 0..2 dropped headers. Oracle: complete is allowed only when no side
# recorded any dropped header -- stated from the inputs, not from the helper.
_SIDE_STATES = [
    (key, dropped)
    for key in (None, *_KEYS)
    for dropped in ([], ["a.h"], ["a.h", "b.h"])
    if not (key is None and dropped)
]


@pytest.mark.parametrize(
    ("old_state", "new_state"), list(product(_SIDE_STATES, _SIDE_STATES))
)
def test_dropped_header_on_either_side_never_reads_complete(
    old_state: tuple[str | None, list[str]], new_state: tuple[str | None, list[str]]
) -> None:
    old = _snap("1", _toolchain(*old_state))
    new = _snap("2", _toolchain(*new_state))
    aa = compare(old, new).analysis_assurance
    any_dropped = bool(old_state[1] or new_state[1])
    noted = [n for n in aa.notes if n.startswith("public headers (dropped")]
    assert len(noted) == (bool(old_state[1]) + bool(new_state[1]))
    if any_dropped:
        assert aa.status != "complete"
        for name in old_state[1] + new_state[1]:
            assert any(name in n for n in noted)


def test_both_records_union_without_duplicates() -> None:
    snap = _snap(
        "1",
        {
            "header_parse_excluded": json.dumps(["x.h", "y.h"]),
            "unparseable_headers_excluded": json.dumps(["y.h", "z.h"]),
        },
    )
    notes = [
        n
        for n in compare(snap, _snap("2", None)).analysis_assurance.notes
        if n.startswith("public headers (dropped")
    ]
    assert len(notes) == 1
    assert "dropped 3 header(s)" in notes[0]
    assert "(x.h, y.h, z.h)" in notes[0]


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("castxml") is None or shutil.which("gcc") is None,
    reason="needs castxml + gcc",
)
def test_cli_compare_with_dropped_header_is_not_complete(tmp_path) -> None:
    """User-level: ``compare -H include/`` where castxml cannot compile one
    public header. Before the fix this read ``complete`` on identical
    libraries even though ``g`` was never observed."""
    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "a.h").write_text("int f(int);\n")
    (inc / "sycl.h").write_text(
        '#ifdef __castxml__\n#error "Unsupported compiler"\n#endif\nint g(int);\n'
    )
    src = tmp_path / "a.c"
    src.write_text("int f(int x){return x;}\nint g(int x){return x;}\n")
    out = tmp_path / "out"
    out.mkdir()
    libs = []
    for name in ("liba.so", "libb.so"):
        lib = tmp_path / name
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", str(lib), str(src)], check=True
        )
        libs.append(lib)
    report = out / "r.json"
    res = CliRunner().invoke(
        main,
        ["compare", str(libs[0]), str(libs[1]), "-H", str(inc), "-o", f"json={report}"],
    )
    assert res.exit_code == 0, res.output
    aa = json.loads(report.read_text())["analysis_assurance"]
    assert aa["status"] == "partial"
    assert any("sycl.h" in n and "dropped" in n for n in aa["notes"])
