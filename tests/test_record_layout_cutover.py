# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-063 6B, cohort 3: record layout read through ``SemanticIR``.

The oracle for the equivalence property is written here from the documented
legacy behaviour (compare two ``int`` values when both sides have one), not by
calling the old helper, so a shared bug cannot make both sides agree.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.checker import ChangeKind, compare
from abicheck.compare.declined_comparisons import declined_scope
from abicheck.compare.record_layout import record_layout_changes, record_layout_index
from abicheck.extract.semantic_normalizer import normalize_header_ast
from abicheck.model import AbiSnapshot, Fact, FactStatus, RecordType
from abicheck.model.identity import Namespace, entity_id_for_type
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR
from abicheck.model.semantic_ir_record_layout import (
    LEGACY_LAYOUT_DIAGNOSTIC,
    with_record_layout,
)
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
from abicheck.storage.semantic_ir_codec import (
    semantic_ir_from_document,
    semantic_ir_to_document,
)

_SIZES = st.one_of(st.none(), st.sampled_from([0, 8, 32, 64, 128]))


def _record(
    name: str, size: int | None, align: int | None, *, ident: bool = True
) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        size_bits=size,
        alignment_bits=align,
        qualified_name=f"ns::{name}",
        entity_id=entity_id_for_type((Namespace("ns"),), name) if ident else None,
    )


def _snap(records: list[RecordType], *, with_ir: bool) -> AbiSnapshot:
    ir = (
        normalize_header_ast(
            types=records,
            enums=[],
            typedefs_qualified={},
            typedef_entity_ids={},
            producer="castxml",
        )
        if with_ir
        else None
    )
    return AbiSnapshot(library="l", version="1", types=records, semantic_ir=ir)


def _oracle(o: RecordType, n: RecordType) -> list[tuple[ChangeKind, str, str]]:
    out = []
    for kind, a, b in (
        (ChangeKind.TYPE_SIZE_CHANGED, o.size_bits, n.size_bits),
        (ChangeKind.TYPE_ALIGNMENT_CHANGED, o.alignment_bits, n.alignment_bits),
    ):
        if a is not None and b is not None and a != b:
            out.append((kind, str(a), str(b)))
    return out


def _run(old: AbiSnapshot, new: AbiSnapshot, o: RecordType, n: RecordType):
    got: list = []
    record_layout_changes(
        got,
        o.name,
        o,
        n,
        record_layout_index(old.canonical_ir, old.declarations.types),
        record_layout_index(new.canonical_ir, new.declarations.types),
    )
    return [(c.kind, c.old_value, c.new_value) for c in got]


@settings(max_examples=300, deadline=None)
@given(
    os_=_SIZES,
    oa=_SIZES,
    ns=_SIZES,
    na=_SIZES,
    old_ir=st.booleans(),
    new_ir=st.booleans(),
    old_ident=st.booleans(),
    new_ident=st.booleans(),
)
def test_ir_and_adapter_paths_match_the_documented_contract(
    os_, oa, ns, na, old_ir, new_ir, old_ident, new_ident
) -> None:
    """Every mix of IR-backed / adapted side, identified / unidentified record
    and present / absent layout yields exactly the oracle's findings."""
    o = _record("R", os_, oa, ident=old_ident)
    n = _record("R", ns, na, ident=new_ident)
    old, new = _snap([o], with_ir=old_ir), _snap([n], with_ir=new_ir)
    assert _run(old, new, o, n) == _oracle(o, n)


def test_the_ir_is_the_authority_not_the_record() -> None:
    """An IR whose layout disagrees with its RecordType is believed: this is
    an authority transfer, not a fidelity gate that re-reads the record."""
    o, n = _record("R", 32, 32), _record("R", 32, 32)
    old, new = _snap([o], with_ir=True), _snap([n], with_ir=True)
    occ = OccurrenceId(n.entity_id)
    entity = new.canonical_ir.occurrences[occ]
    new_ir = SemanticIR(
        occurrences={
            **new.canonical_ir.occurrences,
            occ: dataclasses.replace(entity, size_bits=Fact.present(64)),
        }
    )
    assert _run(old, dataclasses.replace(new, semantic_ir=new_ir), o, n) == [
        (ChangeKind.TYPE_SIZE_CHANGED, "32", "64")
    ]


