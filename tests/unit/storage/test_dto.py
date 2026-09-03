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
    GRAPH_SECTION_KIND,
    SECTION_SCHEMA_VERSIONS,
    SEMANTIC_IR_SECTION_KIND,
    TYPES_SECTION_KIND,
    SectionDTO,
    graph_from_dto,
    graph_to_dto,
    legacy_section_from_dto,
    legacy_section_to_dto,
    migrate_section_dto,
    semantic_ir_from_dto,
    semantic_ir_to_dto,
    types_from_dto,
    types_to_dto,
)
from abicheck.storage.graph_section_codec import GraphSection
from abicheck.storage.types_section_codec import TypesSection


class TestSectionDTO:
    def test_round_trips(self) -> None:
        dto = SectionDTO(
            section_kind="graph", section_schema_version=1, payload={"a": 1}
        )
        assert SectionDTO.from_dict(dto.to_dict()) == dto

    def test_mutating_the_caller_s_own_mapping_after_construction_is_inert(
        self,
    ) -> None:
        """`SectionDTO` must hold its own, independent copy of `payload` —
        mutating the mapping the caller passed in must never change what the
        DTO reports or serializes."""
        caller_payload = {"a": 1, "nested": {"x": [1, 2, 3]}}
        dto = SectionDTO(
            section_kind="graph", section_schema_version=1, payload=caller_payload
        )
        before = dto.to_dict()

        caller_payload["a"] = 999
        caller_payload["nested"]["x"].append(4)
        caller_payload["new_key"] = "surprise"

        assert dto.to_dict() == before
        assert dto.payload["a"] == 1
        assert dto.payload["nested"]["x"] == (1, 2, 3)  # type: ignore[index]
        assert "new_key" not in dto.payload

    def test_dto_payload_is_immutable_at_the_top_level(self) -> None:
        """`dto.payload` is a `MappingProxyType`, not a plain `dict` -- item
        assignment is refused outright rather than merely discouraged."""
        dto = SectionDTO(
            section_kind="graph", section_schema_version=1, payload={"a": 1}
        )
        with pytest.raises(TypeError):
            dto.payload["a"] = 999  # type: ignore[index]

    def test_dto_payload_is_immutable_at_every_nested_level(self) -> None:
        """A payload's nested mapping/sequence values are frozen too --
        `MappingProxyType`/`tuple`, not a `dict`/`list` a caller could still
        reach in and mutate (Codex review: a shallow copy on construction
        stops the caller's own mapping from aliasing this DTO's storage, but
        does nothing once the values *inside* that storage are themselves
        mutable)."""
        dto = SectionDTO(
            section_kind="graph",
            section_schema_version=1,
            payload={"nested": {"x": [1, 2, 3]}},
        )
        nested = dto.payload["nested"]
        with pytest.raises(TypeError):
            nested["x"] = "surprise"  # type: ignore[index]
        inner_list = dto.payload["nested"]["x"]  # type: ignore[index]
        assert isinstance(inner_list, tuple)
        with pytest.raises(AttributeError):
            inner_list.append(4)  # type: ignore[attr-defined]

    def test_to_dict_returns_a_detached_mutable_copy(self) -> None:
        """`to_dict()`'s return value must be an ordinary, mutable
        `dict`/`list` tree a caller can freely edit -- and editing it,
        including a *nested* value, must never reach back into the DTO's
        own frozen storage (Codex review: `to_dict()` previously copied only
        the outer mapping, so a caller mutating a nested value in its return
        still mutated this frozen DTO's own content)."""
        dto = SectionDTO(
            section_kind="graph",
            section_schema_version=1,
            payload={"nested": {"x": [1, 2, 3]}},
        )
        encoded = dto.to_dict()
        assert isinstance(encoded["payload"], dict)
        assert isinstance(encoded["payload"]["nested"], dict)
        assert isinstance(encoded["payload"]["nested"]["x"], list)
        encoded["payload"]["nested"]["x"].append(4)
        encoded["payload"]["new_key"] = "surprise"
        assert dto.payload["nested"]["x"] == (1, 2, 3)  # type: ignore[index]
        assert "new_key" not in dto.payload

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

    def test_a_registered_migration_step_actually_runs(self, monkeypatch) -> None:
        """`_MIGRATIONS` is empty for every real section kind today (no
        section has shipped a second version yet -- see this module's own
        docstring), so the loop body that actually calls a registered step
        and advances `version` is otherwise dead code in this build.
        Registering a throwaway step here is the only way to exercise it."""
        import abicheck.storage.dto as dto_module

        monkeypatch.setitem(
            dto_module._MIGRATIONS,
            "graph",
            {1: lambda payload: {**payload, "migrated": True}},
        )
        monkeypatch.setitem(dto_module.SECTION_SCHEMA_VERSIONS, "graph", 2)
        dto = SectionDTO(
            section_kind="graph", section_schema_version=1, payload={"a": 1}
        )
        migrated = migrate_section_dto(dto)
        assert migrated.section_schema_version == 2
        assert migrated.payload == {"a": 1, "migrated": True}


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


