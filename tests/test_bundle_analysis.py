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

"""Unit tests for :func:`abicheck.bundle_analysis.analyze_bundle` itself
(G38 stabilization Phase 12) -- the one bundle-analysis orchestrator both
the live ``compare --release`` path and the stored-facts path
(``bundle_facts.compare_bundle_from_facts``) now call.

``tests/test_cli_compare_release_bundle_signature_wiring.py`` and
``tests/test_bundle_facts.py`` exercise this function through its two real
callers; this module tests the primitive directly, per this repo's
established "primitive-level property tests" convention (AGENTS.md) --
both stages succeeding, either stage raising without losing the other's
results, and the compact-vs-full-snapshot interchangeability Phase 9
established.
"""

from __future__ import annotations

from pathlib import Path

import abicheck.bundle as bundle_mod
import abicheck.bundle_signature_evidence as sig_mod
from abicheck.bundle import _compute_resolution_graph
from abicheck.bundle_analysis import analyze_bundle
from abicheck.bundle_models import BundleSignatureEvidence, BundleSnapshot
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import DiffResult
from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, Visibility


def _meta(
    *,
    soname: str = "",
    needed: list[str] | None = None,
    exports: list[str] | None = None,
    imports: list[str] | None = None,
) -> ElfMetadata:
    syms = [ElfSymbol(name=name, visibility="default") for name in exports or []]
    imps = [ElfImport(name=name) for name in imports or []]
    return ElfMetadata(
        soname=soname or "", needed=needed or [], symbols=syms, imports=imps
    )


def _snapshot(libraries: dict[str, ElfMetadata]) -> BundleSnapshot:
    libs = {name: Path(f"/fake/{name}") for name in libraries}
    graph = _compute_resolution_graph(libs, libraries)
    return BundleSnapshot(
        root=Path("/fake"), libraries=libs, metadata=libraries, resolution=graph
    )


def _bundle_metadata() -> dict[str, ElfMetadata]:
    return {
        "libcore.so": _meta(soname="libcore.so", exports=["core_fn"]),
        "libconsumer.so": _meta(
            soname="libconsumer.so", needed=["libcore.so"], imports=["core_fn"]
        ),
    }


def _diff(library: str, verdict: Verdict = Verdict.NO_CHANGE) -> DiffResult:
    return DiffResult(
        old_version="old", new_version="new", library=library, verdict=verdict
    )


def _elf_only_fn(symbol: str) -> Function:
    return Function(
        name=symbol, mangled=symbol, return_type="?", visibility=Visibility.ELF_ONLY
    )


def _elf_only_snapshot(library: str, version: str) -> AbiSnapshot:
    return AbiSnapshot(
        library=library,
        version=version,
        functions=[_elf_only_fn("core_fn")],
        elf_only_mode=True,
    )


class TestAnalyzeBundleBothStagesSucceed:
    def test_no_signature_evidence_runs_only_compare_bundle(self) -> None:
        metadata = _bundle_metadata()
        old = _snapshot(metadata)
        new = _snapshot(metadata)
        result = analyze_bundle(old, new, [_diff("libcore.so"), _diff("libconsumer.so")])

        assert result.bundle_findings == []
        assert result.analysis_errors == []

    def test_signature_evidence_given_folds_phase4_findings_in(self) -> None:
        metadata = _bundle_metadata()
        old = _snapshot(metadata)
        new = _snapshot(metadata)
        old_evidence = {"libcore.so": _elf_only_snapshot("libcore.so", "old")}
        new_evidence = {"libcore.so": _elf_only_snapshot("libcore.so", "new")}

        result = analyze_bundle(
            old,
            new,
            [_diff("libcore.so"), _diff("libconsumer.so")],
            old_signature_evidence=old_evidence,
            new_signature_evidence=new_evidence,
        )

        assert result.analysis_errors == []
        assert any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED
            for f in result.bundle_findings
        )

    def test_only_one_side_of_evidence_given_does_not_run_phase4(self) -> None:
        """The Phase 4 gate needs *both* sides -- a caller who has evidence
        for only one side (e.g. a stored facts document with no live NEW
        evidence at all) must not crash or silently synthesize the missing
        side; the gate simply doesn't run, same as no evidence at all."""
        metadata = _bundle_metadata()
        old = _snapshot(metadata)
        new = _snapshot(metadata)
        old_evidence = {"libcore.so": _elf_only_snapshot("libcore.so", "old")}

        result = analyze_bundle(
            old,
            new,
            [_diff("libcore.so"), _diff("libconsumer.so")],
            old_signature_evidence=old_evidence,
            new_signature_evidence=None,
        )

        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED
            for f in result.bundle_findings
        )
        assert result.analysis_errors == []

    def test_policy_is_threaded_to_the_returned_result(self) -> None:
        """G38 stabilization Phase 10: the resolved policy must reach the
        returned BundleDiffResult even through this new orchestrator, so a
        later severity/exit-code fold scores bundle findings consistently
        with the displayed verdict."""
        metadata = _bundle_metadata()
        old = _snapshot(metadata)
        new = _snapshot(metadata)
        result = analyze_bundle(
            old, new, [_diff("libcore.so"), _diff("libconsumer.so")], policy="plugin_abi"
        )
        assert result.policy == "plugin_abi"


