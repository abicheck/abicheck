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

"""P0.4 round-9 review findings, split out of ``test_analysis_assurance.py``
(``_extra``-style sibling, matching e.g. ``test_call_graph_extra.py``) purely
to stay under the AI-readiness file-size hard cap -- the parent file was
already at the 1500-line soft-limit WARN before these two findings' tests
were added, and the tests below (~260 lines) pushed it past the 2000-line
hard cap.

Finding 1: ``DiffResult.requested_depth`` was never populated by any front
end, so an explicit ``compare --depth source`` that never reached source
evidence could still report ``status="complete"``.

Finding 2: the round-8 asymmetric-confirmed-graph-family fix only detected a
fully DISJOINT family-set pair (``isdisjoint()``); a partially overlapping
pair (old confirms a superset of what new confirms) fell through to
``"complete"`` even though the uncovered family is only trusted on one side.

Duplicates the two small fixtures it needs (``_header_pair``/``_write``)
rather than importing them from the parent module -- every other
``_extra``-style sibling test file in this suite is self-contained the same
way, and there is no existing cross-test-module import to follow instead.

Finding 3 (PR #767 follow-up, P2 review ``discussion_r3787839902``): Finding
1's fix above was CLI-only (``cli_compare_helpers._report_compare_result``);
a direct ``service.run_compare_request()``/``CompareRequest`` caller -- the
typed Python API / MCP ``abi_compare`` entry point, which never routes
through that CLI helper -- still read ``requested_depth=None``/
``depth_satisfied=None`` for an explicit, successfully-reached
``CompareRequest.depth``. ``TestRequestedDepthPropagationSharedPipeline``
below is the direct-API-level regression the CLI-only test suite never
covered.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck import checker
from abicheck.analysis_assurance import compute_analysis_assurance
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import save_snapshot, snapshot_to_json


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int", visibility=Visibility.PUBLIC
    )


def _write(tmp_path: Path, old: AbiSnapshot, new: AbiSnapshot) -> tuple[Path, Path]:
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return old_p, new_p


def _header_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """A real, header-scoped, unchanged pair -- resolves a clean public
    surface, so ``scope_resolved`` is True and ``status`` should read
    ``"complete"`` with no gating flag involved. Mirrors
    ``test_analysis_assurance._header_pair`` exactly.
    """
    common = {"library": "libfoo.so.1", "from_headers": True}
    fns = [_fn("pub_a", "_Z5pub_av")]
    return (
        AbiSnapshot(version="1.0", functions=fns, **common),
        AbiSnapshot(version="2.0", functions=fns, **common),
    )


class TestRequestedDepthPropagation:
    """Round-9 review, Finding 1: the CLI's own resolved ``--depth`` flag
    was never copied onto ``DiffResult.requested_depth`` before
    ``analysis_assurance`` read it, so an explicit ``compare --depth
    source`` that never actually reached source evidence (both sides lack
    a compile database, say -- the effective depth stays ``headers``)
    silently read ``requested_depth=None``/``depth_satisfied=None`` and
    could still report ``status="complete"``, letting
    ``--require-complete-analysis`` exit 0 despite the explicitly requested
    depth never being reached.
    """

    def test_unit_level_unsatisfied_requested_depth_is_failed(self) -> None:
        """Direct unit-level check: compute_analysis_assurance reads
        DiffResult.requested_depth (now populated by the CLI) and gates on
        it -- headers-only evidence never satisfies an explicit 'source'
        request."""
        old, new = _header_pair()
        result = checker.compare(old, new)
        assert result.analysis_assurance.status == "complete"  # sanity: no gap yet
        result.requested_depth = "source"
        aa = compute_analysis_assurance(result, old, new)
        assert aa.requested_depth == "source"
        assert aa.effective_depth == "headers"
        assert aa.depth_satisfied is False
        assert aa.status == "failed"
        assert any("requested depth 'source' not reached" in n for n in aa.notes), (
            aa.notes
        )

    def test_unit_level_satisfied_requested_depth_stays_complete(self) -> None:
        """Companion positive case: an explicit requested depth that IS
        reached must not regress to a non-complete status."""
        old, new = _header_pair()
        result = checker.compare(old, new)
        result.requested_depth = "headers"
        aa = compute_analysis_assurance(result, old, new)
        assert aa.requested_depth == "headers"
        assert aa.effective_depth == "headers"
        assert aa.depth_satisfied is True
        assert aa.status == "complete"

    def test_cli_depth_source_with_no_compile_database_is_not_complete(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: `compare --depth source` on two plain header-scoped
        JSON snapshots, with no --sources/--build-info given at all (so no
        L4/L5 evidence is ever collected and the effective depth stays
        'headers') must not read status='complete'."""
        old, new = _header_pair()
        old_p, new_p = _write(tmp_path, old, new)

        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--depth",
                "source",
                "--format",
                "json",
            ],
        )
        assert res.exit_code in (0, 1, 2, 4), res.output
        payload = json.loads(res.output[res.output.index("{") :])
        aa = payload["analysis_assurance"]
        assert aa["requested_depth"] == "source", aa
        assert aa["effective_depth"] == "headers", aa
        assert aa["depth_satisfied"] is False, aa
        assert aa["status"] == "failed", aa

    def test_require_complete_analysis_exits_nonzero_for_unsatisfied_depth(
        self, tmp_path: Path
    ) -> None:
        old, new = _header_pair()
        old_p, new_p = _write(tmp_path, old, new)

        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--depth",
                "source",
                "--require-complete-analysis",
            ],
        )
        assert res.exit_code != 0, res.output

    def test_depth_omitted_does_not_populate_requested_depth(
        self, tmp_path: Path
    ) -> None:
        """No --depth flag at all must leave requested_depth None (never an
        inferred value), matching the pre-existing default behavior for
        every caller that doesn't opt into an explicit depth request."""
        old, new = _header_pair()
        old_p, new_p = _write(tmp_path, old, new)

        res = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--format", "json"],
        )
        assert res.exit_code in (0, 1, 2, 4), res.output
        payload = json.loads(res.output[res.output.index("{") :])
        aa = payload["analysis_assurance"]
        assert aa["requested_depth"] is None, aa
        assert aa["status"] == "complete", aa


