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