class TestAnalyzeBundleCompactVsFullSnapshotInterchangeable:
    """G38 stabilization Phase 9's duck-type-compatible projection, exercised
    directly through the new orchestrator: a compact
    `BundleSignatureEvidence` and a full `AbiSnapshot` carrying the
    identical function/variable/elf_only_mode facts must produce identical
    findings, and the two sides need not even agree on which shape they
    use (a stored old side is a real `AbiSnapshot`; a live new side may
    already be projected down to the compact shape)."""

    def test_compact_and_full_produce_identical_findings(self) -> None:
        metadata = _bundle_metadata()
        old = _snapshot(metadata)
        new = _snapshot(metadata)
        full_old = _elf_only_snapshot("libcore.so", "old")
        full_new = _elf_only_snapshot("libcore.so", "new")
        compact_old = BundleSignatureEvidence.from_snapshot(full_old)
        compact_new = BundleSignatureEvidence.from_snapshot(full_new)

        full_result = analyze_bundle(
            old,
            new,
            [_diff("libcore.so"), _diff("libconsumer.so")],
            old_signature_evidence={"libcore.so": full_old},
            new_signature_evidence={"libcore.so": full_new},
        )
        compact_result = analyze_bundle(
            old,
            new,
            [_diff("libcore.so"), _diff("libconsumer.so")],
            old_signature_evidence={"libcore.so": compact_old},
            new_signature_evidence={"libcore.so": compact_new},
        )

        assert full_result.bundle_findings == compact_result.bundle_findings
        assert any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED
            for f in compact_result.bundle_findings
        )

    def test_mixed_full_old_compact_new_still_works(self) -> None:
        """A stored old side (a real `AbiSnapshot`, since `BundleFacts.
        per_library_snapshots` is always one) paired with a live new side
        already projected to the compact shape (Phase 9's memory fix) --
        the exact combination `bundle_facts.compare_bundle_from_facts()`
        produces once a future Phase 13 CLI consumer threads a compact
        NEW-side evidence map through."""
        metadata = _bundle_metadata()
        old = _snapshot(metadata)
        new = _snapshot(metadata)
        full_old = _elf_only_snapshot("libcore.so", "old")
        compact_new = BundleSignatureEvidence.from_snapshot(
            _elf_only_snapshot("libcore.so", "new")
        )

        result = analyze_bundle(
            old,
            new,
            [_diff("libcore.so"), _diff("libconsumer.so")],
            old_signature_evidence={"libcore.so": full_old},
            new_signature_evidence={"libcore.so": compact_new},
        )

        assert any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED
            for f in result.bundle_findings
        )


