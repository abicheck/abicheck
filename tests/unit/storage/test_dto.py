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
    BINARY_SECTION_KIND,
    BUILD_SECTION_KIND,
    DEBUG_SECTION_KIND,
    DECLARATIONS_SECTION_KIND,
    GRAPH_SECTION_KIND,
    LAYOUT_SECTION_KIND,
    PROVENANCE_SECTION_KIND,
    SECTION_SCHEMA_VERSIONS,
    SEMANTIC_IR_SECTION_KIND,
    TYPES_SECTION_KIND,
    SectionDTO,
    binary_from_dto,
    binary_to_dto,
    build_from_dto,
    build_to_dto,
    debug_from_dto,
    debug_to_dto,
    declarations_from_dto,
    declarations_to_dto,
    graph_from_dto,
    graph_to_dto,
    layout_from_dto,
    layout_to_dto,
    legacy_section_from_dto,
    legacy_section_to_dto,
    migrate_section_dto,
    provenance_from_dto,
    provenance_to_dto,
    semantic_ir_from_dto,
    semantic_ir_to_dto,
    types_from_dto,
    types_to_dto,
)
from abicheck.storage.graph_section_codec import GraphSection
from abicheck.storage.sparse_section_codec import (
    BinarySection,
    BuildSection,
    DebugSection,
    DeclarationsSection,
    LayoutSection,
    ProvenanceSection,
)
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

    def test_round_trips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # As of ADR-063 Track 4 (8B)'s third slice, every real
        # `LEGACY_SECTION_KINDS` member has its own dedicated DTO -- there is
        # no longer a live section kind this generic pass-through actually
        # serves (see `TestGraphSectionDTO`/`TestSparseSectionDTOs` for their
        # own dedicated coverage). It stays defined as the fallback a
        # future, not-yet-specialized ninth section kind would use
        # (`legacy_section_to_dto`'s own docstring), so this test proves
        # that fallback path still works by registering one such synthetic
        # kind rather than asserting the function is merely unreachable.
        import abicheck.storage.dto as dto_module

        monkeypatch.setitem(dto_module.SECTION_SCHEMA_VERSIONS, "future_section", 1)
        # `legacy_section_from_dto`'s own guard is stricter than
        # `legacy_section_to_dto`'s (it also requires D8 vocabulary
        # membership, `LEGACY_SECTION_KINDS`, not just a registered DTO
        # version) -- patch that too so the synthetic kind is a fully valid
        # not-yet-specialized section for both halves of the round trip.
        monkeypatch.setattr(
            dto_module,
            "LEGACY_SECTION_KINDS",
            (*dto_module.LEGACY_SECTION_KINDS, "future_section"),
        )
        dto = legacy_section_to_dto("future_section", {"a": 1})
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

    def test_constructor_refuses_a_non_mapping_surface_graph_directly(self) -> None:
        """Codex review, PR #1044: a caller constructing `GraphSection`
        directly (bypassing `from_document`'s own validation) with a
        `list`/sequence `surface_graph` must be rejected, not silently
        coerced by `dict(...)` into an empty or fabricated graph."""
        with pytest.raises(ValueError, match="must be a mapping"):
            GraphSection(surface_graph=[])  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="must be a mapping"):
            GraphSection(surface_graph=[("nodes", [])])  # type: ignore[arg-type]

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


#: ADR-063 Track 4 (8B), third slice: one entry per remaining sparse legacy
#: section, `(cls, kind, to_dto, from_dto, required_kwargs, extra)` --
#: `required_kwargs` is exactly what a real document always carries for that
#: section (`_REQUIRED_SECTION_FIELDS`), `extra` is a representative
#: optional-field payload including at least one nested list (to exercise
#: the deep-freeze/unfreeze round trip the way `TestTypesSectionDTO`/
#: `TestGraphSectionDTO` already do for their own single field).
_SPARSE_SECTION_CASES = [
    pytest.param(
        BinarySection,
        BINARY_SECTION_KIND,
        binary_to_dto,
        binary_from_dto,
        {"elf": {"symbols": ["a"]}, "pe": None, "macho": None},
        {"platform": "linux", "kabi": None},
        id="binary",
    ),
    pytest.param(
        DeclarationsSection,
        DECLARATIONS_SECTION_KIND,
        declarations_to_dto,
        declarations_from_dto,
        {
            "functions": [{"name": "f"}],
            "variables": [],
            "enums": [],
            "typedefs": {"MyInt": "int"},
            "sycl": None,
        },
        {"constants": {"C": "1"}},
        id="declarations",
    ),
    pytest.param(
        LayoutSection,
        LAYOUT_SECTION_KIND,
        layout_to_dto,
        layout_from_dto,
        {},
        {"contract": {"scope": "public"}, "scope_fallback": "elf-symbols"},
        id="layout",
    ),
    pytest.param(
        DebugSection,
        DEBUG_SECTION_KIND,
        debug_to_dto,
        debug_from_dto,
        {"dwarf": {"present": True}, "dwarf_advanced": None},
        {"ast_producer": "clang", "ast_compile_args": ["-std=c++17"]},
        id="debug",
    ),
    pytest.param(
        BuildSection,
        BUILD_SECTION_KIND,
        build_to_dto,
        build_from_dto,
        {},
        {"build_source": {"kind": "cmake"}},
        id="build",
    ),
    pytest.param(
        ProvenanceSection,
        PROVENANCE_SECTION_KIND,
        provenance_to_dto,
        provenance_from_dto,
        {"library": "libfoo.so.1", "version": "1.0.0"},
        {"git_commit": "abc123", "dependency_info": {"nodes": [], "edges": []}},
        id="provenance",
    ),
]


