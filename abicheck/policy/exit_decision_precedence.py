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
what stage this additive work belongs to; this module implements exactly
the same stage (1a: pure resolvers, not yet wired into any real call site),
just physically split for the line-count cap.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from .exit_decision import ExitDecision, ExitReason, resolve_exit_decision

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
    operational_error_contribution: int = 0,
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
    preserved: tuple[int, ...]
    if prior is not None:
        preserved = (
            prior.compatibility_contribution,
            prior.contract_coverage_contribution,
            prior.analysis_assurance_contribution,
            prior.crosscheck_promotion_contribution,
            prior.operational_error_contribution,
        )
    else:
        preserved = (
            compatibility_contribution,
            contract_coverage_contribution,
            analysis_assurance_contribution,
            operational_error_contribution,
        )
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
            **{dominant_field: code},
        )
    return ExitDecision(
        code=code,
        reasons=(reason,),
        compatibility_contribution=compatibility_contribution,
        contract_coverage_contribution=contract_coverage_contribution,
        analysis_assurance_contribution=analysis_assurance_contribution,
        operational_error_contribution=operational_error_contribution,
        **{dominant_field: code},
    )


def resolve_scan_exit_decision(
    *,
    budget_overflow_before_evidence_check: bool = False,
    evidence_contract_error: bool = False,
    budget_overflow: bool = False,
    not_comparable: bool = False,
    evidence_contract_error_code: int = 1,
    budget_overflow_code: int = 5,
    not_comparable_code: int = 6,
    prior_decision: ExitDecision | None = None,
) -> ExitDecision | None:
    """ADR-064's outer precedence layer for `scan`, ahead of
    :func:`resolve_compare_exit_decision`'s gate/coverage/assurance fold.

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

    This is pure, additive logic (ADR-064's first stage) -- it is not yet
    called from `scan_engine.py`/`cli_scan_baseline.py`, so no existing
    call site's actually-returned exit code changes because this function
    exists. The `*_code` keyword arguments default to `scan`'s own real
    numbers (1/5/6, shared by both budget-overflow axes since they map to
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


ScanAbortAxis = Literal["budget_overflow", "evidence_contract_error"]
#: `run_scan_core`'s two abort exceptions -> the verdict/exit_code pair
#: `service_scan.ScanResult` already used before it carried a `report` too.
_SCAN_ABORT_VERDICTS: dict[ScanAbortAxis, tuple[str, int]] = {
    "budget_overflow": ("BUDGET_OVERFLOW", 5),
    "evidence_contract_error": ("EVIDENCE_CONTRACT_ERROR", 1),
}


class ScanAbortResultFields(TypedDict):
    """``ScanResult(**scan_abort_result_fields(axis))`` -- a `TypedDict`
    (not a plain ``dict[str, object]``) so mypy checks each field's type
    against `service_scan.ScanResult`'s own constructor when ``**``-unpacked,
    instead of rejecting the unpack outright the way it does for an untyped
    dict (whose values it cannot attribute to individual parameters).
    """

    verdict: str
    exit_code: int
    report: dict[str, object]


def scan_abort_result_fields(axis: ScanAbortAxis) -> ScanAbortResultFields:
    """ADR-064 stage 1b: every `ScanResult` field `service_scan.run_scan`/
    `_run_scan_one_member` need for one of `run_scan_core`'s two abort
    exceptions, so the verdict/exit_code pairing stays next to the
    `ExitDecision` that now explains it, instead of duplicated at each
    `except` site. `report` mirrors what `scan_engine.py`'s own
    ``NOT_COMPARABLE`` outcome already persists via ``resolve_scan_exit_
    decision(not_comparable=True)``.

    Both exceptions abort before a `DiffResult` exists, so there is never a
    `prior_decision` to carry through, and the caller already knows which
    single exception fired (mutually exclusive at runtime) -- `resolve_scan_
    exit_decision`'s finer "before vs. after the evidence-contract check"
    distinction between the two `_BudgetOverflow` raise sites doesn't matter
    here, since both map to the same code/reason.
    """
    decision = resolve_scan_exit_decision(
        budget_overflow=axis == "budget_overflow",
        evidence_contract_error=axis == "evidence_contract_error",
    )
    assert decision is not None  # axis always selects one of the two above
    verdict, exit_code = _SCAN_ABORT_VERDICTS[axis]
    return ScanAbortResultFields(
        verdict=verdict, exit_code=exit_code, report={"exit": decision.to_dict()}
    )


def resolve_release_exit_decision(
    *,
    not_comparable: bool,
    severity_scheme_active: bool,
    verdict_or_severity_contribution: int,
    removed_required_library: bool = False,
    contract_coverage_contribution: int = 0,
    operational_error_contribution: int = 0,
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
            operational_error_contribution=operational_error_contribution,
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
                operational_error_contribution=operational_error_contribution,
            )
        return resolve_exit_decision(
            compatibility_contribution=verdict_or_severity_contribution,
            contract_coverage_contribution=contract_coverage_contribution,
            operational_error_contribution=operational_error_contribution,
        )

    # Legacy scheme: a nonzero fold of the verdict/severity and operational-
    # error axes wins outright, ahead of removed-required-library -- checked
    # only once both are 0.
    if verdict_or_severity_contribution != 0 or operational_error_contribution != 0:
        return resolve_exit_decision(
            compatibility_contribution=verdict_or_severity_contribution,
            contract_coverage_contribution=contract_coverage_contribution,
            operational_error_contribution=operational_error_contribution,
        )
    if removed_required_library:
        # Both axes are 0 here by construction (the branch above already
        # returned otherwise); coverage is preserved for the same reason
        # as the severity-scheme branch above.
        return _dominant_decision(
            removed_required_library_code,
            ExitReason.REMOVED_REQUIRED_LIBRARY,
            contract_coverage_contribution=contract_coverage_contribution,
        )
    return resolve_exit_decision(
        compatibility_contribution=0,
        contract_coverage_contribution=contract_coverage_contribution,
    )


def _compute_release_legacy_exit_code(
    worst_verdict: str,
    library_results: list[dict[str, object]],
    release_global_verdict: str = "NO_CHANGE",
) -> int:
    """Worst legacy-scheme exit code across libraries *and* release-global
    (bundle/probe-matrix) findings.

    ADR-064 stage 1b. Mirrors ``cli_compare_release_helpers.
    _compute_release_severity_exit_code``'s per-library independence for
    the *legacy* scheme: ``_RELEASE_VERDICT_ORDER``'s collapsed
    ``worst_verdict`` ranks ``"ERROR"``/``"not_comparable"`` above every
    real :class:`~abicheck.checker_policy.Verdict`, so using it directly as
    the legacy compatibility-gate contribution would let an unrelated
    library's operational failure hide a real ``BREAKING``/``API_BREAK``
    verdict from a *different* library in the same release -- exactly the
    gap :func:`resolve_release_exit_decision`'s own docstring calls out as
    "real, additional scope for stage 1b's wiring work." Scanning
    *library_results* alone, though, misses the opposite case (Codex
    review, fresh evidence): a bundle or probe-matrix break with every
    library itself ``NO_CHANGE`` raises the *aggregate* ``worst_verdict``
    (``cli_compare_release._collect_bundle_result``/``_collect_matrix_
    result``) without ever setting any library's own ``"verdict"`` key, so
    the per-library scan alone would find ``0``. Folding in *worst_verdict*
    itself (when it names a real ``Verdict``, i.e. not ``"ERROR"``/
    ``"not_comparable"``) via ``max()`` catches both: an aggregate real
    verdict at least as bad as any library's own, and a library's own real
    verdict the aggregate's ``"ERROR"`` collapse would otherwise hide.

    *release_global_verdict* (Codex review, fresh evidence, second round)
    is the caller's own uncollapsed bundle/probe-matrix verdict --
    independent of *worst_verdict*, which is *already* the max of every
    library's verdict, every release-global verdict, **and** the ``ERROR``/
    ``not_comparable`` sentinels together, so once an unrelated library's
    ``ERROR`` outranks a real release-global ``BREAKING`` in that same
    collapse, *worst_verdict* alone can no longer tell the two apart --
    unlike a library-level break, a release-global one never appears in
    *library_results* either, so there is nothing left to scan it out of.
    Folded in via the same ``max()`` treatment as a library's own verdict,
    so a real release-global break is never silently dropped just because
    some other library's operational failure happens to rank higher.
    """
    from ..checker_policy import Verdict
    from .severity import legacy_exit_code

    worst = 0
    for entry in library_results:
        if not isinstance(entry, dict):
            continue
        verdict_str = entry.get("verdict")
        if isinstance(verdict_str, str) and verdict_str in Verdict.__members__:
            worst = max(worst, legacy_exit_code(Verdict[verdict_str]))
    if worst_verdict in Verdict.__members__:
        worst = max(worst, legacy_exit_code(Verdict[worst_verdict]))
    if release_global_verdict in Verdict.__members__:
        worst = max(worst, legacy_exit_code(Verdict[release_global_verdict]))
    return worst


def resolve_release_exit_decision_for_report(
    worst_verdict: str,
    fail_on_removed: bool,
    removed_keys: list[str],
    severity_exit_code: int | None,
    contract_coverage_exit_contribution: int,
    library_results: list[dict[str, object]],
    release_global_verdict: str = "NO_CHANGE",
) -> ExitDecision:
    """ADR-064 stage 1b: the release fan-out's persisted, explainable
    ``exit`` block.

    Reproduces ``cli_compare_release_helpers._exit_compare_release``'s own
    precedence via :func:`resolve_release_exit_decision`, for **report
    purposes only** -- this function never calls ``sys.exit`` and is not
    itself called from ``_exit_compare_release``, which keeps computing the
    real process exit code exactly the way it always has (`tests/
    test_exit_code_integrity.py` pins that function's own signature and
    numeric outputs; rewriting it in place to delegate here risked exactly
    the kind of silent exit-code regression ADR-064 exists to prevent, for
    a function CI gates directly depend on).

    ``.code`` is nonetheless *provably* always equal to what
    ``_exit_compare_release`` sys.exits with, given the same inputs -- not
    merely "expected to agree": every legacy-scheme code
    :func:`_compute_release_legacy_exit_code` can produce caps at ``4``
    (``legacy_exit_code(BREAKING)``), which is also the fixed floor
    ``_exit_compare_release`` applies for an operational ``"ERROR"``
    sentinel (``max(4, ...)``), so the two can never diverge numerically --
    only in which reasons/contributions the returned :class:`ExitDecision`
    records. Concretely, a release with one ``BREAKING`` library and a
    second, unrelated library that failed to compare (an ``"ERROR"``
    verdict) collapses to ``worst_verdict == "ERROR"`` in today's
    ``_RELEASE_VERDICT_ORDER`` rollup (ADR-050 D2's ``"not_comparable"``
    ranks higher still, but is handled by its own dominant branch below) --
    ``_exit_compare_release`` never even computes the ``BREAKING``
    library's own code in that case, since its ``ERROR`` short-circuit
    fires first, while this function still finds it via
    :func:`_compute_release_legacy_exit_code` and names both
    ``COMPATIBILITY_GATE`` and ``OPERATIONAL_ERROR`` in ``reasons`` -- both
    tied at ``4``. `tests/test_exit_code_integrity.py`'s
    `TestReleaseExitDecisionForReportAgreesWithRealExit` proves the
    numeric-agreement claim across the same input matrix
    ``_exit_compare_release``'s own tests already cover.

    *library_results* alone does not capture a bundle/probe-matrix-only
    break (no library's own verdict changes) -- see
    :func:`_compute_release_legacy_exit_code`'s own docstring for how the
    legacy branch also folds in *worst_verdict* itself to cover that case,
    and *release_global_verdict* (Codex review, fresh evidence, second
    round) for the sibling case that fix alone still missed: an unrelated
    library's ``"ERROR"`` outranking a real release-global ``BREAKING`` in
    the very same ``worst_verdict`` collapse.

    *severity_exit_code* being not ``None`` is what "severity scheme
    active" means, matching ``_exit_compare_release``'s own check.
    *operational_error_contribution* scans *library_results* directly
    (Codex review, fresh evidence) rather than checking ``worst_verdict ==
    "ERROR"`` -- an earlier revision did the latter, which reads ``0``
    whenever a *different* library's ``"not_comparable"`` verdict outranks
    ``"ERROR"`` in ``_RELEASE_VERDICT_ORDER`` and becomes the aggregate
    ``worst_verdict``, even though a real operational failure still
    happened elsewhere in the release and `resolve_release_exit_decision`'s
    own ``not_comparable`` branch already preserves this value for exactly
    that explainability case.
    """
    not_comparable = worst_verdict == "not_comparable"
    severity_scheme_active = severity_exit_code is not None
    removed_required_library = fail_on_removed and bool(removed_keys)
    operational_error_contribution = 4 if any(
        isinstance(e, dict) and e.get("verdict") == "ERROR" for e in library_results
    ) else 0
    verdict_or_severity_contribution = (
        (severity_exit_code or 0)
        if severity_scheme_active
        else _compute_release_legacy_exit_code(
            worst_verdict, library_results, release_global_verdict
        )
    )
    return resolve_release_exit_decision(
        not_comparable=not_comparable,
        severity_scheme_active=severity_scheme_active,
        verdict_or_severity_contribution=verdict_or_severity_contribution,
        removed_required_library=removed_required_library,
        contract_coverage_contribution=contract_coverage_exit_contribution,
        operational_error_contribution=operational_error_contribution,
    )