def test_one_sided_layout_is_recorded_as_declined() -> None:
    o, n = _record("R", 32, 32), _record("R", None, 32)
    with declined_scope() as got:
        assert _run(_snap([o], with_ir=True), _snap([n], with_ir=True), o, n) == []
    assert [d.entity for d in got] == ["ns::R"]


def test_backfilled_layout_reaches_the_ir_at_construction() -> None:
    """A clang record normalizes with no size; DWARF fills the RecordType
    afterwards. The snapshot boundary must carry that into the IR."""
    bare = _record("R", None, None)
    ir = normalize_header_ast(
        types=[bare],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="clang",
    )
    filled = dataclasses.replace(bare, size_bits=64, alignment_bits=32)
    snap = AbiSnapshot(library="l", version="1", types=[filled], semantic_ir=ir)
    entity = snap.canonical_ir.occurrences[OccurrenceId(bare.entity_id)]
    assert entity.size_bits == Fact.present(64)
    assert entity.alignment_bits == Fact.present(32)


def test_fill_never_overrides_an_established_or_failed_fact() -> None:
    r = _record("R", 64, 32)
    occ = OccurrenceId(r.entity_id)
    ir = SemanticIR(
        occurrences={
            occ: CanonicalEntity(
                canonical_spelling=Fact.present("ns::R"),
                size_bits=Fact.present(8),
                alignment_bits=Fact.failed("layout walk errored"),
            )
        }
    )
    out = with_record_layout(ir, [r]).occurrences[occ]
    assert out.size_bits == Fact.present(8)
    assert out.alignment_bits.status is FactStatus.FAILED


def test_conflicting_records_under_one_identity_fill_nothing() -> None:
    a, b = _record("R", 64, 32), _record("R", 128, 32)
    occ = OccurrenceId(a.entity_id)
    ir = SemanticIR(
        occurrences={occ: CanonicalEntity(canonical_spelling=Fact.present("ns::R"))}
    )
    out = with_record_layout(ir, [a, b]).occurrences[occ]
    assert out.size_bits.status is FactStatus.NOT_COLLECTED


class TestCodec:
    def test_round_trip_writes_version_2_and_layout_only_for_records(self) -> None:
        snap = _snap([_record("R", 64, 32)], with_ir=True)
        doc = semantic_ir_to_document(snap.canonical_ir, {})
        assert doc["semantic_ir"]["version"] == 2
        entity = doc["semantic_ir"]["occurrences"][0]["entity"]
        assert entity["size_bits"]["value"] == 64
        ir, _ = semantic_ir_from_document(json.loads(json.dumps(doc)))
        assert ir == snap.canonical_ir

    def test_a_version_1_document_loads_and_is_filled_from_its_records(self) -> None:
        snap = _snap([_record("R", 64, 32)], with_ir=True)
        d = snapshot_to_dict(snap)
        d = json.loads(json.dumps(d))
        _downgrade_ir_to_v1(d)
        ir, _ = semantic_ir_from_document(d)
        entity = next(iter(ir.occurrences.values()))
        assert entity.size_bits.status is FactStatus.NOT_COLLECTED
        assert LEGACY_LAYOUT_DIAGNOSTIC in entity.size_bits.diagnostics
        loaded = snapshot_from_dict(d)
        assert next(
            iter(loaded.canonical_ir.occurrences.values())
        ).size_bits == Fact.present(64)

    @pytest.mark.parametrize("bad", [True, "64", 6.4])
    def test_a_non_integer_size_is_refused(self, bad) -> None:
        doc = semantic_ir_to_document(
            _snap([_record("R", 64, 32)], with_ir=True).semantic_ir, {}
        )
        doc["semantic_ir"]["occurrences"][0]["entity"]["size_bits"]["value"] = bad
        with pytest.raises(ValueError):
            semantic_ir_from_document(doc)

    def test_a_future_version_is_refused(self) -> None:
        doc = semantic_ir_to_document(
            _snap([_record("R", 64, 32)], with_ir=True).semantic_ir, {}
        )
        doc["semantic_ir"]["version"] = 3
        with pytest.raises(ValueError):
            semantic_ir_from_document(doc)

    def test_a_version_2_record_missing_its_layout_is_truncated(self) -> None:
        doc = semantic_ir_to_document(
            _snap([_record("R", 64, 32)], with_ir=True).semantic_ir, {}
        )
        del doc["semantic_ir"]["occurrences"][0]["entity"]["size_bits"]
        with pytest.raises(ValueError):
            semantic_ir_from_document(doc)