class TestSparseSectionDTOs:
    """ADR-063 Track 4 (8B), third slice: the six remaining legacy sections'
    typed DTOs (`sparse_section_codec.py`) -- parametrized across all six
    since the contract each must satisfy is identical (only the field names
    and required/optional split differ)."""

    @pytest.mark.parametrize(
        "cls,kind,to_dto,from_dto,required,extra", _SPARSE_SECTION_CASES
    )
    def test_round_trips_through_a_dict_document(
        self,
        cls: Any,
        kind: str,
        to_dto: Any,
        from_dto: Any,
        required: dict,
        extra: dict,
    ) -> None:
        section = cls(**required, extra=extra)
        dto = to_dto(section)
        assert dto.section_kind == kind
        reloaded = SectionDTO.from_dict(dto.to_dict())
        section2 = from_dto(reloaded)
        assert section2 == section
        # The document merges required fields + extra back into one flat
        # mapping -- exactly `split_legacy_document`'s own section-payload
        # shape, so a round trip through this wrapper changes nothing about
        # the stored keys.
        document = section2.to_document()
        assert document == {**required, **extra}

    @pytest.mark.parametrize(
        "cls,kind,to_dto,from_dto,required,extra", _SPARSE_SECTION_CASES
    )
    def test_from_document_refuses_a_non_mapping_payload(
        self,
        cls: Any,
        kind: str,
        to_dto: Any,
        from_dto: Any,
        required: dict,
        extra: dict,
    ) -> None:
        with pytest.raises(ValueError, match="must be a mapping"):
            cls.from_document(["not", "a", "mapping"])

    @pytest.mark.parametrize(
        "cls,kind,to_dto,from_dto,required,extra", _SPARSE_SECTION_CASES
    )
    def test_from_document_refuses_an_unknown_extra_key(
        self,
        cls: Any,
        kind: str,
        to_dto: Any,
        from_dto: Any,
        required: dict,
        extra: dict,
    ) -> None:
        payload = {**required, "totally_unknown_field": 1}
        with pytest.raises(ValueError, match="may only carry"):
            cls.from_document(payload)

    @pytest.mark.parametrize(
        "cls,kind,to_dto,from_dto,required,extra", _SPARSE_SECTION_CASES
    )
    def test_wrong_section_kind_is_refused(
        self,
        cls: Any,
        kind: str,
        to_dto: Any,
        from_dto: Any,
        required: dict,
        extra: dict,
    ) -> None:
        other_kind = "types" if kind != "types" else "graph"
        dto = SectionDTO(section_kind=other_kind, section_schema_version=1, payload={})
        with pytest.raises(ValueError, match="expected section kind"):
            from_dto(dto)

    @pytest.mark.parametrize(
        "cls,kind,to_dto,from_dto,required,extra", _SPARSE_SECTION_CASES
    )
    def test_nested_lists_are_deep_unfrozen_not_left_as_tuples(
        self,
        cls: Any,
        kind: str,
        to_dto: Any,
        from_dto: Any,
        required: dict,
        extra: dict,
    ) -> None:
        section = cls(**required, extra=extra)
        dto = to_dto(section)
        reloaded = SectionDTO.from_dict(dto.to_dict())
        section2 = from_dto(reloaded)
        document = section2.to_document()
        for value in document.values():
            if isinstance(value, list):
                assert not any(isinstance(v, tuple) for v in value)
        # json.dumps must accept the reconstructed document -- a leftover
        # MappingProxyType/tuple would raise.
        import json

        json.dumps(document)

    @pytest.mark.parametrize(
        "cls,kind,to_dto,from_dto,required,extra", _SPARSE_SECTION_CASES
    )
    def test_legacy_section_to_dto_refuses_the_kind(
        self,
        cls: Any,
        kind: str,
        to_dto: Any,
        from_dto: Any,
        required: dict,
        extra: dict,
    ) -> None:
        with pytest.raises(ValueError, match="not a legacy section kind"):
            legacy_section_to_dto(kind, {**required, **extra})

    @pytest.mark.parametrize(
        "cls,kind,to_dto,from_dto,required,extra", _SPARSE_SECTION_CASES
    )
    def test_legacy_section_from_dto_refuses_the_kind(
        self,
        cls: Any,
        kind: str,
        to_dto: Any,
        from_dto: Any,
        required: dict,
        extra: dict,
    ) -> None:
        dto = SectionDTO(
            section_kind=kind,
            section_schema_version=SECTION_SCHEMA_VERSIONS[kind],
            payload={**required, **extra},
        )
        with pytest.raises(ValueError, match="not a legacy section kind"):
            legacy_section_from_dto(dto)

    @pytest.mark.parametrize(
        "cls,kind,to_dto,from_dto,required,extra", _SPARSE_SECTION_CASES
    )
    def test_extra_is_frozen_against_caller_mutation(
        self,
        cls: Any,
        kind: str,
        to_dto: Any,
        from_dto: Any,
        required: dict,
        extra: dict,
    ) -> None:
        """Mirrors `TestTypesSectionDTO`/`TestGraphSectionDTO`'s identical
        test: `__post_init__` freezes `extra` (and every required field) the
        same way `SectionDTO.__post_init__`/`TypesSection.__post_init__`
        already do, so a caller's own mutable object (or a later document
        handed back by `to_document()`) cannot silently change this
        section's content after construction."""
        mutable_extra = dict(extra)
        section = cls(**required, extra=mutable_extra)
        mutable_extra["totally_new_key_after_construction"] = "x"
        assert "totally_new_key_after_construction" not in section.to_document()

        document = section.to_document()
        document["totally_new_key_injected"] = "y"
        assert "totally_new_key_injected" not in section.to_document()

    def test_only_required_fields_are_omitted_when_missing_a_key(self) -> None:
        """A section with *no* required fields (`layout`/`build`) accepts an
        entirely empty payload -- the section is only ever created by
        `split_legacy_document` when at least one field is present, but the
        DTO layer itself has no reason to additionally forbid an empty one
        (it is a legitimate, if degenerate, document)."""
        assert LayoutSection.from_document({}) == LayoutSection(extra={})
        assert BuildSection.from_document({}) == BuildSection(extra={})

    @pytest.mark.parametrize(
        "cls,kind,required_keys",
        [
            (BinarySection, BINARY_SECTION_KIND, ("elf", "pe", "macho")),
            (
                DeclarationsSection,
                DECLARATIONS_SECTION_KIND,
                ("functions", "variables", "enums", "typedefs", "sycl"),
            ),
            (DebugSection, DEBUG_SECTION_KIND, ("dwarf", "dwarf_advanced")),
            (ProvenanceSection, PROVENANCE_SECTION_KIND, ("library", "version")),
        ],
    )
    def test_from_document_refuses_each_missing_required_field_individually(
        self, cls: Any, kind: str, required_keys: tuple[str, ...]
    ) -> None:
        full = {name: None for name in required_keys}
        for missing_key in required_keys:
            payload = {k: v for k, v in full.items() if k != missing_key}
            with pytest.raises(ValueError, match="must carry"):
                cls.from_document(payload)

    @pytest.mark.parametrize(
        "payload",
        [
            {"elf": [], "pe": None, "macho": None},
            {"elf": None, "pe": "not-a-mapping", "macho": None},
            {"elf": None, "pe": None, "macho": 1},
        ],
    )
    def test_binary_from_document_refuses_a_malformed_required_field(
        self, payload: dict
    ) -> None:
        """Codex review, PR #1044: a required field's own top-level wire
        shape is checked before freezing -- `elf: []` (a `list`, when
        `AbiSnapshot.elf` is `ElfMetadata | None`, so `null` or a mapping)
        must be rejected, not silently frozen and later read back by
        `serialization.snapshot_from_dict` as a confirmed-absent `elf`,
        turning corrupted evidence into missing evidence."""
        with pytest.raises(ValueError, match="must be a mapping"):
            BinarySection.from_document(payload)

    def test_declarations_from_document_refuses_malformed_required_fields(
        self,
    ) -> None:
        base = {
            "functions": [],
            "variables": [],
            "enums": [],
            "typedefs": {},
            "sycl": None,
        }
        with pytest.raises(ValueError, match="must be a list"):
            DeclarationsSection.from_document({**base, "functions": {}})
        with pytest.raises(ValueError, match="must be a mapping"):
            DeclarationsSection.from_document({**base, "typedefs": []})
        with pytest.raises(ValueError, match="must be a mapping"):
            DeclarationsSection.from_document({**base, "sycl": "nope"})

    def test_debug_from_document_refuses_malformed_required_fields(self) -> None:
        with pytest.raises(ValueError, match="must be a mapping"):
            DebugSection.from_document({"dwarf": [], "dwarf_advanced": None})
        with pytest.raises(ValueError, match="must be a mapping"):
            DebugSection.from_document({"dwarf": None, "dwarf_advanced": 1})

    def test_provenance_from_document_refuses_malformed_required_fields(self) -> None:
        with pytest.raises(ValueError, match="must be a str"):
            ProvenanceSection.from_document({"library": None, "version": "1.0.0"})
        with pytest.raises(ValueError, match="must be a str"):
            ProvenanceSection.from_document({"library": "libfoo.so.1", "version": 1})

    def test_layout_and_build_have_no_shape_checked_required_fields(self) -> None:
        """Neither section has any `REQUIRED_FIELDS` at all (both postdate
        schema v1 entirely), so there is nothing for
        `REQUIRED_FIELD_SHAPES` to name -- this pins that the shape-
        validation machinery is inert for them, not merely untested."""
        assert LayoutSection.REQUIRED_FIELD_SHAPES == {}
        assert BuildSection.REQUIRED_FIELD_SHAPES == {}

    @pytest.mark.parametrize(
        "cls,kind,required,extra",
        [
            (BuildSection, BUILD_SECTION_KIND, {}, {"build_source": []}),
            (
                DebugSection,
                DEBUG_SECTION_KIND,
                {"dwarf": None, "dwarf_advanced": None},
                {"fact_provenance": []},
            ),
        ],
    )
    def test_from_document_refuses_a_malformed_optional_field(
        self, cls: Any, kind: str, required: dict, extra: dict
    ) -> None:
        """Codex review, PR #1044, second round: the exact reproduction
        cases named in the finding -- `_freeze_extra` previously validated
        only which *keys* `extra` may carry, not each key's own value
        shape, so a malformed optional field would freeze and round-trip
        unchanged instead of being rejected."""
        with pytest.raises(ValueError, match="must be a mapping"):
            cls.from_document({**required, **extra})

    @pytest.mark.parametrize(
        "cls",
        [
            BinarySection,
            DeclarationsSection,
            LayoutSection,
            DebugSection,
            BuildSection,
            ProvenanceSection,
        ],
    )
    def test_every_optional_field_has_a_declared_shape(self, cls: Any) -> None:
        """`OPTIONAL_FIELD_SHAPES` is meant to be exhaustive (unlike
        `REQUIRED_FIELD_SHAPES`, which only covers fields that need it) --
        pin that no `OPTIONAL_FIELDS` entry was missed across all six
        sections, so a newly-added optional field fails this test rather
        than silently reaching `extra` unchecked."""
        assert set(cls.OPTIONAL_FIELD_SHAPES) == cls.OPTIONAL_FIELDS

    def test_ast_compile_args_rejects_a_set_a_real_list_and_tuple_still_work(
        self,
    ) -> None:
        """Codex review, PR #1044, fourth round: `ast_compile_args` is a
        real compiler invocation's ordered argument list -- accepting a
        `set` let `canonical_form`'s own sorting silently invent an
        argument order a `set` never had, turning real provenance into
        fabricated provenance. `list`/`tuple` (the real `AbiSnapshot.
        ast_compile_args` wire/attribute shapes) must still work."""
        with pytest.raises(ValueError, match="must be a list"):
            DebugSection.from_document(
                {"dwarf": None, "dwarf_advanced": None, "ast_compile_args": {"-O2"}}
            )
        DebugSection.from_document(
            {"dwarf": None, "dwarf_advanced": None, "ast_compile_args": ["-O2"]}
        )
        DebugSection(
            dwarf=None, dwarf_advanced=None, extra={"ast_compile_args": ("-O2",)}
        )

    def test_build_context_defines_accepts_a_set(self) -> None:
        """The one genuinely unordered field -- `set`/`frozenset` must still
        be accepted here, unlike `ast_compile_args` above."""
        section = DebugSection.from_document(
            {
                "dwarf": None,
                "dwarf_advanced": None,
                "build_context_defines": {"FOO", "BAR"},
            }
        )
        assert set(section.to_document()["build_context_defines"]) == {"FOO", "BAR"}

    def test_source_size_rejects_a_fractional_value(self) -> None:
        """Codex review, PR #1044, fourth round: `source_size` is `int |
        None` (`Path.stat().st_size` is always an `int`) -- a fractional
        value must be rejected rather than silently persisted and later
        breaking `fold_l0_hard_removals`'s binary-identity comparison."""
        with pytest.raises(ValueError, match="must be a int or none"):
            BinarySection.from_document(
                {"elf": None, "pe": None, "macho": None, "source_size": 1.5}
            )
        # A real int must still work.
        BinarySection.from_document(
            {"elf": None, "pe": None, "macho": None, "source_size": 1024}
        )
