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
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
from abicheck.storage.canonical import canonical_form
from abicheck.storage.dto import (
    BINARY_SECTION_KIND,
    BUILD_SECTION_KIND,
    DEBUG_SECTION_KIND,
    DECLARATIONS_SECTION_KIND,
    GRAPH_SECTION_KIND,
    LAYOUT_SECTION_KIND,
    PROVENANCE_SECTION_KIND,
    SEMANTIC_IR_SECTION_KIND,
    SectionDTO,
    binary_from_dto,
    build_from_dto,
    debug_from_dto,
    declarations_from_dto,
    graph_from_dto,
    layout_from_dto,
    provenance_from_dto,
    semantic_ir_from_dto,
)
from abicheck.storage.import_v1 import (
    export_legacy_snapshot,
    import_legacy_snapshot as _import_legacy_snapshot,
)
from abicheck.storage.legacy_sections import _SECTION_FIELDS, LEGACY_SECTION_KINDS
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
    return AbiSnapshot(
        library="libfoo.so.1",
        version="1.0.0",
        semantic_ir=ir,
        # A populated `surface_graph` exercises the `"graph"` section's own
        # `GraphSection` DTO branch in `test_export_round_trips_a_full_
        # document` below, the same way `semantic_ir` already exercises its
        # own specialized section here.
        surface_graph=SourceGraphSummary(graph_id="sha256:full-doc"),
    )


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
        # `version` alongside `library`: a real `snapshot_to_dict()` document
        # always carries both (`AbiSnapshot.version` has no default), and
        # since ADR-063 Track 4 (8B)'s third slice `ProvenanceSection.
        # from_document` now enforces that structurally at import time too
        # (not just at export), matching `_REQUIRED_SECTION_FIELDS
        # ["provenance"]`.
        doc: dict[str, Any] = {"library": "libfoo.so.1", "version": "1.0.0"}
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

    def test_graph_round_trips_through_its_own_section(self) -> None:
        """ADR-063 Track 4 (8B), second slice: the `"graph"` section is
        stored via `GraphSection`/`graph_to_dto`, not the generic
        `legacy_section_to_dto` pass-through -- verified the same way
        `test_semantic_ir_round_trips_through_its_own_section` verifies its
        own specialized section, by reading the stored `SectionDTO` back and
        decoding it through the dedicated decoder."""
        snap = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0.0",
            surface_graph=SourceGraphSummary(graph_id="sha256:abc"),
        )
        doc = snapshot_to_dict(snap)
        assert "surface_graph" in doc
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        sections = manifest.artifact_refs[0].sections
        assert GRAPH_SECTION_KIND in sections
        dto = SectionDTO.from_dict(store.get(sections[GRAPH_SECTION_KIND].digest))
        assert dto.section_kind == GRAPH_SECTION_KIND
        section = graph_from_dto(dto)
        assert section.to_document() == {"surface_graph": doc["surface_graph"]}

    @pytest.mark.parametrize(
        "section_kind,from_dto_fn",
        [
            (BINARY_SECTION_KIND, binary_from_dto),
            (DECLARATIONS_SECTION_KIND, declarations_from_dto),
            (LAYOUT_SECTION_KIND, layout_from_dto),
            (DEBUG_SECTION_KIND, debug_from_dto),
            (BUILD_SECTION_KIND, build_from_dto),
            (PROVENANCE_SECTION_KIND, provenance_from_dto),
        ],
    )
    def test_each_remaining_sparse_section_round_trips_through_its_own_section(
        self, section_kind: str, from_dto_fn: Any
    ) -> None:
        """ADR-063 Track 4 (8B), third slice: every remaining legacy section
        is stored via its own dedicated `sparse_section_codec.py` DTO, not
        the generic pass-through -- verified the same way `test_semantic_ir_
        round_trips_through_its_own_section`/`test_graph_round_trips_
        through_its_own_section` verify their own specialized sections.
        `_snapshot_with_ir()` carries real, non-default values for every
        field these six sections cover (library/version/functions/enums/...,
        an `elf`/`dwarf` metadata object, and so on) via `snapshot_to_dict`,
        so this exercises the real production shape, not a hand-built
        minimal document."""
        doc = snapshot_to_dict(_snapshot_with_ir())
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        sections = manifest.artifact_refs[0].sections
        assert section_kind in sections
        dto = SectionDTO.from_dict(store.get(sections[section_kind].digest))
        assert dto.section_kind == section_kind
        section = from_dto_fn(dto)
        # Every key this section owns (per `legacy_sections._SECTION_FIELDS`)
        # that the source document actually carries must survive the round
        # trip unchanged -- `canonical_form` only normalizes tuple/list
        # representation, never content.
        expected = {
            key: doc[key] for key in _SECTION_FIELDS[section_kind] if key in doc
        }
        assert canonical_form(section.to_document()) == canonical_form(expected)

    def test_the_legacy_sections_exclude_the_promoted_keys(self) -> None:
        doc = snapshot_to_dict(_snapshot_with_ir())
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        sections = manifest.artifact_refs[0].sections
        for kind in LEGACY_SECTION_KINDS:
            if kind not in sections:
                continue
            payload = store.get(sections[kind].digest)["payload"]
            assert "semantic_ir" not in payload
            assert "semantic_ir_conflicts" not in payload
            assert "schema_version" not in payload
        # `library` lands in the `provenance` section, untouched.
        provenance = store.get(sections["provenance"].digest)["payload"]
        assert provenance["library"] == "libfoo.so.1"

    def test_every_present_legacy_field_lands_in_exactly_one_section(self) -> None:
        doc = snapshot_to_dict(_snapshot_with_ir())
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        sections = manifest.artifact_refs[0].sections
        seen: dict[str, str] = {}
        for kind in LEGACY_SECTION_KINDS:
            if kind not in sections:
                continue
            payload = store.get(sections[kind].digest)["payload"]
            for key in payload:
                assert key not in seen, (
                    f"{key!r} appears in both {seen.get(key)!r} and {kind!r}"
                )
                seen[key] = kind
        expected = set(doc) - {"semantic_ir", "semantic_ir_conflicts", "schema_version"}
        assert set(seen) == expected

    def test_no_semantic_ir_section_when_the_snapshot_carries_none(self) -> None:
        doc = snapshot_to_dict(AbiSnapshot(library="libfoo.so.1", version="1.0.0"))
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        assert SEMANTIC_IR_SECTION_KIND not in manifest.artifact_refs[0].sections
        assert "provenance" in manifest.artifact_refs[0].sections
        assert "semantic_ir" not in manifest.versions.section_schema_versions

    def test_export_round_trips_a_full_document(self) -> None:
        snap = _snapshot_with_ir()
        doc = snapshot_to_dict(snap)
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        rebuilt = export_legacy_snapshot(
            manifest.artifact_refs[0],
            store=store,
            source_schema_version=manifest.versions.source_schema_version,
        )
        # `canonical_form` normalizes tuples the round trip through JSON-
        # shaped section storage already turns into lists (the DTO layer's
        # own storage format, not a lossy conversion this test should
        # penalize) -- the same normalization every section's own storage
        # already applies before hashing/comparing it.
        assert canonical_form(rebuilt) == canonical_form(doc)

    def test_export_round_trips_a_document_with_no_semantic_ir(self) -> None:
        doc = snapshot_to_dict(AbiSnapshot(library="libfoo.so.1", version="1.0.0"))
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        rebuilt = export_legacy_snapshot(
            manifest.artifact_refs[0],
            store=store,
            source_schema_version=manifest.versions.source_schema_version,
        )
        # `canonical_form` normalizes tuples the round trip through JSON-
        # shaped section storage already turns into lists (the DTO layer's
        # own storage format, not a lossy conversion this test should
        # penalize) -- the same normalization every section's own storage
        # already applies before hashing/comparing it.
        assert canonical_form(rebuilt) == canonical_form(doc)

    @pytest.mark.parametrize("malformed", [0, -1, -38])
    def test_export_refuses_an_unstated_source_schema_version(
        self, malformed: int
    ) -> None:
        """`StorageVersions.source_schema_version` normalizes a missing or
        malformed `manifest.json` value to `0`, its own 'unstated' sentinel
        -- `export_legacy_snapshot` must not inject that sentinel as a real
        legacy `schema_version` (it would silently change which reliability
        backfills `serialization.snapshot_from_dict` applies), so it must
        refuse a non-positive value outright rather than writing it into the
        rebuilt document (Codex review)."""
        doc = snapshot_to_dict(AbiSnapshot(library="libfoo.so.1", version="1.0.0"))
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        with pytest.raises(ValueError, match="positive"):
            export_legacy_snapshot(
                manifest.artifact_refs[0],
                store=store,
                source_schema_version=malformed,
            )

    def test_export_refuses_a_section_payload_missing_a_required_field(
        self,
    ) -> None:
        """A section whose *object* hashes and decodes fine can still have
        lost a field within its own JSON content -- `join_legacy_document`
        alone only checks that every *present* key belongs to the right
        section, not that every field a real write always includes is
        present. Left unchecked, dropping `functions` from the
        "declarations" payload would silently read back as `[]` once
        `snapshot_from_dict` parses the rebuilt document -- a false symbol
        removal, not a loud failure (Codex review)."""
        import dataclasses

        from abicheck.storage.package import ObjectRef

        # `snapshot_to_dict()` always emits `functions` (via `asdict()`, as
        # `[]` when there are none) -- the "declarations" section carries
        # the key regardless of whether any function is actually present,
        # so an empty snapshot already exercises the key-presence gap.
        doc = snapshot_to_dict(AbiSnapshot(library="libfoo.so.1", version="1.0.0"))
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        artifact = manifest.artifact_refs[0]
        old_ref = artifact.sections["declarations"]
        tampered = dict(store.get(old_ref.digest))
        payload = dict(tampered["payload"])
        assert "functions" in payload
        del payload["functions"]
        tampered["payload"] = payload
        new_digest = store.put(tampered)
        tampered_artifact = dataclasses.replace(
            artifact,
            sections={
                **artifact.sections,
                "declarations": ObjectRef(kind="declarations", digest=new_digest),
            },
        )
        with pytest.raises(ValueError, match="functions"):
            export_legacy_snapshot(
                tampered_artifact,
                store=store,
                source_schema_version=manifest.versions.source_schema_version,
            )

    def test_export_refuses_a_non_int_source_schema_version(self) -> None:
        doc = snapshot_to_dict(AbiSnapshot(library="libfoo.so.1", version="1.0.0"))
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        with pytest.raises(ValueError, match="must be an int"):
            export_legacy_snapshot(
                manifest.artifact_refs[0],
                store=store,
                source_schema_version="3",  # type: ignore[arg-type]
            )

    def test_artifact_kind_defaults_to_elf_when_the_document_states_no_platform(
        self,
    ) -> None:
        doc = snapshot_to_dict(_snapshot_with_ir())
        assert doc.get("platform") is None
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        assert manifest.artifact_refs[0].kind == "elf"

    @pytest.mark.parametrize("platform", ["pe", "macho", "elf"])
    def test_artifact_kind_is_derived_from_the_documents_own_platform(
        self, platform: str
    ) -> None:
        snap = _snapshot_with_ir()
        snap.platform = platform
        doc = snapshot_to_dict(snap)
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        assert manifest.artifact_refs[0].kind == platform

    def test_an_explicit_artifact_kind_overrides_the_documents_platform(
        self,
    ) -> None:
        """A caller who already knows the real kind is never second-guessed —
        the document's own `platform` is only consulted when the caller took
        the default."""
        snap = _snapshot_with_ir()
        snap.platform = "pe"
        doc = snapshot_to_dict(snap)
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(
            doc, store=store, artifact_id="libfoo", artifact_kind="macho"
        )
        assert manifest.artifact_refs[0].kind == "macho"

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
            first.artifact_refs[0].sections["provenance"].digest
        )
        second_digest = (
            second.artifact_refs[0].sections["provenance"].digest
        )
        assert first_digest == second_digest

    def test_a_pre_v8_document_with_the_legacy_evidence_pack_key_imports(
        self,
    ) -> None:
        """`serialization.snapshot_from_dict` still falls back to the
        pre-schema-v8 `evidence_pack` key when `build_source_pack` is
        absent (ADR-028's evidence->buildsource rename) -- a real
        schema-v7-or-older document can carry it instead, and
        `import_legacy_snapshot` must not reject it as an unknown field
        (Codex review)."""
        doc = {
            "library": "libfoo.so.1",
            "version": "1.0.0",
            "schema_version": 7,
            # A real (even old) `snapshot_to_dict()` document always carries
            # every `AbiSnapshot` field via `asdict()`, these included --
            # `missing_required_section_fields` now enforces that the
            # rebuilt "provenance" section isn't missing them.
            "language_profile": None,
            "dependency_info": None,
            "git_commit": None,
            "git_tag": None,
            "created_at": None,
            "evidence_pack": {
                "schema_version": 1,
                "content_hash": "sha256:abc",
                "path_hint": "libfoo.evidence/",
                "coverage_summary": {},
            },
        }
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        assert "build" in manifest.artifact_refs[0].sections
        rebuilt = export_legacy_snapshot(
            manifest.artifact_refs[0],
            store=store,
            source_schema_version=manifest.versions.source_schema_version,
        )
        assert rebuilt["evidence_pack"] == doc["evidence_pack"]


