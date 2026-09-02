# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""`abicheck.storage.sectioned_document` -- ADR-062/063 Phase 8's redesign:
the D8 section split packaged as one JSON document instead of a
directory-backed `ProjectSnapshot` package.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from abicheck.model.snapshot import AbiSnapshot
from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
from abicheck.storage.canonical import canonical_form
from abicheck.storage.sectioned_document import (
    SECTION_SCHEMA_VERSIONS_KEY,
    SECTIONS_KEY,
    from_sectioned_document,
    is_sectioned_document,
    to_sectioned_document,
)

_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "schema"


def _round_trip(doc: dict) -> dict:
    sectioned = to_sectioned_document(doc, max_known_schema_version=SCHEMA_VERSION)
    return from_sectioned_document(sectioned)


class TestSectionsKeyNeverCollidesWithARealField:
    def test_sections_key_is_not_an_abi_snapshot_field(self) -> None:
        names = {f.name for f in dataclasses.fields(AbiSnapshot)}
        assert SECTIONS_KEY not in names


class TestIsSectionedDocument:
    def test_true_for_a_sectioned_document(self) -> None:
        doc = snapshot_to_dict(AbiSnapshot(library="libfoo.so.1", version="1.0.0"))
        sectioned = to_sectioned_document(doc, max_known_schema_version=SCHEMA_VERSION)
        assert is_sectioned_document(sectioned) is True

    def test_false_for_a_flat_document(self) -> None:
        doc = snapshot_to_dict(AbiSnapshot(library="libfoo.so.1", version="1.0.0"))
        assert is_sectioned_document(doc) is False

    def test_false_for_an_empty_document(self) -> None:
        assert is_sectioned_document({}) is False


class TestRoundTrip:
    def test_a_fresh_snapshot_round_trips(self) -> None:
        doc = snapshot_to_dict(AbiSnapshot(library="libfoo.so.1", version="1.0.0"))
        rebuilt = _round_trip(doc)
        assert canonical_form(rebuilt) == canonical_form(doc)

    def test_the_sectioned_document_is_json_serializable(self) -> None:
        doc = snapshot_to_dict(AbiSnapshot(library="libfoo.so.1", version="1.0.0"))
        sectioned = to_sectioned_document(doc, max_known_schema_version=SCHEMA_VERSION)
        reparsed = json.loads(json.dumps(sectioned))
        rebuilt = from_sectioned_document(reparsed)
        assert canonical_form(rebuilt) == canonical_form(doc)

    @pytest.mark.parametrize(
        "fixture_name", ["v1.json", "v2.json", "v3.json", "v4.json", "v5.json"]
    )
    def test_a_real_legacy_schema_fixture_round_trips(self, fixture_name: str) -> None:
        doc = json.loads((_FIXTURES_DIR / fixture_name).read_text(encoding="utf-8"))
        rebuilt = _round_trip(doc)
        expected = {**doc, "schema_version": doc.get("schema_version", 1)}
        assert canonical_form(rebuilt) == canonical_form(expected)


class TestEnvelopeVersionIsSeparateFromSourceVersion:
    """Codex review, fresh evidence: `to_sectioned_document()` used to
    write the *legacy document's own* schema_version directly as the
    envelope's top-level `schema_version` -- correct only because every
    real caller converts a document this same build just produced (whose
    own schema_version is always the current SCHEMA_VERSION). Converting a
    genuinely older-but-still-readable document (e.g. one loaded from disk
    at schema_version 41 or earlier) would have stamped the ENVELOPE
    itself as that old version, which a pre-Phase-8 reader (understanding
    up to schema_version 41) would then NOT hard-reject -- defeating the
    whole point of this redesign's SCHEMA_VERSION bump."""

    @pytest.mark.parametrize(
        "fixture_name", ["v1.json", "v2.json", "v3.json", "v4.json", "v5.json"]
    )
    def test_converting_an_old_document_stamps_the_current_envelope_version(
        self, fixture_name: str
    ) -> None:
        doc = json.loads((_FIXTURES_DIR / fixture_name).read_text(encoding="utf-8"))
        old_version = doc.get("schema_version", 1)
        assert old_version < SCHEMA_VERSION
        sectioned = to_sectioned_document(doc, max_known_schema_version=SCHEMA_VERSION)
        # The envelope itself must claim the CURRENT wire-format version --
        # a pre-Phase-8 reader (understanding up to `old_version`) must see
        # a schema_version it cannot understand and hard-reject, rather than
        # `old_version` (which it would accept and misread).
        assert sectioned["schema_version"] == SCHEMA_VERSION
        # The legacy document's own version must still be recoverable, so
        # the reconstructed flat document keeps its original identity.
        assert sectioned["source_schema_version"] == old_version
        rebuilt = from_sectioned_document(sectioned)
        assert rebuilt["schema_version"] == old_version

    def test_a_fresh_document_s_source_version_equals_the_envelope_version(
        self,
    ) -> None:
        doc = snapshot_to_dict(AbiSnapshot(library="libfoo.so.1", version="1.0.0"))
        sectioned = to_sectioned_document(doc, max_known_schema_version=SCHEMA_VERSION)
        assert sectioned["schema_version"] == SCHEMA_VERSION
        assert sectioned["source_schema_version"] == SCHEMA_VERSION


