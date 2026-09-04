# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""`abicheck.bundle_facts_store` — the live-`BundleFacts` adapter over
`storage.import_bundle_facts` (ADR-062 A1.4/A1.5; the Track B/C
reconciliation this module's own module docstring records).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from abicheck.bundle_facts import BundleFacts, capture_bundle_facts
from abicheck.bundle_facts_store import (
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

        # artifact_id may be an opaque, resolve_ref_ids-generated id -- not
        # the library name itself (a real library name can contain
        # characters ArtifactRef.artifact_id's own safety validation
        # refuses) -- so identity is checked via the recovered
        # native_identity library_name instead.
        recovered_names = {
            a.native_identity["library_name"] for a in manifest.artifact_refs
        }
        assert recovered_names == {"liba.so", "libb.so"}
        assert [v.variant_id for v in manifest.variant_refs] == ["default"]
        (variant,) = manifest.variant_refs
        assert set(variant.artifact_ids) == {
            a.artifact_id for a in manifest.artifact_refs
        }
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

    def test_byte_identical_libraries_collapse_to_shared_stored_objects(self) -> None:
        """Two libraries whose per-section content is byte-identical (here:
        two minimal, otherwise-empty snapshots) must not double the object
        count in the store -- `ObjectStore` addresses by digest, not by
        which artifact asked for it."""
        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so"), "libb.so": _snapshot("libb.so")}
        )
        store = InMemoryObjectStore()

        manifest = write_bundle_facts_package(facts, store=store)

        (artifact_a, artifact_b) = sorted(
            manifest.artifact_refs, key=lambda a: a.artifact_id
        )
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

    def test_round_trips_template_instantiation_argument_order(self) -> None:
        """An out-of-alphabetical-order instantiation must not silently
        reorder into a different, unpromised symbol on round trip."""
        instantiation_manifest = InstantiationManifest(
            entries=(
                ManifestEntry(
                    template="acme::train_ops",
                    instantiations=({"Z": "int", "A": "float"},),
                ),
            )
        )
        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so")}, manifest=instantiation_manifest
        )
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)

        round_tripped = read_bundle_facts_package(manifest, store=store)

        assert round_tripped.manifest is not None
        (entry,) = round_tripped.manifest.entries
        assert entry.display_name() == "acme::train_ops<int, float>"

    def test_missing_variant_fingerprint_defaults(self) -> None:
        facts = capture_bundle_facts({"liba.so": _snapshot("liba.so")})
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)

        round_tripped = read_bundle_facts_package(manifest, store=store)

        assert round_tripped.variant_fingerprint == facts.variant_fingerprint

    def test_round_trips_filesystem_facts(self) -> None:
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

    def test_round_trips_an_alias_containing_a_newline(self) -> None:
        """POSIX allows a newline inside a real filename."""
        facts = capture_bundle_facts({"liba.so": _snapshot("liba.so")})
        facts.filesystem_aliases["liba.so"] = ("weird\nname.so", "liba.so.1")
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)

        round_tripped = read_bundle_facts_package(manifest, store=store)

        assert round_tripped.filesystem_aliases == {
            "liba.so": ("weird\nname.so", "liba.so.1")
        }

    def test_unknown_variant_id_raises(self) -> None:
        facts = capture_bundle_facts({"liba.so": _snapshot("liba.so")})
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)

        with pytest.raises(ValueError, match="not a variant_id"):
            read_bundle_facts_package(manifest, store=store, variant_id="nope")

    def test_refuses_an_incompatible_comparison_contract_version(self) -> None:
        """A `PackageManifest` is public and constructible directly, so a
        caller that builds or loads one without routing it through
        `project_snapshot_store.read_manifest_summary` must still be
        refused here at this public reader boundary."""
        import dataclasses

        from abicheck.storage.versioning import StorageVersions

        facts = capture_bundle_facts({"liba.so": _snapshot("liba.so")})
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)
        incompatible = dataclasses.replace(
            manifest,
            versions=StorageVersions(comparison_contract_version=999),
        )

        with pytest.raises(ValueError, match="not readable"):
            read_bundle_facts_package(incompatible, store=store)

    def test_empty_bundle_round_trips_to_no_libraries(self) -> None:
        facts = BundleFacts()
        store = InMemoryObjectStore()

        manifest = write_bundle_facts_package(facts, store=store)
        round_tripped = read_bundle_facts_package(manifest, store=store)

        assert round_tripped.per_library_snapshots == {}

    def test_refuses_to_eagerly_reconstruct_past_the_library_count_bound(
        self, monkeypatch: Any
    ) -> None:
        """A `PackageManifest` may come from another producer -- untrusted
        input a reader must not eagerly materialize without bound."""
        import abicheck.bundle_facts_store as module

        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so"), "libb.so": _snapshot("libb.so")}
        )
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)

        monkeypatch.setattr(module, "DEFAULT_MAX_LIBRARY_COUNT", 1)

        with pytest.raises(ValueError, match="DEFAULT_MAX_LIBRARY_COUNT"):
            read_bundle_facts_package(manifest, store=store)

    def test_refuses_to_eagerly_reconstruct_past_the_decoded_size_budget(
        self, monkeypatch: Any
    ) -> None:
        """A *few* individually-sized artifacts can amplify past the count
        bound too -- charged against `DEFAULT_MAX_BUNDLE_DECODED_BYTES`."""
        import abicheck.bundle_facts_store as module

        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so"), "libb.so": _snapshot("libb.so")}
        )
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)

        monkeypatch.setattr(module, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", 1)

        with pytest.raises(ValueError, match="DEFAULT_MAX_BUNDLE_DECODED_BYTES"):
            read_bundle_facts_package(manifest, store=store)

    def test_rejects_the_single_artifact_that_itself_crosses_the_budget(
        self, monkeypatch: Any
    ) -> None:
        """A one-artifact variant has no *next* iteration to catch an
        over-budget artifact at -- the artifact that itself crosses the
        budget must be rejected on the spot, not returned successfully."""
        import abicheck.bundle_facts_store as module

        facts = capture_bundle_facts({"liba.so": _snapshot("liba.so")})
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)

        monkeypatch.setattr(module, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", 1)

        with pytest.raises(ValueError, match="DEFAULT_MAX_BUNDLE_DECODED_BYTES"):
            read_bundle_facts_package(manifest, store=store)

    def test_charges_each_reconstructed_artifact_incrementally(
        self, monkeypatch: Any
    ) -> None:
        """The decoded-byte budget must be checked *as* each artifact is
        reconstructed, not only after every member of the variant has
        already been retained in memory -- a budget set just above one
        artifact's own size must still reject a second, later artifact
        rather than letting the whole (over-budget) bundle through because
        the check only ran once, at the end."""
        import json

        import abicheck.bundle_facts_store as module
        from abicheck.storage.import_v1 import export_legacy_snapshot

        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so"), "libb.so": _snapshot("libb.so")}
        )
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)

        one_artifact_document = export_legacy_snapshot(
            manifest.artifact_refs[0],
            store=store,
            source_schema_version=manifest.versions.source_schema_version,
        )
        one_artifact_bytes = len(json.dumps(one_artifact_document).encode("utf-8"))
        # Comfortably above one artifact's own size (plus the small
        # bundle-composition section charged first), too small once a
        # second, same-sized artifact is also charged.
        monkeypatch.setattr(
            module, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", one_artifact_bytes + 64
        )

        with pytest.raises(ValueError, match="DEFAULT_MAX_BUNDLE_DECODED_BYTES"):
            read_bundle_facts_package(manifest, store=store)

    def test_refuses_to_eagerly_reconstruct_past_the_alias_node_budget(
        self, monkeypatch: Any
    ) -> None:
        """The alias element-count budget applies on the read side too, not
        only when writing -- a hand-assembled bundle-composition section
        carrying more aliases than `DEFAULT_MAX_JSON_CONTAINER_NODES` must
        be refused even while comfortably under the byte ceiling."""
        import abicheck.bundle_facts_store as module

        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so"), "libb.so": _snapshot("libb.so")}
        )
        facts.filesystem_aliases["liba.so"] = tuple(f"alias{i}" for i in range(10))
        facts.filesystem_aliases["libb.so"] = tuple(f"alias{i}" for i in range(10))
        store = InMemoryObjectStore()

        # Write with a comfortably large node budget so the package itself
        # is producible, then tighten it back down for the read.
        monkeypatch.setattr(module, "DEFAULT_MAX_JSON_CONTAINER_NODES", 10_000)
        manifest = write_bundle_facts_package(facts, store=store)
        monkeypatch.setattr(module, "DEFAULT_MAX_JSON_CONTAINER_NODES", 11)

        with pytest.raises(ValueError, match="DEFAULT_MAX_JSON_CONTAINER_NODES"):
            read_bundle_facts_package(manifest, store=store)