class TestLegacySectionDTO:
    """`legacy_section_to_dto`/`legacy_section_from_dto` must agree with
    each other about which section kinds are legacy ones -- a `semantic_ir`
    DTO has its own decoder (`semantic_ir_from_dto`), so
    `legacy_section_from_dto` accepting one and returning its raw payload
    would silently bypass it (CodeRabbit review, symmetric with the
    `legacy_section_to_dto` refusal that already existed)."""

    def test_round_trips(self) -> None:
        # "layout" -- a still-generic legacy section kind. "graph" moved to
        # its own dedicated `GraphSection` DTO this ADR-063 Track 4 (8B)
        # slice, so it is no longer a valid stand-in here (see
        # `TestGraphSectionDTO` below for its own dedicated coverage).
        dto = legacy_section_to_dto("layout", {"a": 1})
        assert legacy_section_from_dto(dto) == {"a": 1}

    def test_encoding_a_semantic_ir_kind_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a legacy section kind"):
            legacy_section_to_dto(SEMANTIC_IR_SECTION_KIND, {})

    def test_encoding_an_unknown_section_kind_is_refused(self) -> None:
        """The sibling of `test_decoding_an_unknown_section_kind_is_refused`
        below, for the encode direction -- `not_a_real_kind` isn't in
        `SECTION_SCHEMA_VERSIONS` at all, so this exercises the *first*
        operand of the `or` short-circuiting True on its own, distinct from
        every other test here (which all reach this check via the second
        operand, a genuinely-known-but-specialized kind)."""
        with pytest.raises(ValueError, match="not a legacy section kind"):
            legacy_section_to_dto("not_a_real_kind", {})

    def test_decoding_a_semantic_ir_kind_dto_is_refused(self) -> None:
        dto = SectionDTO(
            section_kind=SEMANTIC_IR_SECTION_KIND,
            section_schema_version=SECTION_SCHEMA_VERSIONS[SEMANTIC_IR_SECTION_KIND],
            payload={},
        )
        with pytest.raises(ValueError, match="not a legacy section kind"):
            legacy_section_from_dto(dto)

    def test_decoding_an_unknown_section_kind_is_refused(self) -> None:
        dto = SectionDTO(
            section_kind="not_a_real_kind", section_schema_version=1, payload={}
        )
        with pytest.raises(ValueError, match="not a legacy section kind"):
            legacy_section_from_dto(dto)

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


