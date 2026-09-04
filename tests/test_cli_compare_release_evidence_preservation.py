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

import pytest
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
            (variant,) = sub_manifest.variant_refs
            # The referenced digests, not the section count: `materialize_
            # release_variant_artifacts` also carries the shared variant-
            # level composition section into every single-artifact
            # sub-package (Codex review -- see that function's own
            # docstring), and a shared digest still materializes to one
            # object file, not one per referencing section.
            expected_digests = {ref.digest for ref in artifact.sections.values()}
            expected_digests |= {ref.digest for ref in variant.sections.values()}
            expected_digests |= {
                ref.digest for ref in sub_manifest.project_sections.values()
            }
            assert expected_digests
            assert len(_object_files(sub_dir)) == len(expected_digests)

    def test_shared_objects_are_hard_linked_not_re_copied(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: a variant-/project-level object is
        identical for every artifact in the variant, but each sub-package
        used to re-fetch it from the source package and re-write it under
        its own destination store -- an N-fold *physical* disk cost across
        many artifacts (e.g. 100 artifacts sharing a 100 MB section
        consuming ~10 GB of temporary disk). The second (and later)
        artifact's copy of a shared object must now share an inode with
        the first's, not merely have identical bytes."""
        from abicheck.project_snapshot_store import read_project_manifest
        from abicheck.workflows.release_package import resolve_release_package_map

        old_libs, _ = _old_new_libraries()
        pkg = tmp_path / "pkg"
        _write_package(pkg, old_libs, variant_id="v1")

        resolved = resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        assert len(resolved) == 3

        def _object_path_for_digest(sub_dir: Path, digest: str) -> Path | None:
            objects_dir = sub_dir / "objects"
            if not objects_dir.is_dir():
                return None
            for p in objects_dir.rglob("*"):
                if p.is_file() and digest.split(":", 1)[-1] in p.name:
                    return p
            return None

        # Every sub-package shares the identical variant-level composition
        # section (they were all cut from the same one variant).
        sub_dirs = list(resolved.values())
        (variant,) = read_project_manifest(sub_dirs[0]).variant_refs
        shared_digest = next(iter(variant.sections.values())).digest

        paths = [
            _object_path_for_digest(sub_dir, shared_digest) for sub_dir in sub_dirs
        ]
        assert all(p is not None for p in paths), paths
        inodes = {p.stat().st_ino for p in paths if p is not None}  # type: ignore[union-attr]
        assert len(inodes) == 1, (
            "expected every sub-package's copy of the shared variant-level "
            f"object to share one inode (hard-linked), got {len(inodes)} "
            f"distinct inodes across {len(paths)} sub-packages"
        )
        for p in paths:
            assert p is not None and p.stat().st_nlink >= len(sub_dirs)


class TestEmbeddedInstantiationManifest:
    """Codex review, eighth round: nothing in the ordinary
    `compare-release` bundle-analysis path ever consulted a stored
    package's own embedded `InstantiationManifest`
    (`write_bundle_facts_package`'s own composition section) unless the
    caller passed an explicit `--manifest` -- so a package's own captured
    manifest-drift contract silently went unenforced during a stored/
    stored or stored/live comparison, even after the composition-
    preservation fix made the section survive materialization."""

    def test_manifest_survives_materialization_and_is_read_back(
        self, tmp_path: Path
    ) -> None:
        from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry
        from abicheck.workflows.release_package import (
            read_embedded_manifest,
            resolve_release_package_map,
        )

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
        read_back = read_embedded_manifest(pkg)
        assert read_back is not None
        assert read_back.symbols == {"core_mul"}

        # ...and off any single-artifact sub-package materialized from it.
        resolved = resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        sub_dir = next(iter(resolved.values()))
        sub_read_back = read_embedded_manifest(sub_dir)
        assert sub_read_back is not None
        assert sub_read_back.symbols == {"core_mul"}

    def test_no_manifest_section_reads_back_none(self, tmp_path: Path) -> None:
        from abicheck.workflows.release_package import read_embedded_manifest

        old_libs, _ = _old_new_libraries()
        pkg = tmp_path / "pkg"
        _write_package(pkg, old_libs, variant_id="v1")
        assert read_embedded_manifest(pkg) is None

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
        bundle_findings = doc.get("bundle_findings") or []
        # CodeRabbit review: a bare non-empty check can pass on an unrelated
        # change in libb.so/libc.so -- assert the specific manifest-drift
        # finding this fixture actually promises and removes.
        matching = [
            f
            for f in bundle_findings
            if f.get("kind") == "bundle_manifest_instantiation_removed"
            and f.get("symbol") == "_Z3foov"
        ]
        assert matching, f"expected the foo manifest-drift finding, got: {out}"

    def test_composition_embedded_manifest_is_read_back(self, tmp_path: Path) -> None:
        """Codex review, ninth round: a package written by
        `storage.import_bundle_facts` (not `bundle_facts_store.
        write_bundle_facts_package`) has no project-level manifest
        section at all -- its own captured manifest lives only in the
        variant's `BUNDLE_COMPOSITION_SECTION_KIND` payload, which the
        earlier embedded-manifest fix never consulted."""
        from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
        from abicheck.storage.import_bundle_facts import (
            BUNDLE_FACTS_ARTIFACT_TYPE,
            import_bundle_facts,
        )
        from abicheck.workflows.release_package import (
            read_embedded_manifest,
            resolve_release_package_map,
        )

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
            doc, store=store, max_known_schema_version=SCHEMA_VERSION, variant_id="v1"
        )
        write_project_manifest(pkg, manifest)

        read_back = read_embedded_manifest(pkg)
        assert read_back is not None
        assert read_back.symbols == {"core_mul"}

        resolved = resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        sub_dir = next(iter(resolved.values()))
        sub_read_back = read_embedded_manifest(sub_dir)
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

        filtered, _excluded = dso_only_package_map(resolved)
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

        filtered, _excluded = dso_only_package_map(resolved)
        assert set(filtered) == {"libcore.so"}

    def test_stored_non_pie_executable_is_also_excluded(self, tmp_path: Path) -> None:
        """Codex review, tenth round: `ElfMetadata` carries no `e_type`, so
        a traditional non-PIE `ET_EXEC` (`is_pie=False`) previously passed
        `dso_only_package_map`'s `is_pie`-only check even though the live
        `_is_elf_shared_object` path rejects every non-`ET_DYN` file. Its
        own `PT_INTERP` (`ElfMetadata.interpreter`) is set, like any
        ordinary dynamically-linked executable, and its name doesn't look
        like a shared object -- the same signal the live filename fallback
        uses for this identical ambiguous case."""
        from dataclasses import replace as dc_replace

        from abicheck.elf_metadata import ElfMetadata
        from abicheck.workflows.release_package import (
            dso_only_package_map,
            resolve_release_package_map,
        )

        libs = {
            "libcore.so": _snap("libcore.so", "1.0", [_fn("foo", "_Z3foov")]),
            "myapp": _snap("myapp", "1.0", []),
        }
        libs["libcore.so"] = dc_replace(libs["libcore.so"], elf=ElfMetadata())
        libs["myapp"] = dc_replace(
            libs["myapp"],
            elf=ElfMetadata(interpreter="/lib64/ld-linux-x86-64.so.2", is_pie=False),
        )

        pkg = tmp_path / "pkg"
        _write_package(pkg, libs, variant_id="v1")

        resolved = resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        assert len(resolved) == 2

        filtered, _excluded = dso_only_package_map(resolved)
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
        from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
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
            doc, store=store, max_known_schema_version=SCHEMA_VERSION, variant_id="v1"
        )
        write_project_manifest(pkg, manifest)

        resolved = resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        (sub_dir,) = resolved.values()

        stored_name, stored_aliases, _ = stored_capture_identity(sub_dir)
        assert stored_name == "liba.so.1.2.3"
        assert stored_aliases == ("liba.so.1",)