class TestBundleFactsPackageThroughDirectoryStore:
    """The full round trip through the real, filesystem-backed D6 layout --
    not just an `InMemoryObjectStore`."""

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

        real_bundle_facts_to_dict = module.bundle_facts_to_dict

        def fake_bundle_facts_to_dict(facts: BundleFacts) -> dict[str, Any]:
            document = real_bundle_facts_to_dict(facts)
            # Force the second library to claim a different, still-valid
            # legacy schema_version than the first, simulating two
            # snapshots captured under different abicheck producer epochs.
            for name, snapshot_document in document["per_library_snapshots"].items():
                snapshot_document["schema_version"] = 1 if name == "liba.so" else 2
            return document

        monkeypatch.setattr(module, "bundle_facts_to_dict", fake_bundle_facts_to_dict)
        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so"), "libb.so": _snapshot("libb.so")}
        )
        store = InMemoryObjectStore()

        with pytest.raises(ValueError, match="schema_version"):
            write_bundle_facts_package(facts, store=store)


class TestWriteBundleFactsPackageVariantFingerprint:
    def test_empty_variant_fingerprint_is_rejected_not_normalized(self) -> None:
        """`bundle_multibuild._index_by_fingerprint` already rejects an
        empty fingerprint outright -- silently normalizing it into
        `DEFAULT_VARIANT_FINGERPRINT` on write would let malformed facts
        pair with a legitimate default variant instead."""
        facts = capture_bundle_facts({"liba.so": _snapshot("liba.so")})
        facts.variant_fingerprint = ""
        store = InMemoryObjectStore()

        with pytest.raises(ValueError, match="variant_fingerprint"):
            write_bundle_facts_package(facts, store=store)


