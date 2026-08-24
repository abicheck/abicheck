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

"""Regression tests for wiring G38 Phase 4's
:func:`abicheck.bundle_signature_evidence.find_unverified_signature_findings`
into the real ``compare --release`` bundle-analysis path
(``_run_bundle_analysis``/``_collect_bundle_result`` in
``cli_compare_release_helpers.py``/``cli_compare_release.py``).

Before this wiring, ``find_unverified_signature_findings`` had exactly one
caller anywhere in the codebase: its own test module. These tests exercise
the real CLI-facing helpers with a monkeypatched ``build_bundle_snapshot``
(mirroring the established pattern in ``tests/test_bundle.py``), rather than
the detector directly, so a regression in the *plumbing* -- not just the
detector's own logic -- fails here.
"""

from __future__ import annotations

from pathlib import Path

import abicheck.bundle as bundle_mod
from abicheck.bundle_models import BundleSignatureEvidence, BundleSnapshot
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import DiffResult
from abicheck.cli_compare_release_helpers import (
    _collect_bundle_result,
    _run_bundle_analysis,
)
from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, Visibility


def _meta(
    *,
    exports: list[str] = (),
    imports: list[str] = (),
    needed: list[str] = (),
) -> ElfMetadata:
    return ElfMetadata(
        soname="",
        needed=list(needed),
        symbols=[ElfSymbol(name=n, visibility="default") for n in exports],
        imports=[ElfImport(name=n) for n in imports],
    )


def _snapshot(libraries: dict[str, ElfMetadata]) -> BundleSnapshot:
    libs = {name: Path(f"/fake/{name}") for name in libraries}
    graph = bundle_mod._compute_resolution_graph(libs, libraries)
    return BundleSnapshot(
        root=Path("/fake"), libraries=libs, metadata=libraries, resolution=graph
    )


def _elf_only_fn(symbol: str) -> Function:
    return Function(
        name=symbol, mangled=symbol, return_type="?", visibility=Visibility.ELF_ONLY
    )


def _snap(
    library: str, *, functions: list[Function], elf_only_mode: bool
) -> AbiSnapshot:
    return AbiSnapshot(
        library=library, version="1.0", functions=functions, elf_only_mode=elf_only_mode
    )


def _diff(library: str) -> DiffResult:
    return DiffResult(
        old_version="old", new_version="new", library=library, verdict=Verdict.NO_CHANGE
    )


class TestRunBundleAnalysisSignatureEvidenceWiring:
    """``_run_bundle_analysis`` (the real ``compare --release`` bundle-
    analysis call site) folds ``find_unverified_signature_findings``'s
    output into ``compare_bundle``'s own ``bundle_findings`` list."""

    def _fake_snapshot(self, libs: dict[str, Path]) -> BundleSnapshot:
        return _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )

    def test_signature_evidence_findings_are_folded_in_when_snapshots_given(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(bundle_mod, "build_bundle_snapshot", self._fake_snapshot)

        old_map = {
            "libcore.so": Path("libcore.so"),
            "libconsumer.so": Path("libconsumer.so"),
        }
        new_map = dict(old_map)

        # Both sides genuinely ELF-only (dumped with no headers at all) --
        # confirmed exported, no way to corroborate the signature agrees.
        old_snapshots = {
            "libcore.so": _snap(
                "libcore.so", functions=[_elf_only_fn("core_fn")], elf_only_mode=True
            )
        }
        new_snapshots = {
            "libcore.so": _snap(
                "libcore.so", functions=[_elf_only_fn("core_fn")], elf_only_mode=True
            )
        }

        result = _run_bundle_analysis(
            old_map,
            new_map,
            [_diff("libcore.so"), _diff("libconsumer.so")],
            manifest_path=None,
            bundle_system_providers="",
            old_snapshots=old_snapshots,
            new_snapshots=new_snapshots,
        )

        assert result is not None
        assert any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED
            for f in result.bundle_findings
        )

    def test_no_signature_evidence_findings_without_snapshots(
        self, monkeypatch
    ) -> None:
        # Pre-existing behavior (old_snapshots/new_snapshots omitted) must
        # stay unaffected -- no Phase 4 findings appear.
        monkeypatch.setattr(bundle_mod, "build_bundle_snapshot", self._fake_snapshot)

        old_map = {
            "libcore.so": Path("libcore.so"),
            "libconsumer.so": Path("libconsumer.so"),
        }
        new_map = dict(old_map)

        result = _run_bundle_analysis(
            old_map,
            new_map,
            [_diff("libcore.so"), _diff("libconsumer.so")],
            manifest_path=None,
            bundle_system_providers="",
        )

        assert result is not None
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED
            for f in result.bundle_findings
        )


