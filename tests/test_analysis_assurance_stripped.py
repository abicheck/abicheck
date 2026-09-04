# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Regression coverage for no-DWARF binary comparison receipts."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner, Result

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
from abicheck.serialization import (
    snapshot_from_dict,
    snapshot_to_dict,
    snapshot_to_json,
)


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


def _compare(tmp_path: Path, *extra: str) -> Result:
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


def test_clean_verdict_keeps_default_exit_but_require_complete_fails(
    tmp_path: Path,
) -> None:
    assert _compare(tmp_path).exit_code == 0
    assert _compare(tmp_path, "--require-complete-analysis").exit_code == 1


def _debug_snapshot(
    version: str,
    dwarf: DwarfMetadata,
    advanced: AdvancedDwarfMetadata | None = None,
) -> AbiSnapshot:
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
        "source": "ctf",
        "basic": "parsed",
        "advanced": "not_supported",
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


def test_not_supported_advanced_state_does_not_add_a_false_parse_failure_note() -> None:
    """P2 review, fresh evidence (Codex): ``not_supported`` (a BTF/CTF-
    sourced side's advanced channel -- neither format carries calling-
    convention/value-ABI/frame-register facts at all) was previously
    missing from ``known_states``, so the ``"not in known_states"``
    fallback misclassified it as an unrecognized/failed state and added
    the false "debug evidence was only presence-probed or failed to parse"
    note even though the basic channel parsed cleanly. ``status`` stays
    "partial" regardless (via ``advanced_unavailable``'s own, accurate
    reason) -- this test is about the note text, not the status."""
    old = _debug_snapshot("1.0", BtfMetadata(has_btf=True).to_dwarf_metadata())
    new = _debug_snapshot("2.0", BtfMetadata(has_btf=True).to_dwarf_metadata())

    aa = checker.compare(old, new, scope_to_public_surface=False).analysis_assurance

    assert isinstance(aa, AnalysisAssurance)
    assert aa.status == "partial"
    assert not any(
        "only presence-probed or failed to parse" in note for note in aa.notes
    )


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
        "1.0",
        DwarfMetadata(evidence_state="failed"),
        AdvancedDwarfMetadata(evidence_state="failed"),
    )
    new = _debug_snapshot(
        "2.0",
        DwarfMetadata(evidence_state="failed"),
        AdvancedDwarfMetadata(evidence_state="failed"),
    )

    aa = checker.compare(old, new, scope_to_public_surface=False).analysis_assurance

    assert isinstance(aa, AnalysisAssurance)
    assert aa.debug_evidence["old"]["basic"] == "failed"
    assert aa.debug_evidence["old"]["advanced"] == "failed"
    assert aa.status == "partial"


def test_debug_provenance_fields_round_trip_without_loss() -> None:
    snapshot = _debug_snapshot(
        "1.0",
        DwarfMetadata(
            has_dwarf=True,
            evidence_source="elf_dwarf",
            evidence_state="parsed",
            cu_total=7,
            cu_failed=2,
        ),
        AdvancedDwarfMetadata(
            has_dwarf=True,
            evidence_state="parsed",
            cu_total=7,
            cu_failed=2,
        ),
    )

    restored = snapshot_from_dict(json.loads(snapshot_to_json(snapshot)))

    assert restored.dwarf is not None
    assert restored.dwarf.evidence_source == "elf_dwarf"
    assert restored.dwarf.evidence_state == "parsed"
    assert restored.dwarf.cu_total == 7
    assert restored.dwarf.cu_failed == 2
    assert restored.dwarf_advanced is not None
    assert restored.dwarf_advanced.evidence_state == "parsed"
    assert restored.dwarf_advanced.cu_total == 7
    assert restored.dwarf_advanced.cu_failed == 2


def test_legacy_debug_blocks_are_presence_only_not_claimed_parsed() -> None:
    # snapshot_to_dict() (not snapshot_to_json(), which wraps the flat shape
    # in ADR-062/063 Phase 8's sectioned document envelope) so the dwarf/
    # dwarf_advanced sub-dicts below are reachable directly, matching a real
    # pre-v26 flat snapshot on disk.
    raw = snapshot_to_dict(
        _debug_snapshot(
            "1.0",
            DwarfMetadata(has_dwarf=True),
            AdvancedDwarfMetadata(has_dwarf=True),
        )
    )
    # Provenance arrived in schema v26; a v25 snapshot cannot establish that
    # its DWARF-shaped blocks came from completed parsing.
    raw["schema_version"] = 25
    del raw["dwarf"]["evidence_source"]
    del raw["dwarf"]["evidence_state"]
    del raw["dwarf"]["cu_total"]
    del raw["dwarf"]["cu_failed"]
    del raw["dwarf_advanced"]["evidence_state"]
    del raw["dwarf_advanced"]["cu_total"]
    del raw["dwarf_advanced"]["cu_failed"]
    restored = snapshot_from_dict(raw)

    assert restored.dwarf is not None
    assert restored.dwarf.cu_total == 0
    assert restored.dwarf.cu_failed == 0
    assert restored.dwarf_advanced is not None
    assert restored.dwarf_advanced.cu_total == 0
    assert restored.dwarf_advanced.cu_failed == 0

    aa = checker.compare(
        restored, restored, scope_to_public_surface=False
    ).analysis_assurance

    assert isinstance(aa, AnalysisAssurance)
    assert aa.debug_evidence["old"] == {
        "source": "unknown",
        "basic": "presence_only",
        "advanced": "presence_only",
    }
    assert aa.status == "partial"
