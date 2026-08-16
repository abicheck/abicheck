# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Regression coverage for no-DWARF binary comparison receipts."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck import checker
from abicheck.analysis_assurance import AnalysisAssurance
from abicheck.btf_metadata import BtfMetadata
from abicheck.cli import main
from abicheck.ctf_metadata import CtfMetadata
from abicheck.dwarf_advanced import AdvancedDwarfMetadata
from abicheck.dwarf_metadata import DwarfMetadata
from abicheck.dwarf_presence import _section_presence_metadata
from abicheck.elf_metadata import ElfMetadata
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_from_dict, snapshot_to_json


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


def _debug_snapshot(version: str, dwarf: object, advanced: object | None = None) -> AbiSnapshot:
    return AbiSnapshot(
        version=version,
        library="libfoo.so.1",
        functions=[Function("pub_a", "_Z5pub_av", "int", visibility=Visibility.PUBLIC)],
        dwarf=dwarf,
        dwarf_advanced=advanced,
    )


def test_ctf_source_and_capability_survive_snapshot_round_trip() -> None:
    snapshot = _debug_snapshot("1.0", CtfMetadata(has_ctf=True).to_dwarf_metadata())
    old = snapshot_from_dict(json.loads(snapshot_to_json(snapshot)))
    new = snapshot_from_dict(json.loads(snapshot_to_json(snapshot)))

    aa = checker.compare(old, new, scope_to_public_surface=False).analysis_assurance

    assert isinstance(aa, AnalysisAssurance)
    assert aa.debug_evidence["old"] == {
        "source": "ctf", "basic": "parsed", "advanced": "not_supported"
    }
    assert aa.status == "partial"


def test_btf_basic_evidence_is_not_advanced_capability() -> None:
    old = _debug_snapshot("1.0", BtfMetadata(has_btf=True).to_dwarf_metadata())
    new = _debug_snapshot("2.0", BtfMetadata(has_btf=True).to_dwarf_metadata())

    aa = checker.compare(old, new, scope_to_public_surface=False).analysis_assurance

    assert isinstance(aa, AnalysisAssurance)
    assert aa.debug_evidence == {
        "old": {"source": "btf", "basic": "parsed", "advanced": "not_supported"},
        "new": {"source": "btf", "basic": "parsed", "advanced": "not_supported"},
    }
    assert aa.status == "partial"


def test_presence_only_receipt_does_not_claim_parsed_facts() -> None:
    old_basic, old_advanced = _section_presence_metadata(True, "btf")
    new_basic, new_advanced = _section_presence_metadata(True, "btf")
    old = _debug_snapshot("1.0", old_basic, old_advanced)
    new = _debug_snapshot("2.0", new_basic, new_advanced)

    aa = checker.compare(old, new, scope_to_public_surface=False).analysis_assurance

    assert isinstance(aa, AnalysisAssurance)
    assert aa.debug_evidence["old"]["basic"] == "presence_only"
    assert aa.debug_evidence["old"]["advanced"] == "not_supported"
    assert aa.status == "partial"


def test_failed_debug_parse_is_preserved_in_receipt() -> None:
    old = _debug_snapshot(
        "1.0", DwarfMetadata(evidence_state="failed"),
        AdvancedDwarfMetadata(evidence_state="failed"),
    )
    new = _debug_snapshot(
        "2.0", DwarfMetadata(evidence_state="failed"),
        AdvancedDwarfMetadata(evidence_state="failed"),
    )

    aa = checker.compare(old, new, scope_to_public_surface=False).analysis_assurance

    assert isinstance(aa, AnalysisAssurance)
    assert aa.debug_evidence["old"]["basic"] == "failed"
    assert aa.debug_evidence["old"]["advanced"] == "failed"
    assert aa.status == "partial"
