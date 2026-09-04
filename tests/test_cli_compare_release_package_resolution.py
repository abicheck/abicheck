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

"""ADR-062 A1.7 stored-release comparison -- release-package resolution
regressions (Codex review, tenth+ rounds).

Split out of `test_cli_compare_release_evidence_preservation.py` once that
file crossed the 1200-line test-file cap: this module covers
`workflows.release_package.resolve_release_package_map`'s own materialization/
renaming/matching-key resolution, `workflows.release_package.
read_embedded_manifest`'s variant selection, and `--bundle-facts-out`'s own
manifest attribution -- rather than the evidence-
preservation topics the parent module covers. Shares fixtures/helpers with
the grandparent module via a plain sibling import (this package has no
`__init__.py`; see `tests/test_entity_id_carrier.py` for the same pattern).
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
    capture_bundle_facts,
    write_bundle_facts_package,
    write_project_manifest,
)


class TestMaterializationRenameCollision:
    """Codex review: renaming each raw `artifact_id`-named sub-package
    directly to its own display name can target a *different* artifact's
    still-unrenamed raw directory. With `storage.import_bundle_facts`'s
    own `resolve_ref_ids`-based writer (an already-safe/canonical bundle
    key becomes its own literal `artifact_id`), a versioned library name
    `afoo.so.1.2` resolves to release-matching key `afoo.so` (the version
    suffix stripped) and so a display dirname of `afoo.so-afoo.so.1.2` --
    which is a second, real sibling artifact's own literal, unrenamed raw
    `artifact_id` directory name, if that library happens to be named
    exactly that."""

    def test_display_rename_never_collides_with_a_sibling_raw_directory(
        self, tmp_path: Path
    ) -> None:
        from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
        from abicheck.storage.import_bundle_facts import (
            BUNDLE_FACTS_ARTIFACT_TYPE,
            import_bundle_facts,
        )
        from abicheck.workflows.release_package import resolve_release_package_map

        colliding_key = "afoo.so-afoo.so.1.2"
        doc = {
            "artifact_type": BUNDLE_FACTS_ARTIFACT_TYPE,
            "schema_version": 2,
            "variant_fingerprint": "default",
            "per_library_snapshots": {
                "afoo.so.1.2": snapshot_to_dict(
                    _snap("afoo.so.1.2", "1.0", [_fn("foo", "_Z3foov")])
                ),
                colliding_key: snapshot_to_dict(_snap(colliding_key, "1.0", [])),
            },
            "filesystem_aliases": {},
            "library_filenames": {},
            "manifest": None,
        }
        pkg = tmp_path / "pkg"
        store = DirectoryObjectStore(pkg)
        manifest = import_bundle_facts(
            doc, store=store, max_known_schema_version=SCHEMA_VERSION, variant_id="v1"
        )
        write_project_manifest(pkg, manifest)

        # Must not raise ENOTEMPTY/FileExistsError.
        resolved = resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        assert set(resolved) == {"afoo.so", "afoo.so-afoo.so"}
        for sub_dir in resolved.values():
            assert sub_dir.is_dir()

    def test_display_rename_is_order_independent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`VariantRef.artifact_ids` is always lexicographically sorted
        (`ArtifactRef.__post_init__`), which happens to protect the
        version-suffix-stripping collision above under normal materialize
        order -- but `resolve_release_package_map`'s own rename logic must
        not silently *depend* on that ordering accident to stay correct.
        Forces the adversarial order directly (the collision "source"
        processed before its "target") to prove the two-phase rename is
        genuinely order-independent, not order-lucky."""
        import abicheck.workflows.release_package as release_package_module
        from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
        from abicheck.storage.import_bundle_facts import (
            BUNDLE_FACTS_ARTIFACT_TYPE,
            import_bundle_facts,
        )

        colliding_key = "afoo.so-afoo.so.1.2"
        doc = {
            "artifact_type": BUNDLE_FACTS_ARTIFACT_TYPE,
            "schema_version": 2,
            "variant_fingerprint": "default",
            "per_library_snapshots": {
                "afoo.so.1.2": snapshot_to_dict(
                    _snap("afoo.so.1.2", "1.0", [_fn("foo", "_Z3foov")])
                ),
                colliding_key: snapshot_to_dict(_snap(colliding_key, "1.0", [])),
            },
            "filesystem_aliases": {},
            "library_filenames": {},
            "manifest": None,
        }
        pkg = tmp_path / "pkg"
        store = DirectoryObjectStore(pkg)
        manifest = import_bundle_facts(
            doc, store=store, max_known_schema_version=SCHEMA_VERSION, variant_id="v1"
        )
        write_project_manifest(pkg, manifest)

        real_materialize = release_package_module.materialize_release_variant_artifacts

        def _reversed_order_materialize(*args: object, **kwargs: object) -> dict:
            by_artifact_id = real_materialize(*args, **kwargs)  # type: ignore[arg-type]
            # "afoo.so.1.2" (the collision source) forced first.
            return dict(reversed(list(by_artifact_id.items())))

        monkeypatch.setattr(
            release_package_module,
            "materialize_release_variant_artifacts",
            _reversed_order_materialize,
        )

        resolved = release_package_module.resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        assert set(resolved) == {"afoo.so", "afoo.so-afoo.so"}
        for sub_dir in resolved.values():
            assert sub_dir.is_dir()


