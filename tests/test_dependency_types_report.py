# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""An unread dependency-type side is reported as unread, never as empty.

A binary-only side whose dependency-internal types cannot be classified (a PE
image's decorated exports name no readable own root) used to read as an empty
classification. The report now carries the per-side read state (JSON key
``dependency_types_read``) and, for a side that exports C++ names, a coverage
note beside its other coverage warnings. Bug class
``symbol_rule.single_object_format``.
"""

from __future__ import annotations

from abicheck.checker import compare
from abicheck.model import AbiSnapshot
from abicheck.model.entities import RecordType, TypeField
from abicheck.model.macho_facts import MachoExport, MachoMetadata
from abicheck.model.pe_facts import PeExport, PeMetadata
from abicheck.report.json_sections import add_confidence_evidence
from abicheck.workflows.dump.dependency_internals import dependency_types_report

_API = "/src/lib/api.h"
_DEP = "/src/dep/fakevec/simd.h"


def _records() -> list[RecordType]:
    return [
        RecordType(
            name="lib::State",
            kind="struct",
            fields=[TypeField(name="n", type="int")],
            source_location=f"{_API}:5",
        ),
        RecordType(
            name="fakevec::simd::avx512_tag",
            kind="struct",
            size_bits=8,
            source_location=f"{_DEP}:6",
        ),
    ]


def _pe_side(version: str, exports: list[str]) -> AbiSnapshot:
    return AbiSnapshot(
        library="lib.dll",
        version=version,
        types=_records(),
        pe=PeMetadata(exports=[PeExport(name=n) for n in exports]),
        platform="pe",
    )


def _elf_c_side(version: str) -> AbiSnapshot:
    """A pure-C export table: no C++ name, so nothing to classify or to report."""
    from abicheck.model.elf_facts import ElfMetadata, ElfSymbol

    return AbiSnapshot(
        library="libc.so",
        version=version,
        types=_records(),
        elf=ElfMetadata(
            soname="",
            needed=[],
            symbols=[ElfSymbol(name="plain_c", is_default=True)],
            imports=[],
        ),
    )


def _macho_side(version: str) -> AbiSnapshot:
    return AbiSnapshot(
        library="lib.dylib",
        version=version,
        types=_records(),
        macho=MachoMetadata(
            exports=[MachoExport(name="_ZN3lib3sumERKNS_5StateE")],
        ),
        platform="macho",
    )


def test_pe_side_with_cxx_exports_is_reported_unread() -> None:
    old = _pe_side("1", ["?sum@lib@@YAHXZ"])
    new = _pe_side("2", ["?sum@lib@@YAHXZ"])
    state, notes = dependency_types_report(old, new)
    assert state == {"old": False, "new": False}
    assert len(notes) == 2
    assert all("not classified" in n and "stay in contract" in n for n in notes)


def test_pure_c_unread_side_is_visible_in_state_but_raises_no_note() -> None:
    state, notes = dependency_types_report(None, _elf_c_side("2"))
    assert state == {"new": False}
    assert notes == ()


def test_read_side_is_reported_true_with_no_note() -> None:
    state, notes = dependency_types_report(None, _macho_side("2"))
    assert state == {"new": True}
    assert notes == ()


def test_header_derived_side_is_not_listed() -> None:
    snap = _pe_side("2", ["?sum@lib@@YAHXZ"])
    snap.from_headers = True
    assert dependency_types_report(None, snap) == (None, ())


def test_compare_threads_the_state_into_the_result_and_its_json() -> None:
    old = _pe_side("1", ["?sum@lib@@YAHXZ"])
    new = _pe_side("2", ["?sum@lib@@YAHXZ"])
    result = compare(old, new)
    expected = {"old": False, "new": False}
    assert result.dependency_types_read == expected
    assert any("not classified for the new side" in w for w in result.coverage_warnings)

    doc: dict[str, object] = {}
    add_confidence_evidence(doc, result)
    assert doc["dependency_types_read"] == expected


def test_json_key_is_absent_when_no_side_is_binary_only() -> None:
    snap = _pe_side("1", ["?sum@lib@@YAHXZ"])
    snap.from_headers = True
    result = compare(snap, snap)
    assert result.dependency_types_read is None
    doc: dict[str, object] = {}
    add_confidence_evidence(doc, result)
    assert "dependency_types_read" not in doc
