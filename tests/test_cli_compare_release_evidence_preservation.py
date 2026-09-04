# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""ADR-062 A1.7 stored-release comparison -- evidence-preservation
regressions (Codex review, eighth and ninth rounds).

Split out of `test_cli_compare_release_project_snapshot_package.py` (which
covers the feature's own stored/live parity acceptance tests) once that
file crossed the 1200-line test-file cap: this module covers everything
about a materialized sub-package *preserving* evidence a stored release
package carried -- object-store scoping, an embedded `InstantiationManifest`
(both writer shapes), `--dso-only` filtering, and real filename/alias
identity (both writer shapes) -- rather than the comparison outcomes
themselves. Shares fixtures/helpers with the parent module via a plain
sibling import (this package has no `__init__.py`; see
`tests/test_entity_id_carrier.py` for the same pattern).
"""

from __future__ import annotations

from pathlib import Path

from test_cli_compare_release_project_snapshot_package import (
    DirectoryObjectStore,
    _fn,
    _invoke,
    _old_new_libraries,
    _snap,
    _with_filenames,
    _write_directory,
    _write_package,
    _write_package_with_filenames,
    capture_bundle_facts,
    json,
    write_bundle_facts_package,
    write_project_manifest,
)


class TestMaterializationObjectScoping:
    """Codex review, eighth round, security finding: materializing a
    sub-package used to symlink (or, on hosts without symlink privilege,
    `shutil.copytree`) the *entire* source `objects/` tree into every
    single-artifact sub-package -- an N-fold disk cost for objects
    unrelated to that one library, and `copytree`'s own symlink-following
    default meant a crafted package's `objects/` tree could point outside
    itself and have its content silently copied in, unverified.
    Materialization now goes through `ObjectStore.get()`/`.put()` for
    exactly the referenced digests, which also means every byte is
    digest-verified on the way in/out."""

    def test_only_this_artifacts_own_objects_are_materialized(
        self, tmp_path: Path
    ) -> None:
        from abicheck.project_snapshot_store import read_project_manifest
        from abicheck.workflows.release_package import resolve_release_package_map

        old_libs, _ = _old_new_libraries()
        pkg = tmp_path / "pkg"
        _write_package(pkg, old_libs, variant_id="v1")

        resolved = resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        assert len(resolved) == 3

        def _object_files(root: Path) -> set[Path]:
            objects_dir = root / "objects"
            if not objects_dir.is_dir():
                return set()
            return {p for p in objects_dir.rglob("*") if p.is_file()}

        for sub_dir in resolved.values():
            sub_manifest = read_project_manifest(sub_dir)
            (artifact,) = sub_manifest.artifact_refs
            expected_count = len(artifact.sections)
            assert expected_count > 0
            assert len(_object_files(sub_dir)) == expected_count


class TestEmbeddedInstantiationManifest:
    """Codex review, eighth round: nothing in the ordinary
    `compare-release` bundle-analysis path ever consulted a stored
    package's own embedded `InstantiationManifest`
    (`write_bundle_facts_package`'s `project_sections` entry) unless the
    caller passed an explicit `--manifest` -- so a package's own captured
    manifest-drift contract silently went unenforced during a stored/
    stored or stored/live comparison, even after the composition-
    preservation fix made the section survive materialization."""

    def test_manifest_survives_materialization_and_is_read_back(
        self, tmp_path: Path
    ) -> None:
        from abicheck.bundle_facts_store import read_embedded_instantiation_manifest
        from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry
        from abicheck.workflows.release_package import resolve_release_package_map

        old_libs, _ = _old_new_libraries()
        manifest = InstantiationManifest(entries=(ManifestEntry(symbol="core_mul"),))
        facts = _with_filenames(
            capture_bundle_facts(
                old_libs, manifest=manifest, variant_fingerprint="gcc13-avx2"
            )
        )
        pkg = tmp_path / "pkg"
        store = DirectoryObjectStore(pkg)
        pkg_manifest = write_bundle_facts_package(facts, store=store, variant_id="v1")
        write_project_manifest(pkg, pkg_manifest)

        # Readable straight off the root package...
        read_back = read_embedded_instantiation_manifest(pkg)
        assert read_back is not None
        assert read_back.symbols == {"core_mul"}

        # ...and off any single-artifact sub-package materialized from it.
        resolved = resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        sub_dir = next(iter(resolved.values()))
        sub_read_back = read_embedded_instantiation_manifest(sub_dir)
        assert sub_read_back is not None
        assert sub_read_back.symbols == {"core_mul"}

    def test_no_manifest_section_reads_back_none(self, tmp_path: Path) -> None:
        from abicheck.bundle_facts_store import read_embedded_instantiation_manifest

        old_libs, _ = _old_new_libraries()
        pkg = tmp_path / "pkg"
        _write_package(pkg, old_libs, variant_id="v1")
        assert read_embedded_instantiation_manifest(pkg) is None

    def test_release_compare_uses_the_embedded_manifest_without_a_flag(
        self, tmp_path: Path
    ) -> None:
        """End to end: a stored package's own manifest promises a symbol
        the new side no longer exports -- `compare` must report the drift
        even though no `--manifest` was ever passed."""
        from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry

        old_libs, _ = _old_new_libraries()
        manifest = InstantiationManifest(entries=(ManifestEntry(symbol="_Z3foov"),))
        facts = _with_filenames(
            capture_bundle_facts(
                old_libs, manifest=manifest, variant_fingerprint="gcc13-avx2"
            )
        )
        old_pkg = tmp_path / "old_pkg"
        store = DirectoryObjectStore(old_pkg)
        pkg_manifest = write_bundle_facts_package(facts, store=store, variant_id="v1")
        write_project_manifest(old_pkg, pkg_manifest)

        _, new_libs = _old_new_libraries()  # liba.so's "foo" symbol is removed
        new_pkg = tmp_path / "new_pkg"
        _write_directory(new_pkg, new_libs)

        ec, out = _invoke(
            "compare", str(old_pkg), str(new_pkg), "--format", "json", "-j", "1"
        )
        doc = json.loads(out)
        assert doc.get("bundle_findings"), (
            f"expected a manifest-drift bundle finding, got none: {out}"
        )

    def test_composition_embedded_manifest_is_read_back(self, tmp_path: Path) -> None:
        """Codex review, ninth round: a package written by
        `storage.import_bundle_facts` (not `bundle_facts_store.
        write_bundle_facts_package`) has no project-level manifest
        section at all -- its own captured manifest lives only in the
        variant's `BUNDLE_COMPOSITION_SECTION_KIND` payload, which the
        earlier embedded-manifest fix never consulted."""
        from abicheck.bundle_facts_store import read_embedded_instantiation_manifest
        from abicheck.serialization import snapshot_to_dict
        from abicheck.storage.import_bundle_facts import (
            BUNDLE_FACTS_ARTIFACT_TYPE,
            import_bundle_facts,
        )
        from abicheck.workflows.release_package import resolve_release_package_map

        doc = {
            "artifact_type": BUNDLE_FACTS_ARTIFACT_TYPE,
            "schema_version": 2,
            "variant_fingerprint": "default",
            "per_library_snapshots": {
                "liba.so": snapshot_to_dict(
                    _snap("liba.so", "1.0", [_fn("foo", "_Z3foov")])
                ),
            },
            "filesystem_aliases": {},
            "library_filenames": {},
            "manifest": {"provides": [{"symbol": "core_mul"}]},
        }
        pkg = tmp_path / "pkg"
        store = DirectoryObjectStore(pkg)
        manifest = import_bundle_facts(
            doc, store=store, max_known_schema_version=43, variant_id="v1"
        )
        write_project_manifest(pkg, manifest)

        read_back = read_embedded_instantiation_manifest(pkg)
        assert read_back is not None
        assert read_back.symbols == {"core_mul"}

        resolved = resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        sub_dir = next(iter(resolved.values()))
        sub_read_back = read_embedded_instantiation_manifest(sub_dir)
        assert sub_read_back is not None
        assert sub_read_back.symbols == {"core_mul"}


class TestDsoOnlyStoredFiltering:
    """Codex review, eighth round: `--dso-only` was only ever applied to
    the live-discovery branch of the release fan-out -- a stored
    non-ELF artifact (or, as tested here, a differently-platformed one)
    stayed in scope even when explicitly excluded."""

    def test_non_elf_stored_member_is_excluded(self, tmp_path: Path) -> None:
        from dataclasses import replace as dc_replace

        from abicheck.elf_metadata import ElfMetadata
        from abicheck.project_snapshot_store import (
            read_artifact_ref,
            read_manifest_summary,
        )
        from abicheck.workflows.release_package import (
            dso_only_package_map,
            resolve_release_package_map,
        )

        old_libs, _ = _old_new_libraries()
        # Give one library a non-ELF platform stamp; the other two default
        # to "elf" (import_v1's own fallback when no platform is stated).
        # Those two carry real (non-PIE) ElfMetadata -- dso_only_package_map
        # now also inspects ElfMetadata.is_pie, not just the artifact kind,
        # so a real shared-object snapshot must have that evidence recorded.
        old_libs = dict(old_libs)
        old_libs["liba.so"] = dc_replace(old_libs["liba.so"], elf=ElfMetadata())
        old_libs["libb.so"] = dc_replace(old_libs["libb.so"], elf=ElfMetadata())
        old_libs["libc.so"] = dc_replace(old_libs["libc.so"], platform="pe")

        pkg = tmp_path / "pkg"
        _write_package(pkg, old_libs, variant_id="v1")

        resolved = resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        assert len(resolved) == 3

        def _kind(sub_dir: Path) -> str:
            summary = read_manifest_summary(sub_dir)
            (artifact_id,) = summary.artifact_ids
            return read_artifact_ref(sub_dir, artifact_id).kind

        kinds = {key: _kind(sub_dir) for key, sub_dir in resolved.items()}
        assert sorted(kinds.values()) == ["elf", "elf", "pe"]
        pe_key = next(key for key, kind in kinds.items() if kind == "pe")

        filtered = dso_only_package_map(resolved)
        assert pe_key not in filtered
        assert set(filtered) == set(resolved) - {pe_key}

    def test_stored_pie_executable_is_excluded(self, tmp_path: Path) -> None:
        """Codex review, ninth round: `import_v1` derives `ArtifactRef.kind
        == "elf"` for a DSO and for a PIE executable alike -- checking
        `kind` alone still let a stored application binary through a
        `--dso-only` release comparison even though the live-directory path
        rejects the identical case via `package._is_elf_shared_object`."""
        from dataclasses import replace as dc_replace

        from abicheck.elf_metadata import ElfMetadata
        from abicheck.workflows.release_package import (
            dso_only_package_map,
            resolve_release_package_map,
        )

        libs = {
            "libcore.so": _snap("libcore.so", "1.0", [_fn("foo", "_Z3foov")]),
            "app": _snap("app", "1.0", []),
        }
        libs["libcore.so"] = dc_replace(libs["libcore.so"], elf=ElfMetadata())
        libs["app"] = dc_replace(libs["app"], elf=ElfMetadata(is_pie=True))

        pkg = tmp_path / "pkg"
        _write_package(pkg, libs, variant_id="v1")

        resolved = resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        assert len(resolved) == 2

        filtered = dso_only_package_map(resolved)
        assert set(filtered) == {"libcore.so"}


class TestBundleFactsOutPreservesStoredIdentity:
    """Codex review, eighth round: `capture_bundle_facts(library_paths=...)`
    probed a stored operand's *materialized sub-package directory* with
    filesystem-alias/resolved-basename logic meant for a real file, so a
    `--bundle-facts-out` capture of a stored release recorded the
    sub-package's own synthetic dirname as the library's "real" filename
    and no aliases at all, instead of the identity the package's own
    `ArtifactRef.native_identity` already carries."""

    def test_stored_directory_entry_uses_native_identity_not_dirname(
        self, tmp_path: Path
    ) -> None:
        from abicheck.workflows.release_package import resolve_release_package_map

        libs = {
            "libcore.so": _snap("libcore.so", "1.0", [_fn("foo", "_Z3foov")]),
        }
        pkg = tmp_path / "pkg"
        _write_package_with_filenames(
            pkg, libs, library_filenames={"libcore.so": "libcore.so.2.5.1"}
        )

        resolved = resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        (sub_dir,) = resolved.values()
        # The materialized sub-package directory's own basename must not
        # equal the real filename -- otherwise the assertion below could
        # pass by coincidence rather than proving the fix.
        assert sub_dir.name != "libcore.so.2.5.1"

        facts = capture_bundle_facts(libs, library_paths={"libcore.so": sub_dir})
        assert facts.library_filenames["libcore.so"] == "libcore.so.2.5.1"


class TestStoredIdentityFromVariantComposition:
    """Codex review, ninth round: `bundle._stored_library_identity()` only
    ever read a per-artifact `ArtifactRef.native_identity`
    (`bundle_facts_store.write_bundle_facts_package`'s own writer) --
    `storage.import_bundle_facts.import_bundle_facts`'s own writer instead
    records `library_filenames`/`filesystem_aliases` once, variant-wide, in
    the preserved `BUNDLE_COMPOSITION_SECTION_KIND` section (see
    `TestMaterializationPreservesBundleComposition` above), which was never
    consulted here -- so a stored package built that way still lost real
    filename/alias evidence during bundle analysis and `--bundle-facts-out`
    capture, even after that composition section itself was preserved."""

    def test_composition_filename_recovered_when_native_identity_lacks_it(
        self, tmp_path: Path
    ) -> None:
        from abicheck.bundle import stored_capture_identity
        from abicheck.serialization import snapshot_to_dict
        from abicheck.storage.import_bundle_facts import (
            BUNDLE_FACTS_ARTIFACT_TYPE,
            import_bundle_facts,
        )
        from abicheck.workflows.release_package import resolve_release_package_map

        doc = {
            "artifact_type": BUNDLE_FACTS_ARTIFACT_TYPE,
            "schema_version": 2,
            "variant_fingerprint": "default",
            "per_library_snapshots": {
                "liba.so": snapshot_to_dict(
                    _snap("liba.so", "1.0", [_fn("foo", "_Z3foov")])
                ),
            },
            "filesystem_aliases": {"liba.so": ["liba.so.1"]},
            "library_filenames": {"liba.so": "liba.so.1.2.3"},
            "manifest": None,
        }
        pkg = tmp_path / "pkg"
        store = DirectoryObjectStore(pkg)
        manifest = import_bundle_facts(
            doc, store=store, max_known_schema_version=43, variant_id="v1"
        )
        write_project_manifest(pkg, manifest)

        resolved = resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        (sub_dir,) = resolved.values()

        stored_name, stored_aliases = stored_capture_identity(sub_dir)
        assert stored_name == "liba.so.1.2.3"
        assert stored_aliases == ("liba.so.1",)
