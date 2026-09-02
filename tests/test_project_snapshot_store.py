# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""`abicheck.project_snapshot_store` — the directory-backed `ObjectStore`
and D6 manifest/ref writer/reader (ADR-062 A1.1's other half).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    read_variant_artifact_pair,
    read_variant_ref,
    variant_and_artifact_ids,
    write_project_manifest,
)
from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
from abicheck.snapshot_io import SnapshotCompression, write_snapshot_text
from abicheck.storage.canonical import canonical_json
from abicheck.storage.dto import (
    SEMANTIC_IR_SECTION_KIND,
    SectionDTO,
    semantic_ir_from_dto,
)
from abicheck.storage.import_v1 import import_legacy_snapshot as _import_legacy_snapshot
from abicheck.storage.versioning import StorageVersions


def import_legacy_snapshot(*args: Any, **kwargs: Any) -> Any:
    """`import_legacy_snapshot`, defaulting `max_known_schema_version` to
    this build's real `serialization.SCHEMA_VERSION` -- see the identical
    helper in `tests/unit/storage/test_import_v1.py`."""
    kwargs.setdefault("max_known_schema_version", SCHEMA_VERSION)
    return _import_legacy_snapshot(*args, **kwargs)


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

    def test_a_corrupted_json_object_is_refused_rather_than_silently_returned(
        self, tmp_path: Path
    ) -> None:
        """A file substituted, corrupted, or hand-edited under a digest's own
        path must never come back as if it were the addressed content."""
        store = DirectoryObjectStore(tmp_path)
        digest = store.put({"a": 1})
        other_digest = store.put({"a": 2})
        _algorithm, _sep, hexdigest = digest.partition(":")
        path = tmp_path / "objects" / "sha256" / hexdigest[:2] / f"{hexdigest}.json.zst"
        # Overwrite the first object's file with the *second* object's real,
        # validly-compressed bytes -- a corruption a naive read/parse would
        # not notice, since the substituted content is itself well-formed.
        _other_algorithm, _other_sep, other_hex = other_digest.partition(":")
        other_path = (
            tmp_path / "objects" / "sha256" / other_hex[:2] / f"{other_hex}.json.zst"
        )
        path.write_bytes(other_path.read_bytes())
        with pytest.raises(ValueError, match="does not match its requested digest"):
            store.get(digest)

    def test_a_hand_added_capture_metadata_subtree_is_never_returned(
        self, tmp_path: Path
    ) -> None:
        """`semantic_digest` (and so `put()`'s own digest) strips the
        reserved root `capture` block before hashing (D3) -- so a stored
        object file hand-edited to add an arbitrary `capture` subtree back
        in still matches its own digest, since that subtree was never in
        the hash domain. `get()` must not hand that subtree back merely
        because the digest still checks out: it must return the same
        normalized (capture-stripped) form `InMemoryObjectStore.get()` and
        `ObjectStore.get()`'s own documented contract already promise
        (Codex review)."""
        store = DirectoryObjectStore(tmp_path)
        digest = store.put({"a": 1})
        _algorithm, _sep, hexdigest = digest.partition(":")
        path = tmp_path / "objects" / "sha256" / hexdigest[:2] / f"{hexdigest}.json.zst"
        tampered = {"a": 1, "capture": {"injected": "surprise"}}
        write_snapshot_text(
            canonical_json(tampered), path, compression=SnapshotCompression.ZSTD
        )
        # Still verifies -- `capture` is outside the hash domain by design.
        result = store.get(digest)
        assert result == {"a": 1}
        assert "capture" not in result

    def test_a_corrupted_raw_object_is_refused_rather_than_silently_returned(
        self, tmp_path: Path
    ) -> None:
        store = DirectoryObjectStore(tmp_path)
        digest = store.put(b"real content")
        other_digest = store.put(b"a different payload entirely")
        _algorithm, _sep, hexdigest = digest.partition(":")
        _other_algorithm, _other_sep, other_hex = other_digest.partition(":")
        path = tmp_path / "objects" / "sha256" / hexdigest[:2] / f"{hexdigest}.bin.zst"
        other_path = (
            tmp_path / "objects" / "sha256" / other_hex[:2] / f"{other_hex}.bin.zst"
        )
        path.write_bytes(other_path.read_bytes())
        with pytest.raises(ValueError, match="does not match its requested digest"):
            store.get(digest)

    def test_a_lone_surrogate_round_trips_through_a_real_directory(
        self, tmp_path: Path
    ) -> None:
        """A real POSIX path decoded via `surrogateescape`
        (`os.fsdecode(b"caf\\xe9")` == `"caf\\udce9"`) is content
        `semantic_digest`/`InMemoryObjectStore` already accept -- this
        store must persist and return it too, not raise
        `UnicodeEncodeError` mid-write (Codex review)."""
        store = DirectoryObjectStore(tmp_path)
        content = {"path": "caf" + "\udce9"}
        digest = store.put(content)
        assert store.get(digest) == content

    def test_putting_content_again_repairs_a_corrupted_existing_json_object(
        self, tmp_path: Path
    ) -> None:
        """A previously stored object corrupted on disk (out from under
        this store, not through `put()`/`get()`) must not make a later
        `put()` of the same, correct content silently "succeed" over the
        known-bad file merely because a path already exists there -- that
        would publish a manifest referencing a file `get()` will only
        later discover is broken (Codex review). `put()` must verify an
        existing path before trusting it, and repair it if it doesn't
        match."""
        store = DirectoryObjectStore(tmp_path)
        digest = store.put({"a": 1})
        _algorithm, _sep, hexdigest = digest.partition(":")
        path = tmp_path / "objects" / "sha256" / hexdigest[:2] / f"{hexdigest}.json.zst"
        # Corrupt the stored object out from under the store, the same way
        # test_a_corrupted_json_object_is_refused_rather_than_silently_returned
        # simulates external corruption -- valid, well-formed zstd bytes,
        # just the wrong content.
        other_digest = store.put({"a": 999})
        _other_algorithm, _other_sep, other_hex = other_digest.partition(":")
        other_path = (
            tmp_path / "objects" / "sha256" / other_hex[:2] / f"{other_hex}.json.zst"
        )
        path.write_bytes(other_path.read_bytes())
        # put()'ing the *original* content again must repair the file
        # rather than treating its mere existence as success.
        digest_again = store.put({"a": 1})
        assert digest_again == digest
        assert store.get(digest) == {"a": 1}

    def test_putting_content_again_repairs_a_corrupted_existing_raw_object(
        self, tmp_path: Path
    ) -> None:
        store = DirectoryObjectStore(tmp_path)
        digest = store.put(b"real content")
        _algorithm, _sep, hexdigest = digest.partition(":")
        path = tmp_path / "objects" / "sha256" / hexdigest[:2] / f"{hexdigest}.bin.zst"
        other_digest = store.put(b"a different payload entirely")
        _other_algorithm, _other_sep, other_hex = other_digest.partition(":")
        other_path = (
            tmp_path / "objects" / "sha256" / other_hex[:2] / f"{other_hex}.bin.zst"
        )
        path.write_bytes(other_path.read_bytes())
        digest_again = store.put(b"real content")
        assert digest_again == digest
        assert store.get(digest) == b"real content"


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


