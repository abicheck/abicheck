# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""`abicheck.bundle_facts_store` — the first real multi-artifact
`ProjectSnapshot` package writer/reader (ADR-062 A1.4/A1.5).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from abicheck.bundle_facts import BundleFacts, capture_bundle_facts
from abicheck.bundle_facts_store import (
    INSTANTIATION_MANIFEST_SECTION_KIND,
    read_bundle_facts_package,
    write_bundle_facts_package,
)
from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry
from abicheck.model.snapshot import AbiSnapshot
from abicheck.project_snapshot_store import (
    DirectoryObjectStore,
    read_project_manifest,
    write_project_manifest,
)
from abicheck.storage.package import InMemoryObjectStore


def _snapshot(name: str) -> AbiSnapshot:
    return AbiSnapshot(library=name, version="1.0.0", platform="elf")


class TestWriteBundleFactsPackage:
    def test_builds_one_artifact_per_library_under_one_variant(self) -> None:
        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so"), "libb.so": _snapshot("libb.so")}
        )
        store = InMemoryObjectStore()

        manifest = write_bundle_facts_package(facts, store=store)

        assert {a.artifact_id for a in manifest.artifact_refs} == {"liba.so", "libb.so"}
        assert [v.variant_id for v in manifest.variant_refs] == ["default"]
        (variant,) = manifest.variant_refs
        assert set(variant.artifact_ids) == {"liba.so", "libb.so"}
        for artifact in manifest.artifact_refs:
            assert artifact.variant_id == "default"
            assert artifact.kind == "elf"

    def test_custom_variant_id_is_honored(self) -> None:
        facts = capture_bundle_facts({"liba.so": _snapshot("liba.so")})
        store = InMemoryObjectStore()

        manifest = write_bundle_facts_package(facts, store=store, variant_id="gcc13")

        assert [v.variant_id for v in manifest.variant_refs] == ["gcc13"]
        (artifact,) = manifest.artifact_refs
        assert artifact.variant_id == "gcc13"

    def test_variant_fingerprint_becomes_a_captured_coordinate(self) -> None:
        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so")}, variant_fingerprint="gcc13-avx2"
        )
        store = InMemoryObjectStore()

        manifest = write_bundle_facts_package(facts, store=store)

        (variant,) = manifest.variant_refs
        assert variant.captured["variant_fingerprint"] == "gcc13-avx2"

    def test_no_manifest_means_no_project_sections(self) -> None:
        facts = capture_bundle_facts({"liba.so": _snapshot("liba.so")})
        store = InMemoryObjectStore()

        manifest = write_bundle_facts_package(facts, store=store)

        assert dict(manifest.project_sections) == {}

    def test_instantiation_manifest_becomes_one_project_section(self) -> None:
        instantiation_manifest = InstantiationManifest(
            entries=(ManifestEntry(symbol="_Z3fooi"),)
        )
        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so")}, manifest=instantiation_manifest
        )
        store = InMemoryObjectStore()

        manifest = write_bundle_facts_package(facts, store=store)

        assert INSTANTIATION_MANIFEST_SECTION_KIND in manifest.project_sections

    def test_byte_identical_libraries_collapse_to_shared_stored_objects(self) -> None:
        """Two libraries whose per-section content is byte-identical (here:
        two minimal, otherwise-empty snapshots) must not double the object
        count in the store -- `ObjectStore` addresses by digest, not by
        which artifact asked for it, per D7/A1.5's own dedup guarantee."""
        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so"), "libb.so": _snapshot("libb.so")}
        )
        store = InMemoryObjectStore()

        manifest = write_bundle_facts_package(facts, store=store)

        (artifact_a, artifact_b) = sorted(
            manifest.artifact_refs, key=lambda a: a.artifact_id
        )
        # Every section kind either artifact carries points at the same
        # digest as its sibling's matching section, since the underlying
        # AbiSnapshot content is identical apart from the library name
        # itself (which "binary"/"provenance" each carry) -- checked on
        # every *other* section kind, which genuinely carries no per-library
        # difference here.
        identity_bearing = {"binary", "provenance"}
        shared_kinds = (
            set(artifact_a.sections) & set(artifact_b.sections) - identity_bearing
        )
        assert shared_kinds
        for kind in shared_kinds:
            assert artifact_a.sections[kind].digest == artifact_b.sections[kind].digest


