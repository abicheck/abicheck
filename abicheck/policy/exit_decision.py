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

"""The canonical ``ExitDecision`` — CLI cleanup phase two, PR G1.

``docs/contribute/plans/cli-cleanup-phase-two.md``'s PR 4 records that a
single-pair `compare` invocation's exit code is folded from
several independently-computed, orthogonal contributions today
(`severity.compute_exit_code`/`severity.legacy_exit_code`,
`contract_coverage_exit.fold_coverage_exit`,
`analysis_assurance.fold_analysis_assurance_exit`), each folded in with
`max()` at the call site (`cli._exit_with_severity_or_verdict`) rather than
through one shared, explainable object. That is fine for computing *a*
number, but leaves no answer to "why is this exit 1" when more than one axis
independently contributes -- a caller has to re-derive the answer from
several separately-read report fields.

``ExitDecision``/:func:`resolve_exit_decision` is that explainable object,
built additively (PR G1: no CLI behaviour change, no flag removed) as the
first step toward PR 4/G2's actual algorithm-selector removal. It wraps the
*existing* fold -- `max()` over the axes below -- rather than replacing it,
so every call site that adopts it keeps today's exit code bit-for-bit; what
it adds is `reasons`, the set of axes whose own contribution equals the
final code (and therefore genuinely explains it, as opposed to a lower
contribution that never determined the result).

**Deliberately scoped to the axes that already coexist on one
`DiffResult`-backed report today** -- the compatibility gate (legacy verdict
or severity-aware), contract coverage, and analysis assurance. Three more
axes the reviewed plan also names -- `not_comparable`, a release's
removed-required-library policy, and `scan`'s budget-overflow/evidence-
contract-error floors -- are raised through different code paths today
(`sys.exit` before a coherent `DiffResult` exists, or a release/scan-specific
fold with its own, mode-dependent precedence against the compatibility
gate).

``docs/contribute/adr/064-canonical-gate-algorithm-and-exit-decision.md``
("ADR-064") is the settled design for those three axes and for PR 4/G2's
actual `--exit-code-scheme` removal. Its *additive* first stage
(stage 1a) is implemented in the sibling module
:mod:`abicheck.policy.exit_decision_precedence` --
:func:`~abicheck.policy.exit_decision_precedence.resolve_scan_exit_decision`
and :func:`~abicheck.policy.exit_decision_precedence.
resolve_release_exit_decision` are pure functions reproducing today's exact
precedence for `scan` and the directory/package release fan-out
respectively (verified against `scan_engine.py`/
`cli_compare_release_helpers.py` at the commit that added them) -- neither
is wired into any call site's *actually returned* exit code yet, exactly
the same "wraps the existing behaviour, doesn't call it yet" scoping PR G1
itself used for the three axes above. Split into its own module purely for
this package's 800-line production cap (Codex review: the combined module
reached 824 lines) -- both modules implement the identical stage 1a scope.
Wiring these into the report's `exit` block (`scan --against`'s nested
`diff.exit`, the release fan-out's own summary) is stage 1b. Stage 1b
landed *partially*: :meth:`ExitDecision.to_dict` now serializes all five
ADR-064 fields (report schema 2.47/1.22), `scan`'s `NOT_COMPARABLE` outcome
persists a real `diff.exit` block via `resolve_scan_exit_decision`, and the
release fan-out's JSON summary gains an `exit` block via
`resolve_release_exit_decision`, verified to always agree numerically with
`cli_compare_release_helpers._exit_compare_release`'s own, independently
computed and heavily pinned exit code (`tests/test_exit_code_integrity.py`'s
`TestReleaseExitDecisionForReportAgreesWithRealExit`) without changing that
function itself. `scan`'s `_BudgetOverflow`/`_EvidenceContractError` abort
points (`scan_engine.py`), which raise before any report exists, have since
landed too: the typed `service_scan.ScanResult` API persists a real
`ExitDecision` into `report["exit"]` for both
(`abicheck.workflows.scan_abort_result.scan_abort_result_fields`, prior
gate/coverage/assurance contributions preserved across a *late*
`_BudgetOverflow` via `attach_prior_on_budget_overflow`), and the native
`scan` CLI's own `--format json` invocation gets the same report shape on
either abort via `cli_scan._emit_scan_abort_report` (`--format text` is
unaffected, per that design's own account of what remained genuinely open).
See ADR-064's own "Stage 1b, further split" section for the full account.
The atomic `--exit-code-scheme` removal remains stage 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..checker_types import DiffResult
    from .severity import SeverityConfig


class ExitReason(str, Enum):
    """Which orthogonal axis a resolved :class:`ExitDecision` traces to.

    A member appears in :attr:`ExitDecision.reasons` only when its own
    contribution equals the decision's final :attr:`ExitDecision.code` --
    i.e. it is one of the axes that actually determined the result, not
    merely one that happened to be non-zero. A coverage floor of ``1``
    sitting underneath a compatibility gate's ``4`` explains nothing about
    why the exit is ``4``; it stays out of ``reasons`` for that decision
    (its own finding/failure still appears elsewhere in the report --
    ``reasons`` is about the exit code specifically, not about hiding the
    axis).
    """

    COMPATIBILITY_GATE = "compatibility_gate"
    SCOPED_GATE = "scoped_gate"
    CONTRACT_COVERAGE = "contract_coverage"
    ANALYSIS_ASSURANCE = "analysis_assurance"
    CLEAN = "clean"
    #: `scan --against` only. A maintainer-promoted `--crosscheck KEY=error`
    #: finding (`scan_engine._crosscheck_severity_exit`) raised the exit code
    #: past what the three compatibility/coverage/assurance axes would have
    #: produced on their own. :func:`resolve_exit_decision` *does* model
    #: this as a real fourth contribution
    #: (`ExitDecision.crosscheck_promotion_contribution`) when a caller
    #: passes one in -- `resolve_compare_exit_decision` (native `compare`)
    #: never does, since crosscheck promotion has no meaning outside
    #: `scan --against`, so it is always `0`/absent there. The scan-only
    #: half that stays true is *when* the contribution is known:
    #: `scan_engine._promote_published_gate` re-resolves the whole decision
    #: through `resolve_exit_decision` (with the crosscheck contribution
    #: filled in) only *after* the fact, once a promotion actually fires --
    #: mirroring how that same function already patches the persisted
    #: `severity` block for the identical reason -- a published `exit`
    #: block that still named `compatibility_gate` for a code the
    #: crosscheck promotion actually produced would be exactly the kind of
    #: "explains nothing about why the exit is N" trap this enum exists to
    #: avoid.
    PROMOTED_CROSSCHECK = "promoted_crosscheck"

    #: `scan` only (ADR-037 D5). A pinned, non-`auto` `--depth`/
    #: `--source-method` had no source evidence to satisfy it
    #: (`scan_engine._EvidenceContractError`) -- raised during evidence
    #: collection, before a candidate/baseline comparison is even attempted.
    #: Dominates every other axis below it in ADR-064's precedence order,
    #: since none of them were ever computed for this run.
    EVIDENCE_CONTRACT_ERROR = "evidence_contract_error"
    #: `scan` only. `--budget` overflowed (`scan_engine._BudgetOverflow`).
    #: Checked *after* a `not_comparable` result may already have been
    #: decided for the same run, and -- per ADR-064's "budget dominates
    #: not-comparable" rule, reproducing `scan_engine.run_scan_core`'s own
    #: unconditional post-comparison budget check -- discards that result
    #: rather than losing to it.
    BUDGET_OVERFLOW = "budget_overflow"
    #: OLD and NEW (or, for a release, at least one library pair) were not
    #: extracted under a comparable profile/scope contract (ADR-050 D2), so
    #: no verdict was ever produced. Dominates the compatibility gate and
    #: removed-required-library axes below it, but never `scan`'s own
    #: `BUDGET_OVERFLOW` (see that reason's own docstring). No `DiffResult`
    #: exists for this outcome, so contract-coverage/analysis-assurance are
    #: never computed either -- every other contribution is `0` when this
    #: reason applies.
    NOT_COMPARABLE = "not_comparable"
    #: A directory/package release comparison only. `--fail-on-removed-
    #: library` is set and at least one library was removed between
    #: releases. Its rank relative to the compatibility gate is
    #: **mode-dependent, not fixed** -- see
    #: :func:`resolve_release_exit_decision`'s own docstring and ADR-064's
    #: "Removed-required-library is mode-dependent" section for the exact
    #: switch this reason's precedence reproduces.
    REMOVED_REQUIRED_LIBRARY = "removed_required_library"
    #: A directory/package release comparison only. The aggregated code
    #: `resolve_release_exit_decision` folded as its compatibility axis
    #: came from a library that failed to dump/extract/compare (the
    #: release fan-out's own operational `ERROR` sentinel, floored to `4`)
    #: -- not from a real `API_BREAK`/`BREAKING` verdict or a severity
    #: category. Passing that code through under `COMPATIBILITY_GATE`
    #: (Codex review, fresh evidence) would falsely claim an ABI/API or
    #: policy finding decided the exit, when the comparison for that
    #: library never actually ran to completion.
    OPERATIONAL_ERROR = "operational_error"
    #: A directory/package release comparison only (ADR-065 D6). A
    #: selected, expected member of the comparison scope never reached a
    #: completed comparison (unmatched with no counterpart supplied,
    #: unsupported by this build, failed, or ambiguously matched) and the
    #: run's completeness policy is ``block`` -- so the scope axis
    #: contributed ``1``, folded with ``max()`` exactly like
    #: ``CONTRACT_COVERAGE``: it raises a clean ``0`` to ``1`` and never
    #: lowers a real ``2``/``4``. Under the default ``warn`` policy the axis
    #: contributes ``0`` and never appears here, while the report's
    #: ``run_outcome.scope`` still reads ``incomplete``.
    INCOMPLETE_SCOPE = "incomplete_scope"
    #: A directory/package release comparison only (ADR-065 D7). The
    #: selected scope produced no valid comparison at all -- zero matched
    #: pairs, or every selected member failed/was unsupported. Contributes
    #: ``1`` under **every** completeness policy: ``warn`` can downgrade
    #: missing members, never "nothing compared" into "compatibility
    #: checked". A fold participant (never dominant), so a proven
    #: removed-library ``8`` or a ``not_comparable`` ``16`` still wins.
    NO_COMPARISON_COMPLETED = "no_comparison_completed"


@dataclass(frozen=True)
class ExitDecision:
    """One comparison's fully explainable exit code.

    ``code`` is exactly ``max()`` over every contribution field below --
    the first four (``compatibility_contribution`` through
    ``crosscheck_promotion_contribution``) are the axes PR G1 modeled: the
    first three are the identical value today's ad hoc fold chain in
    ``cli._exit_with_severity_or_verdict`` already produces, computed once
    here instead of via three separately-called functions; the fourth
    exists only for `scan --against`'s own maintainer-promoted
    `--crosscheck KEY=error` finding and is always `0` for a native
    `compare` report (see :class:`ExitReason.PROMOTED_CROSSCHECK`).
    ``reasons`` names every axis tied for that maximum; see
    :class:`ExitReason` for why a lower, non-winning contribution is
    excluded.

    A fifth field, ``operational_error_contribution``, joins that same
    tie-inclusive fold -- ADR-064's addition for a directory/package
    release, where one library's operational `ERROR` (a dump/extract/
    compare failure, not a `Verdict`) and *another* library's real
    compatibility-gate finding are independently computed and can
    genuinely tie (`resolve_release_exit_decision`'s own docstring). It is
    a full fold participant, not one of the four "dominant" fields below,
    precisely because it must be able to tie with ``compatibility_
    contribution`` rather than override it -- an earlier revision folded
    an operational failure into ``compatibility_contribution`` itself
    under a relabeled reason, which silently dropped the tied
    compatibility-gate finding from ``reasons`` whenever both happened to
    equal the same code (Codex review, fresh evidence). `0` for every
    caller but the release resolver.

    The remaining four fields (``evidence_contract_error_contribution``
    through ``removed_required_library_contribution``) are ADR-064's other
    addition, set only by the sibling
    :mod:`abicheck.policy.exit_decision_precedence` module's
    ``resolve_scan_exit_decision``/``resolve_release_exit_decision`` --
    every decision built by :func:`resolve_exit_decision`/
    :func:`resolve_compare_exit_decision`
    (every existing call site) leaves all four at their `0` default, so
    the invariant above is unaffected for them. A decision built by one of
    the two ADR-064 resolvers sets *at most one* of these four to a
    nonzero value (they are mutually exclusive by construction -- each
    corresponds to one precedence-ordered early-return branch, never two
    at once; a preserved, non-deciding `operational_error_contribution`
    may still accompany one of them -- `resolve_release_exit_decision`'s
    `not_comparable` and severity-scheme removed-library branches both do
    this, see their own comments), and that value is always `code` itself:
    each of those axes' real numeric code
    (`1`/`5`/`6`/`8`/`16`) is chosen large enough that it always exceeds
    whatever the other fields could independently contribute (`0`-`4`), so
    recording a "prior" or already-available value there alongside a
    nonzero dominant field can never change which one is the maximum.

    **Now serialized by :meth:`to_dict` (ADR-064 stage 1b, report schema
    2.47/1.22).** All five fields default to ``0`` and stay ``0`` for every
    decision built by :func:`resolve_exit_decision`/
    :func:`resolve_compare_exit_decision` (every pre-existing `compare`/
    `scan --against` call site), so a report produced by an already-shipped
    code path gains five always-``0`` keys and nothing else changes -- the
    additive bump this class's own field docstrings anticipated. A decision
    built by :func:`~abicheck.policy.exit_decision_precedence.
    resolve_scan_exit_decision`/:func:`~abicheck.policy.
    exit_decision_precedence.resolve_release_exit_decision` sets at most one
    of the four dominant fields nonzero, per those fields' own docstrings.
    """

    code: int
    reasons: tuple[ExitReason, ...]
    compatibility_contribution: int
    contract_coverage_contribution: int
    analysis_assurance_contribution: int
    #: `scan --against` only -- what a maintainer-promoted `--crosscheck
    #: KEY=error` finding contributes (`0` for every other caller, and for
    #: a scan run where no promotion fired). A *fourth* axis, not a
    #: bolt-on mutation of `code`/`reasons` after the fact (Codex review,
    #: fresh evidence): `scan_engine._promote_published_gate` used to patch
    #: only those two fields, leaving the three contributions above
    #: summing to less than the new `code` -- silently breaking this
    #: class's own documented invariant that `code == max(the
    #: contributions)`, and also never adding `PROMOTED_CROSSCHECK` to
    #: `reasons` on an exact tie (a hand-rolled strict `>` check, unlike
    #: this function's own tie-inclusive fold). Modeling it as a real
    #: contribution lets `_promote_published_gate` reconstruct the whole
    #: decision through :func:`resolve_exit_decision` instead of hand-
    #: patching two of its five fields.
    crosscheck_promotion_contribution: int = 0
    #: A directory/package release comparison only (ADR-064). What one
    #: library's operational `ERROR` sentinel (a dump/extract/compare
    #: failure, not a `Verdict`) contributes -- a genuine fold participant
    #: alongside `compatibility_contribution`, so a real compatibility-gate
    #: finding from a *different* library in the same release and this
    #: axis can tie and both be named in `reasons`, rather than one
    #: silently replacing the other's reason. `0` for every caller but
    #: `resolve_release_exit_decision`. See :class:`ExitReason.
    #: OPERATIONAL_ERROR` and this class's own docstring above.
    operational_error_contribution: int = 0
    #: A directory/package release comparison only (ADR-065 D6). ``0``/``1``,
    #: a fold participant shaped exactly like ``contract_coverage_
    #: contribution`` -- see :class:`ExitReason.INCOMPLETE_SCOPE`. ``0`` for
    #: every caller but `resolve_release_exit_decision`, and ``0`` under the
    #: default ``warn`` completeness policy even when the scope is
    #: incomplete (the report's ``run_outcome.scope`` carries that fact).
    incomplete_scope_contribution: int = 0
    #: A directory/package release comparison only (ADR-065 D7). ``0``/``1``,
    #: a fold participant -- see :class:`ExitReason.NO_COMPARISON_
    #: COMPLETED`. ``0`` for every caller but `resolve_release_exit_decision`.
    no_comparison_completed_contribution: int = 0

    #: `scan` only. Nonzero (always equal to `code`) exactly when
    #: :func:`resolve_scan_exit_decision` returned this decision because
    #: `_EvidenceContractError` fired. `0` for every decision built any
    #: other way.
    evidence_contract_error_contribution: int = 0
    #: `scan` only. Nonzero (always equal to `code`) exactly when
    #: :func:`resolve_scan_exit_decision` returned this decision because
    #: `_BudgetOverflow` fired. `0` for every decision built any other way.
    budget_overflow_contribution: int = 0
    #: Nonzero (always equal to `code`) exactly when
    #: :func:`resolve_scan_exit_decision`/:func:`resolve_release_exit_
    #: decision` returned this decision because the comparison was not
    #: comparable (ADR-050 D2). `0` for every decision built any other way
    #: -- including native `compare`'s own not-comparable outcome, which
    #: has no `DiffResult`/`ExitDecision` at all (see
    #: :func:`resolve_compare_exit_decision`'s own docstring).
    not_comparable_contribution: int = 0
    #: A directory/package release comparison only. Nonzero (always equal
    #: to `code`) exactly when :func:`resolve_release_exit_decision`
    #: returned this decision because removed-required-library won its
    #: mode-dependent precedence check. `0` whenever removed-required-
    #: library was not consulted at all (a nonzero legacy-scheme verdict/
    #: `ERROR` contribution already won first) as well as whenever it was
    #: consulted and lost (it wasn't set) -- both are genuinely "did not
    #: determine this outcome," which `0` correctly states either way.
    removed_required_library_contribution: int = 0

    def to_dict(self) -> dict[str, object]:
        """JSON-serializable form, for the report's ``exit`` block.

        The five ADR-064 fields (``operational_error_contribution`` through
        ``removed_required_library_contribution``) joined this shape in
        report schema 2.47/1.22 (stage 1b) -- see this class's own
        docstring for why every pre-existing decision emits them as ``0``
        rather than omitting them.
        """
        return {
            "code": self.code,
            "reasons": [r.value for r in self.reasons],
            "compatibility_contribution": self.compatibility_contribution,
            "contract_coverage_contribution": self.contract_coverage_contribution,
            "analysis_assurance_contribution": self.analysis_assurance_contribution,
            "crosscheck_promotion_contribution": self.crosscheck_promotion_contribution,
            "operational_error_contribution": self.operational_error_contribution,
            "evidence_contract_error_contribution": (
                self.evidence_contract_error_contribution
            ),
            "budget_overflow_contribution": self.budget_overflow_contribution,
            "not_comparable_contribution": self.not_comparable_contribution,
            "removed_required_library_contribution": (
                self.removed_required_library_contribution
            ),
            "incomplete_scope_contribution": self.incomplete_scope_contribution,
            "no_comparison_completed_contribution": (
                self.no_comparison_completed_contribution
            ),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ExitDecision:
        """Reconstruct a decision from :meth:`to_dict`'s own output.

        The exact inverse of :meth:`to_dict` -- round-trips every field,
        including the five ADR-064 additions, which default to ``0`` via
        ``.get`` so a pre-2.47/1.22 persisted dict (missing those keys
        entirely) reconstructs the same way an already-in-memory decision
        built before those fields existed would: "never asked," not
        "evaluated and came out clean." Exists for a caller that only has
        the JSON-serialized form available -- e.g. a raw ``diff_summary
        ["exit"]`` dict a scan engine persisted earlier in a run, carried
        across an exception boundary that cannot hold the dataclass itself
        (`abicheck.scan_engine._BudgetOverflow`'s own ``prior_decision``,
        ADR-064's "preserve prior contributions on a later budget overflow"
        follow-up).
        """
        return cls(
            code=d["code"],
            reasons=tuple(ExitReason(r) for r in d["reasons"]),
            compatibility_contribution=d["compatibility_contribution"],
            contract_coverage_contribution=d["contract_coverage_contribution"],
            analysis_assurance_contribution=d["analysis_assurance_contribution"],
            crosscheck_promotion_contribution=d.get(
                "crosscheck_promotion_contribution", 0
            ),
            operational_error_contribution=d.get("operational_error_contribution", 0),
            evidence_contract_error_contribution=d.get(
                "evidence_contract_error_contribution", 0
            ),
            budget_overflow_contribution=d.get("budget_overflow_contribution", 0),
            not_comparable_contribution=d.get("not_comparable_contribution", 0),
            removed_required_library_contribution=d.get(
                "removed_required_library_contribution", 0
            ),
            incomplete_scope_contribution=d.get("incomplete_scope_contribution", 0),
            no_comparison_completed_contribution=d.get(
                "no_comparison_completed_contribution", 0
            ),
        )


def resolve_exit_decision(
    *,
    compatibility_contribution: int,
    contract_coverage_contribution: int = 0,
    analysis_assurance_contribution: int = 0,
    crosscheck_promotion_contribution: int = 0,
    operational_error_contribution: int = 0,
    evidence_contract_error_contribution: int = 0,
    budget_overflow_contribution: int = 0,
    not_comparable_contribution: int = 0,
    incomplete_scope_contribution: int = 0,
    no_comparison_completed_contribution: int = 0,
    compatibility_reason: ExitReason = ExitReason.COMPATIBILITY_GATE,
) -> ExitDecision:
    """Fold the axis contributions below into one explainable decision.

    *compatibility_contribution* is the caller's own pre-computed
    compatibility-gate exit code -- either `severity.legacy_exit_code`
    (verdict-based, no severity config in effect) or
    `severity.compute_exit_code` (a severity map is in effect), or (Codex
    review) the *pre-fold* `--used-by`/`--required-symbol(s)` scoped gate
    contribution -- never an already-folded value, or a tie with coverage/
    assurance would be silently hidden behind whichever reason this slot is
    labelled. This function does not choose between the unscoped
    algorithms; that selection is exactly what PR 4/G2 still has to
    consolidate into one automatic algorithm.
    *contract_coverage_contribution*/*analysis_assurance_contribution*
    default to ``0`` (their "never asked the question" value, matching
    `contract_coverage_exit.coverage_exit_floor`/`analysis_assurance.
    analysis_assurance_exit_contribution`'s own fail-open defaults) so a
    caller with neither `--contract` nor `--require-complete-analysis` in
    effect can omit both. *compatibility_reason* names which
    :class:`ExitReason` this slot represents -- `COMPATIBILITY_GATE` by
    default, or `SCOPED_GATE` when the caller's compatibility contribution
    is the scoped application/plugin-host gate rather than the full-library
    one; every other axis's reason is unaffected either way.
    *crosscheck_promotion_contribution* defaults to ``0`` (every caller but
    `scan_engine._promote_published_gate`, which is the only place a
    maintainer-promoted `--crosscheck KEY=error` finding's own exit
    contribution is known) -- see :class:`ExitDecision`'s own field
    docstring for why this has to be a real axis rather than a post-hoc
    patch to `code`/`reasons`.
    *operational_error_contribution* defaults to ``0`` (every caller but
    `resolve_release_exit_decision`, ADR-064's release resolver) --
    :class:`ExitReason.OPERATIONAL_ERROR`'s own fixed reason, unlike
    *compatibility_reason*, since a caller with a genuine operational
    failure independent of any real ABI/API/policy finding needs it to
    coexist with (and, on a tie, be named alongside) `compatibility_
    contribution` rather than replace its reason -- see
    `resolve_release_exit_decision`'s own docstring for why collapsing the
    two into one contribution under a single reason (an earlier revision's
    `is_operational_error` boolean) hid a real, independently-computed
    compatibility-gate finding whenever both happened to tie (Codex
    review, fresh evidence).
    *evidence_contract_error_contribution*/*budget_overflow_contribution*/
    *not_comparable_contribution* default to ``0`` (every caller but
    ``buildsource.check_report._neutralize_gate``, which preserves
    whichever single one of these "the comparison never completed" axes a
    report's own pre-existing decision already carried, alongside
    *operational_error_contribution* -- ADR-064's ``resolve_scan_exit_
    decision`` treats them as mutually exclusive abort paths, so at most
    one is ever nonzero on a real persisted decision; folded here the same
    tie-inclusive way as every other axis purely so a caller preserving one
    does not have to duplicate this function's own fold logic).
    *incomplete_scope_contribution*/*no_comparison_completed_contribution*
    (ADR-065 D6/D7) default to ``0`` (every caller but
    `resolve_release_exit_decision`) -- two more ``0``/``1`` fold
    participants, so a genuine tie with coverage/assurance/operational-error
    is named rather than hidden, exactly as for the coverage axis.
    """
    contributions = {
        compatibility_reason: compatibility_contribution,
        ExitReason.CONTRACT_COVERAGE: contract_coverage_contribution,
        ExitReason.ANALYSIS_ASSURANCE: analysis_assurance_contribution,
        ExitReason.PROMOTED_CROSSCHECK: crosscheck_promotion_contribution,
        ExitReason.OPERATIONAL_ERROR: operational_error_contribution,
        ExitReason.EVIDENCE_CONTRACT_ERROR: evidence_contract_error_contribution,
        ExitReason.BUDGET_OVERFLOW: budget_overflow_contribution,
        ExitReason.NOT_COMPARABLE: not_comparable_contribution,
        ExitReason.INCOMPLETE_SCOPE: incomplete_scope_contribution,
        ExitReason.NO_COMPARISON_COMPLETED: no_comparison_completed_contribution,
    }
    code = max(contributions.values())
    if code == 0:
        reasons: tuple[ExitReason, ...] = (ExitReason.CLEAN,)
    else:
        reasons = tuple(
            reason
            for reason, contribution in contributions.items()
            if contribution == code
        )
    return ExitDecision(
        code=code,
        reasons=reasons,
        compatibility_contribution=compatibility_contribution,
        contract_coverage_contribution=contract_coverage_contribution,
        analysis_assurance_contribution=analysis_assurance_contribution,
        crosscheck_promotion_contribution=crosscheck_promotion_contribution,
        operational_error_contribution=operational_error_contribution,
        evidence_contract_error_contribution=evidence_contract_error_contribution,
        budget_overflow_contribution=budget_overflow_contribution,
        not_comparable_contribution=not_comparable_contribution,
        incomplete_scope_contribution=incomplete_scope_contribution,
        no_comparison_completed_contribution=no_comparison_completed_contribution,
    )


def resolve_compare_exit_decision(
    result: DiffResult,
    sev_config: SeverityConfig | None,
    scheme: str,
    *,
    require_complete_analysis: bool = False,
) -> ExitDecision:
    """:func:`resolve_exit_decision`, deriving every contribution from
    *result* the same way `cli._exit_with_severity_or_verdict` does today.

    The call site a native `compare` invocation needs: it reproduces
    that function's exact fold order (compatibility → coverage floor →
    assurance floor, each `max`-based) as one canonical resolution, so a
    caller building the report's ``exit`` block and a caller computing the
    real process exit code cannot read two different numbers for the same
    comparison.

    **`scan --against` also calls this function (CLI cleanup phase two, PR
    E), from `cli_scan_baseline._run_baseline_compare`, which nests the
    result at ``diff.exit`` rather than the report's top level -- matching
    where its own constituent `analysis_assurance_exit_contribution`/
    `contract_coverage_exit_contribution` fields already live, not
    `ScanOutcome`'s own top-level ``verdict``/``exit_code``.** That
    top-level pair folds strictly more than this function ever will for a
    scan: budget overflow, `NOT_COMPARABLE`, and a maintainer-promoted
    `--crosscheck KEY=error` finding (`scan_engine._crosscheck_severity_
    exit`) are scan-only axes raised through their own code paths, not
    modeled by this resolver (see this module's own docstring for why).
    `scan_engine._promote_published_gate` keeps the persisted ``diff.exit``
    block honest for the one of those three that can happen *after* this
    function already ran -- crosscheck promotion -- by raising its ``code``
    and re-stamping ``reasons`` to ``PROMOTED_CROSSCHECK``, the same way it
    already patches the persisted ``severity`` block. Budget overflow
    aborts before a report is built at all; `NOT_COMPARABLE` has no
    `DiffResult` for this resolver to read from, so no ``exit`` block is
    emitted for that case either.

    **`--used-by`/`--required-symbol(s)` scoped gating overrides the
    compatibility axis entirely (Codex review, fresh evidence).**
    `cli_compare_helpers._apply_scoped_gating` floors the *full-library*
    verdict/severity gate this function would otherwise compute to the
    scoped application/plugin-host contract's own result -- `cli.py` itself
    exits on the (separately, already-folded) `result.scoped_exit_code`
    directly (`sys.exit(scoped_exit_code)`), never reaching
    `_exit_with_severity_or_verdict`/this function's own compatibility-axis
    computation. An earlier revision of this function ignored that and
    derived `compatibility_contribution` from `result.verdict`/severity
    regardless -- reporting the full-library gate's code (informational-only
    under scoping) while the real process exited on the scoped one.

    **A later revision fixed that but introduced a second bug (Codex
    review): it read the already-folded `result.scoped_exit_code` as the
    compatibility contribution, so a tie with coverage/assurance was hidden
    behind a `SCOPED_GATE` reason that may not have contributed at all.**
    `result.scoped_compatibility_contribution` (`cli_compare_helpers`,
    persisted immediately before that fold runs) is the *pre-fold* scoped
    value -- passed through the same `resolve_exit_decision` every other
    caller uses, with `compatibility_reason=SCOPED_GATE`, so a genuine tie
    between the scoped gate and coverage/assurance is named correctly
    instead of always attributed to scoping.
    """
    from ..analysis_assurance import analysis_assurance_exit_contribution
    from .contract_coverage_exit import coverage_exit_floor
    from .severity import compute_exit_code, legacy_exit_code

    coverage_contribution = coverage_exit_floor(result)
    assurance_contribution = analysis_assurance_exit_contribution(
        result, require_complete=require_complete_analysis
    )

    scoped_exit_code = getattr(result, "scoped_exit_code", None)
    if scoped_exit_code is not None:
        scoped_compatibility_contribution = getattr(
            result, "scoped_compatibility_contribution", scoped_exit_code,
        )
        return resolve_exit_decision(
            compatibility_contribution=scoped_compatibility_contribution,
            contract_coverage_contribution=coverage_contribution,
            analysis_assurance_contribution=assurance_contribution,
            compatibility_reason=ExitReason.SCOPED_GATE,
        )

    if scheme == "severity":
        assert sev_config is not None
        compatibility_contribution = compute_exit_code(
            result.changes,
            sev_config,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
    else:
        compatibility_contribution = legacy_exit_code(result.verdict)
    return resolve_exit_decision(
        compatibility_contribution=compatibility_contribution,
        contract_coverage_contribution=coverage_contribution,
        analysis_assurance_contribution=assurance_contribution,
    )

