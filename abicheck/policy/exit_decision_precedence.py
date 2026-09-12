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

"""ADR-064's additive scan/release exit-decision resolvers.

Split out of :mod:`abicheck.policy.exit_decision` (Codex review: the
combined module grew to 824 lines against this package's 800-line
production cap -- `abicheck/policy/AGENTS.md`'s own "Conventions" section).
New module, not a moved flat-path shim -- there is no legacy
``abicheck.exit_decision_precedence`` to preserve, since these functions
did not exist before ADR-064 -- but its own public names (``resolve_scan_
exit_decision``, ``resolve_release_exit_decision``) still reach
``abicheck.exit_decision``'s flat shim via a re-export, per that module's
"re-export the moved module's full public surface" contract, since the two
functions moved *out of* the module the shim mirrors.

See ``exit_decision.py``'s own module docstring for what ADR-064 is and
what stage this additive work belongs to; this module implements the same
stage 1a resolvers, just physically split for the line-count cap. Stage 1b
has since wired `resolve_scan_exit_decision` into real call sites --
`scan_engine.py`'s `NOT_COMPARABLE` outcome and (via
`abicheck.workflows.scan_abort_result`) its `_BudgetOverflow`/
`_EvidenceContractError` aborts -- so this module is no longer additive-only
dead code the way it was when first split out.

`docs/contribute/plans/one-comparison-product.md` Phase 8 (ADR-068 D6):
`deps compare`/`deps tree` needed no new resolver *here* -- unlike `scan`'s
axes, `deps`'s "not comparable" case (ADR-050 D2's profile/scope mismatch,
surfaced as `StackChange.not_comparable_reason`) and its independent
loadability axis both fold through the sibling `exit_decision.
resolve_exit_decision`'s existing generic contribution parameters
(`not_comparable_contribution`, the new `loadability_contribution`) with no
`_dominant_decision`-style override needed, since `deps`'s own numbers
(0/1/4/5) already sort correctly under a plain tie-inclusive `max()` fold.
See `stack_checker.exit_decision_for_stack_compare`/
`exit_decision_for_stack_tree` for the two call sites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .exit_decision import ExitDecision, ExitReason, resolve_exit_decision

if TYPE_CHECKING:
    from datetime import date

    from ..checker_types import DiffResult
    from .severity import SeverityConfig

#: Which of :class:`ExitDecision`'s four ADR-064 fields corresponds to each
#: dominant :class:`ExitReason`. `_dominant_decision` uses this so exactly
#: one of the four is ever set (to `code`), keeping `code == max()` over
#: every field literally true rather than merely true "by convention."
_DOMINANT_FIELD = {
    ExitReason.EVIDENCE_CONTRACT_ERROR: "evidence_contract_error_contribution",
    ExitReason.BUDGET_OVERFLOW: "budget_overflow_contribution",
    ExitReason.NOT_COMPARABLE: "not_comparable_contribution",
    ExitReason.REMOVED_REQUIRED_LIBRARY: "removed_required_library_contribution",
}


def _dominant_decision(
    code: int,
    reason: ExitReason,
    *,
    prior: ExitDecision | None = None,
    compatibility_contribution: int = 0,
    contract_coverage_contribution: int = 0,
    analysis_assurance_contribution: int = 0,
    evidence_contract_error_contribution: int = 0,
    removed_required_library_contribution: int = 0,
    operational_error_contribution: int = 0,
    incomplete_scope_contribution: int = 0,
    no_comparison_completed_contribution: int = 0,
) -> ExitDecision:
    """One of ADR-064's four axes overrides whatever the ordinary
    gate/coverage/assurance fold would otherwise have decided.

    Sets *reason*'s own dedicated field (`_DOMINANT_FIELD`) to *code* --
    the only one of the four ADR-064 fields this decision ever sets to
    nonzero -- so `ExitDecision`'s own `code == max()` invariant holds
    literally, not merely "by convention," even though this decision was
    reached by an early-return override rather than a flat fold.

    *prior* is the ordinary :class:`ExitDecision` already computed for this
    run before the dominant axis fired, when the caller has one available
    (e.g. `scan`'s budget check runs *after* a comparable baseline compare
    already built a full decision) -- its four PR-G1 contributions are
    carried through so a report reader can see what the gate/coverage/
    assurance/crosscheck axes actually were, even though they did not
    decide `code`. The three `*_contribution` keywords are a narrower
    escape hatch for a caller that has one or more already-computed
    *values* available but no full prior `ExitDecision` object to pass
    (the release fan-out's own removed-required-library and
    not-comparable branches, which receive the aggregated verdict/severity
    code and coverage floor as bare ints). Leave everything at its `0`
    default -- genuinely "not asked," not "evaluated and came out clean"
    -- only when nothing was computed at all (no `DiffResult` exists yet:
    `scan`'s evidence-contract error).

    Every dominant `code` this module produces (`1`/`5`/`6`/`8`/`16`) is
    chosen so it always exceeds any contribution the ordinary fold or a
    release's own severity/coverage axes can produce (`0`-`4`) -- so
    carrying a prior/raw value through here can never make `code` stop
    being the maximum contribution -- **enforced**, not merely assumed:
    every caller's *_code default is safe today, but the two public
    resolvers below also accept a caller-supplied custom code (for a
    future command with the same axes but different numbering, per
    ADR-064's "numbers are not unified across commands" rule), and a
    custom code that does not exceed a preserved prior/raw contribution
    would silently produce an object violating `ExitDecision`'s own
    `code == max(contributions)` invariant and drop a genuinely tied axis
    from `reasons` (Codex review, fresh evidence, with the exact
    counter-example: a custom code of `1` alongside a preserved
    compatibility contribution of `4`). Fail loudly instead.
    """
    dominant_field = _DOMINANT_FIELD[reason]
    # One mapping built once, then used for *both* the guard's `preserved`
    # tuple and the constructed `ExitDecision`, so the two cannot disagree
    # about which axes this decision carries. Two earlier revisions listed
    # them separately and a field added to the signature reached neither
    # (Codex review, twice: `evidence_contract_error_contribution`, then
    # `removed_required_library_contribution`) -- accepted as a keyword,
    # silently dropped from the result, and invisible to the guard that is
    # supposed to catch exactly that. A field added to the signature from
    # here on has to be added to this mapping to be accepted at all.
    raw: dict[str, int] = {
        "compatibility_contribution": compatibility_contribution,
        "contract_coverage_contribution": contract_coverage_contribution,
        "analysis_assurance_contribution": analysis_assurance_contribution,
        "evidence_contract_error_contribution": evidence_contract_error_contribution,
        "removed_required_library_contribution": (
            removed_required_library_contribution
        ),
        "operational_error_contribution": operational_error_contribution,
        "incomplete_scope_contribution": incomplete_scope_contribution,
        "no_comparison_completed_contribution": no_comparison_completed_contribution,
    }
    # A field that is *also* the dominant axis is not a preserved value: it
    # is set to `code` below. Generic, where this was a hand-written special
    # case for the one field that then applied -- two of the eight are now
    # both preservable and dominant-capable.
    if dominant_field in raw:
        raw[dominant_field] = 0
    preserved: tuple[int, ...]
    if prior is not None:
        preserved = (
            prior.compatibility_contribution,
            prior.contract_coverage_contribution,
            prior.analysis_assurance_contribution,
            prior.crosscheck_promotion_contribution,
            prior.operational_error_contribution,
            prior.removed_required_library_contribution,
            prior.incomplete_scope_contribution,
            prior.no_comparison_completed_contribution,
        )
    else:
        preserved = tuple(raw.values())
    if code <= max(preserved, default=0):
        raise ValueError(
            f"{reason.value}'s code ({code}) must strictly exceed every "
            f"preserved contribution {preserved} -- a custom code that "
            "only ties or falls below one would either violate "
            "ExitDecision's own code == max(contributions) invariant "
            "(if lower) or silently drop a genuinely tied axis from "
            "`reasons` (if equal)"
        )
    if prior is not None:
        return ExitDecision(
            code=code,
            reasons=(reason,),
            compatibility_contribution=prior.compatibility_contribution,
            contract_coverage_contribution=prior.contract_coverage_contribution,
            analysis_assurance_contribution=prior.analysis_assurance_contribution,
            crosscheck_promotion_contribution=prior.crosscheck_promotion_contribution,
            operational_error_contribution=prior.operational_error_contribution,
            incomplete_scope_contribution=prior.incomplete_scope_contribution,
            no_comparison_completed_contribution=(
                prior.no_comparison_completed_contribution
            ),
            **{
                # `dominant_field` may name one of the fields above, so this
                # is a mapping rather than a keyword: passing both would be
                # a duplicate-keyword TypeError. Dominant assigned last.
                **{
                    "removed_required_library_contribution": (
                        prior.removed_required_library_contribution
                    )
                },
                dominant_field: code,
            },
        )
    contributions = dict(raw)
    contributions[dominant_field] = code
    return ExitDecision(code=code, reasons=(reason,), **contributions)


#: ADR-064's exit code for a failed evidence contract, and ADR-037 D5's
#: before it: a run that pinned evidence (`--depth build`/`--depth source`,
#: `--abi3`) it never actually reached. Named here, next to the resolver
#: that owns the axis, because a second consumer now folds the same axis
#: outside this resolver -- `report.no_baseline.no_baseline_exit_code`, the
#: single-build audit's own `max` fold (ADR-068 D2 gives that run no
#: compatibility contribution to fold through
#: :func:`resolve_compare_exit_decision_with_abort_axes`, so it cannot
#: reuse the resolver itself). One constant, so the two can never disagree.
EXIT_EVIDENCE_CONTRACT_ERROR = 7


def resolve_scan_exit_decision(
    *,
    budget_overflow_before_evidence_check: bool = False,
    evidence_contract_error: bool = False,
    budget_overflow: bool = False,
    not_comparable: bool = False,
    evidence_contract_error_code: int = EXIT_EVIDENCE_CONTRACT_ERROR,
    budget_overflow_code: int = 5,
    not_comparable_code: int = 6,
    prior_decision: ExitDecision | None = None,
) -> ExitDecision | None:
    """ADR-064's outer precedence layer for `scan`, ahead of
    :func:`resolve_compare_exit_decision`'s gate/coverage/assurance fold.

    **Also called from `resolve_compare_exit_decision_with_abort_axes`
    below** (`one-comparison-product.md` P3): when a `DiffResult` carries
    `evidence_contract_error`/`budget_overflow` (currently never set by any
    CLI-reachable `compare` path -- see those fields' own docstrings in
    `checker_types.py`), native `compare` folds its own gate/coverage/
    assurance decision through this exact function as `prior_decision`,
    reusing the identical precedence rule rather than a second copy. The
    `*_code` defaults (`7`/`5`) are `scan`'s own numbers, shared as-is --
    ADR-064 assigns the same two codes to the same two conditions regardless
    of which command raises them, unlike e.g. `not_comparable_code`, which
    the release resolver below overrides to `16`.

    Reproduces `scan_engine.run_scan_core`'s exact raise/check order --
    which, contrary to an earlier revision's simpler "evidence always beats
    budget" rule, puts `_BudgetOverflow` on **both** sides of
    `_EvidenceContractError` (Codex review, fresh evidence against the real
    line order): candidate-snapshot collection is deadline-guarded
    (`scan_engine.py:1180-1221`) and raises `_BudgetOverflow` if it
    overruns, *before* `_check_scan_evidence_contract` is even called
    (`scan_engine.py:1229`) -- so a budget overflow at that specific,
    earlier stage preempts the evidence-contract check entirely and must
    win. Only *after* that check passes does the run reach the baseline
    compare's own deadline scope and the final, unconditional
    `_check_scan_budget` call -- so a budget overflow at *those* later
    stages comes after `_EvidenceContractError` had its chance to fire and
    must lose to it.

    *budget_overflow_before_evidence_check* is that earlier axis --
    dominates everything, including `evidence_contract_error`, since
    nothing after candidate-snapshot collection ever ran. There is never a
    `prior_decision` for it either (nothing later was computed).
    *evidence_contract_error* dominates the later *budget_overflow* and
    *not_comparable* -- both raised by code that runs only once the
    evidence-contract check has already passed. *budget_overflow* (the
    later axis: the baseline compare's deadline, or the final
    `_check_scan_budget` call after a `not_comparable` result may already
    have been decided) discards that already-decided result rather than
    losing to it -- the `ScanOutcome`/report is never constructed once
    `_BudgetOverflow` propagates -- so it wins over `not_comparable` too.
    When it fires *after* a comparable baseline compare already built a
    full gate/coverage/assurance decision, pass that decision as
    *prior_decision* so it is preserved in the returned object's own
    contribution fields for explainability -- it did not decide `code`,
    but a report reader can still see what it was. `not_comparable` never
    has a `prior_decision` (no `DiffResult` exists for that outcome, so
    nothing was computed). Returns `None` when none of the four axes
    apply, meaning the comparison actually ran and the caller should
    resolve an ordinary :class:`ExitDecision` for it instead (via
    :func:`resolve_compare_exit_decision`/:func:`resolve_exit_decision`).

    Pure resolver logic (ADR-064's first stage); stage 1b has since wired it
    into `scan_engine.py`'s `NOT_COMPARABLE`/`_BudgetOverflow`/
    `_EvidenceContractError` outcomes, always producing the same code an
    unwired caller already computed by hand -- so no existing call site's
    actually-returned exit code changed *because* this function exists. The
    `*_code` keyword arguments default to `scan`'s own real
    numbers (7/5/6, shared by both budget-overflow axes since they map to
    the identical exit code regardless of which raised it) but are
    accepted explicitly rather than hard-coded, so a future caller for a
    different command with the same axes (none is known today) is not
    forced to share `scan`'s numbering, per ADR-064's "numbers are not
    unified across commands" rule -- but a custom code that does not
    strictly exceed a preserved prior contribution raises `ValueError`
    (`_dominant_decision`'s own docstring), rather than silently returning
    a self-contradictory `ExitDecision`.
    """
    if budget_overflow_before_evidence_check:
        return _dominant_decision(budget_overflow_code, ExitReason.BUDGET_OVERFLOW)
    if evidence_contract_error:
        return _dominant_decision(
            evidence_contract_error_code, ExitReason.EVIDENCE_CONTRACT_ERROR
        )
    if budget_overflow:
        return _dominant_decision(
            budget_overflow_code,
            ExitReason.BUDGET_OVERFLOW,
            prior=prior_decision,
        )
    if not_comparable:
        return _dominant_decision(not_comparable_code, ExitReason.NOT_COMPARABLE)
    return None


def resolve_compare_exit_decision_with_abort_axes(
    result: DiffResult,
    sev_config: SeverityConfig | None,
    scheme: str,
    *,
    require_complete_analysis: bool = False,
    today: date | None = None,
) -> ExitDecision:
    """`one-comparison-product.md` P3: `resolve_compare_exit_decision`,
    extended with the two ADR-064 abort axes `scan` already has.

    Same signature and same ordinary gate/coverage/assurance fold as
    :func:`~abicheck.policy.exit_decision.resolve_compare_exit_decision` (in
    fact calls it directly for that fold) -- this wrapper lives in this
    sibling module, not that one, purely so it can call
    :func:`resolve_scan_exit_decision` without `exit_decision.py` importing
    *this* module back (this module already imports from `exit_decision.py`
    at the top of the file; the reverse edge would be a fresh import cycle
    the AI-readiness `import-cycle-growth` gate rejects -- "move shared
    logic to a leaf module both sides can depend on" per `AGENTS.md`'s own
    "Don't" list).

    `result.evidence_contract_error`/`.budget_overflow` default `False` on
    every `DiffResult` any existing caller builds (`compare` has no
    CLI-reachable trigger for either yet -- no `--budget` flag, and
    ADR-037 D5's auto-strict `--depth`/`--source-method` enforcement remains
    `scan`-only), so for every pre-existing invocation this returns exactly
    what the wrapped ordinary fold does. When a `DiffResult` does carry one,
    the ordinary fold is still computed first and passed through as
    `resolve_scan_exit_decision`'s own `prior_decision` -- see that
    function's own docstring for which of the two axes preserves it and
    which does not; this wrapper does not alter either rule, only calls it.
    *today*, forwarded to the ordinary fold, keeps this agreeing with an
    already-frozen ``ReportEnvelope`` (Codex review, fresh evidence).

    Both real production consumers of the *ordinary* resolver
    (`reporter_contract_blocks.add_contract_context`'s real JSON `exit`
    block, `cli._exit_with_severity_or_verdict`'s real process exit) call
    this wrapper instead, so both are P3-aware without either duplicating
    the precedence.
    """
    from .exit_decision import resolve_compare_exit_decision

    ordinary = resolve_compare_exit_decision(
        result,
        sev_config,
        scheme,
        require_complete_analysis=require_complete_analysis,
        today=today,
    )
    evidence_contract_error = getattr(result, "evidence_contract_error", False)
    budget_overflow = getattr(result, "budget_overflow", False)
    if not (evidence_contract_error or budget_overflow):
        return ordinary
    # `resolve_scan_exit_decision` only returns `None` when none of its four
    # axes apply -- unreachable here, since at least one of the two we pass
    # is always `True` by the guard above.
    dominant = resolve_scan_exit_decision(
        evidence_contract_error=evidence_contract_error,
        budget_overflow=budget_overflow,
        prior_decision=ordinary,
    )
    assert dominant is not None
    return dominant


def resolve_release_exit_decision(
    *,
    not_comparable: bool,
    severity_scheme_active: bool,
    verdict_or_severity_contribution: int,
    removed_required_library: bool = False,
    contract_coverage_contribution: int = 0,
    analysis_assurance_contribution: int = 0,
    evidence_contract_error_contribution: int = 0,
    operational_error_contribution: int = 0,
    incomplete_scope_contribution: int = 0,
    no_comparison_completed_contribution: int = 0,
    not_comparable_code: int = 16,
    removed_required_library_code: int = 8,
) -> ExitDecision:
    """ADR-064's precedence for a directory/package release comparison,
    reproducing `cli_compare_release_helpers._exit_compare_release` exactly
    -- including the one asymmetry that function's own two branches encode,
    which a flat `max()` over contributions cannot express (this is exactly
    why ADR-064 calls removed-required-library's rank "mode-dependent, not
    a fixed slot" rather than folding it into `resolve_exit_decision`):

    - `not_comparable` dominates everything, in both schemes (mirrors
      native `compare`'s own `16`, and release's `_exit_compare_release`
      checking `worst_verdict == "not_comparable"` first, unconditionally).
    - **Severity-aware scheme** (`severity_scheme_active=True`):
      `removed_required_library` wins *outright* over the aggregated
      verdict/severity code -- including over `contract_coverage_
      contribution`/`operational_error_contribution`, neither of which is
      folded in when removed-library fires. This is a real, pre-existing
      asymmetry in today's code (the severity branch's `sys.exit(8)` for a
      removed library runs before the coverage floor is even read), not a
      simplification introduced here.
    - **Legacy scheme** (`severity_scheme_active=False`): the opposite
      priority -- a nonzero fold of *verdict_or_severity_contribution*
      and *operational_error_contribution* (an `API_BREAK`/`BREAKING`
      verdict, or the release's own operational `ERROR` sentinel floored
      to `4` -- mutually exclusive in today's real code, since a release's
      single `worst_verdict` string can only be one or the other) wins
      outright over removed-required-library, which is checked only once
      both are `0`. `contract_coverage_contribution` folds in via `max()`
      alongside whichever of the two decided the code, exactly as
      `_exit_compare_release`'s own `max(code, contract_coverage_exit_
      contribution)` calls do.

    *verdict_or_severity_contribution* and *operational_error_
    contribution* are two **separately computed** axes, not one
    pre-folded value -- pass them independently rather than folding them
    yourself first (Codex review, fresh evidence: an earlier revision took
    one pre-folded `verdict_or_severity_contribution` plus an
    `is_operational_error` boolean, which could not represent the real
    severity-scheme case where library A's own severity-gate finding and
    library B's operational `ERROR` are independently computed by
    `_compute_release_severity_exit_code`/`_fold_release_global_severity`
    and then combined with `max()` -- a genuine tie the boolean design
    collapsed into a single, wrongly-labelled reason, silently dropping
    whichever finding lost the coin flip from `reasons`). Folding both
    into one `resolve_exit_decision` call, below, lets a real tie between
    them be named correctly instead.

    *verdict_or_severity_contribution* is the caller's own already-computed
    aggregated *verdict/severity-only* code for the active scheme -- for
    severity, `severity_exit_code` alone; for legacy, `legacy_exit_code(
    Verdict[worst_verdict])` (`0` when `worst_verdict` is `"ERROR"`, since
    that string is not a `Verdict` member). *operational_error_
    contribution* is `4` when `worst_verdict == "ERROR"`, else `0`. This
    function does not compute either fold itself, matching
    `resolve_compare_exit_decision`'s own convention of taking
    already-resolved contributions rather than re-deriving them.

    **Legacy scheme, known gap in today's real caller, not in this
    resolver (Codex review, fresh evidence).** This function already
    preserves a genuine tie between *verdict_or_severity_contribution* and
    *operational_error_contribution* for the legacy scheme exactly as it
    does for severity (both fold through :func:`resolve_exit_decision`'s
    tie-inclusive logic below) -- but today's real `worst_verdict`
    aggregation (`cli_compare_release.py`'s `_RELEASE_VERDICT_ORDER` loop)
    ranks `"ERROR"` *above* `"BREAKING"` and collapses the whole release to
    one scalar, so a release with one `BREAKING` library and a second,
    unrelated library that failed to compare never gets to *supply* a
    nonzero legacy `verdict_or_severity_contribution` alongside a nonzero
    `operational_error_contribution` in the first place -- unlike severity
    mode, where `_compute_release_severity_exit_code` already iterates
    `library_results` independently of `worst_verdict` and so never
    discards a real per-library finding this way. Closing this needs new
    aggregation logic in `cli_compare_release.py` (a legacy-scheme "worst
    verdict among non-`ERROR`/non-`not_comparable` libraries", mirroring
    `_compute_release_severity_exit_code`'s existing per-library loop) --
    real, additional scope for stage 1b's wiring work, not something this
    pure resolver can manufacture from an input its real caller does not
    yet compute.

    Pure, additive logic (ADR-064's first stage): not yet called from
    `cli_compare_release_helpers.py`, so no existing release comparison's
    actually-returned exit code changes because this function exists. As
    with `resolve_scan_exit_decision`, a custom `not_comparable_code`/
    `removed_required_library_code` that does not strictly exceed a
    preserved contribution raises `ValueError`.

    *evidence_contract_error_contribution* (PR #1195) is a member's pinned
    ``--depth build``/``--depth source`` that its own live evidence never
    reached -- ADR-064's exit-7 axis, aggregated across the release by
    ``frontends.cli.release_evidence_contract``. Unlike the ``0``/``1``
    floors it is a real code and a *dominant* axis: it is checked directly
    below ``not_comparable`` and above everything else, because a run whose
    pinned evidence contract was not met never established what changed.
    That ranking is the same one :func:`resolve_scan_exit_decision` gives
    the identical axis for ``scan``, and it is why this cannot be folded
    with ``max()`` alongside the floors -- ``7`` would then silently
    outrank a real ``4``/``8`` decided by a *different* member without
    being named as the reason.

    *analysis_assurance_contribution* (ADR-070) is the release's own
    ``assurance.require_complete`` floor -- ``max`` over every compared
    member's, resolved by ``policy.release_assurance.
    resolve_release_assurance_decision``. Shaped exactly like
    *contract_coverage_contribution* and folded in every non-dominant branch
    below, in **both** schemes, so it raises a clean ``0`` to ``1`` and never
    lowers a real ``2``/``4`` -- and preserved, never deciding, under a
    dominant ``16``/``8``/``7``. Deliberately the *same* axis
    ``resolve_compare_exit_decision`` fills for a scalar ``compare`` rather
    than a release-only sibling field: over one member the fold is the
    identity, so a one-member package must gate and report identically to
    the scalar path (ADR-070 D1), and a second field would leave a consumer
    reading ``analysis_assurance_contribution`` a ``0`` for a run this axis
    actually floored.

    *incomplete_scope_contribution*/*no_comparison_completed_contribution*
    (ADR-065 D6/D7, S2) are two more ``0``/``1`` fold participants, shaped
    exactly like *contract_coverage_contribution*: folded with ``max()``
    in every non-dominant branch, in **both** schemes, so they raise a
    clean ``0`` to ``1`` and never lower a real ``2``/``4`` -- and
    preserved, never deciding, under a dominant ``16``/``8``. The
    removed-required-library branch is the one place their rank differs
    from coverage's: since S2 that branch fires only on a *proven*
    removal (the caller derives *removed_required_library* from the
    acquisition record's proven-complete new-side inventory, no longer
    from a raw set difference), so an unproven unmatched member reaches
    this resolver as the scope axis instead.
    """
    if not_comparable:
        # *verdict_or_severity_contribution*, *contract_coverage_
        # contribution* and *operational_error_contribution* are all
        # already-resolved parameters at this point regardless of which
        # branch below would otherwise run -- `not_comparable` short-
        # circuits *this* function's own choice among them, it does not
        # mean nothing was computed upstream (a release's aggregated
        # severity/coverage code is folded across every library before
        # `_exit_compare_release` is even called). Carry them through for
        # explainability; `16` always exceeds any of them (severity/verdict/
        # operational-error codes cap at `4`, coverage at `1`), so
        # `reasons` still names only `NOT_COMPARABLE`.
        return _dominant_decision(
            not_comparable_code,
            ExitReason.NOT_COMPARABLE,
            compatibility_contribution=verdict_or_severity_contribution,
            contract_coverage_contribution=contract_coverage_contribution,
            analysis_assurance_contribution=analysis_assurance_contribution,
            # The evidence branch below is unreachable once this returns, so
            # without this a release that is `not_comparable` *and* short of
            # its pinned rung would report `0` for the axis while the member
            # entry and stderr both record it (Codex review). `16` > `7`, so
            # `code`/`reasons` are unchanged; only the report gains the fact.
            evidence_contract_error_contribution=evidence_contract_error_contribution,
            operational_error_contribution=operational_error_contribution,
            incomplete_scope_contribution=incomplete_scope_contribution,
            no_comparison_completed_contribution=no_comparison_completed_contribution,
        )

    if evidence_contract_error_contribution:
        # Below `not_comparable`, and below a *proven* removed library --
        # which is a correction of this branch as first written (Codex
        # review, P2). Two independent reasons, and the second is the
        # stronger one:
        #
        # 1. A proven removal (ADR-065 D6: NEW's own inventory asserted
        #    complete) does not rest on the depth evidence this axis
        #    reports short. Depth governs change detection *within* a
        #    library; the removal set comes from *inventory* completeness.
        #    The first version of this branch justified outranking `8` on
        #    the grounds that an unmet contract "never established what
        #    changed, including whether the removal set is trustworthy" --
        #    that conflates the two, and ADR-065 already refuses to emit
        #    `8` at all on an unproven inventory, so the distrust it
        #    guarded against cannot reach here.
        # 2. It was not even representable. `_dominant_decision` requires
        #    the dominant `code` to exceed every contribution it carries
        #    (`ExitDecision`'s documented `code == max(...)` invariant,
        #    pinned by `test_dominant_decisions_satisfy_the_code_equals_
        #    max_invariant`), so "dominant `7`, preserved `8`" has no legal
        #    encoding. Ranking evidence first therefore had to *drop* the
        #    removal axis -- which dropped the exit from `8` to `7` and
        #    turned `run_outcome.gate` from `abi_breaking` to `none`, so an
        #    unrelated depth shortfall hid a removed library outright.
        #
        # `removed_required_library` alone is not enough to prefer it: its
        # rank is mode-dependent (see this function's docstring), so it is
        # only an active axis where the scheme below would actually have
        # honoured it -- always under severity, and under legacy only once
        # the verdict/operational fold is `0`. Where it is *not* active the
        # evidence axis decides and carries no removal contribution, which
        # is honest rather than lossy: the axis genuinely did not apply.
        removal_is_active = removed_required_library and (
            severity_scheme_active
            or (
                verdict_or_severity_contribution == 0
                and operational_error_contribution == 0
            )
        )
        if removal_is_active and (
            removed_required_library_code > evidence_contract_error_contribution
        ):
            return _dominant_decision(
                removed_required_library_code,
                ExitReason.REMOVED_REQUIRED_LIBRARY,
                compatibility_contribution=verdict_or_severity_contribution,
                contract_coverage_contribution=contract_coverage_contribution,
                analysis_assurance_contribution=analysis_assurance_contribution,
                evidence_contract_error_contribution=evidence_contract_error_contribution,
                operational_error_contribution=operational_error_contribution,
                incomplete_scope_contribution=incomplete_scope_contribution,
                no_comparison_completed_contribution=no_comparison_completed_contribution,
            )
        return _dominant_decision(
            evidence_contract_error_contribution,
            ExitReason.EVIDENCE_CONTRACT_ERROR,
            compatibility_contribution=verdict_or_severity_contribution,
            contract_coverage_contribution=contract_coverage_contribution,
            analysis_assurance_contribution=analysis_assurance_contribution,
            removed_required_library_contribution=(
                removed_required_library_code if removal_is_active else 0
            ),
            operational_error_contribution=operational_error_contribution,
            incomplete_scope_contribution=incomplete_scope_contribution,
            no_comparison_completed_contribution=no_comparison_completed_contribution,
        )

    if severity_scheme_active:
        if removed_required_library:
            # `contract_coverage_contribution`/`operational_error_
            # contribution` are preserved even though today's real
            # `_exit_compare_release` never reads them once this branch's
            # own `sys.exit(8)` fires first -- both are already-computed,
            # available values (the caller resolves them unconditionally
            # before calling this function at all), and `8` always exceeds
            # either (`0`/`1` and `0`/`4` respectively), so preserving them
            # cannot affect `code`/`reasons`, only the report's
            # explainability.
            return _dominant_decision(
                removed_required_library_code,
                ExitReason.REMOVED_REQUIRED_LIBRARY,
                compatibility_contribution=verdict_or_severity_contribution,
                contract_coverage_contribution=contract_coverage_contribution,
                analysis_assurance_contribution=analysis_assurance_contribution,
                operational_error_contribution=operational_error_contribution,
                incomplete_scope_contribution=incomplete_scope_contribution,
                no_comparison_completed_contribution=no_comparison_completed_contribution,
            )
        return resolve_exit_decision(
            compatibility_contribution=verdict_or_severity_contribution,
            contract_coverage_contribution=contract_coverage_contribution,
            analysis_assurance_contribution=analysis_assurance_contribution,
            operational_error_contribution=operational_error_contribution,
            incomplete_scope_contribution=incomplete_scope_contribution,
            no_comparison_completed_contribution=no_comparison_completed_contribution,
        )

    # Legacy scheme: a nonzero fold of the verdict/severity and operational-
    # error axes wins outright, ahead of removed-required-library -- checked
    # only once both are 0.
    if verdict_or_severity_contribution != 0 or operational_error_contribution != 0:
        return resolve_exit_decision(
            compatibility_contribution=verdict_or_severity_contribution,
            contract_coverage_contribution=contract_coverage_contribution,
            analysis_assurance_contribution=analysis_assurance_contribution,
            operational_error_contribution=operational_error_contribution,
            incomplete_scope_contribution=incomplete_scope_contribution,
            no_comparison_completed_contribution=no_comparison_completed_contribution,
        )
    if removed_required_library:
        # Both axes are 0 here by construction (the branch above already
        # returned otherwise); coverage is preserved for the same reason
        # as the severity-scheme branch above.
        return _dominant_decision(
            removed_required_library_code,
            ExitReason.REMOVED_REQUIRED_LIBRARY,
            contract_coverage_contribution=contract_coverage_contribution,
            analysis_assurance_contribution=analysis_assurance_contribution,
            incomplete_scope_contribution=incomplete_scope_contribution,
            no_comparison_completed_contribution=no_comparison_completed_contribution,
        )
    return resolve_exit_decision(
        compatibility_contribution=0,
        contract_coverage_contribution=contract_coverage_contribution,
        analysis_assurance_contribution=analysis_assurance_contribution,
        incomplete_scope_contribution=incomplete_scope_contribution,
        no_comparison_completed_contribution=no_comparison_completed_contribution,
    )


def __getattr__(name: str) -> object:
    """Re-export the release *adapter* half, which now lives next door.

    A lazy module-level ``__getattr__`` rather than a static ``from
    .release_exit_decision import ...``: that module imports this one for
    the precedence function it adapts to, so a static re-export here would
    be a genuine import cycle. Same shim pattern as
    ``cli_buildsource.py``'s.
    """
    if name in {
        "release_evidence_contract_contribution",
        "resolve_release_exit_decision_for_report",
    }:
        from importlib import import_module

        return getattr(import_module(".release_exit_decision", __package__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