class TestRequestedDepthPropagationSharedPipeline:
    """PR #767 follow-up (P2 review, ``discussion_r3787839902``): the CLI-only
    fix above (``cli_compare_helpers._report_compare_result``) left the
    shared ``service_compare_pipeline.classify_compare_pair`` -- what
    ``service.run_compare_request()`` (the typed Python API / MCP
    ``abi_compare`` entry point) actually runs -- never populating either
    ``DiffResult.requested_depth`` or ``analysis_assurance.depth_satisfied``
    for a direct caller's explicit ``CompareRequest.depth``.
    """

    def _snapshot_files(self, tmp_path: Path) -> tuple[Path, Path]:
        old, new = _header_pair()
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        save_snapshot(old, old_p)
        save_snapshot(new, new_p)
        return old_p, new_p

    def test_direct_api_satisfied_depth_populates_requested_depth(
        self, tmp_path: Path
    ) -> None:
        """The exact repro from the review finding: a direct
        ``run_compare_request(CompareRequest(..., depth="headers"))`` call on
        two plain header-scoped snapshots reaches 'headers' evidence (so the
        request IS satisfied) -- before this fix, the returned result still
        silently read ``requested_depth=None``/``depth_satisfied=None``
        despite the caller's explicit, successfully-reached request."""
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.service import run_compare_request

        old_p, new_p = self._snapshot_files(tmp_path)
        request = CompareRequest(
            old=InputSpec.of(old_p), new=InputSpec.of(new_p), depth="headers"
        )
        result = run_compare_request(request).diff

        assert result.requested_depth == "headers", result
        aa = result.analysis_assurance
        assert aa.requested_depth == "headers", aa
        assert aa.effective_depth == "headers", aa
        assert aa.depth_satisfied is True, aa
        assert aa.status == "complete", aa

    def test_direct_api_uppercase_depth_is_normalized(self, tmp_path: Path) -> None:
        """PR #767 follow-up (P2 review, ``service_compare_pipeline.py:476``):
        ``CompareRequest.validation_errors()`` (``_depth_errors`` in
        ``api_types.py``) accepts ``depth`` case-insensitively
        (``depth.lower() in USER_DEPTHS``), so a direct caller passing
        ``depth="HEADERS"`` reaches classification successfully. Before this
        fix, ``classify_compare_pair`` stamped the raw, un-normalized
        ``request.depth`` onto ``DiffResult.requested_depth``, which broke
        every downstream reader expecting the lowercase
        ``EVIDENCE_DEPTH_VALUES`` spelling -- most visibly,
        ``validate_evidence_depth()`` (every JSON reporter) raised
        ``ValueError`` outright. This must fail against the pre-fix code."""
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.checker_types import validate_evidence_depth
        from abicheck.reporter import to_json
        from abicheck.service import run_compare_request

        old_p, new_p = self._snapshot_files(tmp_path)
        request = CompareRequest(
            old=InputSpec.of(old_p), new=InputSpec.of(new_p), depth="HEADERS"
        )
        result = run_compare_request(request).diff

        assert result.requested_depth == "headers", result
        aa = result.analysis_assurance
        assert aa.requested_depth == "headers", aa
        assert aa.effective_depth == "headers", aa
        assert aa.depth_satisfied is True, aa
        assert aa.status == "complete", aa

        # Does not raise -- pre-fix, the uppercase requested_depth crashed
        # validate_evidence_depth() / every JSON reporter.
        validate_evidence_depth("requested_depth", result.requested_depth)
        to_json(result)

    def test_direct_api_no_depth_leaves_requested_depth_none(
        self, tmp_path: Path
    ) -> None:
        """Companion negative case: a caller that never sets
        ``CompareRequest.depth`` must see the identical, unaffected
        ``requested_depth=None`` behavior as before this fix."""
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.service import run_compare_request

        old_p, new_p = self._snapshot_files(tmp_path)
        request = CompareRequest(old=InputSpec.of(old_p), new=InputSpec.of(new_p))
        result = run_compare_request(request).diff

        assert result.requested_depth is None, result
        assert result.analysis_assurance.requested_depth is None
        assert result.analysis_assurance.depth_satisfied is None
        assert result.analysis_assurance.status == "complete"

    def test_classify_compare_pair_unsatisfied_depth_is_not_complete(
        self, tmp_path: Path
    ) -> None:
        """Direct unit-level check of the fixed function itself
        (``classify_compare_pair``), bypassing ``resolve_compare_request``'s
        own separate ``enforce_requested_depth`` hard-fail (a request whose
        depth is unreachable from a plain JSON-snapshot resolve raises
        ``ValidationError`` before classification is ever reached -- see
        ``service_input_resolution.enforce_requested_depth``) so the
        'requested but not satisfied' half of this fix's own gate
        (``analysis_assurance.status != 'complete'``) is exercised the same
        way the CLI-fix's own
        ``test_unit_level_unsatisfied_requested_depth_is_failed`` above
        exercises it -- by constructing an already-resolved pair directly
        rather than going through the full resolve step."""
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.service_compare_evidence import SideEvidence
        from abicheck.service_compare_pipeline import (
            ResolvedComparePair,
            classify_compare_pair,
        )

        old, new = _header_pair()
        old_p, new_p = self._snapshot_files(tmp_path)
        request = CompareRequest(
            old=InputSpec.of(old_p),
            new=InputSpec.of(new_p),
            depth="source",
        )
        evidence = SideEvidence(
            headers=[], compile=None, collect_mode="off", dump_manifest=None
        )
        pair = ResolvedComparePair(
            old=old,
            new=new,
            old_fmt=None,
            new_fmt=None,
            old_evidence=evidence,
            new_evidence=evidence,
        )
        result = classify_compare_pair(request, pair).diff

        assert result.requested_depth == "source", result
        aa = result.analysis_assurance
        assert aa.requested_depth == "source", aa
        assert aa.effective_depth == "headers", aa
        assert aa.depth_satisfied is False, aa
        assert aa.status != "complete", aa

    def test_classify_compare_pair_reads_requested_depth_from_resolved_execution_context(
        self, tmp_path: Path
    ) -> None:
        """ADR-063 Track 3 (One Semantic Pipeline plan, sub-phase 4B's first
        real consumer): ``classify_compare_pair`` must stamp ``DiffResult.
        requested_depth`` off ``pair.resolved_execution_context.
        requested_depth`` when one is attached, rather than re-normalizing
        ``request.depth`` a second time. Proven the only way that is
        distinguishable from "both happen to agree": attach a context whose
        own ``requested_depth`` deliberately disagrees with what
        ``request.depth.lower()`` would independently compute -- the result
        must follow the object, not the re-derivation."""
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.service_compare_evidence import SideEvidence
        from abicheck.service_compare_pipeline import (
            ResolvedComparePair,
            classify_compare_pair,
        )
        from abicheck.workflows.resolved_execution_context import (
            EvidenceView,
            ResolvedExecutionContext,
        )

        old, new = _header_pair()
        old_p, new_p = self._snapshot_files(tmp_path)
        request = CompareRequest(
            old=InputSpec.of(old_p),
            new=InputSpec.of(new_p),
            depth="source",
        )
        evidence = SideEvidence(
            headers=[], compile=None, collect_mode="off", dump_manifest=None
        )
        context = ResolvedExecutionContext(
            operation="compare",
            evidence=EvidenceView.for_request("headers"),
        )
        pair = ResolvedComparePair(
            old=old,
            new=new,
            old_fmt=None,
            new_fmt=None,
            old_evidence=evidence,
            new_evidence=evidence,
            resolved_execution_context=context,
        )
        result = classify_compare_pair(request, pair).diff

        # Follows the attached context's own value ("headers"), not
        # request.depth.lower() ("source") -- the re-derivation this fix
        # retires.
        assert result.requested_depth == "headers", result