class TestTypesSectionDTO:
    """ADR-063 Track 4 (8B): `TypesSection`, the `"types"` D8 legacy
    section's own typed DTO."""

    def test_round_trips_through_a_dict_document(self) -> None:
        section = TypesSection(types=({"kind": "record", "name": "Foo"},))
        dto = types_to_dto(section)
        reloaded = SectionDTO.from_dict(dto.to_dict())
        section2 = types_from_dto(reloaded)
        assert section2 == section

    def test_from_document_refuses_a_non_mapping_payload(self) -> None:
        with pytest.raises(ValueError, match="must be a mapping"):
            TypesSection.from_document(["not", "a", "mapping"])  # type: ignore[arg-type]

    def test_from_document_refuses_a_non_list_types_value(self) -> None:
        with pytest.raises(ValueError, match="must carry a 'types' list"):
            TypesSection.from_document({"types": "not-a-list"})

    def test_from_document_refuses_a_missing_types_key(self) -> None:
        with pytest.raises(ValueError, match="must carry a 'types' list"):
            TypesSection.from_document({})

    def test_from_document_refuses_extra_keys(self) -> None:
        with pytest.raises(ValueError, match="may only carry 'types'"):
            TypesSection.from_document({"types": [], "extra": 1})

    def test_wrong_section_kind_is_refused(self) -> None:
        dto = SectionDTO(section_kind="graph", section_schema_version=1, payload={})
        with pytest.raises(ValueError):
            types_from_dto(dto)

    def test_nested_lists_are_deep_unfrozen_not_left_as_tuples(self) -> None:
        """Codex review, fresh evidence: `SectionDTO.payload` freezes every
        nested mapping/list recursively (`_freeze`) — `types_from_dto` must
        read back through `to_dict()`'s own deep `_unfreeze`, not the frozen
        `payload` attribute directly, or a type entry's own nested list
        (e.g. a `RecordType`'s `bases`) would round-trip as a `tuple` while
        a freshly-dumped comparison side holds a plain `list`, producing a
        spurious mismatch a downstream detector reads as a real change.
        `TypesSection.types` is itself frozen internally (mirroring
        `SectionDTO.payload`), so the ordinary-`dict`/`list` assertion below
        goes through the public `to_document()` accessor, not `.types`
        directly."""
        section = TypesSection(types=({"name": "Foo", "bases": ["Base"]},))
        dto = types_to_dto(section)
        reloaded = SectionDTO.from_dict(dto.to_dict())
        section2 = types_from_dto(reloaded)
        entry = section2.to_document()["types"][0]
        assert isinstance(entry, dict)
        assert isinstance(entry["bases"], list)
        # json.dumps must accept the reconstructed document -- a leftover
        # MappingProxyType/tuple would raise.
        import json

        json.dumps(section2.to_document())

    def test_types_field_is_frozen_against_caller_mutation(self) -> None:
        """Codex review, fresh evidence: a `frozen=True` dataclass whose one
        field is a plain `tuple` of ordinary `dict`/`list` entries is not
        actually immutable -- the caller's own entry objects (or a document
        `to_document()` hands back) stay reachable and mutable, so mutating
        either could silently change a `TypesSection`'s own content after
        construction. `__post_init__` freezes every entry the same way
        `SectionDTO.__post_init__` already freezes its own `payload`."""
        original_entry = {"name": "Foo", "bases": ["Base"]}
        section = TypesSection(types=(original_entry,))
        original_entry["bases"].append("Mutated")
        assert section.to_document()["types"][0]["bases"] == ["Base"]

        document = section.to_document()
        document["types"][0]["bases"].append("Mutated")
        assert section.to_document()["types"][0]["bases"] == ["Base"]

    def test_legacy_section_to_dto_refuses_the_types_kind(self) -> None:
        with pytest.raises(ValueError, match="not a legacy section kind"):
            legacy_section_to_dto(TYPES_SECTION_KIND, {"types": []})

    def test_legacy_section_from_dto_refuses_a_types_kind_dto(self) -> None:
        dto = SectionDTO(
            section_kind=TYPES_SECTION_KIND,
            section_schema_version=SECTION_SCHEMA_VERSIONS[TYPES_SECTION_KIND],
            payload={"types": []},
        )
        with pytest.raises(ValueError, match="not a legacy section kind"):
            legacy_section_from_dto(dto)