class TestFromSectionedDocumentRejectsMalformedInput:
    def test_a_non_object_sections_value_is_refused(self) -> None:
        with pytest.raises(ValueError, match="sections"):
            from_sectioned_document({"schema_version": 1, "sections": []})

    def test_a_missing_sections_key_is_refused(self) -> None:
        with pytest.raises(ValueError, match="sections"):
            from_sectioned_document({"schema_version": 1})

    def test_a_non_int_schema_version_is_refused(self) -> None:
        with pytest.raises(ValueError, match="schema_version"):
            from_sectioned_document({"schema_version": "41", "sections": {}})

    def test_a_missing_schema_version_is_refused(self) -> None:
        with pytest.raises(ValueError, match="schema_version"):
            from_sectioned_document({"sections": {}})

    def test_a_non_int_source_schema_version_is_refused(self) -> None:
        with pytest.raises(ValueError, match="source_schema_version"):
            from_sectioned_document(
                {
                    "schema_version": 42,
                    "sections": {},
                    "section_schema_versions": {},
                    "source_schema_version": "41",
                }
            )

    def test_a_missing_source_schema_version_is_refused(self) -> None:
        with pytest.raises(ValueError, match="source_schema_version"):
            from_sectioned_document(
                {"schema_version": 42, "sections": {}, "section_schema_versions": {}}
            )

    def test_a_truncated_section_payload_is_refused(self) -> None:
        """The same completeness check `import_v1.export_legacy_snapshot`
        already applies (Codex review on the directory package) reaches
        the single-file shape too, since both share `export_legacy_snapshot`
        -- a `declarations` section missing `functions` must be refused,
        not silently read back as `[]`."""
        doc = snapshot_to_dict(AbiSnapshot(library="libfoo.so.1", version="1.0.0"))
        sectioned = to_sectioned_document(doc, max_known_schema_version=SCHEMA_VERSION)
        payload = dict(sectioned[SECTIONS_KEY]["declarations"]["payload"])
        assert "functions" in payload
        del payload["functions"]
        sectioned[SECTIONS_KEY]["declarations"]["payload"] = payload
        with pytest.raises(ValueError, match="functions"):
            from_sectioned_document(sectioned)

    def test_a_whole_section_dropped_from_sections_is_refused(self) -> None:
        """A `declarations` section dropped entirely (not just a field
        within it) must be refused too -- `export_legacy_snapshot` only
        iterates the sections it is handed, so an entirely missing section
        is otherwise invisible to it and reads back as an empty/confirmed-
        absent `functions`/`variables`/`types` list rather than failing
        loudly (Codex review, fresh evidence beyond the directory package's
        equivalent `manifest.json` cross-check)."""
        doc = snapshot_to_dict(AbiSnapshot(library="libfoo.so.1", version="1.0.0"))
        sectioned = to_sectioned_document(doc, max_known_schema_version=SCHEMA_VERSION)
        assert "declarations" in sectioned[SECTION_SCHEMA_VERSIONS_KEY]
        del sectioned[SECTIONS_KEY]["declarations"]
        with pytest.raises(ValueError, match="declarations"):
            from_sectioned_document(sectioned)

    def test_an_unadvertised_extra_section_is_refused(self) -> None:
        """The inverse of the missing-section case: a section present in
        `sections` but absent from `SECTION_SCHEMA_VERSIONS_KEY` -- e.g.
        injected by a hand edit -- must be refused too, mirroring the
        directory-backed package's identical missing/extra pair (Codex/
        CodeRabbit review, fresh evidence). Self-consistent (its own
        `section_kind` field matches its key) so this exercises the new
        manifest cross-check itself, not export_legacy_snapshot's unrelated
        pre-existing per-DTO kind-consistency check."""
        from abicheck.storage.dto import SEMANTIC_IR_SECTION_KIND, SectionDTO

        doc = snapshot_to_dict(AbiSnapshot(library="libfoo.so.1", version="1.0.0"))
        sectioned = to_sectioned_document(doc, max_known_schema_version=SCHEMA_VERSION)
        assert SEMANTIC_IR_SECTION_KIND not in sectioned[SECTIONS_KEY]
        bogus_dto = SectionDTO(
            section_kind=SEMANTIC_IR_SECTION_KIND, section_schema_version=1, payload={}
        )
        sectioned[SECTIONS_KEY][SEMANTIC_IR_SECTION_KIND] = bogus_dto.to_dict()
        with pytest.raises(ValueError, match=SEMANTIC_IR_SECTION_KIND):
            from_sectioned_document(sectioned)

    def test_a_missing_section_schema_versions_key_is_refused(self) -> None:
        doc = snapshot_to_dict(AbiSnapshot(library="libfoo.so.1", version="1.0.0"))
        sectioned = to_sectioned_document(doc, max_known_schema_version=SCHEMA_VERSION)
        del sectioned[SECTION_SCHEMA_VERSIONS_KEY]
        with pytest.raises(ValueError, match="section_schema_versions"):
            from_sectioned_document(sectioned)
