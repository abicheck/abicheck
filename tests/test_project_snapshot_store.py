# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""`abicheck.project_snapshot_store` — the directory-backed `ObjectStore`
and D6 manifest/ref writer/reader (ADR-062 A1.1's other half).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.model.fact import Fact
from abicheck.model.identity import Namespace, Record, entity_id_for_type
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR
from abicheck.model.snapshot import AbiSnapshot
from abicheck.project_snapshot_store import (
    DirectoryObjectStore,
    read_artifact_ref,
    read_manifest_summary,
    read_project_manifest,
    read_variant_ref,
    variant_and_artifact_ids,
    write_project_manifest,
)
from abicheck.serialization import snapshot_to_dict
from abicheck.storage.dto import (
    SEMANTIC_IR_SECTION_KIND,
    SectionDTO,
    semantic_ir_from_dto,
)
from abicheck.storage.import_v1 import import_legacy_snapshot


def _snapshot_with_ir() -> AbiSnapshot:
    eid = entity_id_for_type((Namespace("ns"), Record("Outer")), "Inner")
    occ = OccurrenceId(eid, disambiguator="tu-a")
    entity = CanonicalEntity(canonical_spelling=Fact.present("ns::Outer::Inner"))
    ir = SemanticIR(occurrences={occ: entity})
    return AbiSnapshot(library="libfoo.so.1", version="1.0.0", semantic_ir=ir)


class TestDirectoryObjectStore:
    def test_json_content_round_trips(self, tmp_path: Path) -> None:
        store = DirectoryObjectStore(tmp_path)
        digest = store.put({"b": 2, "a": 1})
        assert store.has(digest)
        assert store.get(digest) == {"a": 1, "b": 2}

    def test_raw_binary_content_round_trips(self, tmp_path: Path) -> None:
        store = DirectoryObjectStore(tmp_path)
        payload = b"\x00\x01\xffnot json"
        digest = store.put(payload)
        assert store.has(digest)
        assert store.get(digest) == payload

    def test_storing_identical_content_twice_is_idempotent(
        self, tmp_path: Path
    ) -> None:
        store = DirectoryObjectStore(tmp_path)
        digest1 = store.put({"x": 1})
        digest2 = store.put({"x": 1})
        assert digest1 == digest2
        json_files = list((tmp_path / "objects").rglob("*.json.zst"))
        assert len(json_files) == 1

    def test_a_json_value_and_an_unrelated_raw_buffer_never_collide(
        self, tmp_path: Path
    ) -> None:
        store = DirectoryObjectStore(tmp_path)
        json_digest = store.put({})
        raw_digest = store.put(b"{}")
        assert store.get(json_digest) == {}
        assert store.get(raw_digest) == b"{}"

    def test_missing_digest_raises_key_error(self, tmp_path: Path) -> None:
        store = DirectoryObjectStore(tmp_path)
        with pytest.raises(KeyError):
            store.get("sha256:" + "ab" * 32)

    def test_a_non_string_digest_is_refused(self, tmp_path: Path) -> None:
        store = DirectoryObjectStore(tmp_path)
        with pytest.raises(TypeError):
            store.get(123)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            store.has(123)  # type: ignore[arg-type]

    def test_the_object_actually_lands_on_disk_at_the_d6_layout(
        self, tmp_path: Path
    ) -> None:
        store = DirectoryObjectStore(tmp_path)
        digest = store.put({"a": 1})
        _algorithm, _sep, hexdigest = digest.partition(":")
        expected = (
            tmp_path / "objects" / "sha256" / hexdigest[:2] / f"{hexdigest}.json.zst"
        )
        assert expected.exists()


class TestManifestRoundTrip:
    def test_full_package_round_trips_through_a_real_directory(
        self, tmp_path: Path
    ) -> None:
        snap = _snapshot_with_ir()
        doc = snapshot_to_dict(snap)
        store = DirectoryObjectStore(tmp_path)
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        write_project_manifest(tmp_path, manifest)

        loaded = read_project_manifest(tmp_path)
        assert loaded == manifest

        variant_ids, artifact_ids = variant_and_artifact_ids(tmp_path)
        assert variant_ids == ("default",)
        assert artifact_ids == ("libfoo",)

        assert read_variant_ref(tmp_path, "default") == manifest.variant_refs[0]
        assert read_artifact_ref(tmp_path, "libfoo") == manifest.artifact_refs[0]

        art = loaded.artifact_refs[0]
        dto = SectionDTO.from_dict(
            store.get(art.sections[SEMANTIC_IR_SECTION_KIND].digest)
        )
        ir, _conflicts = semantic_ir_from_dto(dto)
        assert ir == snap.semantic_ir

    def test_manifest_json_is_small_and_does_not_embed_full_records(
        self, tmp_path: Path
    ) -> None:
        doc = snapshot_to_dict(_snapshot_with_ir())
        store = DirectoryObjectStore(tmp_path)
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        write_project_manifest(tmp_path, manifest)

        raw = (tmp_path / "manifest.json").read_text(encoding="utf-8")
        # The actual IR payload (occurrences/entities) lives in objects/, not
        # here — the manifest may still name "semantic_ir" as a section-
        # schema-version key, which is small, informational metadata.
        assert "occurrences" not in raw
        assert "sections" not in raw  # artifact records live in refs/artifacts/

        summary = read_manifest_summary(tmp_path)
        assert summary.variant_ids == ("default",)
        assert summary.artifact_ids == ("libfoo",)
        assert (
            summary.versions.source_schema_version
            == manifest.versions.source_schema_version
        )

    def test_the_d6_directory_tree_is_exactly_what_gets_written(
        self, tmp_path: Path
    ) -> None:
        doc = snapshot_to_dict(_snapshot_with_ir())
        store = DirectoryObjectStore(tmp_path)
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        write_project_manifest(tmp_path, manifest)

        assert (tmp_path / "manifest.json").is_file()
        assert (tmp_path / "refs" / "variants" / "default.json").is_file()
        assert (tmp_path / "refs" / "artifacts" / "libfoo.json").is_file()
        assert any((tmp_path / "objects").rglob("*.json.zst"))
