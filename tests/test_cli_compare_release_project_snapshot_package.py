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
    sub-package directory is named by the artifact's opaque `artifact_id`
    (`project_snapshot_legacy.materialize_release_variant_artifacts`'s own
    docstring: collision-safety, not a display name), so use
    `_sorted_outcomes` instead to compare a stored side against anything."""
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