class TestBundleFactsOutDoesNotDuplicateStoredLibraries:
    """Codex review: `write_bundle_facts_out`'s ``basename_to_key`` lookup
    matched a diff's ``Path(diff.library).name`` against ``old_map``'s
    *values* -- for a stored operand those are materialized sub-package
    directories, not the real versioned filename a snapshot reports. The
    lookup missed, so the snapshot was captured once under its own
    unmatched name, and again by the "stranded library" fallback under its
    real canonical key -- a captured baseline with the library counted
    twice."""

    def test_versioned_stored_library_is_captured_exactly_once(
        self, tmp_path: Path
    ) -> None:
        from abicheck.serialization import (
            SCHEMA_VERSION,
            load_bundle_facts,
            snapshot_to_dict,
        )
        from abicheck.storage.import_bundle_facts import (
            BUNDLE_FACTS_ARTIFACT_TYPE,
            import_bundle_facts,
        )

        versioned_name = "liba.so.1.2.3"
        old_snapshot = _snap(versioned_name, "1.0", [_fn("foo", "_Z3foov")])
        doc = {
            "artifact_type": BUNDLE_FACTS_ARTIFACT_TYPE,
            "schema_version": 2,
            "variant_fingerprint": "default",
            "per_library_snapshots": {versioned_name: snapshot_to_dict(old_snapshot)},
            "filesystem_aliases": {},
            "library_filenames": {},
            "manifest": None,
        }
        old_pkg = tmp_path / "old_pkg"
        store = DirectoryObjectStore(old_pkg)
        manifest = import_bundle_facts(
            doc, store=store, max_known_schema_version=SCHEMA_VERSION, variant_id="v1"
        )
        write_project_manifest(old_pkg, manifest)

        # Live new side, same versioned filename, one function removed --
        # a genuine diff pair so the affected lookup is actually exercised.
        new_pkg = tmp_path / "new_pkg"
        _write_directory(new_pkg, {versioned_name: _snap(versioned_name, "2.0", [])})

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
        assert len(captured.per_library_snapshots) == 1, (
            "the versioned stored library was captured more than once in "
            f"the --bundle-facts-out baseline: {sorted(captured.per_library_snapshots)}"
        )


class TestStagingNamespaceCollision:
    """Codex review: the two-phase rename's staging name
    (`.resolving-<artifact_id>`) isn't guaranteed disjoint from a real
    artifact_id -- a leading dot is legal, so an artifact literally named
    ``.resolving-a`` collides with artifact ``a``'s own staging name.
    Sorted `VariantRef.artifact_ids` happens to rename ``.resolving-a``
    away first, so this forces the adversarial order directly (as
    `TestMaterializationRenameCollision`'s sibling test does)."""

    def test_staging_name_never_collides_with_a_literal_artifact_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.workflows.release_package as release_package_module
        from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
        from abicheck.storage.import_bundle_facts import (
            BUNDLE_FACTS_ARTIFACT_TYPE,
            import_bundle_facts,
        )

        colliding_artifact_id = ".resolving-a"
        doc = {
            "artifact_type": BUNDLE_FACTS_ARTIFACT_TYPE,
            "schema_version": 2,
            "variant_fingerprint": "default",
            "per_library_snapshots": {
                "a": snapshot_to_dict(_snap("a", "1.0", [_fn("foo", "_Z3foov")])),
                colliding_artifact_id: snapshot_to_dict(
                    _snap(colliding_artifact_id, "1.0", [])
                ),
            },
            "filesystem_aliases": {},
            "library_filenames": {},
            "manifest": None,
        }
        pkg = tmp_path / "pkg"
        store = DirectoryObjectStore(pkg)
        manifest = import_bundle_facts(
            doc, store=store, max_known_schema_version=SCHEMA_VERSION, variant_id="v1"
        )
        write_project_manifest(pkg, manifest)

        real_materialize = release_package_module.materialize_release_variant_artifacts

        def _artifact_a_first_materialize(*args: object, **kwargs: object) -> dict:
            by_artifact_id = real_materialize(*args, **kwargs)  # type: ignore[arg-type]
            # "a" (the collision source, whose staging name is
            # ".resolving-a") forced first, before the real
            # ".resolving-a" artifact directory is renamed away.
            return dict(sorted(by_artifact_id.items(), key=lambda kv: kv[0] != "a"))

        monkeypatch.setattr(
            release_package_module,
            "materialize_release_variant_artifacts",
            _artifact_a_first_materialize,
        )

        # Must not raise ENOTEMPTY/FileExistsError.
        resolved = release_package_module.resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        assert set(resolved) == {"a", colliding_artifact_id}
        for sub_dir in resolved.values():
            assert sub_dir.is_dir()
        # The staging container itself must not survive as a stray
        # sibling of the final display directories.
        leftover = {p.name for p in (tmp_path / "resolved").iterdir()}
        assert leftover == {sub_dir.name for sub_dir in resolved.values()}


