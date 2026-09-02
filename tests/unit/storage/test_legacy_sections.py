# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""`abicheck.storage.legacy_sections` — ADR-063 Phase 8's full D8 legacy
document section split.
"""

from __future__ import annotations

import dataclasses

import pytest

from abicheck.model.snapshot import AbiSnapshot
from abicheck.storage.legacy_sections import (
    _SECTION_FIELDS,
    LEGACY_SECTION_KINDS,
    SCHEMA_VERSION_KEY,
    join_legacy_document,
    split_legacy_document,
)
from abicheck.storage.package import SECTION_KINDS

#: The three keys `serialization.snapshot_to_dict()` never assigns to a
#: legacy section — `semantic_ir`/`semantic_ir_conflicts` (their own DTO)
#: and `schema_version` (`StorageVersions.source_schema_version`) — plus
#: `AbiSnapshot`'s own runtime-only fields, which `snapshot_to_dict()`
#: strips before a document is ever built (`serialization.py`'s own
#: `d.pop(...)` calls): the three lazy lookup caches and
#: `from_headers_inferred`.
_NEVER_IN_A_DOCUMENT = (
    "semantic_ir",
    "semantic_ir_conflicts",
    "from_headers_inferred",
    "_func_by_mangled",
    "_var_by_mangled",
    "_type_by_name",
)

#: Keys `_SECTION_FIELDS` assigns that are *not* `AbiSnapshot` dataclass
#: fields at all. `dump_provenance`: a real `dump` invocation's CLI write
#: path (`cli_dump_helpers.fold_dump_provenance_into_dict`) adds this to the
#: document dict after `snapshot_to_dict()` already ran, so a document a
#: real `dump` produces carries it even though it never appears on
#: `AbiSnapshot` itself. `evidence_pack`: the pre-schema-v8 spelling of
#: `build_source_pack` (ADR-028's evidence->buildsource rename) --
#: `serialization.snapshot_from_dict` still falls back to it, so a real
#: schema-v7-or-older document can carry it instead, even though this
#: build's own `snapshot_to_dict()` never writes it.
_DOCUMENT_ONLY_KEYS = ("dump_provenance", "evidence_pack")


def _abi_snapshot_field_names() -> set[str]:
    return {f.name for f in dataclasses.fields(AbiSnapshot)}


class TestSectionFieldsCompleteness:
    """The exhaustiveness property this module's own docstring promises:
    every real `AbiSnapshot` field lands in exactly one legacy section, is
    one of the three promoted keys, or is dropped before a document is ever
    built -- never silently unaccounted for."""

    def test_every_abi_snapshot_field_is_accounted_for(self) -> None:
        fields = _abi_snapshot_field_names()
        assigned = {
            field for fields_ in _SECTION_FIELDS.values() for field in fields_
        }
        accounted = (
            assigned
            | set(_NEVER_IN_A_DOCUMENT)
            | {"semantic_ir", "semantic_ir_conflicts"}
            | set(_DOCUMENT_ONLY_KEYS)
        )
        assert fields <= accounted, (
            f"AbiSnapshot fields missing from storage.legacy_sections: "
            f"{sorted(fields - accounted)}"
        )
        # Everything `_SECTION_FIELDS` assigns that is not itself a real
        # `AbiSnapshot` field must be an explicitly-named, document-only
        # key -- never an unexplained extra.
        assert assigned - fields <= set(_DOCUMENT_ONLY_KEYS), (
            f"storage.legacy_sections._SECTION_FIELDS assigns key(s) that "
            f"are neither real AbiSnapshot fields nor in _DOCUMENT_ONLY_KEYS: "
            f"{sorted(assigned - fields - set(_DOCUMENT_ONLY_KEYS))}"
        )

    def test_no_field_is_assigned_to_two_sections(self) -> None:
        seen: dict[str, str] = {}
        for kind, fields in _SECTION_FIELDS.items():
            for field in fields:
                assert field not in seen, (
                    f"{field!r} is assigned to both {seen[field]!r} and {kind!r}"
                )
                seen[field] = kind

    def test_every_legacy_section_kind_is_a_real_d8_section_kind(self) -> None:
        assert set(LEGACY_SECTION_KINDS) <= set(SECTION_KINDS)

    def test_no_legacy_section_is_the_semantic_ir_kind(self) -> None:
        assert "semantic_ir" not in LEGACY_SECTION_KINDS


class TestRequiredSectionFieldsMatchTheV1Fixture:
    """`missing_required_section_fields`'s required set must never claim
    more than schema v1's own real, CI-golden document actually carries --
    a second Codex review round found the first attempt (requiring every
    `_SECTION_FIELDS` key) broke exactly this, rejecting real v1-v5
    documents that never had fields like `platform`/`build_mode` at all.
    This test derives the expected required set directly from the real
    fixture rather than restating `_REQUIRED_SECTION_FIELDS` by hand, so it
    cannot silently drift from the ground truth it is meant to encode."""

    def test_required_fields_are_exactly_the_v1_fixtures_present_keys(
        self,
    ) -> None:
        import json
        from pathlib import Path

        from abicheck.storage.legacy_sections import (
            _REQUIRED_SECTION_FIELDS,
            split_legacy_document,
        )

        v1_path = (
            Path(__file__).resolve().parents[2] / "fixtures" / "schema" / "v1.json"
        )
        v1_doc = json.loads(v1_path.read_text(encoding="utf-8"))
        v1_sections = split_legacy_document(v1_doc)
        for section_kind, required in _REQUIRED_SECTION_FIELDS.items():
            present = set(v1_sections.get(section_kind, {}))
            assert required <= present, (
                f"{section_kind!r} requires {sorted(required - present)}, "
                "which the real v1 fixture does not carry"
            )


class TestSplitLegacyDocument:
    def test_splits_by_section(self) -> None:
        doc = {"library": "libfoo.so.1", "types": [{"name": "Foo"}], "elf": {"a": 1}}
        sections = split_legacy_document(doc)
        assert sections["provenance"] == {"library": "libfoo.so.1"}
        assert sections["types"] == {"types": [{"name": "Foo"}]}
        assert sections["binary"] == {"elf": {"a": 1}}

    def test_promoted_keys_are_excluded(self) -> None:
        doc = {
            "library": "libfoo.so.1",
            "semantic_ir": {"occurrences": {}},
            "semantic_ir_conflicts": {"a": "b"},
            SCHEMA_VERSION_KEY: 38,
        }
        sections = split_legacy_document(doc)
        for payload in sections.values():
            assert "semantic_ir" not in payload
            assert "semantic_ir_conflicts" not in payload
            assert SCHEMA_VERSION_KEY not in payload

    def test_a_section_with_no_present_keys_is_omitted(self) -> None:
        doc = {"library": "libfoo.so.1"}
        sections = split_legacy_document(doc)
        assert "graph" not in sections
        assert "types" not in sections
        assert sections == {"provenance": {"library": "libfoo.so.1"}}

    def test_an_unknown_key_is_refused(self) -> None:
        doc = {"library": "libfoo.so.1", "totally_new_field": 1}
        with pytest.raises(ValueError, match="no assigned D8 section"):
            split_legacy_document(doc)

    def test_rejects_a_non_mapping(self) -> None:
        with pytest.raises(TypeError):
            split_legacy_document(None)  # type: ignore[arg-type]

    def test_empty_document_splits_to_nothing(self) -> None:
        assert split_legacy_document({}) == {}


class TestJoinLegacyDocument:
    def test_inverse_of_split(self) -> None:
        doc = {
            "library": "libfoo.so.1",
            "version": "1.0",
            "types": [{"name": "Foo"}],
            "elf": {"a": 1},
            "functions": [],
        }
        sections = split_legacy_document(doc)
        assert join_legacy_document(sections) == doc

    def test_unknown_section_kind_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown legacy section kind"):
            join_legacy_document({"not_a_real_kind": {}})

    def test_a_misfiled_key_is_refused(self) -> None:
        # `library` belongs to "provenance", not "binary".
        with pytest.raises(ValueError, match="not in its own allowlist"):
            join_legacy_document({"binary": {"library": "libfoo.so.1"}})

    def test_empty_sections_join_to_an_empty_document(self) -> None:
        assert join_legacy_document({}) == {}

    def test_rejects_a_non_mapping_payload(self) -> None:
        with pytest.raises(TypeError):
            join_legacy_document({"binary": None})  # type: ignore[dict-item]
