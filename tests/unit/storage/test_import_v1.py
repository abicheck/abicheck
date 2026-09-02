# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""`abicheck.storage.import_v1` — the v1-v25 import adapter (ADR-062 A1.2,
`storage-format-v2.md` Phase 1 step 2).
"""

from __future__ import annotations

from typing import Any

import pytest

from abicheck.model.fact import Fact
from abicheck.model.identity import Namespace, Record, entity_id_for_type
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR
from abicheck.model.snapshot import AbiSnapshot
from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
from abicheck.storage.dto import (
    SEMANTIC_IR_SECTION_KIND,
    SectionDTO,
    semantic_ir_from_dto,
)
from abicheck.storage.import_v1 import (
    LEGACY_DOCUMENT_SECTION_KIND,
    import_legacy_snapshot as _import_legacy_snapshot,
)
from abicheck.storage.package import InMemoryObjectStore


def import_legacy_snapshot(*args: Any, **kwargs: Any) -> Any:
    """`import_legacy_snapshot`, defaulting `max_known_schema_version` to
    this build's real `serialization.SCHEMA_VERSION` — every test below
    exercises something other than that specific parameter, so this keeps
    them from each having to restate the same current value."""
    kwargs.setdefault("max_known_schema_version", SCHEMA_VERSION)
    return _import_legacy_snapshot(*args, **kwargs)


def _snapshot_with_ir() -> AbiSnapshot:
    eid = entity_id_for_type((Namespace("ns"), Record("Outer")), "Inner")
    occ = OccurrenceId(eid, disambiguator="tu-a")
    entity = CanonicalEntity(canonical_spelling=Fact.present("ns::Outer::Inner"))
    ir = SemanticIR(occurrences={occ: entity})
    return AbiSnapshot(library="libfoo.so.1", version="1.0.0", semantic_ir=ir)


class TestImportLegacySnapshot:
    def test_rejects_a_non_mapping_document(self) -> None:
        with pytest.raises(TypeError):
            import_legacy_snapshot(
                None,  # type: ignore[arg-type]
                store=InMemoryObjectStore(),
                artifact_id="libfoo",
            )

    def test_produces_one_variant_and_one_artifact(self) -> None:
        doc = snapshot_to_dict(_snapshot_with_ir())
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        assert [v.variant_id for v in manifest.variant_refs] == ["default"]
        assert [a.artifact_id for a in manifest.artifact_refs] == ["libfoo"]
        assert manifest.artifact_refs[0].variant_id == "default"
        assert manifest.variant_refs[0].artifact_ids == ("libfoo",)

    def test_records_the_source_schema_version(self) -> None:
        doc = snapshot_to_dict(_snapshot_with_ir())
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        assert manifest.versions.source_schema_version == doc["schema_version"]

    def test_a_document_with_no_schema_version_defaults_to_one(self) -> None:
        doc: dict[str, Any] = {"library": "libfoo.so.1"}
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        assert manifest.versions.source_schema_version == 1

    def test_semantic_ir_round_trips_through_its_own_section(self) -> None:
        snap = _snapshot_with_ir()
        doc = snapshot_to_dict(snap)
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        sections = manifest.artifact_refs[0].sections
        assert SEMANTIC_IR_SECTION_KIND in sections
        dto = SectionDTO.from_dict(store.get(sections[SEMANTIC_IR_SECTION_KIND].digest))
        ir, _conflicts = semantic_ir_from_dto(dto)
        assert ir == snap.semantic_ir

    def test_the_legacy_remainder_excludes_the_promoted_keys(self) -> None:
        doc = snapshot_to_dict(_snapshot_with_ir())
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        sections = manifest.artifact_refs[0].sections
        remainder = store.get(sections[LEGACY_DOCUMENT_SECTION_KIND].digest)
        assert "semantic_ir" not in remainder
        assert "semantic_ir_conflicts" not in remainder
        # Everything else survives untouched.
        assert remainder["library"] == "libfoo.so.1"

    def test_no_semantic_ir_section_when_the_snapshot_carries_none(self) -> None:
        doc = snapshot_to_dict(AbiSnapshot(library="libfoo.so.1", version="1.0.0"))
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        assert SEMANTIC_IR_SECTION_KIND not in manifest.artifact_refs[0].sections
        assert LEGACY_DOCUMENT_SECTION_KIND in manifest.artifact_refs[0].sections
        assert "semantic_ir" not in manifest.versions.section_schema_versions

    def test_custom_artifact_and_variant_ids_are_honored(self) -> None:
        doc = snapshot_to_dict(_snapshot_with_ir())
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(
            doc,
            store=store,
            artifact_id="mylib",
            variant_id="cpu-gcc",
            artifact_kind="pe",
        )
        assert manifest.artifact_refs[0].artifact_id == "mylib"
        assert manifest.artifact_refs[0].variant_id == "cpu-gcc"
        assert manifest.artifact_refs[0].kind == "pe"
        assert manifest.variant_refs[0].variant_id == "cpu-gcc"

    def test_storing_the_same_document_twice_deduplicates_content(self) -> None:
        doc = snapshot_to_dict(_snapshot_with_ir())
        store = InMemoryObjectStore()
        first = import_legacy_snapshot(doc, store=store, artifact_id="a")
        second = import_legacy_snapshot(doc, store=store, artifact_id="b")
        first_digest = (
            first.artifact_refs[0].sections[LEGACY_DOCUMENT_SECTION_KIND].digest
        )
        second_digest = (
            second.artifact_refs[0].sections[LEGACY_DOCUMENT_SECTION_KIND].digest
        )
        assert first_digest == second_digest


class TestMaxKnownSchemaVersion:
    """A document newer than this build knows how to interpret must be
    refused, not silently imported with an unrecognized `schema_version`
    stamped only on the informational `source_schema_version` axis."""

    def test_a_newer_schema_version_is_refused(self) -> None:
        doc = snapshot_to_dict(_snapshot_with_ir())
        doc["schema_version"] = SCHEMA_VERSION + 1
        store = InMemoryObjectStore()
        with pytest.raises(ValueError, match="newer than this build"):
            import_legacy_snapshot(doc, store=store, artifact_id="libfoo")

    def test_the_current_schema_version_is_accepted(self) -> None:
        doc = snapshot_to_dict(_snapshot_with_ir())
        doc["schema_version"] = SCHEMA_VERSION
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        assert manifest.versions.source_schema_version == SCHEMA_VERSION

    def test_an_older_schema_version_is_accepted(self) -> None:
        doc = snapshot_to_dict(_snapshot_with_ir())
        doc["schema_version"] = 1
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        assert manifest.versions.source_schema_version == 1

    def test_a_lower_explicit_ceiling_is_honored(self) -> None:
        """The parameter is the caller's own stated ceiling, not always
        `serialization.SCHEMA_VERSION` -- a caller asking for a stricter
        bound gets it."""
        doc = snapshot_to_dict(_snapshot_with_ir())
        doc["schema_version"] = 5
        store = InMemoryObjectStore()
        with pytest.raises(ValueError, match="newer than this build"):
            _import_legacy_snapshot(
                doc, store=store, artifact_id="libfoo", max_known_schema_version=4
            )

    @pytest.mark.parametrize(
        "malformed",
        [38.9, "38", True, None, [38], {"v": 38}],
    )
    def test_a_non_integral_schema_version_is_refused_not_truncated(
        self, malformed: object
    ) -> None:
        """`int(38.9)` truncates to `38`, which would silently manufacture a
        smaller, fabricated version that evades `max_known_schema_version`
        entirely -- a malformed value must be refused outright, never
        coerced into a plausible-looking int (Codex review, a second
        finding on this same field)."""
        doc = snapshot_to_dict(_snapshot_with_ir())
        doc["schema_version"] = malformed
        store = InMemoryObjectStore()
        with pytest.raises(ValueError, match="schema_version"):
            import_legacy_snapshot(doc, store=store, artifact_id="libfoo")

    def test_a_real_integer_schema_version_is_still_accepted(self) -> None:
        doc = snapshot_to_dict(_snapshot_with_ir())
        doc["schema_version"] = 3
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        assert manifest.versions.source_schema_version == 3