class TestGraphCompletenessPartialFamilyOverlap:
    """Round-9 review, Finding 2: the round-8 fix only flagged a fully
    DISJOINT confirmed-family pair (``isdisjoint()``); a partially
    overlapping pair -- old confirmed {call_graph, type_graph}, new
    confirmed only {call_graph} -- is not disjoint (they share
    call_graph), so it fell through to "complete" even though
    buildsource/source_graph_findings.py only trusts a family when BOTH
    sides cover it, meaning type_graph was silently skipped for this
    comparison.
    """

    def _pack_with_graph(self, tmp_path: Path, name: str, graph) -> object:
        from abicheck.buildsource.pack import BuildSourcePack

        return BuildSourcePack(root=tmp_path / name, source_graph=graph)

    def test_partial_overlap_confirmed_families_is_not_complete(
        self, tmp_path: Path
    ) -> None:
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = _header_pair()
        old.build_source = self._pack_with_graph(
            tmp_path,
            "old_pack",
            SourceGraphSummary(
                extractor_passes={"call_graph": True, "type_graph": True}
            ),
        )
        new.build_source = self._pack_with_graph(
            tmp_path,
            "new_pack",
            SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness != "complete", aa
        assert aa.graph_completeness == "unknown", aa
        assert aa.status == "partial", aa
        assert any("confirmed on only one side" in n for n in aa.notes), aa.notes
        assert any("type_graph" in n for n in aa.notes), aa.notes

    def test_require_complete_analysis_exits_nonzero_for_partial_overlap(
        self, tmp_path: Path
    ) -> None:
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = _header_pair()
        old.build_source = self._pack_with_graph(
            tmp_path,
            "old_pack",
            SourceGraphSummary(
                extractor_passes={"call_graph": True, "type_graph": True}
            ),
        )
        new.build_source = self._pack_with_graph(
            tmp_path,
            "new_pack",
            SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        old_p, new_p = _write(tmp_path, old, new)

        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--require-complete-analysis",
            ],
        )
        assert res.exit_code != 0, res.output

    def test_disjoint_confirmed_families_still_asymmetric(self, tmp_path: Path) -> None:
        """Companion: round 8's original disjoint case must not regress
        under the new inequality-based check."""
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = _header_pair()
        old.build_source = self._pack_with_graph(
            tmp_path,
            "old_pack",
            SourceGraphSummary(
                extractor_passes={
                    "header_call_graph": True,
                    "header_type_graph": True,
                }
            ),
        )
        new.build_source = self._pack_with_graph(
            tmp_path,
            "new_pack",
            SourceGraphSummary(
                extractor_passes={"call_graph": True, "type_graph": True}
            ),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "unknown", aa
        assert aa.status == "partial", aa
        assert any("confirmed on only one side" in n for n in aa.notes), aa.notes

    def test_identical_confirmed_families_still_complete(self, tmp_path: Path) -> None:
        """Companion: both sides confirming the EXACT same family set must
        still read complete -- the new inequality check must not regress
        the ordinary, matching-family case."""
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = _header_pair()
        old.build_source = self._pack_with_graph(
            tmp_path,
            "old_pack",
            SourceGraphSummary(
                extractor_passes={"call_graph": True, "type_graph": True}
            ),
        )
        new.build_source = self._pack_with_graph(
            tmp_path,
            "new_pack",
            SourceGraphSummary(
                extractor_passes={"call_graph": True, "type_graph": True}
            ),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "complete", aa
        assert aa.status == "complete", aa

    def test_unit_level_isdisjoint_vs_inequality_direct(self, tmp_path: Path) -> None:
        """Direct unit-level check of _graph_completeness itself, isolating
        the predicate from the rest of the rollup."""
        from abicheck.analysis_assurance import _graph_completeness
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old_pack = BuildSourcePack(
            root=tmp_path / "old",
            source_graph=SourceGraphSummary(
                extractor_passes={"call_graph": True, "type_graph": True}
            ),
        )
        new_pack = BuildSourcePack(
            root=tmp_path / "new",
            source_graph=SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        status, notes = _graph_completeness(old_pack, new_pack)
        assert status == "unknown"
        assert any("confirmed on only one side" in n for n in notes), notes


class TestExportAccountingDoesNotDoubleCount:
    """lab/Codex review, fresh evidence: an export classified into
    ``non_public_symbol_to_reason`` (dependency/internal/own -- ACCOUNTED
    for, per ``_classify_non_public_exports``'s own docstring) must not
    ALSO count toward ``export_accounting.unaccounted``.
    ``source_link.link_source_abi`` previously left a classified symbol in
    ``symbols_without_decl`` too, so a comparison where every export on both
    sides was fully classified as non-public still reported
    ``unaccounted == total`` instead of ``0``, keeping
    ``--require-complete-analysis`` stuck at ``status="partial"`` for no real
    gap (reported against a real Bazel lab project: 6 exports/side, all
    non-public, ``internal=6, unaccounted=6`` for a 12-export total).
    """

    def test_end_to_end_via_real_link_source_abi(self, tmp_path: Path) -> None:
        """Exercises the real ``link_source_abi`` entry point end to end on
        both sides -- not a hand-built ``SourceAbiSurface`` with
        already-disjoint fields, which can't reproduce this bug."""
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiTu
        from abicheck.buildsource.source_link import link_source_abi

        # Every one of these is classified by _classify_non_public_exports
        # with no library/source-namespace context needed (dependency:stdlib,
        # dependency:tbb, internal_or_private_export) -- so symbols_without_
        # decl comes back genuinely empty once the fix is in place.
        exports = [
            "_ZNSt6vectorIiSaIiEEC1Ev",
            "_ZN3tbb6detail2r13fooEv",
            "_private_c",
        ]
        old, new = _header_pair()
        for snap in (old, new):
            surface = link_source_abi([SourceAbiTu()], exported_symbols=exports)
            assert surface.unmatched["symbols_without_decl"] == []
            snap.build_source = BuildSourcePack(
                root=tmp_path / "nonexistent-pack-no-double-count",
                source_abi=surface,
            )

        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.export_accounting.total == len(exports) * 2
        assert aa.export_accounting.internal == len(exports) * 2
        assert aa.export_accounting.unaccounted == 0
        # Every export here is classified non-public -- none is genuinely
        # linked to a public source declaration, so source_linked must be 0,
        # not the total (Codex review: source_linked = total - unaccounted
        # alone silently double-counted internal exports as ALSO
        # source_linked once unaccounted correctly excluded them).
        assert aa.export_accounting.source_linked == 0


class TestExportAccountingDedupsLegacyPersistedOverlap:
    """Codex review, PR #788, round 2: the rollup fix above assumed every
    ``SourceAbiSurface`` it sees was produced by the FIXED
    ``link_source_abi``/``relink_surface_exports`` (which no longer leaves a
    classified symbol in ``symbols_without_decl`` too). A surface persisted
    by a pre-fix build still carries that stale overlap after round-tripping
    through ``SourceAbiSurface.from_dict()`` -- the ordinary "compare an
    existing baseline JSON against a fresh candidate" upgrade workflow -- so
    counting ``len(symbols_without_decl)`` and ``len(non_public_symbol_to_
    reason)`` independently double-subtracted the same symbol from ``total``.
    """

    def test_legacy_overlapping_surface_is_deduplicated_at_rollup(
        self, tmp_path: Path
    ) -> None:
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        old, new = _header_pair()
        # Hand-built to reproduce the pre-fix persisted shape directly --
        # "_Z6privXv" sits in BOTH symbols_without_decl AND
        # non_public_symbol_to_reason, exactly what SourceAbiSurface.
        # from_dict() would still carry for a snapshot written before the
        # link_source_abi double-count fix.
        surface = SourceAbiSurface(
            coverage={"compile_units_selected": 1, "compile_units_parsed": 1},
            roots={
                "exported_symbols": ["_Z5pub_av", "_Z6privXv"],
                "public_header_declarations": [],
                "forced_public": [],
            },
            unmatched={
                "symbols_without_decl": ["_Z6privXv"],
                "decls_without_symbol": [],
            },
            mappings={
                "source_decl_to_binary_symbol": {},
                "source_type_to_debug_type": {},
                "public_header_to_target": {},
                "synthesized_symbol_to_owner": {},
                "template_instantiation_symbol_to_decl": {},
                "allocator_interposer_symbol_to_owner": {},
                "non_public_symbol_to_reason": {"_Z6privXv": "internal"},
            },
        )
        for snap in (old, new):
            snap.build_source = BuildSourcePack(
                root=tmp_path / "legacy-overlapping-pack", source_abi=surface,
            )

        result = checker.compare(old, new)
        aa = result.analysis_assurance
        # 2 exports/side x 2 sides == 4 total; "_Z6privXv" is genuinely
        # classified internal on each side and must count ONLY as internal,
        # not also as unaccounted -- the legacy overlap must not survive
        # the rollup.
        assert aa.export_accounting.total == 4
        assert aa.export_accounting.internal == 2
        assert aa.export_accounting.unaccounted == 0
        assert aa.export_accounting.source_linked == 2
class TestGraphCompletenessConditionallyApplicableFamily:
    """PR #767 follow-up (P2 review): the round-9 ``!=`` fix above (Finding 2
    in that round) is too aggressive for a *conditionally applicable* pass
    family. ``fold_archive_graph`` (``inline_graph_fold.py``) only ever
    records ``archive_graph`` when the graph carries at least one
    ``static_library`` node -- it returns silently, with no
    ``ExtractorRecord`` at all, when there is nothing to introspect. A
    release that drops its last static-library link input entirely (old side
    confirmed ``archive_graph``, new side genuinely has no
    ``static_library`` node to examine) is a legitimate difference between
    the two builds, not evidence asymmetry, and must not read as
    ``graph_completeness == "unknown"``.
    """

    def _pack_with_graph(self, tmp_path: Path, name: str, graph) -> object:
        from abicheck.buildsource.pack import BuildSourcePack

        return BuildSourcePack(root=tmp_path / name, source_graph=graph)

    def test_archive_graph_correctly_inapplicable_on_one_side_is_complete(
        self, tmp_path: Path
    ) -> None:
        """The exact repro from the review finding: old confirms
        ``archive_graph`` because it linked a static library; new links no
        static library at all, so the pass correctly never ran there. This
        must read as complete, not unknown."""
        from abicheck.analysis_assurance import _graph_completeness
        from abicheck.buildsource.graph_facts import GraphNode
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old_pack = BuildSourcePack(
            root=tmp_path / "old",
            source_graph=SourceGraphSummary(
                nodes=[
                    GraphNode(id="static_library://libfoo.a", kind="static_library")
                ],
                extractor_passes={"archive_graph": True, "call_graph": True},
            ),
        )
        new_pack = BuildSourcePack(
            root=tmp_path / "new",
            source_graph=SourceGraphSummary(
                nodes=[], extractor_passes={"call_graph": True}
            ),
        )
        status, notes = _graph_completeness(old_pack, new_pack)
        assert status == "complete", notes

    def test_archive_graph_correctly_inapplicable_end_to_end(
        self, tmp_path: Path
    ) -> None:
        """Same repro through the full ``checker.compare()`` rollup, not just
        the unit-level ``_graph_completeness`` call."""
        from abicheck.buildsource.graph_facts import GraphNode
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = _header_pair()
        old.build_source = self._pack_with_graph(
            tmp_path,
            "old_pack",
            SourceGraphSummary(
                nodes=[
                    GraphNode(id="static_library://libfoo.a", kind="static_library")
                ],
                extractor_passes={"archive_graph": True, "call_graph": True},
            ),
        )
        new.build_source = self._pack_with_graph(
            tmp_path,
            "new_pack",
            SourceGraphSummary(nodes=[], extractor_passes={"call_graph": True}),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "complete", aa
        assert aa.status == "complete", aa

    def test_archive_graph_genuine_asymmetry_still_detected(
        self, tmp_path: Path
    ) -> None:
        """Companion: BOTH sides have a static_library link input (so the
        pass is applicable on both), but only one side actually confirmed
        the pass ran. This is a real evidence gap and must still read as
        unknown/asymmetric -- the conditional-applicability exemption must
        not over-correct into silencing a genuine shortfall for this
        family."""
        from abicheck.analysis_assurance import _graph_completeness
        from abicheck.buildsource.graph_facts import GraphNode
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old_pack = BuildSourcePack(
            root=tmp_path / "old",
            source_graph=SourceGraphSummary(
                nodes=[
                    GraphNode(id="static_library://libfoo.a", kind="static_library")
                ],
                extractor_passes={"archive_graph": True},
            ),
        )
        new_pack = BuildSourcePack(
            root=tmp_path / "new",
            source_graph=SourceGraphSummary(
                nodes=[
                    GraphNode(id="static_library://libfoo.a", kind="static_library")
                ],
                extractor_passes={},
            ),
        )
        status, notes = _graph_completeness(old_pack, new_pack)
        assert status == "unknown", notes
        assert any("archive_graph" in n for n in notes), notes

    def test_archive_graph_genuine_asymmetry_end_to_end(self, tmp_path: Path) -> None:
        """End-to-end companion to the genuine-asymmetry unit test above."""
        from abicheck.buildsource.graph_facts import GraphNode
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = _header_pair()
        old.build_source = self._pack_with_graph(
            tmp_path,
            "old_pack",
            SourceGraphSummary(
                nodes=[
                    GraphNode(id="static_library://libfoo.a", kind="static_library")
                ],
                extractor_passes={"archive_graph": True},
            ),
        )
        new.build_source = self._pack_with_graph(
            tmp_path,
            "new_pack",
            SourceGraphSummary(
                nodes=[
                    GraphNode(id="static_library://libfoo.a", kind="static_library")
                ],
                extractor_passes={},
            ),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "unknown", aa
        assert aa.status == "partial", aa

    def test_unconditional_family_absent_on_both_sides_still_asymmetric(
        self, tmp_path: Path
    ) -> None:
        """Sanity check that the exemption is scoped to
        conditionally-applicable families only: an ordinary,
        unconditionally-attempted family (``call_graph``) confirmed on one
        side and not the other must still read as asymmetric -- there is no
        node-presence signal that could ever exempt it."""
        from abicheck.analysis_assurance import _graph_completeness
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old_pack = BuildSourcePack(
            root=tmp_path / "old",
            source_graph=SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        new_pack = BuildSourcePack(
            root=tmp_path / "new",
            source_graph=SourceGraphSummary(extractor_passes={}),
        )
        status, notes = _graph_completeness(old_pack, new_pack)
        assert status == "unknown", notes
        assert any("call_graph" in n for n in notes), notes