class TestFilesystemBackedNames:
    """CodeRabbit review: `BundleSnapshot.filesystem_backed` is snapshot-
    wide, so `build_bundle_snapshot_mixed` passing `filesystem_backed=False`
    (required for its stored members -- see `TestStoredEntry...
    NeverProbedAgainstCwd` in the parent module) also denied every *live*
    member `_detect_soname_skew`'s own real-symlink-target resolution,
    purely because a stored member happened to also participate.
    `filesystem_backed_names` is the per-member override that fixes this."""

    def test_live_members_stay_resolvable_alongside_stored_ones(
        self, tmp_path: Path
    ) -> None:
        from abicheck.bundle import build_bundle_snapshot_from_metadata
        from abicheck.elf_metadata import ElfMetadata

        snap = build_bundle_snapshot_from_metadata(
            {
                "live.so": ElfMetadata(soname="live.so.1"),
                "stored.so": ElfMetadata(soname="stored.so.1"),
            },
            paths={
                "live.so": tmp_path / "live.so",
                "stored.so": tmp_path / "stored.so",
            },
            filesystem_backed=False,
            filesystem_backed_names=frozenset({"live.so"}),
        )
        assert snap.member_is_filesystem_backed("live.so") is True
        assert snap.member_is_filesystem_backed("stored.so") is False

    def test_none_falls_back_to_the_snapshot_wide_flag(self) -> None:
        """No `filesystem_backed_names` given -- every pre-existing caller's
        behavior is unchanged: every member follows the one flag."""
        from abicheck.bundle import build_bundle_snapshot_from_metadata
        from abicheck.elf_metadata import ElfMetadata

        snap = build_bundle_snapshot_from_metadata(
            {"a.so": ElfMetadata(soname="a.so.1")},
            filesystem_backed=True,
        )
        assert snap.member_is_filesystem_backed("a.so") is True
        assert snap.member_is_filesystem_backed("nonexistent") is True


