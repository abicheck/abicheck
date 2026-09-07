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

Phases 2c and 2d are **closed**: ``compare`` now carries ``--since``/
``--changed-path`` (changed-path localization, ADR-043 D7's POI scoping rule)
and ``--abi3`` (the candidate-side stable-ABI audit, ADR-068 D3), so their two
entries were deleted from this registry in the same PR that landed them --
which is exactly what the registry is for.

The first row of Phase 2a is closed too: ``unversioned_exported_symbol`` now
runs automatically inside ``checker.compare()`` (``cross_source_checks``,
default ``True`` -- see ``workflows/cross_source_evolution.py``), reached by
every front end through ``compare()``'s ordinary path with no opt-in flag
(ADR-068 D4/D5). The other ten cross-source checks in this dict remain
unmigrated.

**This registry is the red set the migration must turn empty.** A test in
this package asserts, for each registered key, that the capability is
present under ``scan`` and absent under ``compare`` today. When a Phase 2
PR gives ``compare`` the capability, the corresponding parity test starts
asserting a contradiction (``scan`` and ``compare`` now agree) and fails
loudly until the entry below is deleted in that same PR — see
``test_gap_registry_contract.py``.

Do not add an entry here for a loss that isn't one of the originally listed
fifteen scan-only capabilities (eleven checks + pattern scan + preprocessor
scan + changed-path localization + abi3 audit; the last two are closed) —
an *unexplained* loss anywhere else is a real regression the harness must
fail on, not something to file away quietly. ``EXPECTED_GAPS`` (this dict)
is what ``runner.py``'s scan-vs-compare diff checks against;
``ALL_EXPECTED_GAPS`` below folds in ``NOT_YET_IMPLEMENTED_ANYWHERE`` too
(currently empty — ADR-068 Phase 1 item 2 landed the ``finding_evolution``
vocabulary it used to hold), purely for ``test_gap_registry_contract.py``'s
own completeness bookkeeping — never to decide whether a scan finding's
*absence* from `compare` is expected.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Shorthand for the eleven cross-source checks, all closed together
#: (docs/contribute/plans/one-comparison-product.md §3 #3-#5, #6.8 note,
#: §6 Phase 2a): "cross-source checks, per side, evolution-stated".
_PHASE_2A = "Phase 2a — cross-source checks become a compare pipeline stage (plan §6)"
_PHASE_2B = "Phase 2b — pattern + preprocessor scans move onto compare (plan §6)"


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
}

#: F-8/F-9 (plan §7) used to be tracked here as `finding_evolution`: the
#: `not_evaluated`/`resolved` evolution axis existed under neither tool.
#: ADR-068 Phase 1 item 2 landed the generic vocabulary (`checker_policy.
#: FindingEvolution`, `Change.evolution`/`DiffResult.resolved_findings`,
#: `policy.finding_evolution`'s correspondence primitive) -- see
#: `test_evolution_state_gap.py` for the now-real F-8/F-9 demonstration --
#: so this registry is empty until a genuinely new "neither tool has this at
#: all" gap appears. Kept as its own dict (rather than deleted outright)
#: because it is a structurally different kind of gap from EXPECTED_GAPS
#: (which is specifically "scan has it, compare doesn't") and a future gap
#: of this second kind should have an obvious place to register, per this
#: module's own docstring.
NOT_YET_IMPLEMENTED_ANYWHERE: dict[str, ExpectedGap] = {}


#: The complete *tracked* set: every scan-only capability, plus whatever
#: NOT_YET_IMPLEMENTED_ANYWHERE holds (currently nothing -- see its own
#: comment). `test_gap_registry_contract.py` pins this against `EXPECTED_GAPS`
#: alone while it's empty; `EXPECTED_GAPS` itself is down to thirteen (11
#: checks + pattern_scan + preprocessor_scan) since Phase 2c/2d deleted
#: changed_path_localization and abi3_audit on landing them. Used only for
#: registry-completeness bookkeeping -- `runner.py`'s scan-vs-compare diff
#: checks against `EXPECTED_GAPS` alone (see this module's docstring).
ALL_EXPECTED_GAPS: dict[str, ExpectedGap] = {
    **EXPECTED_GAPS,
    **NOT_YET_IMPLEMENTED_ANYWHERE,
}