class TestGraphSectionDTO:
    """ADR-063 Track 4 (8B), second slice: `GraphSection`, the `"graph"` D8
    legacy section's own typed DTO."""

    def test_round_trips_through_a_dict_document(self) -> None:
        section = GraphSection(surface_graph={"nodes": [], "edges": []})
        dto = graph_to_dto(section)
        reloaded = SectionDTO.from_dict(dto.to_dict())
        section2 = graph_from_dto(reloaded)
        assert section2 == section

    def test_from_document_refuses_a_non_mapping_payload(self) -> None:
        with pytest.raises(ValueError, match="must be a mapping"):
            GraphSection.from_document(["not", "a", "mapping"])  # type: ignore[arg-type]

    def test_from_document_refuses_a_non_mapping_surface_graph_value(self) -> None:
        with pytest.raises(ValueError, match="must carry a 'surface_graph' mapping"):
            GraphSection.from_document({"surface_graph": "not-a-mapping"})

    def test_from_document_refuses_a_missing_surface_graph_key(self) -> None:
        with pytest.raises(ValueError, match="must carry a 'surface_graph' mapping"):
            GraphSection.from_document({})

    def test_from_document_refuses_extra_keys(self) -> None:
        with pytest.raises(ValueError, match="may only carry 'surface_graph'"):
            GraphSection.from_document({"surface_graph": {}, "extra": 1})

    def test_wrong_section_kind_is_refused(self) -> None:
        dto = SectionDTO(section_kind="types", section_schema_version=1, payload={})
        with pytest.raises(ValueError):
            graph_from_dto(dto)

    def test_nested_lists_are_deep_unfrozen_not_left_as_tuples(self) -> None:
        """Mirrors `TestTypesSectionDTO`'s identical test: `SectionDTO
        .payload` freezes every nested mapping/list recursively -- reading
        back must go through `to_dict()`'s own deep `_unfreeze`, not the
        frozen `payload` attribute directly, or a nested list would
        round-trip as a `tuple` while a freshly-dumped comparison side holds
        a plain `list`."""
        section = GraphSection(surface_graph={"nodes": ["a", "b"]})
        dto = graph_to_dto(section)
        reloaded = SectionDTO.from_dict(dto.to_dict())
        section2 = graph_from_dto(reloaded)
        document = section2.to_document()
        assert isinstance(document["surface_graph"], dict)
        assert isinstance(document["surface_graph"]["nodes"], list)
        import json

        json.dumps(document)

    def test_surface_graph_field_is_frozen_against_caller_mutation(self) -> None:
        """Mirrors `TestTypesSectionDTO`'s identical test for `TypesSection
        .types`: a `frozen=True` dataclass whose one field is a plain
        `dict`/`list` tree is not actually immutable unless
        `__post_init__` freezes every reachable container too."""
        original = {"nodes": ["a"]}
        section = GraphSection(surface_graph=original)
        original["nodes"].append("mutated")
        assert section.to_document()["surface_graph"]["nodes"] == ["a"]

        document = section.to_document()
        document["surface_graph"]["nodes"].append("mutated")
        assert section.to_document()["surface_graph"]["nodes"] == ["a"]

    def test_legacy_section_to_dto_refuses_the_graph_kind(self) -> None:
        with pytest.raises(ValueError, match="not a legacy section kind"):
            legacy_section_to_dto(GRAPH_SECTION_KIND, {"surface_graph": {}})

    def test_legacy_section_from_dto_refuses_a_graph_kind_dto(self) -> None:
        dto = SectionDTO(
            section_kind=GRAPH_SECTION_KIND,
            section_schema_version=SECTION_SCHEMA_VERSIONS[GRAPH_SECTION_KIND],
            payload={"surface_graph": {}},
        )
        with pytest.raises(ValueError, match="not a legacy section kind"):
            legacy_section_from_dto(dto)