def _downgrade_ir_to_v1(d: dict) -> None:
    """Rewrite a document's IR the way a pre-v53 writer produced it."""
    ir_doc = _find_ir(d)
    ir_doc.pop("version")
    for entry in ir_doc["occurrences"]:
        entry["entity"].pop("size_bits", None)
        entry["entity"].pop("alignment_bits", None)


def _find_ir(node):
    if isinstance(node, dict):
        if "occurrences" in node and "version" in node:
            return node
        for v in node.values():
            found = _find_ir(v)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_ir(v)
            if found is not None:
                return found
    return None


def test_end_to_end_compare_reports_a_size_change_from_the_ir() -> None:
    old = _snap([_record("R", 32, 32)], with_ir=True)
    new = _snap([_record("R", 64, 32)], with_ir=True)
    kinds = {c.kind for c in compare(old, new).changes}
    assert ChangeKind.TYPE_SIZE_CHANGED in kinds


class TestCutoverGate:
    def _gate(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import semantic_ir_cutover

        return semantic_ir_cutover

    def test_registered_and_clean(self) -> None:
        gate = self._gate()
        from findings_report import Findings

        assert "record_layout" in {c.name for c in gate.MIGRATED_COHORTS}
        findings = Findings()
        gate.check_semantic_ir_cutover(findings)
        assert findings.errors == []

    @pytest.mark.parametrize(
        "source",
        [
            "x = t_old.size_bits",
            "x = rec.alignment_bits",
            "x = snap.types",
            'x = getattr(t, "size_bits")',
        ],
    )
    def test_fires_on_a_legacy_layout_read(self, source: str) -> None:
        forbidden = frozenset({"types", "size_bits", "alignment_bits"})
        assert self._gate().legacy_collection_reads(ast.parse(source), forbidden)


class TestProjectSnapshotSection:
    def test_section_is_written_at_v2(self) -> None:
        from abicheck.storage.dto import SECTION_SCHEMA_VERSIONS, semantic_ir_to_dto

        dto = semantic_ir_to_dto(
            _snap([_record("R", 64, 32)], with_ir=True).semantic_ir, {}
        )
        assert dto.section_schema_version == SECTION_SCHEMA_VERSIONS["semantic_ir"] == 2

    def test_a_v1_section_migrates_and_reads_layout_as_not_recorded(self) -> None:
        from abicheck.storage.dto import SectionDTO, semantic_ir_from_dto

        doc = semantic_ir_to_document(
            _snap([_record("R", 64, 32)], with_ir=True).semantic_ir, {}
        )
        _downgrade_ir_to_v1(doc)
        ir, _ = semantic_ir_from_dto(
            SectionDTO(
                section_kind="semantic_ir", section_schema_version=1, payload=doc
            )
        )
        entity = next(iter(ir.occurrences.values()))
        assert LEGACY_LAYOUT_DIAGNOSTIC in entity.size_bits.diagnostics

    def test_a_v1_section_claiming_an_ir_version_is_refused(self) -> None:
        from abicheck.storage.dto import SectionDTO, semantic_ir_from_dto

        doc = semantic_ir_to_document(
            _snap([_record("R", 64, 32)], with_ir=True).semantic_ir, {}
        )
        with pytest.raises(ValueError):
            semantic_ir_from_dto(
                SectionDTO(
                    section_kind="semantic_ir", section_schema_version=1, payload=doc
                )
            )


@pytest.mark.parametrize("with_ir", [False, True])
def test_unidentified_same_spelling_records_are_answered_per_record(with_ir) -> None:
    """Two records with no identity and one spelling (ODR duplicates) each
    keep their own layout: the index answers for the record the caller
    paired, not for whichever came first."""
    old_a, old_b = (
        _record("Dup", 64, 32, ident=False),
        _record("Dup", 128, 32, ident=False),
    )
    new_a, new_b = (
        _record("Dup", 64, 32, ident=False),
        _record("Dup", 256, 32, ident=False),
    )
    anchor = _record("Other", 8, 8)  # gives the IR a record occurrence of its own
    old = _snap([anchor, old_a, old_b], with_ir=with_ir)
    new = _snap([anchor, new_a, new_b], with_ir=with_ir)
    assert _run(old, new, old_b, new_b) == [
        (ChangeKind.TYPE_SIZE_CHANGED, "128", "256")
    ]
    assert _run(old, new, old_a, new_a) == []
