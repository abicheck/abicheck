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

import json
from pathlib import Path

import abicheck.bundle as bundle_mod
from abicheck.bundle_models import BundleSignatureEvidence, BundleSnapshot
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import DiffResult
from abicheck.cli_compare_release_helpers import (
    _collect_bundle_result,
    _format_release_json,
    _release_md_bundle_findings,
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
            bundle_system_providers=(),
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
            bundle_system_providers=(),
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
            bundle_system_providers=(),
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
            bundle_system_providers=(),
        )
        full_result, _ = _collect_bundle_result(
            _entries(old_snap, new_snap),
            old_map,
            new_map,
            "NO_CHANGE",
            manifest_path=None,
            bundle_system_providers=(),
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


class TestBundleAnalysisErrorsAreStructural:
    """G38 stabilization Phase 11 / P0-D: a failure inside bundle analysis
    (``compare_bundle()`` itself, or the Phase 4 signature-evidence check)
    must be recorded in ``BundleDiffResult.analysis_errors``, not only
    echoed to stderr -- so a JSON/Markdown report consumer can tell "ran
    clean" apart from "ran, but degraded" without grepping logs."""

    def _fake_snapshot(self, libs: dict[str, Path]) -> BundleSnapshot:
        return _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )

    def test_compare_bundle_failure_is_recorded_in_analysis_errors(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(bundle_mod, "build_bundle_snapshot", self._fake_snapshot)

        def _boom(*args, **kwargs):
            raise RuntimeError("synthetic compare_bundle failure")

        monkeypatch.setattr(bundle_mod, "compare_bundle", _boom)

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
            bundle_system_providers=(),
        )

        assert result is not None
        assert result.bundle_findings == []
        assert len(result.analysis_errors) == 1
        assert "synthetic compare_bundle failure" in result.analysis_errors[0]

    def test_signature_evidence_failure_is_appended_to_analysis_errors(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(bundle_mod, "build_bundle_snapshot", self._fake_snapshot)

        def _boom(*args, **kwargs):
            raise RuntimeError("synthetic signature-evidence failure")

        # The real call site does `from .bundle_signature_evidence import
        # find_unverified_signature_findings` *inside* the function body,
        # so the patch target is the source module's own attribute, not a
        # name re-exported by `cli_compare_release_helpers`.
        import abicheck.bundle_signature_evidence as sig_mod

        monkeypatch.setattr(sig_mod, "find_unverified_signature_findings", _boom)

        old_map = {
            "libcore.so": Path("libcore.so"),
            "libconsumer.so": Path("libconsumer.so"),
        }
        new_map = dict(old_map)
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
            bundle_system_providers=(),
            old_snapshots=old_snapshots,
            new_snapshots=new_snapshots,
        )

        assert result is not None
        assert len(result.analysis_errors) == 1
        assert "synthetic signature-evidence failure" in result.analysis_errors[0]

    def test_analysis_errors_surface_in_json_summary(self, monkeypatch) -> None:
        monkeypatch.setattr(bundle_mod, "build_bundle_snapshot", self._fake_snapshot)

        def _boom(*args, **kwargs):
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(bundle_mod, "compare_bundle", _boom)

        old_map = {"libcore.so": Path("libcore.so")}
        new_map = dict(old_map)
        result = _run_bundle_analysis(
            old_map,
            new_map,
            [_diff("libcore.so")],
            manifest_path=None,
            bundle_system_providers=(),
        )
        assert result is not None

        doc = _format_release_json(
            "COMPATIBLE",
            Path("/old"),
            Path("/new"),
            [],
            [],
            [],
            {},
            {},
            [],
            bundle_result=result,
            matrix_result=None,
        )
        payload = json.loads(doc)
        assert payload["bundle_analysis_errors"] == [
            "bundle analysis raised: synthetic failure"
        ]

    def test_no_analysis_errors_key_when_bundle_analysis_is_clean(self) -> None:
        from abicheck.bundle import BundleDiffResult

        clean_result = BundleDiffResult(old_root=Path("/old"), new_root=Path("/new"))
        doc = _format_release_json(
            "COMPATIBLE",
            Path("/old"),
            Path("/new"),
            [],
            [],
            [],
            {},
            {},
            [],
            bundle_result=clean_result,
            matrix_result=None,
        )
        payload = json.loads(doc)
        assert "bundle_analysis_errors" not in payload

    def test_analysis_errors_surface_in_markdown_even_with_no_findings(self) -> None:
        from abicheck.bundle import BundleDiffResult

        degraded_result = BundleDiffResult(
            old_root=Path("/old"),
            new_root=Path("/new"),
            analysis_errors=["synthetic failure"],
        )
        lines = _release_md_bundle_findings(degraded_result)
        text = "\n".join(lines)
        assert "Bundle Analysis Warnings" in text
        assert "synthetic failure" in text