class TestWriteBundleFactsPackageMirrorsReaderLimits:
    """`write_bundle_facts_package` must not hand back a `PackageManifest`
    its own promised inverse, `read_bundle_facts_package`, then refuses to
    reconstruct."""

    def test_refuses_to_write_past_the_library_count_bound(
        self, monkeypatch: Any
    ) -> None:
        import abicheck.bundle_facts_store as module

        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so"), "libb.so": _snapshot("libb.so")}
        )
        store = InMemoryObjectStore()

        monkeypatch.setattr(module, "DEFAULT_MAX_LIBRARY_COUNT", 1)

        with pytest.raises(ValueError, match="DEFAULT_MAX_LIBRARY_COUNT"):
            write_bundle_facts_package(facts, store=store)

    def test_refuses_to_write_past_the_decoded_size_budget(
        self, monkeypatch: Any
    ) -> None:
        import abicheck.bundle_facts_store as module

        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so"), "libb.so": _snapshot("libb.so")}
        )
        store = InMemoryObjectStore()

        monkeypatch.setattr(module, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", 1)

        with pytest.raises(ValueError, match="DEFAULT_MAX_BUNDLE_DECODED_BYTES"):
            write_bundle_facts_package(facts, store=store)

    def test_refuses_to_write_past_the_alias_node_budget(
        self, monkeypatch: Any
    ) -> None:
        """A reader decoding many small alias arrays back pays one
        allocation per element -- a node-count amplification a byte-size
        charge alone cannot see, so the writer must refuse a bundle whose
        aggregate alias element count a reader would then refuse too."""
        import abicheck.bundle_facts_store as module

        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so"), "libb.so": _snapshot("libb.so")}
        )
        facts.filesystem_aliases["liba.so"] = tuple(f"alias{i}" for i in range(10))
        facts.filesystem_aliases["libb.so"] = tuple(f"alias{i}" for i in range(10))
        store = InMemoryObjectStore()

        monkeypatch.setattr(module, "DEFAULT_MAX_JSON_CONTAINER_NODES", 11)

        with pytest.raises(ValueError, match="DEFAULT_MAX_JSON_CONTAINER_NODES"):
            write_bundle_facts_package(facts, store=store)


