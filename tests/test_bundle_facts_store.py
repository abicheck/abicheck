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
        already been retained in memory. Three same-sized libraries, a
        budget that admits the composition section plus one full artifact
        charge and half of a second: the *second* artifact's own charge
        must cross the budget (it is still reconstructed before its charge
        raises, matching the write-side incremental test's own reasoning),
        but the third must never even be reconstructed -- proving the check
        runs incrementally, not as one charge over an already-fully-
        materialized bundle.

        The budget margin is calibrated against the exact
        `bounded_encode_utf8` (indent=2) primitive the real charge uses, and
        against the *charged* shapes -- the bundle-composition section
        first, then each artifact as `{"library_name": ..., "snapshot":
        ...}` -- not a compact `json.dumps` estimate of the bare snapshot
        alone. A too-tight two-library margin can make the *first*
        artifact's own (omitted-from-the-estimate) charges already cross
        the budget, so the test would pass without the second artifact ever
        proving anything (CodeRabbit review) -- `export_legacy_snapshot`
        call-count instrumentation proves the third artifact is never
        reconstructed, the same way the write-side incremental test proves
        conversion order rather than trusting a single assert-raises."""
        import sys

        import abicheck.bundle_facts_store as module
        from abicheck.storage.bundle_archive_json_guard import bounded_encode_utf8
        from abicheck.storage.dto import BUNDLE_COMPOSITION_SECTION_KIND
        from abicheck.storage.import_v1 import export_legacy_snapshot as real_export

        facts = capture_bundle_facts(
            {
                "liba.so": _snapshot("liba.so"),
                "libb.so": _snapshot("libb.so"),
                "libc.so": _snapshot("libc.so"),
            }
        )
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)

        composition_ref = manifest.variant_refs[0].sections[
            BUNDLE_COMPOSITION_SECTION_KIND
        ]
        composition_bytes = len(
            bounded_encode_utf8(store.get(composition_ref.digest), 2**31)
        )
        first_artifact = manifest.artifact_refs[0]
        first_document = real_export(
            first_artifact,
            store=store,
            source_schema_version=manifest.versions.source_schema_version,
        )
        first_wrapped = {
            "library_name": first_artifact.native_identity["library_name"],
            "snapshot": first_document,
        }
        first_wrapped_bytes = len(bounded_encode_utf8(first_wrapped, 2**31))
        # Room for the composition section plus one fully-charged artifact,
        # plus half of a second -- the second artifact's own charge must
        # cross the budget, not the third's.
        monkeypatch.setattr(
            module,
            "DEFAULT_MAX_BUNDLE_DECODED_BYTES",
            composition_bytes + first_wrapped_bytes + first_wrapped_bytes // 2,
        )

        exported: list[str] = []

        def counting_export(artifact: Any, **kwargs: Any) -> Any:
            exported.append(artifact.native_identity["library_name"])
            return real_export(artifact, **kwargs)

        # `abicheck.storage.__init__` re-exports `import_bundle_facts` (the
        # function) as a package attribute, shadowing the submodule of the
        # same name -- both a plain `import abicheck.storage
        # .import_bundle_facts as ...` and monkeypatch's own dotted-string
        # attribute resolution land on that function instead of the
        # submodule this patch needs. `sys.modules` still holds the real
        # submodule regardless of what shadows its parent's attribute.
        monkeypatch.setattr(
            sys.modules["abicheck.storage.import_bundle_facts"],
            "export_legacy_snapshot",
            counting_export,
        )

        with pytest.raises(ValueError, match="DEFAULT_MAX_BUNDLE_DECODED_BYTES"):
            read_bundle_facts_package(manifest, store=store)

        assert exported == ["liba.so", "libb.so"], (
            "libc.so must never be reconstructed -- its own budget check "
            "should never be reached once libb.so's charge already crosses "
            f"the limit, but export order was {exported!r}"
        )

    def test_charges_the_recovered_library_name_against_the_decoded_size_budget(
        self, monkeypatch: Any
    ) -> None:
        """The recovered library name becomes a `per_library_snapshots` key
        in the document this reconstructs -- an arbitrarily large name must
        be charged against the budget too, not retained for free just
        because it lives in `native_identity` rather than the exported
        snapshot document itself (Codex review). The library name here is
        the `per_library_snapshots` *dict key*, deliberately distinct from
        the (small) `AbiSnapshot.library` field, so only the fix under test
        -- not the snapshot document's own size -- can catch this."""
        import json

        import abicheck.bundle_facts_store as module
        from abicheck.storage.dto import BUNDLE_COMPOSITION_SECTION_KIND
        from abicheck.storage.import_v1 import export_legacy_snapshot

        long_name = "liba" + "x" * 10_000 + ".so"
        facts = capture_bundle_facts({long_name: _snapshot("liba.so")})
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)

        artifact_document = export_legacy_snapshot(
            manifest.artifact_refs[0],
            store=store,
            source_schema_version=manifest.versions.source_schema_version,
        )
        artifact_bytes = len(json.dumps(artifact_document).encode("utf-8"))
        composition_ref = manifest.variant_refs[0].sections[
            BUNDLE_COMPOSITION_SECTION_KIND
        ]
        composition_bytes = len(
            json.dumps(store.get(composition_ref.digest)).encode("utf-8")
        )
        # Large enough for the small snapshot document plus the small
        # bundle-composition section, too small once the recovered (large)
        # library name is also charged.
        monkeypatch.setattr(
            module,
            "DEFAULT_MAX_BUNDLE_DECODED_BYTES",
            artifact_bytes + composition_bytes + 256,
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

    def test_a_malformed_stored_alias_value_defers_to_the_real_validator(
        self,
    ) -> None:
        """A hand-corrupted store can carry a `filesystem_aliases` value
        that isn't a list (e.g. a stray integer) for one library, bypassing
        `import_bundle_facts`'s own `_validated_filesystem_aliases` check --
        `read_bundle_facts_package`'s own node-count budget must not crash
        with an unhandled `TypeError` on that shape before
        `bundle_facts_from_dict`'s `validated_alias_map` ever gets a chance
        to report the documented `ValueError` (CodeRabbit review;
        `_alias_element_count`'s own malformed-input contract)."""
        import dataclasses

        from abicheck.storage.dto import (
            BUNDLE_COMPOSITION_SECTION_KIND,
            bundle_composition_to_dto,
        )

        facts = capture_bundle_facts({"liba.so": _snapshot("liba.so")})
        store = InMemoryObjectStore()
        manifest = write_bundle_facts_package(facts, store=store)

        real_ref = manifest.variant_refs[0].sections[BUNDLE_COMPOSITION_SECTION_KIND]
        real_payload = dict(store.get(real_ref.digest)["payload"])
        real_payload["filesystem_aliases"] = {"liba.so": 1}
        doctored_dto = bundle_composition_to_dto(real_payload)
        doctored_digest = store.put(doctored_dto.to_dict())
        doctored_ref = dataclasses.replace(real_ref, digest=doctored_digest)
        doctored_variant = dataclasses.replace(
            manifest.variant_refs[0],
            sections={
                **manifest.variant_refs[0].sections,
                BUNDLE_COMPOSITION_SECTION_KIND: doctored_ref,
            },
        )
        doctored_manifest = dataclasses.replace(
            manifest, variant_refs=(doctored_variant,)
        )

        with pytest.raises(ValueError, match="filesystem_aliases"):
            read_bundle_facts_package(doctored_manifest, store=store)


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

        def fake_bundle_facts_to_dict(
            facts: BundleFacts, **kwargs: Any
        ) -> dict[str, Any]:
            document = real_bundle_facts_to_dict(facts, **kwargs)
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

    def test_charges_each_converted_snapshot_incrementally_on_write(
        self, monkeypatch: Any
    ) -> None:
        """The decoded-byte budget must be checked *as* each library's
        snapshot is converted, not only after every member of `facts` has
        already been converted and retained in one combined document. Three
        libraries, a budget that admits exactly two charged snapshots: the
        third must never even be *converted* -- proving the check runs
        incrementally, not as one charge over an already-fully-materialized
        document (Codex review). A plain assert-raises test alone cannot
        distinguish the two: both eventually raise the same error for a
        budget this tight; only the conversion *call count* can show the
        difference. Note that the *offending* item (the second one here,
        which crosses the budget) is itself still converted before its own
        charge raises -- only items *after* it are never reached; a naive
        `len(converted) < 3` assertion would not actually distinguish
        incremental from non-incremental charging if the budget instead
        happened to admit the third item's own conversion too, which is why
        the budget here is calibrated (via the exact `bounded_encode_utf8`
        primitive the real charge uses, not an approximate compact
        `json.dumps`) to cross specifically on the second item."""
        import abicheck.bundle_facts_store as module
        import abicheck.serialization as serialization_module
        from abicheck.storage.bundle_archive_json_guard import bounded_encode_utf8

        facts = capture_bundle_facts(
            {
                "liba.so": _snapshot("liba.so"),
                "libb.so": _snapshot("libb.so"),
                "libc.so": _snapshot("libc.so"),
            }
        )
        store = InMemoryObjectStore()

        wrapped = {
            "library_name": "liba.so",
            "snapshot": serialization_module.snapshot_to_dict(_snapshot("liba.so")),
        }
        wrapped_bytes = len(bounded_encode_utf8(wrapped, 2**31))
        # Room for exactly one full charge plus half of a second -- the
        # second item's own charge must cross the budget, not the third's.
        monkeypatch.setattr(
            module,
            "DEFAULT_MAX_BUNDLE_DECODED_BYTES",
            wrapped_bytes + wrapped_bytes // 2,
        )

        real_snapshot_to_dict = serialization_module.snapshot_to_dict
        converted: list[str] = []

        def counting_snapshot_to_dict(snap: AbiSnapshot) -> dict[str, Any]:
            converted.append(snap.library)
            return real_snapshot_to_dict(snap)

        monkeypatch.setattr(
            serialization_module, "snapshot_to_dict", counting_snapshot_to_dict
        )

        with pytest.raises(ValueError, match="DEFAULT_MAX_BUNDLE_DECODED_BYTES"):
            write_bundle_facts_package(facts, store=store)

        assert converted == ["liba.so", "libb.so"], (
            "libc.so must never be converted -- its own budget check should "
            "never be reached once libb.so's charge already crosses the "
            f"limit, but conversion order was {converted!r}"
        )

    def test_charges_the_library_name_against_the_write_side_decoded_size_budget(
        self, monkeypatch: Any
    ) -> None:
        """`library_name` becomes a `per_library_snapshots` key in the
        written document, exactly the way `export_bundle_facts`'s own
        `on_document` hook charges the recovered library name on the read
        side -- an arbitrarily large key must be charged on write too, or
        `write_bundle_facts_package` can hand back a package
        `read_bundle_facts_package` then refuses to reopen (Codex review).
        The library name here is the `per_library_snapshots` *dict key*,
        deliberately distinct from the (small) `AbiSnapshot.library` field,
        so only the fix under test -- not the snapshot's own size -- can
        catch this."""
        import abicheck.bundle_facts_store as module
        from abicheck.serialization import snapshot_to_dict
        from abicheck.storage.bundle_archive_json_guard import bounded_encode_utf8

        long_name = "liba" + "x" * 10_000 + ".so"
        facts = capture_bundle_facts({long_name: _snapshot("liba.so")})
        store = InMemoryObjectStore()

        # Charging uses `bounded_encode_utf8` (indent=2), not a compact
        # `json.dumps` -- computed via the identical primitive so the
        # budget below is exact, not a guess that happens to work.
        def _encoded_len(obj: Any) -> int:
            encoded = bounded_encode_utf8(obj, 2**31)
            assert encoded is not None
            return len(encoded)

        snapshot_only_bytes = _encoded_len(snapshot_to_dict(_snapshot("liba.so")))
        document = module.bundle_facts_to_dict(facts)
        composition_only_bytes = _encoded_len(
            {k: v for k, v in document.items() if k != "per_library_snapshots"}
        )
        # Enough for the snapshot document plus the small composition
        # content, too small once the (large) library name is also
        # charged.
        monkeypatch.setattr(
            module,
            "DEFAULT_MAX_BUNDLE_DECODED_BYTES",
            snapshot_only_bytes + composition_only_bytes + 32,
        )

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