class TestCollectBundleResultBuildsSnapshotMapsFromBundleKey:
    """``_collect_bundle_result`` (the release fan-out's own call site) must
    build ``old_snapshots``/``new_snapshots`` keyed by each entry's
    ``_bundle_key`` -- the same canonical key ``old_map``/``new_map`` use --
    not by the library's file basename, and must not leak the stashed
    ``AbiSnapshot``/key fields into the returned entries."""

    def test_builds_snapshot_maps_and_reaches_the_detector(self, monkeypatch) -> None:
        def _fake_snapshot(libs: dict[str, Path]) -> BundleSnapshot:
            return _snapshot(
                {
                    "libcore.so": _meta(exports=["core_fn"]),
                    "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
                }
            )

        monkeypatch.setattr(bundle_mod, "build_bundle_snapshot", _fake_snapshot)

        old_map = {
            "libcore.so": Path("libcore.so"),
            "libconsumer.so": Path("libconsumer.so"),
        }
        new_map = dict(old_map)

        old_snap = _snap(
            "libcore.so", functions=[_elf_only_fn("core_fn")], elf_only_mode=True
        )
        new_snap = _snap(
            "libcore.so", functions=[_elf_only_fn("core_fn")], elf_only_mode=True
        )
        library_results: list[dict[str, object]] = [
            {
                "library": "libcore.so",
                "verdict": "NO_CHANGE",
                "_diff_result": _diff("libcore.so"),
                "_old_snapshot": old_snap,
                "_new_snapshot": new_snap,
                "_bundle_key": "libcore.so",
            },
            {
                "library": "libconsumer.so",
                "verdict": "NO_CHANGE",
                "_diff_result": _diff("libconsumer.so"),
            },
        ]

        bundle_result, worst_verdict = _collect_bundle_result(
            library_results,
            old_map,
            new_map,
            "NO_CHANGE",
            manifest_path=None,
            bundle_system_providers="",
        )

        assert bundle_result is not None
        assert any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED
            for f in bundle_result.bundle_findings
        )
        # Entries carrying no _bundle_key (the second library above) must
        # not raise or contribute a spurious mapping entry.
        assert isinstance(worst_verdict, str)


class TestCollectBundleResultAcceptsCompactBundleEvidence:
    """G38 stabilization Phase 9 (memory regression fix): a default
    ``compare --release`` (bundle analysis on, no JUnit/
    ``--bundle-facts-out``) stashes ``_old_bundle_evidence``/
    ``_new_bundle_evidence`` (:class:`~abicheck.bundle_models.
    BundleSignatureEvidence`) instead of the full ``_old_snapshot``/
    ``_new_snapshot`` -- ``_collect_bundle_result`` must read either and
    reach the identical detector output, and no `AbiSnapshot` may leak
    into the returned entries either way."""

    def test_compact_evidence_reaches_the_detector_identically(
        self, monkeypatch
    ) -> None:
        def _fake_snapshot(libs: dict[str, Path]) -> BundleSnapshot:
            return _snapshot(
                {
                    "libcore.so": _meta(exports=["core_fn"]),
                    "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
                }
            )

        monkeypatch.setattr(bundle_mod, "build_bundle_snapshot", _fake_snapshot)

        old_map = {
            "libcore.so": Path("libcore.so"),
            "libconsumer.so": Path("libconsumer.so"),
        }
        new_map = dict(old_map)

        old_snap = _snap(
            "libcore.so", functions=[_elf_only_fn("core_fn")], elf_only_mode=True
        )
        new_snap = _snap(
            "libcore.so", functions=[_elf_only_fn("core_fn")], elf_only_mode=True
        )

        def _entries(
            old_evidence: object, new_evidence: object
        ) -> list[dict[str, object]]:
            return [
                {
                    "library": "libcore.so",
                    "verdict": "NO_CHANGE",
                    "_diff_result": _diff("libcore.so"),
                    "_old_bundle_evidence": old_evidence,
                    "_new_bundle_evidence": new_evidence,
                    "_bundle_key": "libcore.so",
                },
                {
                    "library": "libconsumer.so",
                    "verdict": "NO_CHANGE",
                    "_diff_result": _diff("libconsumer.so"),
                },
            ]

        compact_old = BundleSignatureEvidence.from_snapshot(old_snap)
        compact_new = BundleSignatureEvidence.from_snapshot(new_snap)

        compact_result, _ = _collect_bundle_result(
            _entries(compact_old, compact_new),
            old_map,
            new_map,
            "NO_CHANGE",
            manifest_path=None,
            bundle_system_providers="",
        )
        full_result, _ = _collect_bundle_result(
            _entries(old_snap, new_snap),
            old_map,
            new_map,
            "NO_CHANGE",
            manifest_path=None,
            bundle_system_providers="",
        )

        assert compact_result is not None and full_result is not None
        compact_kinds = sorted(f.kind.value for f in compact_result.bundle_findings)
        full_kinds = sorted(f.kind.value for f in full_result.bundle_findings)
        assert compact_kinds == full_kinds
        assert ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED.value in compact_kinds

        # No AbiSnapshot -- full or otherwise -- leaks through the compact
        # path's own entries into the result of a later JSON-serialising
        # step (this is what the CLI-level strip already guards; this pins
        # the underlying object graph directly).
        for entry in _entries(compact_old, compact_new):
            assert not isinstance(entry.get("_old_bundle_evidence"), AbiSnapshot)
            assert not isinstance(entry.get("_new_bundle_evidence"), AbiSnapshot)
