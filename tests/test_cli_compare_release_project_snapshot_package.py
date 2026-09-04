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

"""ADR-062 A1.7 -- stored/live and stored/stored release comparison
reachable from the standard `compare` CLI.

`cli_compare_release.py`'s existing per-library fan-out (loose directories
of `.so`/JSON files) now also accepts a multi-artifact `ProjectSnapshot`
package directory (written by `bundle_facts_store.write_bundle_facts_package`)
as either operand -- unpacked into the same `old_map`/`new_map` shape via
`workflows.release_package.resolve_release_package_map`. This module
exercises the plan's own named acceptance test: `stored/live`, `live/stored`,
and `stored/stored`, each against a small (2-3 library) fixture, asserted to
produce the same per-library findings a `live/live` run over the equivalent
loose directory would -- the "Stored-versus-live parity" row the plan's own
Validation corpus section commits to.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.bundle_facts import BundleFacts, capture_bundle_facts
from abicheck.bundle_facts_store import write_bundle_facts_package
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.project_snapshot_store import DirectoryObjectStore, write_project_manifest
from abicheck.serialization import snapshot_to_json


def _snap(name: str, version: str, functions: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(
        library=name, version=version, functions=functions, from_headers=True
    )


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int", visibility=Visibility.PUBLIC
    )


def _old_new_libraries() -> tuple[dict[str, AbiSnapshot], dict[str, AbiSnapshot]]:
    """Three libraries, three independent outcomes: a breaking removal, a
    compatible addition, and no change at all -- enough to tell a real
    per-library fan-out apart from one that silently collapsed every member
    onto the same verdict."""
    old = {
        "liba.so": _snap("liba.so", "1.0", [_fn("foo", "_Z3foov")]),
        "libb.so": _snap("libb.so", "1.0", []),
        "libc.so": _snap("libc.so", "1.0", [_fn("bar", "_Z3barv")]),
    }
    new = {
        "liba.so": _snap("liba.so", "2.0", []),  # breaking: foo removed
        "libb.so": _snap(
            "libb.so", "1.1", [_fn("baz", "_Z3bazv")]
        ),  # compatible addition
        "libc.so": _snap("libc.so", "1.0", [_fn("bar", "_Z3barv")]),  # no change
    }
    return old, new


def _with_filenames(facts: BundleFacts) -> BundleFacts:
    """`facts`, with `library_filenames` stamped to each library's own bare
    name -- what a real capture run records, and what
    `workflows.release_package._release_match_key` needs to derive the same
    canonical key a live directory operand's own `_build_match_map` would."""
    names = tuple(facts.per_library_snapshots)
    return BundleFacts(
        variant_fingerprint=facts.variant_fingerprint,
        per_library_snapshots=facts.per_library_snapshots,
        manifest=facts.manifest,
        filesystem_aliases=facts.filesystem_aliases,
        library_filenames={name: name for name in names},
    )


def _write_package(
    root: Path, libraries: dict[str, AbiSnapshot], *, variant_id: str = "default"
) -> None:
    facts = _with_filenames(
        capture_bundle_facts(libraries, variant_fingerprint="gcc13-avx2")
    )
    store = DirectoryObjectStore(root)
    manifest = write_bundle_facts_package(facts, store=store, variant_id=variant_id)
    write_project_manifest(root, manifest)