class TestAliasNodeBudgetAggregation:
    """CodeRabbit review, security finding (CWE-400, Denial of Service):
    `bundle._stored_library_identity()` always passed `0` as
    `decode_native_identity_aliases`'s `nodes_so_far`, resetting the
    aggregate JSON-node budget for every artifact -- so many artifacts each
    individually under the per-array limit could still sum to an unbounded
    aggregate decode cost. The budget must now be threaded across calls,
    the same way `bundle_facts_store.read_bundle_facts_package`'s own
    `alias_nodes_so_far` already does."""

    def test_budget_accumulates_across_two_stored_artifacts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dataclasses import replace as dc_replace

        from abicheck import bundle
        from abicheck.elf_metadata import ElfMetadata
        from abicheck.workflows.release_package import resolve_release_package_map

        monkeypatch.setattr(
            "abicheck.storage.native_identity_aliases.DEFAULT_MAX_JSON_CONTAINER_NODES",
            20,
        )

        libs = {
            "liba.so": dc_replace(
                _snap("liba.so", "1.0", [_fn("foo", "_Z3foov")]),
                elf=ElfMetadata(soname="liba.so"),
            ),
            "libb.so": dc_replace(
                _snap("libb.so", "1.0", [_fn("bar", "_Z3barv")]),
                elf=ElfMetadata(soname="libb.so"),
            ),
        }
        facts = capture_bundle_facts(libs, variant_fingerprint="gcc13-avx2")
        # 15 aliases each -- 16 nodes per array (< 20 individually) but 32
        # combined (> 20).
        aliases_a = tuple(f"liba-alias-{i}.so" for i in range(15))
        aliases_b = tuple(f"libb-alias-{i}.so" for i in range(15))
        from abicheck.bundle_facts import BundleFacts

        facts = BundleFacts(
            variant_fingerprint=facts.variant_fingerprint,
            per_library_snapshots=facts.per_library_snapshots,
            manifest=facts.manifest,
            filesystem_aliases={"liba.so": aliases_a, "libb.so": aliases_b},
            library_filenames={"liba.so": "liba.so", "libb.so": "libb.so"},
        )
        pkg = tmp_path / "pkg"
        store = DirectoryObjectStore(pkg)
        manifest = write_bundle_facts_package(facts, store=store, variant_id="v1")
        write_project_manifest(pkg, manifest)

        resolved = resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        assert len(resolved) == 2

        nodes_so_far = 0
        decoded_alias_counts = []
        for sub_dir in resolved.values():
            _, aliases, nodes_so_far = bundle._stored_library_identity(
                sub_dir, nodes_so_far
            )
            decoded_alias_counts.append(len(aliases))

        # The first resolved must succeed (well under the 20-node budget on
        # its own); the second must have degraded to no aliases at all,
        # since 16 + 16 = 32 exceeds the 20-node aggregate budget -- proof
        # the budget was actually carried across the two calls, not reset.
        assert sorted(decoded_alias_counts) == [0, 15]