class TestReadBundleFactsPackage:
    def test_round_trips_an_equivalent_bundle_facts(self) -> None:
        instantiation_manifest = InstantiationManifest(
            entries=(ManifestEntry(symbol="_Z3fooi"),)
        )
        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so"), "libb.so": _snapshot("libb.so")},
            manifest=instantiation_manifest,
            variant_fingerprint="gcc13",
        )
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)

        round_tripped = read_bundle_facts_package(manifest, store=store)

        assert sorted(round_tripped.per_library_snapshots) == ["liba.so", "libb.so"]
        assert round_tripped.per_library_snapshots["liba.so"].library == "liba.so"
        assert round_tripped.variant_fingerprint == "gcc13"
        assert round_tripped.manifest is not None
        assert round_tripped.manifest.entries[0].symbol == "_Z3fooi"

    def test_missing_variant_fingerprint_defaults(self) -> None:
        facts = capture_bundle_facts({"liba.so": _snapshot("liba.so")})
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)

        round_tripped = read_bundle_facts_package(manifest, store=store)

        assert round_tripped.variant_fingerprint == facts.variant_fingerprint

    def test_native_identity_round_trips_filesystem_facts(self) -> None:
        facts = capture_bundle_facts({"liba.so": _snapshot("liba.so")})
        facts.library_filenames["liba.so"] = "liba.so.1.2.3"
        facts.filesystem_aliases["liba.so"] = ("liba.so.1", "liba.so.1.2")
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)

        round_tripped = read_bundle_facts_package(manifest, store=store)

        assert round_tripped.library_filenames == {"liba.so": "liba.so.1.2.3"}
        assert round_tripped.filesystem_aliases == {
            "liba.so": ("liba.so.1", "liba.so.1.2")
        }

    def test_unknown_variant_id_raises(self) -> None:
        facts = capture_bundle_facts({"liba.so": _snapshot("liba.so")})
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)

        with pytest.raises(ValueError, match="not a variant_id"):
            read_bundle_facts_package(manifest, store=store, variant_id="nope")

    def test_empty_bundle_round_trips_to_no_libraries(self) -> None:
        facts = BundleFacts()
        store = InMemoryObjectStore()

        manifest = write_bundle_facts_package(facts, store=store)
        round_tripped = read_bundle_facts_package(manifest, store=store)

        assert round_tripped.per_library_snapshots == {}


class TestBundleFactsPackageThroughDirectoryStore:
    """The full round trip through the real, filesystem-backed D6 layout --
    not just an `InMemoryObjectStore` -- exercising
    `write_project_manifest`/`read_project_manifest`'s own newly-added
    `PackageManifest.project_sections` plumbing end to end."""

    def test_full_directory_round_trip(self, tmp_path: Path) -> None:
        instantiation_manifest = InstantiationManifest(
            entries=(ManifestEntry(symbol="_Z3fooi"),)
        )
        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so"), "libb.so": _snapshot("libb.so")},
            manifest=instantiation_manifest,
        )
        facts.library_filenames["liba.so"] = "liba.so.1.2.3"

        store = DirectoryObjectStore(tmp_path)
        manifest = write_bundle_facts_package(facts, store=store)
        write_project_manifest(tmp_path, manifest)

        reloaded_manifest = read_project_manifest(tmp_path)
        assert INSTANTIATION_MANIFEST_SECTION_KIND in reloaded_manifest.project_sections

        round_tripped = read_bundle_facts_package(
            reloaded_manifest, store=DirectoryObjectStore(tmp_path)
        )
        assert sorted(round_tripped.per_library_snapshots) == ["liba.so", "libb.so"]
        assert round_tripped.library_filenames == {"liba.so": "liba.so.1.2.3"}
        assert round_tripped.manifest is not None
        assert round_tripped.manifest.entries[0].symbol == "_Z3fooi"


class TestWriteBundleFactsPackageSchemaVersionConsistency:
    def test_disagreeing_source_schema_versions_raise(self, monkeypatch: Any) -> None:
        import abicheck.bundle_facts_store as module

        real_snapshot_to_dict = module.snapshot_to_dict

        def fake_snapshot_to_dict(snap: AbiSnapshot) -> dict[str, Any]:
            document = real_snapshot_to_dict(snap)
            # Force the second library to claim a different, still-valid
            # legacy schema_version than the first, simulating two
            # snapshots captured under different abicheck producer epochs.
            document["schema_version"] = 1 if snap.library == "liba.so" else 2
            return document

        monkeypatch.setattr(module, "snapshot_to_dict", fake_snapshot_to_dict)
        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so"), "libb.so": _snapshot("libb.so")}
        )
        store = InMemoryObjectStore()

        with pytest.raises(ValueError, match="disagrees with"):
            write_bundle_facts_package(facts, store=store)
