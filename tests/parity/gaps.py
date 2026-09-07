# SPDX-License-Identifier: Apache-2.0
"""The expected-gap registry — the migration's own definition of done.

``docs/contribute/adr/068-one-comparison-product-and-scan-retirement.md``
identifies the capabilities ``compare`` cannot reach today: the eleven
cross-source checks (``abicheck/buildsource/crosscheck.py``), the lexical
pattern pre-scan (``pattern_scan.py``), the preprocessor scan
(``preprocessor_scan.py``), changed-path localization, and the ``abi3``
audit. Every one of those is registered here, each with the plan phase
that is expected to close it (``docs/contribute/plans/one-comparison-product.md``
§6) — never a bare ``xfail``, so a reader always has a name and a phase to
check rather than a silent skip.

**This registry is the red set the migration must turn empty.** A test in
this package asserts, for each registered key, that the capability is
present under ``scan`` and absent under ``compare`` today. When a Phase 2
PR gives ``compare`` the capability, the corresponding parity test starts
asserting a contradiction (``scan`` and ``compare`` now agree) and fails
loudly until the entry below is deleted in that same PR — see
``test_gap_registry_contract.py``.

Do not add an entry here for a loss that isn't one of the fifteen listed
scan-only capabilities (eleven checks + pattern scan + preprocessor scan +
changed-path localization + abi3 audit) — an *unexplained* loss anywhere
else is a real regression the harness must fail on, not something to file
away quietly. ``EXPECTED_GAPS`` (this dict) is what ``runner.py``'s
scan-vs-compare diff checks against; ``ALL_EXPECTED_GAPS`` below folds in
the separate ``finding_evolution`` tracking entry too, purely for
``test_gap_registry_contract.py``'s own completeness bookkeeping — it is
never used to decide whether a scan finding's *absence* from `compare` is
expected, since ``finding_evolution`` is not a real ``Finding.kind`` any
scan-side result could ever carry.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Shorthand for the eleven cross-source checks, all closed together
#: (docs/contribute/plans/one-comparison-product.md §3 #3-#5, #6.8 note,
#: §6 Phase 2a): "cross-source checks, per side, evolution-stated".
_PHASE_2A = "Phase 2a — cross-source checks become a compare pipeline stage (plan §6)"
_PHASE_2B = "Phase 2b — pattern + preprocessor scans move onto compare (plan §6)"
_PHASE_2C = "Phase 2c — changed-path localization (--since/--changed-path) (plan §6)"
_PHASE_2D = "Phase 2d — abi3 candidate-side enrichment (plan §6)"
_PHASE_1 = "Phase 1 — FindingEvolution state on the canonical finding model (plan §6)"


@dataclass(frozen=True)
class ExpectedGap:
    """One capability ``compare`` cannot reach yet."""

    #: Why the gap exists today (mirrors ADR-068 §1's call-site finding).
    reason: str
    #: The plan phase (docs/contribute/plans/one-comparison-product.md §6)
    #: expected to close it. Not a promise of *when* — just *which* PR.
    plan_phase: str
    #: The table row in the plan's §3 capability map, for cross-reference.
    plan_row: str


#: The eleven cross-source checks (buildsource/crosscheck.py). Verified by
#: call site (ADR-068 §1): their only production caller anywhere under
#: abicheck/ is scan_engine.py.
_CROSSCHECK_REASON = (
    "cross-source check (abicheck/buildsource/crosscheck.py); its only "
    "production caller under abicheck/ is scan_engine.py (ADR-068 §1)"
)

EXPECTED_GAPS: dict[str, ExpectedGap] = {
    "exported_not_public": ExpectedGap(_CROSSCHECK_REASON, _PHASE_2A, "§3 #3/#5"),
    "public_not_exported": ExpectedGap(_CROSSCHECK_REASON, _PHASE_2A, "§3 #3/#5"),
    "header_build_context_mismatch": ExpectedGap(
        _CROSSCHECK_REASON, _PHASE_2A, "§3 #3/#10"
    ),
    "private_header_leak": ExpectedGap(_CROSSCHECK_REASON, _PHASE_2A, "§3 #3/#4"),
    "odr_type_variant": ExpectedGap(_CROSSCHECK_REASON, _PHASE_2A, "§3 #3"),
    "public_to_internal_dependency": ExpectedGap(
        _CROSSCHECK_REASON, _PHASE_2A, "§3 #3"
    ),
    "unversioned_exported_symbol": ExpectedGap(_CROSSCHECK_REASON, _PHASE_2A, "§3 #3"),
    "rtti_for_internal_type": ExpectedGap(_CROSSCHECK_REASON, _PHASE_2A, "§3 #3"),
    "identity_collision_detected": ExpectedGap(_CROSSCHECK_REASON, _PHASE_2A, "§3 #3"),
    "compile_context_conflict": ExpectedGap(_CROSSCHECK_REASON, _PHASE_2A, "§3 #3/#10"),
    "source_surface_dso_mismatch": ExpectedGap(_CROSSCHECK_REASON, _PHASE_2A, "§3 #3"),
    "pattern_scan": ExpectedGap(
        "lexical pattern pre-scan (abicheck/buildsource/pattern_scan.py); its "
        "only production caller under abicheck/ is scan_engine.py (ADR-068 §1)",
        _PHASE_2B,
        "§3 #6",
    ),
    "preprocessor_scan": ExpectedGap(
        "preprocessor scan (abicheck/buildsource/preprocessor_scan.py); its "
        "only production caller under abicheck/ is scan_engine.py (ADR-068 §1)",
        _PHASE_2B,
        "§3 #8",
    ),
    "changed_path_localization": ExpectedGap(
        "--since/--changed-path exist only on `scan` (cli_scan.py, "
        "buildsource/poi.py); `compare` has no such option",
        _PHASE_2C,
        "§3 #12",
    ),
    "abi3_audit": ExpectedGap(
        "--abi3 single-artifact stable-ABI audit exists only on `scan` "
        "(scan_abi3_resolve.py, scan_engine._run_abi3_audit); `compare` has "
        "no --abi3 option at all",
        _PHASE_2D,
        "§3 #15",
    ),
}

#: F-8/F-9 (plan §7): the `not_evaluated`/`resolved` evolution axis for a
#: one-sided check re-run across a baseline. This is not a scan-vs-compare
#: gap at all — neither tool has this vocabulary today (`FindingEvolution`
#: is *new* Phase-1 work, not a migrated capability) — so it is tracked
#: separately from EXPECTED_GAPS, which is specifically "scan has it,
#: compare doesn't".
NOT_YET_IMPLEMENTED_ANYWHERE: dict[str, ExpectedGap] = {
    "finding_evolution": ExpectedGap(
        "no FindingEvolution state (introduced/resolved/persistent/"
        "not_evaluated) exists on the canonical finding model under either "
        "`scan` or `compare` today; scan's own crosscheck pass is single-"
        "sided even with --against and does not distinguish a pre-existing "
        "issue from a newly introduced one",
        _PHASE_1,
        "§5 P2 / D3",
    ),
}


#: The complete *tracked* set: every scan-only capability plus the
#: separately-tracked F-8/F-9 evolution gap. `test_gap_registry_contract.py`
#: pins this at exactly sixteen (11 checks + pattern_scan + preprocessor_scan
#: + changed_path_localization + abi3_audit + finding_evolution). Used only
#: for that registry-completeness bookkeeping -- `runner.py`'s scan-vs-compare
#: diff checks against `EXPECTED_GAPS` alone (see this module's docstring).
ALL_EXPECTED_GAPS: dict[str, ExpectedGap] = {
    **EXPECTED_GAPS,
    **NOT_YET_IMPLEMENTED_ANYWHERE,
}