class TestWriteBundleFactsPackageValidatesManifestStructure:
    def test_refuses_a_template_entry_with_no_instantiations(self) -> None:
        """`ManifestEntry`'s own dataclass construction doesn't enforce
        `manifest_from_dict`'s "a template entry needs a non-empty
        instantiations list" constraint -- a directly-constructed
        `InstantiationManifest` violating it must be refused rather than
        written successfully and only fail on a later read."""
        invalid_manifest = InstantiationManifest(
            entries=(ManifestEntry(template="acme::train_ops", instantiations=()),)
        )
        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so")}, manifest=invalid_manifest
        )
        store = InMemoryObjectStore()

        with pytest.raises(ValueError, match="instantiations"):
            write_bundle_facts_package(facts, store=store)


class TestBundleFactsPackageSurrogateEscapedFilenames:
    def test_round_trips_a_filename_with_a_non_utf8_byte(self) -> None:
        """A real POSIX basename containing a non-UTF-8 byte decodes (via
        `os.fsdecode`) to a lone surrogate character."""
        facts = capture_bundle_facts({"liba.so": _snapshot("liba.so")})
        facts.library_filenames["liba.so"] = "caf\udce9"
        store = InMemoryObjectStore()

        manifest = write_bundle_facts_package(facts, store=store)
        round_tripped = read_bundle_facts_package(manifest, store=store)

        assert round_tripped.library_filenames == {"liba.so": "caf\udce9"}


class TestBundleFactsPackageLegalButUnsafeLibraryNames:
    def test_round_trips_case_only_distinct_library_names(self) -> None:
        """ELF library matching is deliberately case-sensitive, so
        `libFoo.so`/`libfoo.so` are two distinct, legal bundle members."""
        facts = capture_bundle_facts(
            {"libFoo.so": _snapshot("libFoo.so"), "libfoo.so": _snapshot("libfoo.so")}
        )
        store = InMemoryObjectStore()

        manifest = write_bundle_facts_package(facts, store=store)
        round_tripped = read_bundle_facts_package(manifest, store=store)

        assert sorted(round_tripped.per_library_snapshots) == ["libFoo.so", "libfoo.so"]

    def test_round_trips_a_library_name_with_a_colon(self) -> None:
        """A `:` is not a `_safe_ref_id`-valid artifact_id character but is
        a legal byte in a real SONAME/basename."""
        facts = capture_bundle_facts({"weird:name.so": _snapshot("weird:name.so")})
        store = InMemoryObjectStore()

        manifest = write_bundle_facts_package(facts, store=store)
        round_tripped = read_bundle_facts_package(manifest, store=store)

        assert list(round_tripped.per_library_snapshots) == ["weird:name.so"]