class TestAnalyzeBundleDegradesAdditivelyOnFailure:
    """G38 stabilization Phase 11's structured-error contract, now enforced
    through the shared orchestrator: a failure in either stage must not
    lose the other stage's already-computed results, and must be recorded
    in `analysis_errors`, not only raised/swallowed."""

    def test_compare_bundle_failure_still_runs_phase4_and_records_the_error(
        self, monkeypatch
    ) -> None:
        def _boom(*args, **kwargs):
            raise RuntimeError("synthetic compare_bundle failure")

        monkeypatch.setattr(bundle_mod, "compare_bundle", _boom)

        metadata = _bundle_metadata()
        old = _snapshot(metadata)
        new = _snapshot(metadata)
        old_evidence = {"libcore.so": _elf_only_snapshot("libcore.so", "old")}
        new_evidence = {"libcore.so": _elf_only_snapshot("libcore.so", "new")}

        result = analyze_bundle(
            old,
            new,
            [_diff("libcore.so"), _diff("libconsumer.so")],
            old_signature_evidence=old_evidence,
            new_signature_evidence=new_evidence,
        )

        assert len(result.analysis_errors) == 1
        assert "synthetic compare_bundle failure" in result.analysis_errors[0]
        # The Phase 4 stage is independent of compare_bundle()'s own
        # success/failure -- it still ran and still found its finding, even
        # though the graph-native stage raised.
        assert any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED
            for f in result.bundle_findings
        )
        # Codex review (P1): a compare_bundle() failure must not discard
        # already-computed per-library verdicts, or `.verdict`/
        # `.per_library_verdict` silently read NO_CHANGE for a library that
        # was e.g. BREAKING -- a false-green aggregate.
        assert len(result.per_library) == 2
        assert {r.library for r in result.per_library} == {
            "libcore.so",
            "libconsumer.so",
        }

    def test_signature_evidence_failure_does_not_lose_compare_bundle_findings(
        self, monkeypatch
    ) -> None:
        def _boom(*args, **kwargs):
            raise RuntimeError("synthetic signature-evidence failure")

        monkeypatch.setattr(sig_mod, "find_unverified_signature_findings", _boom)

        old_metadata = {
            "libcore.so": _meta(soname="libcore.so", exports=["core_fn"]),
            "libconsumer.so": _meta(
                soname="libconsumer.so", needed=["libcore.so"], imports=["core_fn"]
            ),
        }
        old = _snapshot(old_metadata)
        # A real graph-native finding: libcore.so removed in the new
        # release, still needed by libconsumer.so.
        new_metadata = {
            "libconsumer.so": _meta(
                soname="libconsumer.so", needed=["libcore.so"], imports=["core_fn"]
            ),
        }
        new = _snapshot(new_metadata)
        old_evidence = {"libcore.so": _elf_only_snapshot("libcore.so", "old")}
        new_evidence = {"libcore.so": _elf_only_snapshot("libcore.so", "new")}

        result = analyze_bundle(
            old,
            new,
            [_diff("libconsumer.so")],
            old_signature_evidence=old_evidence,
            new_signature_evidence=new_evidence,
        )

        assert len(result.analysis_errors) == 1
        assert "synthetic signature-evidence failure" in result.analysis_errors[0]
        # compare_bundle()'s own findings survive a Phase 4 failure.
        assert any(
            f.kind == ChangeKind.BUNDLE_LIBRARY_REMOVED for f in result.bundle_findings
        )
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED
            for f in result.bundle_findings
        )

    def test_both_stages_failing_records_both_errors(self, monkeypatch) -> None:
        def _boom_compare(*args, **kwargs):
            raise RuntimeError("synthetic compare_bundle failure")

        def _boom_sig(*args, **kwargs):
            raise RuntimeError("synthetic signature-evidence failure")

        monkeypatch.setattr(bundle_mod, "compare_bundle", _boom_compare)
        monkeypatch.setattr(sig_mod, "find_unverified_signature_findings", _boom_sig)

        metadata = _bundle_metadata()
        old = _snapshot(metadata)
        new = _snapshot(metadata)
        old_evidence = {"libcore.so": _elf_only_snapshot("libcore.so", "old")}
        new_evidence = {"libcore.so": _elf_only_snapshot("libcore.so", "new")}

        result = analyze_bundle(
            old,
            new,
            [_diff("libcore.so"), _diff("libconsumer.so")],
            old_signature_evidence=old_evidence,
            new_signature_evidence=new_evidence,
        )

        assert result.bundle_findings == []
        assert len(result.analysis_errors) == 2
        joined = " ".join(result.analysis_errors)
        assert "synthetic compare_bundle failure" in joined
        assert "synthetic signature-evidence failure" in joined


class TestAnalyzeBundleForwardsPolicyFile:
    """Codex review on PR #883: a caller's ``policy_file`` reached
    per-library findings (``service.compare_snapshots``) but never
    ``BundleDiffResult.bundle_verdict`` -- the ``BUNDLE_*``-kind aggregate
    was always scored under the bare ``policy`` name alone. Pinned here at
    the ``analyze_bundle``/``compare_bundle`` boundary (the shared
    orchestrator both the live and stored-facts callers route through),
    and again below at the real ``compare_release_against_bundle_facts``
    entry point."""

    def test_policy_file_override_reaches_bundle_verdict(self) -> None:
        from abicheck.policy_file import PolicyFile

        # BUNDLE_LIBRARY_REMOVED requires a surviving consumer that actually
        # depended on the removed library's exports (bundle.py's own
        # `_detect_library_structural_changes` gate).
        old = _snapshot(_bundle_metadata())
        new_meta = {
            "libconsumer.so": _meta(
                soname="libconsumer.so", needed=["libcore.so"], imports=["core_fn"]
            )
        }
        new = _snapshot(new_meta)

        without_override = analyze_bundle(
            old, new, [_diff("libconsumer.so")],
        )
        found_kinds = {f.kind for f in without_override.bundle_findings}
        assert ChangeKind.BUNDLE_LIBRARY_REMOVED in found_kinds
        assert without_override.bundle_verdict == Verdict.BREAKING

        # Both findings (the removal itself, and the now-unresolved import
        # it necessarily causes) must be overridden for the aggregate to
        # actually change -- overriding only one still leaves the other's
        # default BREAKING classification in effect.
        pf = PolicyFile(
            overrides={kind: Verdict.COMPATIBLE for kind in found_kinds}
        )
        with_override = analyze_bundle(
            old, new, [_diff("libconsumer.so")], policy_file=pf,
        )
        assert {f.kind for f in with_override.bundle_findings} == found_kinds
        assert with_override.bundle_verdict == Verdict.COMPATIBLE
