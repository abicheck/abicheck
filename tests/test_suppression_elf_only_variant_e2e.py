"""End to end: a base-kind suppression hides a removal whichever kind it gets.

The same removed function is ``func_removed`` when a public header declares it
and ``func_removed_elf_only`` when the run has no headers. One suppression file
naming ``change_kind: func_removed`` must hide it in both runs (exit 0, finding
recorded as suppressed). The inverse rule, naming the ELF-only kind, must hide
only the headerless run.

Linux only: the libraries are built with gcc.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests._abicheck_subprocess import abicheck_command, abicheck_env

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux") or shutil.which("gcc") is None,
        reason="builds ELF libraries with gcc; Linux only",
    ),
]

_OLD_SRC = "int keep_fn(void) { return 1; }\nint gone_fn(void) { return 2; }\n"
_NEW_SRC = "int keep_fn(void) { return 1; }\n"
_OLD_HDR = "int keep_fn(void);\nint gone_fn(void);\n"
_NEW_HDR = "int keep_fn(void);\n"
_REMOVAL_KINDS = frozenset({"func_removed", "func_removed_elf_only"})


def _build(tmp: Path, name: str, src: str) -> Path:
    source = tmp / f"{name}.c"
    source.write_text(src)
    lib = tmp / f"lib{name}.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-O0", str(source), "-o", str(lib)], check=True
    )
    return lib


@pytest.fixture
def pair(tmp_path: Path) -> dict[str, Path]:
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    files = {
        "old_lib": _build(old_dir, "x", _OLD_SRC),
        "new_lib": _build(new_dir, "x", _NEW_SRC),
    }
    (tmp_path / "old.h").write_text(_OLD_HDR)
    (tmp_path / "new.h").write_text(_NEW_HDR)
    files["old_h"] = tmp_path / "old.h"
    files["new_h"] = tmp_path / "new.h"
    return files


def _suppression(tmp: Path, change_kind: str) -> Path:
    path = tmp / f"supp-{change_kind}.yml"
    path.write_text(
        "version: 1\n"
        "suppressions:\n"
        f"  - change_kind: {change_kind}\n"
        "    symbol: gone_fn\n"
        "    reason: removed on purpose; consumers migrated\n"
    )
    return path


def _run(
    tmp: Path, pair: dict[str, Path], *, with_headers: bool, suppress: Path
) -> tuple[int, dict]:
    report = tmp / f"report-{with_headers}-{suppress.stem}.json"
    args: list[str] = [
        "compare",
        str(pair["old_lib"]),
        str(pair["new_lib"]),
        "--suppress",
        str(suppress),
        "-o",
        f"json={report}",
    ]
    if with_headers:
        args += ["-H", f"old={pair['old_h']}", "-H", f"new={pair['new_h']}"]
    proc = subprocess.run(
        abicheck_command(*args),
        capture_output=True,
        text=True,
        env=abicheck_env(),
        check=False,
    )
    return proc.returncode, json.loads(report.read_text())


def _removal_kinds(report: dict) -> list[str]:
    """The removal kinds the run's own high-risk audit saw for ``gone_fn``."""
    return [
        m["kind"]
        for m in report["suppression_audit"]["high_risk_matches"]
        if m.get("symbol") == "gone_fn"
    ]


def test_base_kind_rule_hides_the_removal_headerless(
    tmp_path: Path, pair: dict[str, Path]
) -> None:
    code, report = _run(
        tmp_path,
        pair,
        with_headers=False,
        suppress=_suppression(tmp_path, "func_removed"),
    )
    assert code == 0
    assert _removal_kinds(report) == ["func_removed_elf_only"]
    assert report["disposition_audit"]["counts"]["suppressed"] == 1
    assert not [c for c in report["changes"] if c["kind"] in _REMOVAL_KINDS]


def test_base_kind_rule_hides_the_removal_with_headers(
    tmp_path: Path, pair: dict[str, Path]
) -> None:
    code, report = _run(
        tmp_path,
        pair,
        with_headers=True,
        suppress=_suppression(tmp_path, "func_removed"),
    )
    assert code == 0
    assert _removal_kinds(report) == ["func_removed"]
    assert report["disposition_audit"]["counts"]["suppressed"] == 1
    assert not [c for c in report["changes"] if c["kind"] in _REMOVAL_KINDS]


def test_elf_only_rule_hides_only_the_headerless_removal(
    tmp_path: Path, pair: dict[str, Path]
) -> None:
    suppress = _suppression(tmp_path, "func_removed_elf_only")

    code, report = _run(tmp_path, pair, with_headers=False, suppress=suppress)
    assert code == 0
    assert _removal_kinds(report) == ["func_removed_elf_only"]

    # The variant rule does not reach the header-backed kind: the removal stays.
    code, report = _run(tmp_path, pair, with_headers=True, suppress=suppress)
    assert code != 0
    assert _removal_kinds(report) == []
    assert "func_removed" in {c["kind"] for c in report["changes"]}
