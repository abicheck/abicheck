# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The two PE debug paths ``extract_pe`` keeps on the header-scoped and the
failed-PDB routes, with the PE reader, PDB outcome and scoped dump stubbed.

A header-scoped dump keeps the PDB's debug facts on the surface it returns: the
same ``debug_layout`` and ``debug_advanced`` objects the PDB produced, not copies.
A PDB whose extractor failed marks the ``debug`` section failed with its
diagnostic (ADR-062 D15), so the failure is never read as an absent PDB.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import abicheck.extract.export_table_read as export_table_read
import abicheck.pe_metadata as pe_metadata
from abicheck.model import AbiSnapshot
from abicheck.model.section_availability import SectionStatus
from abicheck.workflows.dump import pe as pe_dump

LIB = Path("/fixture/libk.dll")


def _pe_meta() -> SimpleNamespace:
    return SimpleNamespace(
        machine="IMAGE_FILE_MACHINE_AMD64",
        exports=[SimpleNamespace(name="k_open", ordinal=1)],
    )


def _stub_pe_reader(monkeypatch) -> None:
    monkeypatch.setattr(
        export_table_read, "read_exports_or_fail", lambda *_a, **_k: _pe_meta()
    )
    monkeypatch.setattr(pe_metadata, "parse_pe_metadata", lambda *_a, **_k: None)


def test_a_failed_pdb_marks_the_debug_section_failed_with_its_diagnostic() -> None:
    snap = AbiSnapshot(library="libk.dll", version="1.0")
    pe_dump._record_pdb_failure(snap, "pdb: libk.pdb: bad magic")
    debug = [s for s in snap.section_availability if s.section == "debug"]
    assert len(debug) == 1
    assert debug[0].status is SectionStatus.FAILED
    assert debug[0].diagnostics == ("pdb: libk.pdb: bad magic",)


def test_no_pdb_failure_records_nothing() -> None:
    snap = AbiSnapshot(library="libk.dll", version="1.0")
    pe_dump._record_pdb_failure(snap, None)
    assert not any(s.section == "debug" for s in snap.section_availability)


def test_a_header_scoped_dump_keeps_the_pdb_debug_facts(monkeypatch) -> None:
    _stub_pe_reader(monkeypatch)
    meta, adv = object(), object()
    monkeypatch.setattr(
        pe_dump, "extract_pdb_debug_outcome", lambda *_a, **_k: (meta, adv, None)
    )
    scoped = AbiSnapshot(library="libk.dll", version="1.0")

    def fake_scoped(*_a, **_k):
        return scoped, None

    monkeypatch.setattr(pe_dump, "try_header_scoped_dump", fake_scoped)
    result = pe_dump.extract_pe(
        LIB, "1.0", headers=[Path("/fixture/k.h")], pdb_path=None
    )
    assert result is scoped
    assert result.declarations.debug_layout is meta
    assert result.declarations.debug_advanced is adv


def test_a_header_scoped_dump_without_pdb_facts_keeps_none(monkeypatch) -> None:
    _stub_pe_reader(monkeypatch)
    monkeypatch.setattr(
        pe_dump, "extract_pdb_debug_outcome", lambda *_a, **_k: (None, None, None)
    )
    scoped = AbiSnapshot(library="libk.dll", version="1.0")
    monkeypatch.setattr(
        pe_dump, "try_header_scoped_dump", lambda *_a, **_k: (scoped, None)
    )
    result = pe_dump.extract_pe(LIB, "1.0", headers=[Path("/fixture/k.h")])
    assert result is scoped
    assert result.declarations.debug_layout is None
    assert result.declarations.debug_advanced is None