class TestStoredElfMetadataContainsMalformedInput:
    """CodeRabbit review: `_stored_elf_metadata()`'s own try/except only
    wrapped `read_legacy_snapshot_document` -- a malformed `schema_version`/
    `elf` field in an otherwise-readable document raised straight out of
    this function (and, uncaught, out of `build_bundle_snapshot_mixed`'s own
    per-member loop too), aborting the *entire* bundle analysis for one bad
    member instead of degrading just that member the way every other
    malformed-input case here already does."""

    def test_malformed_schema_version_degrades_to_none_not_a_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck import bundle

        monkeypatch.setattr(
            "abicheck.project_snapshot_legacy.read_legacy_snapshot_document",
            lambda path: {
                "elf": {"soname": "liba.so.1"},
                "schema_version": "not-an-int",
            },
        )
        assert bundle._stored_elf_metadata(tmp_path) is None

    def test_malformed_member_does_not_abort_the_whole_mixed_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck.bundle import build_bundle_snapshot_mixed

        monkeypatch.setattr(
            "abicheck.project_snapshot_legacy.read_legacy_snapshot_document",
            lambda path: {
                "elf": {"soname": "bad.so.1"},
                "schema_version": "not-an-int",
            },
        )
        bad_dir = tmp_path / "bad"
        bad_dir.mkdir()
        # Must not raise -- the malformed member is simply dropped, matching
        # every other "can't resolve this one" case in build_bundle_snapshot
        # _mixed (an unparseable document, no ELF metadata present, ...).
        snap = build_bundle_snapshot_mixed({"bad.so": bad_dir})
        assert "bad.so" not in snap.metadata


class TestReleasePackageResolutionCatchesKeyError:
    """CodeRabbit review: `DirectoryObjectStore.get()` raises `KeyError`
    (not `OSError`) for an object entirely absent from `objects/` -- a
    truncated or partially-copied package directory, an ordinary release
    operand. `_resolve_release_package_side`'s own exception tuple named
    only `(ValueError, OSError, SnapshotError)`, so this case escaped as an
    unhandled `KeyError` (an abicheck crash) instead of the intended
    `click.UsageError` (exit 64)."""

    def test_missing_object_becomes_a_usage_error(self, tmp_path: Path) -> None:
        import click

        from abicheck.cli_compare_release_matrix import _resolve_release_package_side

        old_libs, _ = _old_new_libraries()
        pkg = tmp_path / "pkg"
        _write_package(pkg, old_libs, variant_id="v1")

        # Delete one object -- simulates a truncated/partially-copied
        # package directory.
        object_files = [p for p in (pkg / "objects").rglob("*") if p.is_file()]
        assert object_files
        object_files[0].unlink()

        counter = 0

        def make_temp_dir(prefix: str) -> Path:
            nonlocal counter
            counter += 1
            dest = tmp_path / f"{prefix}{counter}"
            dest.mkdir()
            return dest

        with pytest.raises(click.UsageError):
            _resolve_release_package_side(pkg, None, make_temp_dir)