def _write_directory(root: Path, libraries: dict[str, AbiSnapshot]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name, snap in libraries.items():
        (root / f"{name}.json").write_text(snapshot_to_json(snap), encoding="utf-8")


def _invoke(*args: str) -> tuple[int, str]:
    from abicheck.cli import main

    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


_Outcome = tuple[str, int, int, int, int, int]


def _outcome_tuple(entry: dict[str, object]) -> _Outcome:
    return (
        str(entry["verdict"]),
        int(entry.get("breaking", 0) or 0),
        int(entry.get("source_breaks", 0) or 0),
        int(entry.get("risk_changes", 0) or 0),
        int(entry.get("compatible_additions", 0) or 0),
        int(entry.get("quality_issues", 0) or 0),
    )


def _library_outcomes(release_json: str) -> dict[str, _Outcome]:
    """`{name: (verdict, breaking, source_breaks, risk, additions, quality)}`
    -- the finding-count fields the release JSON reports per library, keyed
    by the discovered filename (a live directory's own literal
    `old_path.name`, e.g. ``liba.so.json``). Only meaningful for a *live*
    directory operand -- a stored-package operand's own materialized
    sub-package directory basename (`workflows.release_package.
    resolve_release_package_map`) is a sanitized display form of the
    canonical match key plus a short `artifact_id` suffix for guaranteed
    uniqueness, not the bare canonical key itself, so use `_sorted_outcomes`
    instead to compare a stored side against anything."""
    doc = json.loads(release_json)
    return {
        str(entry["library"]).removesuffix(".json"): _outcome_tuple(entry)
        for entry in doc["libraries"]
    }


def _sorted_outcomes(release_json: str) -> list[_Outcome]:
    """Every library's `_outcome_tuple`, sorted -- name-independent, so a
    stored-side release (whose per-library `library` field is an opaque
    `artifact_id`, not a real name) can still be compared against a live
    or another stored side: the *set* of (verdict, counts) tuples across a
    release's libraries must agree, even though nothing here claims the
    two sides name the same library the same way."""
    doc = json.loads(release_json)
    return sorted(_outcome_tuple(entry) for entry in doc["libraries"])


class TestStoredVersusLiveReleaseParity:
    def _fixture(self, tmp_path: Path) -> dict[str, Path]:
        old_libs, new_libs = _old_new_libraries()

        old_pkg = tmp_path / "old_pkg"
        new_pkg = tmp_path / "new_pkg"
        _write_package(old_pkg, old_libs)
        _write_package(new_pkg, new_libs)

        old_dir = tmp_path / "old_dir"
        new_dir = tmp_path / "new_dir"
        _write_directory(old_dir, old_libs)
        _write_directory(new_dir, new_libs)

        return {
            "old_pkg": old_pkg,
            "new_pkg": new_pkg,
            "old_dir": old_dir,
            "new_dir": new_dir,
        }

    def test_live_live_baseline_has_three_distinct_outcomes(
        self, tmp_path: Path
    ) -> None:
        # Sanity check on the fixture itself, so a parity "pass" below can
        # never be a vacuous "every library reported NO_CHANGE" false
        # positive.
        paths = self._fixture(tmp_path)
        ec, out = _invoke(
            "compare",
            str(paths["old_dir"]),
            str(paths["new_dir"]),
            "--format",
            "json",
            "-j",
            "1",
        )
        assert ec == 4  # a BREAKING library is present
        outcomes = _library_outcomes(out)
        assert outcomes["liba.so"][0] == "BREAKING"
        assert outcomes["liba.so"][1] == 1  # one breaking change (foo removed)
        assert outcomes["libb.so"][0] == "COMPATIBLE"
        assert outcomes["libb.so"][4] == 1  # one compatible addition (baz)
        assert outcomes["libc.so"][0] == "NO_CHANGE"

    def test_stored_stored_matches_live_live(self, tmp_path: Path) -> None:
        paths = self._fixture(tmp_path)
        _, live_out = _invoke(
            "compare",
            str(paths["old_dir"]),
            str(paths["new_dir"]),
            "--format",
            "json",
            "-j",
            "1",
        )
        ec, stored_out = _invoke(
            "compare",
            str(paths["old_pkg"]),
            str(paths["new_pkg"]),
            "--format",
            "json",
            "-j",
            "1",
        )
        assert ec == 4
        assert _sorted_outcomes(stored_out) == _sorted_outcomes(live_out)

    def test_stored_live_matches_live_live(self, tmp_path: Path) -> None:
        paths = self._fixture(tmp_path)
        _, live_out = _invoke(
            "compare",
            str(paths["old_dir"]),
            str(paths["new_dir"]),
            "--format",
            "json",
            "-j",
            "1",
        )
        ec, mixed_out = _invoke(
            "compare",
            str(paths["old_pkg"]),
            str(paths["new_dir"]),
            "--format",
            "json",
            "-j",
            "1",
        )
        assert ec == 4
        assert _sorted_outcomes(mixed_out) == _sorted_outcomes(live_out)

    def test_live_stored_matches_live_live(self, tmp_path: Path) -> None:
        paths = self._fixture(tmp_path)
        _, live_out = _invoke(
            "compare",
            str(paths["old_dir"]),
            str(paths["new_dir"]),
            "--format",
            "json",
            "-j",
            "1",
        )
        ec, mixed_out = _invoke(
            "compare",
            str(paths["old_dir"]),
            str(paths["new_pkg"]),
            "--format",
            "json",
            "-j",
            "1",
        )
        assert ec == 4
        assert _sorted_outcomes(mixed_out) == _sorted_outcomes(live_out)


class TestStoredBundleAnalysisSeesElfMetadata:
    """Codex security review, PR #1058: `old_map`/`new_map` entries for a
    stored side are directory paths, not live `.so` files -- `bundle.
    build_bundle_snapshot`'s own `_path_looks_like_elf` treats every
    directory as "not ELF, skip", which silently dropped every stored-side
    library from bundle-level analysis (`compare_bundle`'s cross-DSO
    `DT_NEEDED`/symbol-removal checks) rather than raising. A release where
    a stored ``libcore.so`` drops a symbol a sibling still imports would
    then report a clean bundle (and, combined with a per-library scope that
    also missed it, a clean release) instead of ``BUNDLE_INTRA_DEP_REMOVED``
    -- exactly the reported CI-gate-bypass scenario. This exercises the
    real fix end to end: `bundle.build_bundle_snapshot_mixed` resolving
    each stored sub-package's own `AbiSnapshot.elf`, feeding the identical
    `compare_bundle` detector `tests/test_bundle.py::TestIntraDepRemoved
    ::test_detects_missing_import` already covers for a live pair."""

    def test_intra_dep_removal_detected_across_two_stored_libraries(
        self, tmp_path: Path
    ) -> None:
        from abicheck.bundle import build_bundle_snapshot_mixed, compare_bundle
        from abicheck.checker_policy import ChangeKind
        from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol

        def elf_snap(name: str, meta: ElfMetadata) -> AbiSnapshot:
            return AbiSnapshot(
                library=name, version="1.0", elf=meta, from_headers=False
            )

        old_libs = {
            "libcore.so": elf_snap(
                "libcore.so",
                ElfMetadata(
                    soname="libcore.so.1",
                    symbols=[
                        ElfSymbol(name="core_add"),
                        ElfSymbol(name="core_mul"),
                    ],
                ),
            ),
            "libalgo.so": elf_snap(
                "libalgo.so",
                ElfMetadata(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1"],
                    imports=[ElfImport(name="core_add"), ElfImport(name="core_mul")],
                ),
            ),
        }
        new_libs = {
            "libcore.so": elf_snap(
                "libcore.so",
                ElfMetadata(
                    soname="libcore.so.1", symbols=[ElfSymbol(name="core_add")]
                ),
            ),
            "libalgo.so": old_libs["libalgo.so"],  # unchanged
        }

        old_pkg = tmp_path / "old_pkg"
        new_pkg = tmp_path / "new_pkg"
        _write_package(old_pkg, old_libs)
        _write_package(new_pkg, new_libs)

        from abicheck.workflows.release_package import resolve_release_package_map

        old_map = resolve_release_package_map(
            old_pkg, variant_id=None, dest_root=tmp_path / "resolved_old"
        )
        new_map = resolve_release_package_map(
            new_pkg, variant_id=None, dest_root=tmp_path / "resolved_new"
        )

        old_snap = build_bundle_snapshot_mixed(old_map)
        new_snap = build_bundle_snapshot_mixed(new_map)
        # The mechanism this fix adds: a stored sub-package's own ELF
        # metadata must actually be recovered, not silently dropped.
        assert set(old_snap.metadata) == {"libcore.so", "libalgo.so"}
        assert old_snap.metadata["libcore.so"].soname == "libcore.so.1"
        assert new_snap.metadata["libcore.so"].soname == "libcore.so.1"

        result = compare_bundle(old_snap, new_snap, per_library_results=[])
        kinds = {f.kind for f in result.bundle_findings}
        assert ChangeKind.BUNDLE_INTRA_DEP_REMOVED in kinds
        finding = next(
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
        )
        assert finding.symbol == "core_mul"
        assert finding.consumer_library == "libalgo.so"


class TestReverseMembershipValidation:
    """Codex review, fresh evidence after the collision/symlink/architecture
    fixes: `read_variant_artifact_pair` only validates the "declared"
    membership direction (every id `variant.artifact_ids` names is itself
    published and self-consistent), never the reverse "owned" direction --
    a *different* published artifact whose own `variant_id` also names this
    variant, yet `variant.artifact_ids` simply omits it. A stale/hand-edited
    package could silently exclude exactly the one library carrying a real
    ABI break from the comparison. `PackageManifest.__post_init__` already
    rejects this shape when a manifest is *constructed* in-memory (it's the
    only way a real writer could produce one), so this fixture writes a
    valid package first and then hand-edits its published
    `refs/variants/<id>.json` on disk -- simulating a stale/corrupted
    package the same way a real one could arise (a partial write, a
    hand-edited file), not something `write_project_manifest` itself would
    ever emit."""

    def test_omitted_artifact_with_matching_variant_id_is_rejected(
        self, tmp_path: Path
    ) -> None:
        import json

        from abicheck.model import Function, Visibility
        from abicheck.project_snapshot_legacy import (
            materialize_release_variant_artifacts,
        )
        from abicheck.project_snapshot_store import (
            DirectoryObjectStore,
            write_project_manifest,
        )
        from abicheck.serialization import SCHEMA_VERSION
        from abicheck.storage.canonical import canonical_json
        from abicheck.storage.import_v1 import import_legacy_snapshot
        from abicheck.storage.package import PackageManifest, VariantRef

        root = tmp_path / "pkg"
        store = DirectoryObjectStore(root)
        liba = AbiSnapshot(
            library="liba.so",
            version="1.0",
            functions=[
                Function(
                    name="foo",
                    mangled="_Z3foov",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                )
            ],
        )
        libb = AbiSnapshot(library="libb.so", version="1.0")
        from abicheck.serialization import snapshot_to_dict

        m_a = import_legacy_snapshot(
            snapshot_to_dict(liba),
            store=store,
            artifact_id="a1",
            max_known_schema_version=SCHEMA_VERSION,
            variant_id="v1",
        )
        m_b = import_legacy_snapshot(
            snapshot_to_dict(libb),
            store=store,
            artifact_id="a2",
            max_known_schema_version=SCHEMA_VERSION,
            variant_id="v1",
        )
        (art_a,) = m_a.artifact_refs
        (art_b,) = m_b.artifact_refs
        # First write a genuinely valid package (both artifacts declared).
        good_variant = VariantRef(variant_id="v1", artifact_ids=("a1", "a2"))
        write_project_manifest(
            root,
            PackageManifest(
                versions=m_a.versions,
                variant_refs=(good_variant,),
                artifact_refs=(art_a, art_b),
            ),
        )
        # Then hand-corrupt the published variant ref to omit "a2" -- which
        # still names "v1" as its own variant_id (its refs/artifacts/a2.json
        # is untouched) -- simulating a stale/partial write.
        variant_ref_path = root / "refs" / "variants" / "v1.json"
        data = json.loads(variant_ref_path.read_text())
        data["artifact_ids"] = ["a1"]
        variant_ref_path.write_text(canonical_json(data, indent=2))

        try:
            materialize_release_variant_artifacts(
                root, variant_id="v1", dest_root=tmp_path / "resolved"
            )
        except ValueError as exc:
            assert "a2" in str(exc)
        else:
            raise AssertionError(
                "materialize_release_variant_artifacts silently excluded "
                "artifact 'a2' instead of raising"
            )


class TestVariantSelection:
    def test_single_variant_is_used_without_a_flag(self, tmp_path: Path) -> None:
        old_libs, new_libs = _old_new_libraries()
        old_pkg = tmp_path / "old_pkg"
        new_pkg = tmp_path / "new_pkg"
        _write_package(old_pkg, old_libs, variant_id="gcc13")
        _write_package(new_pkg, new_libs, variant_id="gcc13")

        ec, out = _invoke(
            "compare", str(old_pkg), str(new_pkg), "--format", "json", "-j", "1"
        )
        assert ec == 4
        outcomes = _sorted_outcomes(out)
        assert any(o[0] == "BREAKING" and o[1] == 1 for o in outcomes)

    def _multi_variant_package(self, root: Path) -> None:
        """A package declaring two variants -- ``gcc12`` (``liba.so`` only,
        no breaking change) and ``gcc13`` (the real, breaking ``liba.so``
        fixture from `_old_new_libraries`). Each variant's own artifact_id
        is a deterministic hash of the library *name* alone
        (`bundle_facts_store._artifact_id_for_library`), so two variants
        sharing one library name would collide on one `ArtifactRef` --
        giving each variant a differently-named library sidesteps that
        (a real orthogonal reconciliation of variant-scoped artifact
        identity is A1.4's own still-open follow-up, not something this
        fixture needs to resolve).
        """
        from abicheck.project_snapshot_store import read_project_manifest
        from abicheck.storage.package import PackageManifest

        old_libs, _ = _old_new_libraries()
        store = DirectoryObjectStore(root)
        facts_gcc12 = _with_filenames(
            capture_bundle_facts(
                {"libunchanged.so": _snap("libunchanged.so", "1.0", [])},
                variant_fingerprint="gcc12",
            )
        )
        manifest1 = write_bundle_facts_package(
            facts_gcc12, store=store, variant_id="gcc12"
        )
        write_project_manifest(root, manifest1)

        facts_gcc13 = _with_filenames(
            capture_bundle_facts(old_libs, variant_fingerprint="gcc13-avx2")
        )
        manifest2 = write_bundle_facts_package(
            facts_gcc13, store=store, variant_id="gcc13"
        )
        existing = read_project_manifest(root)
        combined = PackageManifest(
            versions=existing.versions,
            variant_refs=existing.variant_refs + manifest2.variant_refs,
            artifact_refs=existing.artifact_refs + manifest2.artifact_refs,
        )
        write_project_manifest(root, combined)

    def test_ambiguous_variant_without_a_flag_is_a_usage_error(
        self, tmp_path: Path
    ) -> None:
        _, new_libs = _old_new_libraries()
        old_pkg = tmp_path / "old_pkg"
        new_pkg = tmp_path / "new_pkg"
        self._multi_variant_package(old_pkg)
        _write_package(new_pkg, new_libs)

        ec, out = _invoke(
            "compare", str(old_pkg), str(new_pkg), "--format", "json", "-j", "1"
        )
        assert ec == 64
        assert "variant" in out.lower()

    def test_explicit_old_variant_disambiguates(self, tmp_path: Path) -> None:
        _, new_libs = _old_new_libraries()
        old_pkg = tmp_path / "old_pkg"
        new_pkg = tmp_path / "new_pkg"
        self._multi_variant_package(old_pkg)
        _write_package(new_pkg, new_libs)

        ec, out = _invoke(
            "compare",
            str(old_pkg),
            str(new_pkg),
            "--old-variant",
            "gcc13",
            "--format",
            "json",
            "-j",
            "1",
        )
        assert ec == 4
        outcomes = _sorted_outcomes(out)
        assert any(o[0] == "BREAKING" and o[1] == 1 for o in outcomes)
