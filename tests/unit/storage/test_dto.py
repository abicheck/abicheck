# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""`abicheck.storage.dto` — the `ProjectSnapshot` package's per-section DTO
envelope (ADR-062 Phase 1 / ADR-063 Phase 8's D8 constraint).
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, strategies as st

from abicheck.model.fact import Fact
from abicheck.model.identity import Namespace, Record, entity_id_for_type
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR
from abicheck.storage.canonical import canonical_json
from abicheck.storage.dto import (
    SECTION_SCHEMA_VERSIONS,
    SEMANTIC_IR_SECTION_KIND,
    SectionDTO,
    migrate_section_dto,
    semantic_ir_from_dto,
    semantic_ir_to_dto,
)


class TestSectionDTO:
    def test_round_trips(self) -> None:
        dto = SectionDTO(
            section_kind="graph", section_schema_version=1, payload={"a": 1}
        )
        assert SectionDTO.from_dict(dto.to_dict()) == dto

    def test_empty_section_kind_is_refused(self) -> None:
        with pytest.raises(ValueError):
            SectionDTO(section_kind="", section_schema_version=1, payload={})

    @pytest.mark.parametrize("version", [0, -1])
    def test_non_positive_version_is_refused(self, version: int) -> None:
        with pytest.raises(ValueError):
            SectionDTO(section_kind="graph", section_schema_version=version, payload={})

    def test_a_float_version_is_refused(self) -> None:
        with pytest.raises(TypeError):
            SectionDTO(section_kind="graph", section_schema_version=1.0, payload={})  # type: ignore[arg-type]

    @pytest.mark.parametrize("payload", [None, [], "x", 1])
    def test_a_non_mapping_payload_is_refused(self, payload: Any) -> None:
        with pytest.raises(TypeError):
            SectionDTO(section_kind="graph", section_schema_version=1, payload=payload)

    @pytest.mark.parametrize("data", [None, [], "x", 1])
    def test_from_dict_refuses_a_non_mapping_document(self, data: Any) -> None:
        with pytest.raises(TypeError):
            SectionDTO.from_dict(data)

    @pytest.mark.parametrize(
        "field_name", ["section_kind", "section_schema_version", "payload"]
    )
    def test_from_dict_requires_every_field(self, field_name: str) -> None:
        data = {
            "section_kind": "graph",
            "section_schema_version": 1,
            "payload": {},
        }
        del data[field_name]
        with pytest.raises(ValueError):
            SectionDTO.from_dict(data)

    @given(
        keys=st.lists(
            st.text(min_size=1, max_size=6, alphabet="abcdefg"),
            min_size=1,
            max_size=6,
            unique=True,
        )
    )
    def test_payload_key_insertion_order_never_changes_the_persisted_bytes(
        self, keys: list[str]
    ) -> None:
        """The D8 property test `one-semantic-pipeline.md`'s Phase 8 names
        explicitly: renaming an internal field / reordering a dataclass's
        declared fields must not change any persisted DTO's bytes. This is
        the general form of that claim at the one place every section's
        content ultimately passes through — `SectionDTO.to_dict()`, written
        via `canonical_json` (which sorts every mapping's keys, D5) — stated
        as a property over payload key order rather than pinned to one
        concrete domain type, so it holds regardless of which field of which
        future section happens to be renamed or reordered.
        """
        values = {key: index for index, key in enumerate(keys)}
        forward = {key: values[key] for key in keys}
        backward = {key: values[key] for key in reversed(keys)}
        dto_forward = SectionDTO(
            section_kind="graph", section_schema_version=1, payload=forward
        )
        dto_backward = SectionDTO(
            section_kind="graph", section_schema_version=1, payload=backward
        )
        assert canonical_json(dto_forward.to_dict()) == canonical_json(
            dto_backward.to_dict()
        )


class TestMigrateSectionDTO:
    def test_a_current_version_is_a_no_op(self) -> None:
        dto = SectionDTO(
            section_kind=SEMANTIC_IR_SECTION_KIND,
            section_schema_version=SECTION_SCHEMA_VERSIONS[SEMANTIC_IR_SECTION_KIND],
            payload={},
        )
        assert migrate_section_dto(dto) == dto

    def test_an_unregistered_section_kind_is_refused(self) -> None:
        dto = SectionDTO(
            section_kind="not_a_real_kind", section_schema_version=1, payload={}
        )
        with pytest.raises(ValueError):
            migrate_section_dto(dto)

    def test_a_version_with_no_migration_step_is_refused(self) -> None:
        dto = SectionDTO(
            section_kind=SEMANTIC_IR_SECTION_KIND,
            section_schema_version=999,
            payload={},
        )
        with pytest.raises(ValueError):
            migrate_section_dto(dto)


def _entity(spelling: str) -> CanonicalEntity:
    return CanonicalEntity(canonical_spelling=Fact.present(spelling))


class TestSemanticIRDTO:
    def test_round_trips_through_a_dict_document(self) -> None:
        eid = entity_id_for_type((Namespace("ns"), Record("Outer")), "Inner")
        occ = OccurrenceId(eid, disambiguator="tu-a")
        ir = SemanticIR(occurrences={occ: _entity("ns::Outer::Inner")})
        dto = semantic_ir_to_dto(ir, {"a": "conflict"})
        reloaded = SectionDTO.from_dict(dto.to_dict())
        ir2, conflicts2 = semantic_ir_from_dto(reloaded)
        assert ir2 == ir
        assert conflicts2 == {"a": "conflict"}

    def test_an_empty_ir_and_no_conflicts_round_trips_to_none(self) -> None:
        dto = semantic_ir_to_dto(None, {})
        ir2, conflicts2 = semantic_ir_from_dto(dto)
        assert ir2 is None
        assert conflicts2 == {}

    def test_wrong_section_kind_is_refused(self) -> None:
        dto = SectionDTO(section_kind="graph", section_schema_version=1, payload={})
        with pytest.raises(ValueError):
            semantic_ir_from_dto(dto)

    def test_occurrence_insertion_order_does_not_change_the_persisted_bytes(
        self,
    ) -> None:
        eid_a = entity_id_for_type((Namespace("ns"),), "A")
        eid_b = entity_id_for_type((Namespace("ns"),), "B")
        occ_a = OccurrenceId(eid_a, disambiguator="")
        occ_b = OccurrenceId(eid_b, disambiguator="")
        forward = SemanticIR(
            occurrences={occ_a: _entity("ns::A"), occ_b: _entity("ns::B")}
        )
        backward = SemanticIR(
            occurrences={occ_b: _entity("ns::B"), occ_a: _entity("ns::A")}
        )
        dto_forward = semantic_ir_to_dto(forward, {})
        dto_backward = semantic_ir_to_dto(backward, {})
        assert canonical_json(dto_forward.to_dict()) == canonical_json(
            dto_backward.to_dict()
        )