class TestBundleAnalysisForwardsPolicyFile:
    """G38 Phase 16: the release fan-out's own resolved ``PolicyFile`` must
    reach ``analyze_bundle()``/``compare_bundle()`` -- not just the bare
    *policy* preset name -- so a ``--policy custom.yaml`` override for a
    ``bundle_*`` ``ChangeKind`` actually changes the release's aggregate
    ``bundle_verdict``. Before this wiring, ``_run_bundle_analysis`` never
    accepted a ``policy_file`` parameter at all, so such an override was
    silently ignored on this one call site even though the stored-
    ``BundleFacts`` driver (``bundle_facts.compare_bundle_from_facts``)
    already honored one."""

    def _fake_snapshot(self, libs: dict[str, Path]) -> BundleSnapshot:
        return _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )

    def _entries(
        self, old_snap: AbiSnapshot, new_snap: AbiSnapshot
    ) -> list[dict[str, object]]:
        return [
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

    def test_policy_file_override_demotes_the_bundle_verdict(
        self, monkeypatch
    ) -> None:
        from abicheck.policy_file import PolicyFile

        monkeypatch.setattr(bundle_mod, "build_bundle_snapshot", self._fake_snapshot)

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

        # Without a policy_file override, the signature-evidence gate's
        # BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED finding scores under the
        # bare "strict_abi" default.
        unmodified, unmodified_verdict = _collect_bundle_result(
            self._entries(old_snap, new_snap),
            old_map,
            new_map,
            "NO_CHANGE",
            manifest_path=None,
            bundle_system_providers=(),
        )
        assert unmodified is not None
        assert any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED
            for f in unmodified.bundle_findings
        )
        assert unmodified.bundle_verdict != Verdict.COMPATIBLE

        # A policy_file overriding that kind to COMPATIBLE must reach
        # analyze_bundle() -- through _run_bundle_analysis -- and change the
        # aggregate bundle_verdict, exactly the way the same override
        # already changes a single --policy compare's own verdict.
        pf = PolicyFile(
            overrides={
                ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED: Verdict.COMPATIBLE
            }
        )
        overridden, overridden_verdict = _collect_bundle_result(
            self._entries(old_snap, new_snap),
            old_map,
            new_map,
            "NO_CHANGE",
            manifest_path=None,
            bundle_system_providers=(),
            policy_file=pf,
        )
        assert overridden is not None
        assert overridden.bundle_verdict == Verdict.COMPATIBLE
        assert overridden_verdict != unmodified_verdict

    def test_collect_bundle_result_sets_policy_file_before_verdict_is_read(
        self, monkeypatch
    ) -> None:
        """Direct plumbing check: ``_collect_bundle_result`` must set its
        *policy_file* argument on the ``BundleDiffResult`` it gets back from
        ``_run_bundle_analysis`` -- and do so *before* it reads
        ``bundle_verdict`` to fold into ``worst_verdict`` -- independent of
        what any particular ChangeKind override happens to do to a verdict.
        ``_run_bundle_analysis`` itself takes no ``policy_file`` parameter;
        ``BundleDiffResult.policy_file`` is a plain mutable field, set by the
        caller after construction (G38 Phase 16)."""
        from abicheck.policy_file import PolicyFile

        monkeypatch.setattr(bundle_mod, "build_bundle_snapshot", self._fake_snapshot)

        old_map = {"libcore.so": Path("libcore.so")}
        new_map = dict(old_map)
        sentinel_pf = PolicyFile()

        bundle_result, _ = _collect_bundle_result(
            self._entries(
                _snap("libcore.so", functions=[], elf_only_mode=True),
                _snap("libcore.so", functions=[], elf_only_mode=True),
            ),
            old_map,
            new_map,
            "NO_CHANGE",
            manifest_path=None,
            bundle_system_providers=(),
            policy_file=sentinel_pf,
        )

        assert bundle_result is not None
        assert bundle_result.policy_file is sentinel_pf


class TestResolveBundlePolicyFile:
    """``pack_application.resolve_bundle_policy_file`` (G38 Phase 16) --
    the primitive the ``compare-release`` fan-out uses to resolve the
    ``PolicyFile`` bundle analysis should score against. Lives in
    ``pack_application.py``, not ``cli_compare_release_helpers.py``,
    because the latter (and its sibling ``cli_compare_release.py``) are
    pinned at a no-growth line-count baseline (``architecture/debt.yaml``,
    ADR-061); tested here rather than in ``test_pack_application.py``
    because that file is pinned at one too (Codex review, fresh evidence)."""

    def test_no_policy_file_and_no_pack_resolves_none(self) -> None:
        from abicheck.pack_application import resolve_bundle_policy_file

        assert resolve_bundle_policy_file(None, "strict_abi", None, None) is None

    def test_resolves_a_real_policy_document(self, tmp_path: Path) -> None:
        from abicheck.pack_application import resolve_bundle_policy_file

        pol = tmp_path / "policy.yaml"
        pol.write_text("base_policy: strict_abi\noverrides:\n  func_removed: ignore\n")
        pf = resolve_bundle_policy_file(None, "strict_abi", pol, None)
        assert pf is not None
        assert pf.overrides[ChangeKind.FUNC_REMOVED] == Verdict.COMPATIBLE

    def test_folds_a_resolved_pack_application(self, tmp_path: Path) -> None:
        from abicheck.pack_application import (
            PackApplication,
            resolve_bundle_policy_file,
        )

        application = PackApplication(
            policy_overrides={ChangeKind.BUNDLE_INTRA_DEP_REMOVED: Verdict.COMPATIBLE}
        )
        pf = resolve_bundle_policy_file(None, "strict_abi", None, application)
        assert pf is not None
        assert pf.overrides[ChangeKind.BUNDLE_INTRA_DEP_REMOVED] == Verdict.COMPATIBLE