class TestReleaseMatchKeyUsesRealStoredFilename:
    """Codex review: `import_bundle_facts` stamps only the bundle key onto
    each artifact's `native_identity`, never the real on-disk filename --
    that lives once, variant-wide, in the composition's `library_filenames`
    mapping. `_release_match_key` used the bundle key verbatim, so a stored
    side could never match an equivalent live directory operand whenever
    the two differ (e.g. bundle key "provider" for on-disk `libfoo.so.1`)."""

    def test_stored_release_key_matches_the_live_operands_real_filename(
        self, tmp_path: Path
    ) -> None:
        from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
        from abicheck.storage.import_bundle_facts import (
            BUNDLE_FACTS_ARTIFACT_TYPE,
            import_bundle_facts,
        )
        from abicheck.workflows.release_package import resolve_release_package_map

        bundle_key = "provider"
        real_filename = "libfoo.so.1"
        doc = {
            "artifact_type": BUNDLE_FACTS_ARTIFACT_TYPE,
            "schema_version": 2,
            "variant_fingerprint": "default",
            "per_library_snapshots": {
                bundle_key: snapshot_to_dict(
                    _snap(bundle_key, "1.0", [_fn("foo", "_Z3foov")])
                ),
            },
            "filesystem_aliases": {},
            "library_filenames": {bundle_key: real_filename},
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
        # The key must derive from the real filename ("libfoo.so" --
        # version stripped), not the opaque bundle key "provider" verbatim.
        assert set(resolved) == {"libfoo.so"}, (
            f"expected key from {real_filename!r}, not bundle key "
            f"{bundle_key!r}: got {sorted(resolved)}"
        )


class TestEmbeddedManifestReadsTheSelectedVariant:
    """Codex review: a multi-variant package can carry a different manifest
    per variant, but `read_embedded_manifest` always returned the *first*
    variant's manifest in package order regardless of which variant was
    actually selected -- a later variant's own required-symbol removal
    could go unenforced."""

    def test_variant_id_selects_that_variants_own_manifest(
        self, tmp_path: Path
    ) -> None:
        from abicheck.project_snapshot_store import write_project_manifest
        from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
        from abicheck.storage.import_bundle_facts import (
            BUNDLE_FACTS_ARTIFACT_TYPE,
            import_bundle_facts,
        )
        from abicheck.storage.package import PackageManifest
        from abicheck.workflows.release_package import read_embedded_manifest

        def _doc(library: str, fingerprint: str, symbol: str) -> dict[str, object]:
            return {
                "artifact_type": BUNDLE_FACTS_ARTIFACT_TYPE,
                "schema_version": 2,
                "variant_fingerprint": fingerprint,
                "per_library_snapshots": {
                    library: snapshot_to_dict(
                        _snap(library, "1.0", [_fn("foo", "_Z3foov")])
                    )
                },
                "filesystem_aliases": {},
                "library_filenames": {},
                "manifest": {"provides": [{"symbol": symbol}]},
            }

        pkg = tmp_path / "pkg"
        store = DirectoryObjectStore(pkg)
        manifest_v1 = import_bundle_facts(
            _doc("lib1.so", "v1fp", "sym1"),
            store=store,
            max_known_schema_version=SCHEMA_VERSION,
            variant_id="v1",
        )
        manifest_v2 = import_bundle_facts(
            _doc("lib2.so", "v2fp", "sym2"),
            store=store,
            max_known_schema_version=SCHEMA_VERSION,
            variant_id="v2",
        )
        # Combine both single-variant PackageManifests into one real
        # multi-variant package -- exactly the shape a package with two
        # captured variants (e.g. two build configurations) has.
        combined = PackageManifest(
            versions=manifest_v1.versions,
            variant_refs=manifest_v1.variant_refs + manifest_v2.variant_refs,
            artifact_refs=manifest_v1.artifact_refs + manifest_v2.artifact_refs,
        )
        write_project_manifest(pkg, combined)

        read_v1 = read_embedded_manifest(pkg, variant_id="v1")
        read_v2 = read_embedded_manifest(pkg, variant_id="v2")
        assert read_v1 is not None and read_v1.symbols == {"sym1"}
        assert read_v2 is not None and read_v2.symbols == {"sym2"}, (
            "selecting variant v2 must read v2's own manifest, not v1's "
            f"(package order's first match): got {read_v2.symbols if read_v2 else None}"
        )


class TestMalformedStoredAliasesRaise:
    """Codex review: `_stored_library_identity()` caught every alias-decode
    exception, degrading to no aliases indistinguishable from a producer
    that recorded none -- silently losing a no-`DT_SONAME` provider's
    resolution edge. Only the accepted resource-limit degrade
    (`JsonContainerBudgetExceeded`/`JsonNestingTooDeepError`, see
    `TestAliasNodeBudgetAggregation`) should still degrade silently.

    Since Track 1's writer reconciliation, `write_bundle_facts_package` no
    longer stamps `library_filename`/`filesystem_aliases` on the artifact's
    own `native_identity` at all -- every stored package's evidence now
    flows through `bundle._composition_library_identity`'s own preserved
    `BUNDLE_COMPOSITION_SECTION_KIND` section instead, so the malformed
    input is injected there (`bundle_composition_from_dto` raising) rather
    than through the now-unreachable `native_identity_aliases.decode_
    native_identity_aliases` this test used to monkeypatch."""

    def test_malformed_aliases_array_raises_not_silently_degrades(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dataclasses import replace as dc_replace

        from abicheck import bundle
        from abicheck.bundle_facts import BundleFacts
        from abicheck.elf_metadata import ElfMetadata
        from abicheck.errors import SnapshotError
        from abicheck.workflows.release_package import resolve_release_package_map

        libs = {
            "liba.so": dc_replace(
                _snap("liba.so", "1.0", [_fn("foo", "_Z3foov")]),
                elf=ElfMetadata(soname="liba.so"),
            ),
        }
        facts = capture_bundle_facts(libs, variant_fingerprint="gcc13-avx2")
        facts = BundleFacts(
            variant_fingerprint=facts.variant_fingerprint,
            per_library_snapshots=facts.per_library_snapshots,
            manifest=facts.manifest,
            filesystem_aliases={"liba.so": ("liba-alias.so",)},
            library_filenames={"liba.so": "liba.so"},
        )
        pkg = tmp_path / "pkg"
        store = DirectoryObjectStore(pkg)
        manifest = write_bundle_facts_package(facts, store=store, variant_id="v1")
        write_project_manifest(pkg, manifest)
        resolved = resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        (sub_dir,) = resolved.values()

        def _raise_malformed(raw: object) -> object:
            raise ValueError("not a well-formed bundle_composition section")

        monkeypatch.setattr(
            "abicheck.storage.dto.bundle_composition_from_dto", _raise_malformed
        )
        with pytest.raises(SnapshotError):
            bundle._stored_library_identity(sub_dir, 0)


class TestMalformedCompositionShapeRaises:
    """CodeRabbit review, fresh evidence: `bundle_composition_from_dto`
    only asserts the payload is a dict -- it does not validate that
    `library_filenames`/`filesystem_aliases` are themselves mappings, or
    that a `filesystem_aliases[library_name]` value is a sequence rather
    than a string (which `tuple(...)` would otherwise silently split into
    per-character aliases). Either shape must raise `SnapshotError`, the
    same declared-but-corrupted treatment `TestMalformedStoredAliasesRaise`
    already covers for a `bundle_composition_from_dto` failure outright."""

    def _resolved_sub_dir(self, tmp_path: Path) -> Path:
        from dataclasses import replace as dc_replace

        from abicheck.elf_metadata import ElfMetadata
        from abicheck.workflows.release_package import resolve_release_package_map

        libs = {
            "liba.so": dc_replace(
                _snap("liba.so", "1.0", [_fn("foo", "_Z3foov")]),
                elf=ElfMetadata(soname="liba.so"),
            ),
        }
        facts = capture_bundle_facts(libs, variant_fingerprint="gcc13-avx2")
        pkg = tmp_path / "pkg"
        store = DirectoryObjectStore(pkg)
        manifest = write_bundle_facts_package(facts, store=store, variant_id="v1")
        write_project_manifest(pkg, manifest)
        resolved = resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        (sub_dir,) = resolved.values()
        return sub_dir

    def test_non_mapping_library_filenames_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck import bundle
        from abicheck.errors import SnapshotError

        sub_dir = self._resolved_sub_dir(tmp_path)

        def _bad_shape(raw: object) -> dict[str, object]:
            return {"library_filenames": ["not", "a", "mapping"], "filesystem_aliases": {}}

        monkeypatch.setattr(
            "abicheck.storage.dto.bundle_composition_from_dto", _bad_shape
        )
        with pytest.raises(SnapshotError):
            bundle._stored_library_identity(sub_dir, 0)

    def test_string_filesystem_aliases_value_raises_not_splits_chars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck import bundle
        from abicheck.errors import SnapshotError

        sub_dir = self._resolved_sub_dir(tmp_path)

        def _bad_shape(raw: object) -> dict[str, object]:
            return {
                "library_filenames": {"liba.so": "liba.so"},
                "filesystem_aliases": {"liba.so": "liba-alias.so"},
            }

        monkeypatch.setattr(
            "abicheck.storage.dto.bundle_composition_from_dto", _bad_shape
        )
        with pytest.raises(SnapshotError):
            bundle._stored_library_identity(sub_dir, 0)


class TestWriteBundleFactsOutUsesMatchedReleaseKey:
    """Codex review, fresh evidence: a stored snapshot's logical library
    label (e.g. ``"provider"``) can differ from its real filename's own
    canonical form. Re-deriving the persisted key from ``DiffResult.
    library`` (an earlier revision of ``write_bundle_facts_out``) matches
    ``old_map`` under the wrong key, then the stranded-library loop inserts
    the same snapshot a second time under the real key -- two logical
    libraries for what is really one. Each ``diff_pairs`` entry must carry
    its own matched key directly instead."""

    def test_no_duplicate_under_a_re_derived_key(self, tmp_path: Path) -> None:
        from abicheck.checker_policy import Verdict
        from abicheck.checker_types import DiffResult
        from abicheck.cli_compare_release_helpers import write_bundle_facts_out
        from abicheck.elf_metadata import ElfMetadata
        from abicheck.model import AbiSnapshot
        from abicheck.serialization import load_bundle_facts

        real_path = tmp_path / "libfoo.so.1.2"
        real_path.write_bytes(b"")
        # A logical label unrelated to the real filename's canonical form.
        old_map = {"provider": real_path}
        old_snapshot = AbiSnapshot(
            library="libfoo.so.1.2",
            version="old",
            elf=ElfMetadata(soname="libfoo.so", symbols=[]),
        )
        diff = DiffResult(
            old_version="old",
            new_version="new",
            library="libfoo.so.1.2",
            changes=[],
            verdict=Verdict.COMPATIBLE,
        )
        diff_pairs = [("provider", diff, old_snapshot)]

        out = tmp_path / "old.bundlefacts.json"

        def resolve_stranded_library(p: Path) -> AbiSnapshot:
            raise AssertionError("provider is already matched; must not strand")

        write_bundle_facts_out(
            out,
            diff_pairs,
            None,
            old_map,
            resolve_stranded_library=resolve_stranded_library,
        )

        loaded = load_bundle_facts(out)
        assert set(loaded.per_library_snapshots) == {"provider"}, (
            "expected exactly one logical library keyed by the matched "
            f"release key, got {set(loaded.per_library_snapshots)}"
        )


class TestBundleFactsOutNeverAttributesNewsManifestToOld:
    """Codex review: `--bundle-facts-out` captures OLD's own baseline, but
    its shared manifest resolver searched *both* sides -- if OLD had no
    manifest and NEW (a stored package) did, NEW's manifest was silently
    attributed to the captured OLD baseline."""

    def test_new_sides_manifest_is_not_captured_for_old(self, tmp_path: Path) -> None:
        from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry
        from abicheck.serialization import load_bundle_facts

        old_libs, new_libs = _old_new_libraries()
        old_pkg = tmp_path / "old_pkg"
        _write_directory(old_pkg, old_libs)  # live OLD: no manifest at all.

        manifest = InstantiationManifest(entries=(ManifestEntry(symbol="core_mul"),))
        facts = _with_filenames(
            capture_bundle_facts(
                new_libs, manifest=manifest, variant_fingerprint="gcc13-avx2"
            )
        )
        new_pkg = tmp_path / "new_pkg"
        store = DirectoryObjectStore(new_pkg)
        pkg_manifest = write_bundle_facts_package(facts, store=store, variant_id="v1")
        write_project_manifest(new_pkg, pkg_manifest)

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
        assert captured.manifest is None, (
            "NEW's own embedded manifest was attributed to the captured "
            f"OLD baseline: {captured.manifest}"
        )


class TestReleaseMatchKeyLooksUpEmptyLibraryName:
    """Codex review: `import_bundle_facts` explicitly accepts and
    round-trips an empty-string `per_library_snapshots` key, but
    `_release_match_key`'s own `library_name and library_filenames` guard
    used truthiness -- an empty-string `library_name` failed that check, so
    `library_filenames[""]`'s real filename was never looked up and the
    artifact was keyed by its opaque artifact_id instead."""

    def test_empty_bundle_key_still_resolves_its_real_filename(
        self, tmp_path: Path
    ) -> None:
        from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
        from abicheck.storage.import_bundle_facts import (
            BUNDLE_FACTS_ARTIFACT_TYPE,
            import_bundle_facts,
        )
        from abicheck.workflows.release_package import resolve_release_package_map

        bundle_key = ""
        real_filename = "libfoo.so.1"
        doc = {
            "artifact_type": BUNDLE_FACTS_ARTIFACT_TYPE,
            "schema_version": 2,
            "variant_fingerprint": "default",
            "per_library_snapshots": {
                bundle_key: snapshot_to_dict(
                    _snap(bundle_key, "1.0", [_fn("foo", "_Z3foov")])
                ),
            },
            "filesystem_aliases": {},
            "library_filenames": {bundle_key: real_filename},
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
        assert set(resolved) == {"libfoo.so"}, (
            f"expected key from {real_filename!r} for the empty bundle key, "
            f"not the opaque artifact_id: got {sorted(resolved)}"
        )


class TestCompositionIdentityLooksUpEmptyLibraryKey:
    """Codex/CodeRabbit review, fresh evidence: `bundle.
    _composition_library_identity`'s own `library_name` guard used
    truthiness the same way `_release_match_key`'s did (see the sibling
    class above) -- an explicitly-supported empty-string bundle key must
    still resolve its real filename/aliases from the preserved composition
    section, not be silently treated as "no library name recorded"."""

    def test_empty_bundle_key_still_resolves_composition_identity(
        self, tmp_path: Path
    ) -> None:
        from dataclasses import replace as dc_replace

        from abicheck import bundle
        from abicheck.bundle_facts import BundleFacts
        from abicheck.elf_metadata import ElfMetadata
        from abicheck.workflows.release_package import resolve_release_package_map

        bundle_key = ""
        real_filename = "libfoo.so.1"
        libs = {
            bundle_key: dc_replace(
                _snap(bundle_key, "1.0", [_fn("foo", "_Z3foov")]),
                elf=ElfMetadata(soname=""),
            ),
        }
        facts = capture_bundle_facts(libs, variant_fingerprint="gcc13-avx2")
        facts = BundleFacts(
            variant_fingerprint=facts.variant_fingerprint,
            per_library_snapshots=facts.per_library_snapshots,
            manifest=facts.manifest,
            filesystem_aliases={bundle_key: ("libfoo.so.1.0.0",)},
            library_filenames={bundle_key: real_filename},
        )
        pkg = tmp_path / "pkg"
        store = DirectoryObjectStore(pkg)
        manifest = write_bundle_facts_package(facts, store=store, variant_id="v1")
        write_project_manifest(pkg, manifest)

        resolved = resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        (sub_dir,) = resolved.values()

        real, aliases, _nodes = bundle._stored_library_identity(sub_dir, 0)
        assert real is not None and real.name == real_filename, (
            f"expected the empty bundle key's real filename {real_filename!r} "
            f"resolved from composition, got {real}"
        )
        assert aliases == ("libfoo.so.1.0.0",)