class TestEmbeddedManifestForEmptyVariant:
    """Codex review: a stored package's *selected variant* can validly
    carry zero artifacts (a real `BundleFacts` with an empty
    `per_library_snapshots`) and still promise symbols via its own embedded
    manifest -- `_run_bundle_analysis`'s fallback search previously only
    ever looked inside `old_map`/`new_map`'s own member directories, which
    a zero-artifact variant has none of, so the manifest was never
    consulted and a promise the release entirely fails to keep produced no
    finding at all."""

    def test_empty_variant_manifest_still_gates_the_new_side(
        self, tmp_path: Path
    ) -> None:
        from abicheck.bundle_facts import BundleFacts
        from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry

        manifest = InstantiationManifest(entries=(ManifestEntry(symbol="core_mul"),))
        facts = BundleFacts(
            variant_fingerprint="gcc13-avx2",
            per_library_snapshots={},
            manifest=manifest,
            filesystem_aliases={},
            library_filenames={},
        )
        old_pkg = tmp_path / "old_pkg"
        store = DirectoryObjectStore(old_pkg)
        pkg_manifest = write_bundle_facts_package(facts, store=store, variant_id="v1")
        write_project_manifest(old_pkg, pkg_manifest)

        _, new_libs = _old_new_libraries()
        new_pkg = tmp_path / "new_pkg"
        _write_directory(new_pkg, new_libs)

        ec, out = _invoke(
            "compare", str(old_pkg), str(new_pkg), "--format", "json", "-j", "1"
        )
        doc = json.loads(out)
        bundle_findings = doc.get("bundle_findings") or []
        matching = [
            f
            for f in bundle_findings
            if f.get("kind") == "bundle_manifest_instantiation_removed"
            and f.get("symbol") == "core_mul"
        ]
        assert matching, (
            f"expected the core_mul manifest-drift finding even though the "
            f"old side's selected variant has zero artifacts, got: {out}"
        )


class TestBundleFactsOutCarriesEmbeddedManifest:
    """Codex review, fresh evidence: the embedded manifest resolved for
    bundle *analysis* was never threaded to `--bundle-facts-out`'s own
    writer, which still received the raw (`None`, since no explicit
    `--manifest` was given) `manifest_path` -- so a captured baseline
    silently dropped the manifest-drift contract the live comparison
    itself was enforcing in the same run."""

    def test_bundle_facts_out_captures_the_stored_sides_embedded_manifest(
        self, tmp_path: Path
    ) -> None:
        from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry
        from abicheck.serialization import load_bundle_facts

        old_libs, _ = _old_new_libraries()
        manifest = InstantiationManifest(entries=(ManifestEntry(symbol="core_mul"),))
        facts = _with_filenames(
            capture_bundle_facts(
                old_libs, manifest=manifest, variant_fingerprint="gcc13-avx2"
            )
        )
        old_pkg = tmp_path / "old_pkg"
        store = DirectoryObjectStore(old_pkg)
        pkg_manifest = write_bundle_facts_package(facts, store=store, variant_id="v1")
        write_project_manifest(old_pkg, pkg_manifest)

        _, new_libs = _old_new_libraries()
        new_pkg = tmp_path / "new_pkg"
        _write_directory(new_pkg, new_libs)

        out_path = tmp_path / "captured.json"
        ec, out = _invoke(
            "compare",
            str(old_pkg),
            str(new_pkg),
            "--format",
            "json",
            "-j",
            "1",
            "--bundle-facts-out",
            str(out_path),
        )
        assert out_path.exists(), f"--bundle-facts-out was not written: {out}"
        captured = load_bundle_facts(out_path)
        assert captured.manifest is not None, (
            "the stored old side's embedded manifest was not carried into "
            "the captured --bundle-facts-out baseline"
        )
        assert captured.manifest.symbols == {"core_mul"}


class TestMalformedEmbeddedManifestRaises:
    """CodeRabbit review, security finding: a declared-but-corrupted
    embedded manifest section previously decoded to `None`, identical to
    "no manifest was ever recorded" -- silently disabling the manifest-
    drift check a corrupted/hand-edited package could otherwise still
    declare, instead of surfacing as a usage error."""

    def test_corrupted_project_level_manifest_raises_not_none(
        self, tmp_path: Path
    ) -> None:
        from abicheck.bundle_facts import BundleFacts
        from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry
        from abicheck.workflows.release_package import read_embedded_manifest

        manifest = InstantiationManifest(entries=(ManifestEntry(symbol="core_mul"),))
        facts = BundleFacts(
            variant_fingerprint="gcc13-avx2",
            per_library_snapshots={},
            manifest=manifest,
            filesystem_aliases={},
            library_filenames={},
        )
        pkg = tmp_path / "pkg"
        store = DirectoryObjectStore(pkg)
        pkg_manifest = write_bundle_facts_package(facts, store=store, variant_id="v1")
        write_project_manifest(pkg, pkg_manifest)

        # This zero-artifact package's own manifest is its only stored
        # object -- corrupt it in place so DirectoryObjectStore.get()'s own
        # digest check fails, simulating a corrupted/hand-edited package.
        object_files = [p for p in (pkg / "objects").rglob("*") if p.is_file()]
        assert object_files
        for object_file in object_files:
            raw = bytearray(object_file.read_bytes())
            raw[-1] ^= 0xFF
            object_file.write_bytes(bytes(raw))

        with pytest.raises(Exception):  # noqa: B017 -- any decode failure, by design
            read_embedded_manifest(pkg)


