# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Regression coverage for no-DWARF binary comparison receipts."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from abicheck import checker
from abicheck.analysis_assurance import AnalysisAssurance
from abicheck.cli import main
from abicheck.elf_metadata import ElfMetadata
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    common = {
        "library": "libfoo.so.1",
        "functions": [
            Function("pub_a", "_Z5pub_av", "int", visibility=Visibility.PUBLIC)
        ],
        "from_headers": True,
        "elf": ElfMetadata(soname="libfoo.so.1"),
    }
    return AbiSnapshot(version="1.0", **common), AbiSnapshot(version="2.0", **common)


def _compare(tmp_path: Path, *extra: str):
    old, new = _pair()
    old_path, new_path = tmp_path / "old.json", tmp_path / "new.json"
    old_path.write_text(snapshot_to_json(old), encoding="utf-8")
    new_path.write_text(snapshot_to_json(new), encoding="utf-8")
    return CliRunner().invoke(main, ["compare", str(old_path), str(new_path), *extra])


def test_symmetric_binary_without_dwarf_is_partial() -> None:
    result = checker.compare(*_pair())
    assurance = result.analysis_assurance
    assert isinstance(assurance, AnalysisAssurance)
    assert assurance.l0_context_status == "clean"
    assert assurance.dwarf_context_status == "not_evaluated"
    assert assurance.status == "partial"
    assert any("without DWARF" in note for note in assurance.notes)


def test_clean_verdict_keeps_default_exit_but_require_complete_fails(tmp_path: Path) -> None:
    assert _compare(tmp_path).exit_code == 0
    assert _compare(tmp_path, "--require-complete-analysis").exit_code == 1