#: A minimal, currently-readable `versions` block -- used by the malformed-
#: id-list tests below so they exercise the id-list guard specifically,
#: rather than tripping the (also real, separately tested) reader-
#: compatibility guard on an absent/unstated version axis first. Derived
#: from `StorageVersions`' own defaults rather than hand-typed numbers, so
#: it can't silently drift from what a real writer emits.
_VALID_VERSIONS = StorageVersions().to_dict()


class TestReadManifestSummaryValidation:
    """`manifest.json` is untrusted input the moment it's hand-edited or
    corrupted — these state the guards `read_manifest_summary` applies
    before a caller can go on to load anything else from the package."""

    def _write_manifest_json(self, root: Path, content: object) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "manifest.json").write_text(json.dumps(content), encoding="utf-8")

    @pytest.mark.parametrize(
        "variant_ids",
        [
            "ab",  # a bare string iterates into one id per character
            {"default": "x"},  # a mapping iterates into its keys
            [1, 2],  # entries must be strings
        ],
    )
    def test_a_malformed_variant_id_list_is_refused(
        self, tmp_path: Path, variant_ids: object
    ) -> None:
        self._write_manifest_json(
            tmp_path,
            {
                "versions": _VALID_VERSIONS,
                "variant_ids": variant_ids,
                "artifact_ids": [],
            },
        )
        with pytest.raises(ValueError, match="variant_ids"):
            read_manifest_summary(tmp_path)

    @pytest.mark.parametrize(
        "artifact_ids",
        ["ab", {"libfoo": "x"}, [1, 2]],
    )
    def test_a_malformed_artifact_id_list_is_refused(
        self, tmp_path: Path, artifact_ids: object
    ) -> None:
        self._write_manifest_json(
            tmp_path,
            {
                "versions": _VALID_VERSIONS,
                "variant_ids": [],
                "artifact_ids": artifact_ids,
            },
        )
        with pytest.raises(ValueError, match="artifact_ids"):
            read_manifest_summary(tmp_path)

    def test_a_missing_variant_ids_field_is_refused(self, tmp_path: Path) -> None:
        """A manifest that never states `variant_ids` at all must be
        refused, not silently read as an empty membership list -- absence
        of the field is a truncated document, not a package with zero
        variants (Codex review, a second round on this same field: an
        earlier fix validated a *present* malformed list but still let a
        missing key through as `None` -> `()`)."""
        self._write_manifest_json(
            tmp_path,
            {"versions": _VALID_VERSIONS, "artifact_ids": []},
        )
        with pytest.raises(ValueError, match="variant_ids"):
            read_manifest_summary(tmp_path)

    def test_a_missing_artifact_ids_field_is_refused(self, tmp_path: Path) -> None:
        self._write_manifest_json(
            tmp_path,
            {"versions": _VALID_VERSIONS, "variant_ids": []},
        )
        with pytest.raises(ValueError, match="artifact_ids"):
            read_manifest_summary(tmp_path)

    def test_a_null_variant_ids_field_is_refused(self, tmp_path: Path) -> None:
        """Explicit JSON `null` must be refused the same way a missing key
        is -- not quietly re-treated as an empty list either."""
        self._write_manifest_json(
            tmp_path,
            {"versions": _VALID_VERSIONS, "variant_ids": None, "artifact_ids": []},
        )
        with pytest.raises(ValueError, match="variant_ids"):
            read_manifest_summary(tmp_path)

    def test_a_newer_package_format_version_is_refused(self, tmp_path: Path) -> None:
        self._write_manifest_json(
            tmp_path,
            {
                "versions": {"package_format_version": 999_999},
                "variant_ids": [],
                "artifact_ids": [],
            },
        )
        with pytest.raises(ValueError, match="not readable by this build"):
            read_manifest_summary(tmp_path)

    def test_an_unstated_comparison_contract_version_is_refused(
        self, tmp_path: Path
    ) -> None:
        # `versions: {}` -- entirely absent, which `StorageVersions.from_dict`
        # reads as UNSTATED for both fail-closed axes, per D2.
        self._write_manifest_json(tmp_path, {"variant_ids": [], "artifact_ids": []})
        with pytest.raises(ValueError, match="not readable by this build"):
            read_manifest_summary(tmp_path)

    def test_a_duplicate_variant_id_is_refused(self, tmp_path: Path) -> None:
        """`PackageManifest.__post_init__` already refuses a duplicate id,
        but only on the eager `read_project_manifest` path -- the lazy
        `read_manifest_summary`/`variant_and_artifact_ids` primitives never
        construct a `PackageManifest` and so let a manifest naming the same
        variant twice through unchecked (Codex review)."""
        self._write_manifest_json(
            tmp_path,
            {
                "versions": _VALID_VERSIONS,
                "variant_ids": ["default", "default"],
                "artifact_ids": [],
            },
        )
        with pytest.raises(ValueError, match="duplicate id"):
            read_manifest_summary(tmp_path)

    def test_a_duplicate_artifact_id_is_refused(self, tmp_path: Path) -> None:
        self._write_manifest_json(
            tmp_path,
            {
                "versions": _VALID_VERSIONS,
                "variant_ids": [],
                "artifact_ids": ["libfoo", "libfoo"],
            },
        )
        with pytest.raises(ValueError, match="duplicate id"):
            read_manifest_summary(tmp_path)

    def test_case_colliding_variant_ids_are_refused(self, tmp_path: Path) -> None:
        """Two distinct strings that a case-insensitive filesystem would
        still treat as one `refs/variants/*.json` path. `PackageManifest`
        already refuses this, but only on the eager `read_project_manifest`
        path; the lazy primitives this test exercises must refuse it too
        (Codex review)."""
        self._write_manifest_json(
            tmp_path,
            {
                "versions": _VALID_VERSIONS,
                "variant_ids": ["Default", "default"],
                "artifact_ids": [],
            },
        )
        with pytest.raises(ValueError, match="collide"):
            read_manifest_summary(tmp_path)

    def test_unicode_normalization_colliding_artifact_ids_are_refused(
        self, tmp_path: Path
    ) -> None:
        nfc = "caf" + "\u00e9"  # NFC: the accented e as one code point
        nfd = "cafe" + "\u0301"  # NFD: plain e + a combining acute accent
        assert nfc != nfd  # distinct Python strings, canonically equivalent text
        self._write_manifest_json(
            tmp_path,
            {
                "versions": _VALID_VERSIONS,
                "variant_ids": [],
                "artifact_ids": [nfc, nfd],
            },
        )
        with pytest.raises(ValueError, match="collide"):
            read_manifest_summary(tmp_path)

    def test_an_ordinary_current_manifest_is_still_readable(
        self, tmp_path: Path
    ) -> None:
        doc = snapshot_to_dict(_snapshot_with_ir())
        store = DirectoryObjectStore(tmp_path)
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        write_project_manifest(tmp_path, manifest)
        # No exception, and the summary loads with the real membership --
        # a normally-written manifest passes the same check.
        summary = read_manifest_summary(tmp_path)
        assert summary.artifact_ids == ("libfoo",)