class TestBothSidesEmptyVariantsStillEnforceManifests:
    """Codex review, fresh evidence: `_run_bundle_analysis` returned `None`
    immediately whenever *both* `old_map`/`new_map` were empty, before ever
    resolving a manifest -- two valid empty `BundleFacts` packages (both
    selected variants carry zero artifacts) could each declare a
    required-symbol manifest and still compare as `NO_CHANGE`/exit 0."""

    def test_manifest_drift_is_reported_even_when_both_sides_are_empty(
        self, tmp_path: Path
    ) -> None:
        from abicheck.bundle_facts import BundleFacts
        from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry

        manifest = InstantiationManifest(entries=(ManifestEntry(symbol="core_mul"),))
        facts = BundleFacts(
            variant_fingerprint="gcc13-avx2",
            per_library_snapshots={},
            manifest=manifest,
            filesystem_aliases={},
            library_filenames={},
        )
        old_pkg = tmp_path / "old_pkg"
        store = DirectoryObjectStore(old_pkg)
        pkg_manifest = write_bundle_facts_package(facts, store=store, variant_id="v1")
        write_project_manifest(old_pkg, pkg_manifest)

        empty_facts = BundleFacts(
            variant_fingerprint="gcc13-avx2",
            per_library_snapshots={},
            manifest=None,
            filesystem_aliases={},
            library_filenames={},
        )
        new_pkg = tmp_path / "new_pkg"
        new_store = DirectoryObjectStore(new_pkg)
        new_manifest = write_bundle_facts_package(
            empty_facts, store=new_store, variant_id="v1"
        )
        write_project_manifest(new_pkg, new_manifest)

        ec, out = _invoke(
            "compare", str(old_pkg), str(new_pkg), "--format", "json", "-j", "1"
        )
        doc = json.loads(out)
        bundle_findings = doc.get("bundle_findings") or []
        matching = [
            f
            for f in bundle_findings
            if f.get("kind") == "bundle_manifest_instantiation_removed"
            and f.get("symbol") == "core_mul"
        ]
        assert matching, (
            "expected the core_mul manifest-drift finding even though both "
            f"selected variants have zero artifacts, got: {out}"
        )


class TestMismatchedManifestRefKindRaises:
    """Codex review, fresh evidence: a variant's own `sections[
    BUNDLE_COMPOSITION_SECTION_KIND]` `ObjectRef.kind` is a caller-
    controlled label, not verified by `VariantRef` construction -- a
    corrupted/hand-edited package could name an `ObjectRef` of a
    *different* kind there. Silently treating that as "no composition
    declared" would let a corrupted package disable its required-symbol
    check instead of surfacing as a usage error."""

    def test_mismatched_kind_raises_not_none(self, tmp_path: Path) -> None:
        from abicheck.bundle_facts import BundleFacts
        from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry
        from abicheck.workflows.release_package import read_embedded_manifest

        manifest = InstantiationManifest(entries=(ManifestEntry(symbol="core_mul"),))
        facts = BundleFacts(
            variant_fingerprint="gcc13-avx2",
            per_library_snapshots={},
            manifest=manifest,
            filesystem_aliases={},
            library_filenames={},
        )
        pkg = tmp_path / "pkg"
        store = DirectoryObjectStore(pkg)
        pkg_manifest = write_bundle_facts_package(facts, store=store, variant_id="v1")
        write_project_manifest(pkg, pkg_manifest)

        variant_ref_json = pkg / "refs" / "variants" / "v1.json"
        doc = json.loads(variant_ref_json.read_text())
        doc["sections"]["bundle_composition"]["kind"] = "something_else"
        variant_ref_json.write_text(json.dumps(doc))

        with pytest.raises(ValueError, match="kind"):
            read_embedded_manifest(pkg, "v1")
