# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""`abicheck.bundle_facts_store` — the first real multi-artifact
`ProjectSnapshot` package writer/reader (ADR-062 A1.4/A1.5).
"""

from __future__ import annotations

import json
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
from abicheck.serialization import snapshot_to_dict
from abicheck.storage.import_v1 import export_legacy_snapshot
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

        # artifact_id is now an opaque, content-derived id -- not the
        # library name itself (Codex review: a real library name can
        # contain characters ArtifactRef.artifact_id's own safety
        # validation refuses) -- so identity is checked via the recovered
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

    def test_round_trips_template_instantiation_argument_order(self) -> None:
        """`ObjectStore.put()` canonicalizes a mapping by sorting its keys,
        but `_expand_instantiations()` reads an instantiation's *insertion*
        order as template-argument order -- an out-of-alphabetical-order
        instantiation must not silently reorder into a different, unpromised
        symbol on round trip (Codex review)."""
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

    def test_native_identity_round_trips_an_alias_containing_a_newline(self) -> None:
        """POSIX allows a newline inside a real filename -- an alias-array
        encoding that joined on `"\\n"` would silently split this one alias
        into two on read-back (Codex review)."""
        facts = capture_bundle_facts({"liba.so": _snapshot("liba.so")})
        facts.filesystem_aliases["liba.so"] = ("weird\nname.so", "liba.so.1")
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)

        round_tripped = read_bundle_facts_package(manifest, store=store)

        assert round_tripped.filesystem_aliases == {
            "liba.so": ("liba.so.1", "weird\nname.so")
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
        `project_snapshot_store.read_manifest_summary` must still be refused
        here at this public reader boundary (Codex review)."""
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
        input a reader must not eagerly materialize without bound (Codex
        review)."""
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
        bound too -- charged against `DEFAULT_MAX_BUNDLE_DECODED_BYTES`,
        the same aggregate ceiling the G40 bundle-facts archive enforces
        (Codex review, fresh evidence)."""
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
        budget must be rejected on the spot, not returned successfully
        (Codex review, second finding on this same guard)."""
        import abicheck.bundle_facts_store as module

        facts = capture_bundle_facts({"liba.so": _snapshot("liba.so")})
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)

        monkeypatch.setattr(module, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", 1)

        with pytest.raises(ValueError, match="DEFAULT_MAX_BUNDLE_DECODED_BYTES"):
            read_bundle_facts_package(manifest, store=store)

    def test_charges_the_project_manifest_against_the_decoded_size_budget(
        self, monkeypatch: Any
    ) -> None:
        """The instantiation manifest is fetched *after* the per-artifact
        budget loop -- it must be charged too, not parsed for free once the
        artifacts alone already passed (Codex review, third finding on this
        same guard)."""
        import abicheck.bundle_facts_store as module

        instantiation_manifest = InstantiationManifest(
            entries=(ManifestEntry(symbol="_Z3fooi"),)
        )
        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so")}, manifest=instantiation_manifest
        )
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)

        # A budget comfortably above the lone artifact's own decoded size,
        # but too small once the project-level manifest's decoded size is
        # also charged.
        artifact_document = export_legacy_snapshot(
            manifest.artifact_refs[0],
            store=store,
            source_schema_version=manifest.versions.source_schema_version,
        )
        artifact_bytes = len(json.dumps(artifact_document).encode("utf-8"))
        monkeypatch.setattr(
            module, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", artifact_bytes + 1
        )

        with pytest.raises(ValueError, match="DEFAULT_MAX_BUNDLE_DECODED_BYTES"):
            read_bundle_facts_package(manifest, store=store)

    def test_charges_native_identity_against_the_decoded_size_budget(
        self, monkeypatch: Any
    ) -> None:
        """`native_identity` (the filename/aliases facts) lives outside the
        exported snapshot document entirely -- a budget sized exactly to
        the document alone must not let it through for free (Codex review,
        fresh evidence beyond the artifact/project-section budget fixes)."""
        import abicheck.bundle_facts_store as module

        facts = capture_bundle_facts({"liba.so": _snapshot("liba.so")})
        facts.library_filenames["liba.so"] = "liba.so.1.2.3"
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)

        artifact_document = export_legacy_snapshot(
            manifest.artifact_refs[0],
            store=store,
            source_schema_version=manifest.versions.source_schema_version,
        )
        artifact_bytes = len(json.dumps(artifact_document).encode("utf-8"))
        # Exactly the document's own size -- large enough for the document
        # alone, too small once the filename is also charged.
        monkeypatch.setattr(module, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", artifact_bytes)

        with pytest.raises(ValueError, match="DEFAULT_MAX_BUNDLE_DECODED_BYTES"):
            read_bundle_facts_package(manifest, store=store)

    def test_refuses_a_project_section_whose_ref_kind_does_not_match(self) -> None:
        """A `project_sections` key and its `ObjectRef.kind` are two
        independent fields (Codex review) -- a corrupted or hand-assembled
        package naming `instantiation_manifest` but pointing at an
        `ObjectRef` of a different declared kind must be refused, the same
        way `export_legacy_snapshot` already refuses a per-artifact section
        mismatch."""
        import dataclasses

        instantiation_manifest = InstantiationManifest(
            entries=(ManifestEntry(symbol="_Z3fooi"),)
        )
        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so")}, manifest=instantiation_manifest
        )
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)
        real_ref = manifest.project_sections[INSTANTIATION_MANIFEST_SECTION_KIND]
        corrupted = dataclasses.replace(
            manifest,
            project_sections={
                INSTANTIATION_MANIFEST_SECTION_KIND: dataclasses.replace(
                    real_ref, kind="not_an_instantiation_manifest"
                )
            },
        )

        with pytest.raises(ValueError, match="not_an_instantiation_manifest"):
            read_bundle_facts_package(corrupted, store=store)


class TestWriteBundleFactsPackageMirrorsReaderLimits:
    """`write_bundle_facts_package` must not hand back a `PackageManifest`
    its own promised inverse, `read_bundle_facts_package`, then refuses to
    reconstruct (Codex review)."""

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

    def test_refuses_to_write_a_manifest_that_crosses_the_budget(
        self, monkeypatch: Any
    ) -> None:
        import abicheck.bundle_facts_store as module

        instantiation_manifest = InstantiationManifest(
            entries=(ManifestEntry(symbol="_Z3fooi"),)
        )
        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so")}, manifest=instantiation_manifest
        )
        store = InMemoryObjectStore()

        # Let the lone library through, then cross the budget on the
        # project-level manifest itself.
        document = snapshot_to_dict(facts.per_library_snapshots["liba.so"])
        library_bytes = len(json.dumps(document).encode("utf-8"))
        monkeypatch.setattr(
            module, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", library_bytes + 1
        )

        with pytest.raises(ValueError, match="DEFAULT_MAX_BUNDLE_DECODED_BYTES"):
            write_bundle_facts_package(facts, store=store)


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


class TestWriteBundleFactsPackageVariantFingerprint:
    def test_empty_variant_fingerprint_is_rejected_not_normalized(self) -> None:
        """`bundle_multibuild._index_by_fingerprint` already rejects an
        empty fingerprint outright -- silently normalizing it into
        `DEFAULT_VARIANT_FINGERPRINT` on write would let malformed facts
        pair with a legitimate default variant instead (Codex review)."""
        facts = capture_bundle_facts({"liba.so": _snapshot("liba.so")})
        facts.variant_fingerprint = ""
        store = InMemoryObjectStore()

        with pytest.raises(ValueError, match="variant_fingerprint"):
            write_bundle_facts_package(facts, store=store)


class TestManifestDecodeRejectsCorruption:
    def test_rejects_a_stored_instantiation_with_a_duplicate_parameter(self) -> None:
        """A dict comprehension over untrusted stored pairs would silently
        keep only the last of a repeated parameter name, describing a
        different promised template signature than the one actually stored
        (Codex review)."""
        from abicheck.bundle_facts_store import _manifest_document_from_storage

        corrupted = {
            "provides": [
                {
                    "template": "acme::train_ops",
                    "instantiations": [
                        [
                            {"parameter": "T", "value": "int"},
                            {"parameter": "T", "value": "float"},
                        ]
                    ],
                }
            ]
        }

        with pytest.raises(ValueError, match="more than once"):
            _manifest_document_from_storage(corrupted)

    def test_rejects_an_oversized_alias_array_by_node_count(self) -> None:
        """A JSON array of millions of short strings can stay well under
        the aggregate byte budget while still costing `json.loads()` one
        allocation per element -- a node-count amplification a byte-size
        charge alone cannot see (Codex review)."""
        from abicheck.bundle_facts_store import _decode_aliases
        from abicheck.storage.json_budget import JsonContainerBudgetExceeded

        huge_alias_array = json.dumps(["a"] * 2_000_000)

        with pytest.raises(JsonContainerBudgetExceeded):
            _decode_aliases(huge_alias_array, 0)

    def test_rejects_a_bundle_wide_node_total_even_when_each_array_fits(
        self,
    ) -> None:
        """N artifacts can each carry an alias array individually under the
        per-call node cap while summing far past it in aggregate -- the
        per-call cap alone doesn't see this (Codex review, fresh evidence
        on this same guard, twice)."""
        from abicheck.bundle_facts_store import _decode_aliases
        from abicheck.storage.json_budget import JsonContainerBudgetExceeded

        # Each array is comfortably under DEFAULT_MAX_JSON_CONTAINER_NODES
        # (1_000_000) on its own.
        one_array = json.dumps(["a"] * 900_000)

        nodes_so_far = 0
        _aliases, nodes_so_far = _decode_aliases(one_array, nodes_so_far)
        with pytest.raises(JsonContainerBudgetExceeded):
            _decode_aliases(one_array, nodes_so_far)


class TestWriteBundleFactsPackageValidatesManifestStructure:
    def test_refuses_a_template_entry_with_no_instantiations(self) -> None:
        """`ManifestEntry`'s own dataclass construction doesn't enforce
        `manifest_from_dict`'s "a template entry needs a non-empty
        instantiations list" constraint -- a directly-constructed
        `InstantiationManifest` violating it must be refused here, not
        written successfully and only fail `read_bundle_facts_package`'s
        own decode later (Codex review)."""
        invalid_manifest = InstantiationManifest(
            entries=(ManifestEntry(template="acme::train_ops", instantiations=()),)
        )
        facts = capture_bundle_facts(
            {"liba.so": _snapshot("liba.so")}, manifest=invalid_manifest
        )
        store = InMemoryObjectStore()

        with pytest.raises(ValueError, match="instantiations"):
            write_bundle_facts_package(facts, store=store)


class TestReadBundleFactsPackageSectionCrossCheck:
    def test_refuses_an_artifact_carrying_an_unadvertised_section(self) -> None:
        """An artifact whose own `sections` names a kind the package-wide
        `section_schema_versions` never advertises at all is unambiguous
        corruption -- no legitimate write of this package could have
        produced it -- unlike a *missing* section, which a legitimate
        multi-artifact package's per-library variation can produce on its
        own (Codex review)."""
        import dataclasses

        from abicheck.storage.package import ObjectRef

        facts = capture_bundle_facts({"liba.so": _snapshot("liba.so")})
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)
        (artifact,) = manifest.artifact_refs
        bogus_digest = store.put({"bogus": "content"})
        corrupted_artifact = dataclasses.replace(
            artifact,
            sections={
                **artifact.sections,
                "not_a_real_section": ObjectRef(
                    kind="not_a_real_section", digest=bogus_digest
                ),
            },
        )
        corrupted_manifest = dataclasses.replace(
            manifest, artifact_refs=(corrupted_artifact,)
        )

        with pytest.raises(ValueError, match="not_a_real_section"):
            read_bundle_facts_package(corrupted_manifest, store=store)


class TestBundleFactsPackageSurrogateEscapedFilenames:
    def test_round_trips_a_filename_with_a_non_utf8_byte(self) -> None:
        """A real POSIX basename containing a non-UTF-8 byte decodes (via
        `os.fsdecode`) to a lone surrogate character -- a strict
        `.encode("utf-8")` used to measure decoded size raises
        `UnicodeEncodeError` on this, even though the canonical/object-store
        path already supports it (Codex review)."""
        facts = capture_bundle_facts({"liba.so": _snapshot("liba.so")})
        facts.library_filenames["liba.so"] = "caf\udce9"
        store = InMemoryObjectStore()

        manifest = write_bundle_facts_package(facts, store=store)
        round_tripped = read_bundle_facts_package(manifest, store=store)

        assert round_tripped.library_filenames == {"liba.so": "caf\udce9"}


class TestBundleFactsPackageLegalButUnsafeLibraryNames:
    def test_round_trips_case_only_distinct_library_names(self) -> None:
        """ELF library matching is deliberately case-sensitive, so
        `libFoo.so`/`libfoo.so` are two distinct, legal bundle members --
        but two artifact_ids differing only by case collide on a
        case-insensitive filesystem (`PackageManifest`'s own filesystem-
        collision guard), and passing the library name straight through as
        artifact_id made this writer unable to store such a bundle even
        though `BundleFacts` itself accepts it as input (Codex review)."""
        facts = capture_bundle_facts(
            {"libFoo.so": _snapshot("libFoo.so"), "libfoo.so": _snapshot("libfoo.so")}
        )
        store = InMemoryObjectStore()

        manifest = write_bundle_facts_package(facts, store=store)
        round_tripped = read_bundle_facts_package(manifest, store=store)

        assert sorted(round_tripped.per_library_snapshots) == ["libFoo.so", "libfoo.so"]

    def test_round_trips_a_library_name_with_a_colon(self) -> None:
        """A `:` is not a `_safe_ref_id`-valid artifact_id character but is
        a legal byte in a real SONAME/basename (Codex review)."""
        facts = capture_bundle_facts({"weird:name.so": _snapshot("weird:name.so")})
        store = InMemoryObjectStore()

        manifest = write_bundle_facts_package(facts, store=store)
        round_tripped = read_bundle_facts_package(manifest, store=store)

        assert list(round_tripped.per_library_snapshots) == ["weird:name.so"]