class TestReadVariantArtifactPair:
    """`read_variant_ref`/`read_artifact_ref` alone each validate only
    their own document's self-consistency -- neither knows the other
    exists, so a malformed package can state a self-contradictory
    membership graph (an artifact's own `variant_id` naming a real variant
    that doesn't list it back, or the reverse) that neither lazy primitive
    alone catches, even though `PackageManifest.__post_init__` already
    rejects the identical graph on the eager path (Codex review).
    `read_variant_artifact_pair` is the lazy-path primitive that closes
    this for the one pair a caller actually selected."""

    def _write_ref(self, path: Path, content: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content), encoding="utf-8")

    def _write_manifest_json(
        self, root: Path, variant_ids: list[str], artifact_ids: list[str]
    ) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "versions": _VALID_VERSIONS,
                    "variant_ids": variant_ids,
                    "artifact_ids": artifact_ids,
                }
            ),
            encoding="utf-8",
        )

    def test_a_matched_pair_round_trips(self, tmp_path: Path) -> None:
        doc = snapshot_to_dict(_snapshot_with_ir())
        store = DirectoryObjectStore(tmp_path)
        manifest = import_legacy_snapshot(doc, store=store, artifact_id="libfoo")
        write_project_manifest(tmp_path, manifest)
        variant, artifact = read_variant_artifact_pair(tmp_path, "default", "libfoo")
        assert variant.variant_id == "default"
        assert artifact.artifact_id == "libfoo"

    def test_an_artifact_naming_a_variant_that_omits_it_is_refused(
        self, tmp_path: Path
    ) -> None:
        """The exact repro: `v1.json` lists no artifacts, but `a.json`
        claims `variant_id: "v1"` -- both published in `manifest.json` so
        this exercises the ref-cross-check, not the membership check."""
        self._write_manifest_json(tmp_path, ["v1"], ["a"])
        self._write_ref(
            tmp_path / "refs" / "variants" / "v1.json", {"variant_id": "v1"}
        )
        self._write_ref(
            tmp_path / "refs" / "artifacts" / "a.json",
            {"artifact_id": "a", "variant_id": "v1", "kind": "elf"},
        )
        with pytest.raises(ValueError, match="does not list artifact_id"):
            read_variant_artifact_pair(tmp_path, "v1", "a")

    def test_an_artifact_naming_a_different_variant_is_refused(
        self, tmp_path: Path
    ) -> None:
        self._write_manifest_json(tmp_path, ["v1", "v2"], ["a"])
        self._write_ref(
            tmp_path / "refs" / "variants" / "v1.json",
            {"variant_id": "v1", "artifact_ids": ["a"]},
        )
        self._write_ref(
            tmp_path / "refs" / "artifacts" / "a.json",
            {"artifact_id": "a", "variant_id": "v2", "kind": "elf"},
        )
        with pytest.raises(
            ValueError, match="does not belong to the requested variant"
        ):
            read_variant_artifact_pair(tmp_path, "v1", "a")

    def test_a_variant_id_not_published_in_the_manifest_is_refused(
        self, tmp_path: Path
    ) -> None:
        """A directory of stale, injected, or since-removed `refs/*.json`
        files -- self-consistent with each other, but never published in
        `manifest.json` -- must not read as a valid package pair (Codex
        review)."""
        self._write_manifest_json(tmp_path, [], [])
        self._write_ref(
            tmp_path / "refs" / "variants" / "v1.json",
            {"variant_id": "v1", "artifact_ids": ["a"]},
        )
        self._write_ref(
            tmp_path / "refs" / "artifacts" / "a.json",
            {"artifact_id": "a", "variant_id": "v1", "kind": "elf"},
        )
        with pytest.raises(ValueError, match="not a variant_id published"):
            read_variant_artifact_pair(tmp_path, "v1", "a")

    def test_an_artifact_id_not_published_in_the_manifest_is_refused(
        self, tmp_path: Path
    ) -> None:
        self._write_manifest_json(tmp_path, ["v1"], [])
        self._write_ref(
            tmp_path / "refs" / "variants" / "v1.json",
            {"variant_id": "v1", "artifact_ids": ["a"]},
        )
        self._write_ref(
            tmp_path / "refs" / "artifacts" / "a.json",
            {"artifact_id": "a", "variant_id": "v1", "kind": "elf"},
        )
        with pytest.raises(ValueError, match="not an artifact_id published"):
            read_variant_artifact_pair(tmp_path, "v1", "a")

    def test_no_manifest_json_at_all_is_refused(self, tmp_path: Path) -> None:
        """A bare directory of ref files, with no `manifest.json` at all,
        must not silently succeed just because the two ref documents agree
        with each other."""
        self._write_ref(
            tmp_path / "refs" / "variants" / "v1.json",
            {"variant_id": "v1", "artifact_ids": ["a"]},
        )
        self._write_ref(
            tmp_path / "refs" / "artifacts" / "a.json",
            {"artifact_id": "a", "variant_id": "v1", "kind": "elf"},
        )
        with pytest.raises(FileNotFoundError):
            read_variant_artifact_pair(tmp_path, "v1", "a")