class TestRealLegacySchemaFixturesRoundTrip:
    """`tests/fixtures/schema/v1.json` through `v5.json` are real, CI-golden
    documents from `test_schema_compat.py`'s own backward-compatibility
    contract -- a second Codex review round found the storage-v2 completeness
    check (`missing_required_section_fields`) had regressed this exact
    contract by requiring fields those genuinely older schema versions never
    had at all (`platform`/`kabi`/`build_mode`/`source_mtime`/...). Every
    fixture must import and export through the v1-v25 adapter cleanly."""

    @pytest.mark.parametrize(
        "fixture_name", ["v1.json", "v2.json", "v3.json", "v4.json", "v5.json"]
    )
    def test_a_real_schema_fixture_round_trips(self, fixture_name: str) -> None:
        import json
        from pathlib import Path

        fixtures_dir = Path(__file__).resolve().parents[2] / "fixtures" / "schema"
        doc = json.loads((fixtures_dir / fixture_name).read_text(encoding="utf-8"))
        store = InMemoryObjectStore()
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        rebuilt = export_legacy_snapshot(
            manifest.artifact_refs[0],
            store=store,
            source_schema_version=manifest.versions.source_schema_version,
        )
        # v1.json predates `schema_version` entirely (the pre-versioning
        # convention: an absent key reads as v1) -- the round trip correctly
        # makes that implicit version explicit rather than reproducing the
        # key's absence, so compare with it folded in on both sides.
        expected = {**doc, "schema_version": doc.get("schema_version", 1)}
        assert canonical_form(rebuilt) == canonical_form(expected)


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
        "malformed", [float("nan"), float("inf"), 38.9, "38", True, None]
    )
    def test_a_malformed_max_known_schema_version_is_refused(
        self, malformed: object
    ) -> None:
        """`max_known_schema_version` gates the same refusal as the
        document's own `schema_version`, so it must be validated with the
        same rigor. `float("nan")`/`float("inf")` are the sharpest case:
        Python's `>` comparison against either is always `False`, so an
        uncoerced NaN/inf ceiling would silently accept a document of *any*
        schema_version, defeating the refusal entirely rather than merely
        mis-scoping it (CodeRabbit review)."""
        doc = snapshot_to_dict(_snapshot_with_ir())
        doc["schema_version"] = SCHEMA_VERSION + 1000
        store = InMemoryObjectStore()
        with pytest.raises(ValueError, match="max_known_schema_version"):
            _import_legacy_snapshot(
                doc,
                store=store,
                artifact_id="libfoo",
                max_known_schema_version=malformed,  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize("malformed", [0, -1, -38])
    def test_a_non_positive_max_known_schema_version_is_refused(
        self, malformed: int
    ) -> None:
        doc = snapshot_to_dict(_snapshot_with_ir())
        doc["schema_version"] = 1
        store = InMemoryObjectStore()
        with pytest.raises(ValueError, match="max_known_schema_version"):
            _import_legacy_snapshot(
                doc,
                store=store,
                artifact_id="libfoo",
                max_known_schema_version=malformed,
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

    @pytest.mark.parametrize("malformed", [0, -1, -38])
    def test_a_non_positive_schema_version_is_refused(self, malformed: int) -> None:
        """`StorageVersions.source_schema_version` treats `0` (and, since it
        clamps negative values too, anything non-positive) as its own
        'unstated' sentinel -- so passing a non-positive *explicitly stated*
        `schema_version` through unchecked would silently discard the
        document's own claim about which producer epoch governed it,
        degrading it to 'never stated' rather than preserving it as the
        malformed value it is (Codex review)."""
        doc = snapshot_to_dict(_snapshot_with_ir())
        doc["schema_version"] = malformed
        store = InMemoryObjectStore()
        with pytest.raises(ValueError, match="positive"):
            import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
