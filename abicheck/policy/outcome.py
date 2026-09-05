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

"""ADR-063 Phase 7: ``RunOutcome`` -- one report-level, independent-axis
description of what a run decided, so every downstream front end reads five
typed axes instead of re-deriving them from a raw ``exit_code`` integer.

See ``docs/contribute/adr/063-one-semantic-pipeline.md`` D6 and
``docs/contribute/plans/one-semantic-pipeline.md``'s Phase 7 section for the
full design and the corrections layered onto it across review rounds. In
short:

* ``compatibility`` -- the existing ``Verdict``/``None`` axis ("is this
  ABI/API compatible"; ``None`` only for a report that never ran a real
  comparison at all -- a bootstrap/new-target/operational-error synthetic
  report).
* ``assurance`` -- the existing ``AnalysisAssurance`` concept ("how complete
  was the evidence"), stored untyped (``object | None``) for the same
  circular-import reason ``checker_types.DiffResult.analysis_assurance`` is
  -- ``analysis_assurance.py`` is not a leaf this package may import from at
  module scope. Unwrap via :func:`analysis_assurance_dict` (mirrors
  ``analysis_assurance.analysis_assurance_report_dict``'s own narrowing).
* ``gate`` -- :class:`PolicyGateDecision`, a **new, exit-code-free** ordered
  type (``NONE < ADDITION_QUALITY < POTENTIAL_BREAKING < ABI_BREAKING``,
  mirroring ``severity.compute_exit_code``'s own ``IssueCategory`` order).
  Not a reuse of ``severity.GateDecision``, which carries exactly the
  scheme-encoded ``exit_code``/``blocking`` data this axis keeps out of
  domain objects -- that type is what the boundary encoders convert *to*.
* ``operational`` -- :class:`OperationalStatus`, the axis
  ``PolicyGateDecision`` alone cannot represent: a run that never produced a
  real compatibility verdict (a budget overflow, a hard evidence-contract
  error, an extraction failure, a comparability refusal), as opposed to one
  that did and scored it ``ABI_BREAKING``.
* ``lifecycle`` -- :class:`TargetLifecycle`, meaningful only where an
  ``aggregate`` "target" concept exists at all; every other ``RunOutcome``
  construction uses the fixed default ``EXISTING``.

**Boundary encoders only.** Converting ``PolicyGateDecision``/
``OperationalStatus`` to a real process exit code or a ``severity.
GateDecision`` is confined to the small set of encoders ADR-063 D6 names:
``policy/outcome.py`` itself (this module -- the one place a
``RunOutcome`` axis is decoded from raw report fields), plus
``workflows/aggregate/gate.py``, ``cli.py``, ``service.py``, and
``workflows/aggregate/fold.py``/``aggregate``'s own ``exit_code()``. No
other module should compare a ``PolicyGateDecision``/``OperationalStatus``
against a raw integer or literal -- see
``scripts/check_ai_readiness.py``'s ``no-inline-gate-computation`` check.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..model.change_catalog.registry import Verdict

__all__ = [
    "OperationalStatus",
    "PolicyGateDecision",
    "RunOutcome",
    "RUN_OUTCOME_SCHEMA_VERSION",
    "run_outcome_scope_required",
    "ScopeCompleteness",
    "TargetLifecycle",
    "analysis_assurance_dict",
    "fold_gate_and_operational",
    "operational_status_exit_code",
    "policy_gate_decision_exit_code",
    "policy_gate_decision_for_exit_code",
    "worst_real_verdict",
]

#: Versioned independently of ``REPORT_SCHEMA_VERSION``/``SCAN_SCHEMA_
#: VERSION`` (which each still gain their own bump for the new top-level
#: ``run_outcome`` report key) -- the same self-contained-sub-object
#: convention ``analysis_assurance.ANALYSIS_ASSURANCE_SCHEMA_VERSION`` and
#: ``buildsource.model.BUILD_SOURCE_PACK_VERSION`` already use.
#:
#: ``1.1`` (ADR-065 S2): the decision-bearing ``scope`` axis and the
#: ``no_comparison_completed`` operational status. A ``1.0`` block (no
#: ``scope``) still reads through :meth:`RunOutcome.from_dict`, which
#: backfills ``complete`` -- true of every ``1.0`` writer, all of which
#: described one pair.
RUN_OUTCOME_SCHEMA_VERSION = "1.1"


#: Pre-``scope`` ``run_outcome`` versions (``0.x``/``1``/``1.0``): textually
#: the published schema's own exemption pattern, so the readers agree.
_RUN_OUTCOME_SCOPELESS_VERSIONS = re.compile(r"^(0(\.[0-9]+)*|1(\.0+)?)$")


def run_outcome_scope_required(schema_version: object) -> bool:
    """Whether a block stamped *schema_version* must carry ``scope`` (ADR-065
    D6): every version but the pre-axis pattern above. An absent stamp is a
    pre-axis writer; a present unparseable or non-string one is required,
    never read as predating the axis (Codex review)."""
    if schema_version is None:
        return False
    return not (
        isinstance(schema_version, str)
        and _RUN_OUTCOME_SCOPELESS_VERSIONS.match(schema_version) is not None
    )


class PolicyGateDecision(str, Enum):
    """The compatibility-policy gate axis -- ordered, exit-code-free.

    Mirrors the ``IssueCategory`` ordering ``severity.compute_exit_code``
    already uses internally (``NONE < ADDITION_QUALITY < POTENTIAL_BREAKING
    < ABI_BREAKING``). A boundary encoder converts this to a real exit code
    via :func:`policy_gate_decision_exit_code` -- never the reverse.
    """

    NONE = "none"
    ADDITION_QUALITY = "addition_quality"
    POTENTIAL_BREAKING = "potential_breaking"
    ABI_BREAKING = "abi_breaking"


#: The one place ``PolicyGateDecision`` is given exit-code meaning -- the
#: same 0/1/2/4 severity-aware scheme ``policy.severity._CATEGORY_EXIT_
#: CODES``/``workflows.aggregate.gate._VALID_GATE_EXIT`` already share, so a
#: ``PolicyGateDecision`` and a legacy-scheme ``GateDecision.exit_code`` map
#: onto exactly the same four values.
_GATE_EXIT_CODE: dict[PolicyGateDecision, int] = {
    PolicyGateDecision.NONE: 0,
    PolicyGateDecision.ADDITION_QUALITY: 1,
    PolicyGateDecision.POTENTIAL_BREAKING: 2,
    PolicyGateDecision.ABI_BREAKING: 4,
}
_EXIT_CODE_GATE: dict[int, PolicyGateDecision] = {
    code: gate for gate, code in _GATE_EXIT_CODE.items()
}


def policy_gate_decision_exit_code(gate: PolicyGateDecision) -> int:
    """The exit code *gate* contributes under the shared 0/1/2/4 scheme."""
    return _GATE_EXIT_CODE[gate]


def policy_gate_decision_for_exit_code(code: int) -> PolicyGateDecision:
    """The :class:`PolicyGateDecision` a raw compatibility exit *code*
    represents.

    *code* must be one of ``0``/``1``/``2``/``4`` -- the same scheme
    ``workflows.aggregate.gate._VALID_GATE_EXIT`` already validates against.
    An unrecognized code fails safe to :attr:`PolicyGateDecision.ABI_
    BREAKING`, matching ``severity.classify_change``'s own "unclassified is
    treated as breaking" fail-safe convention, rather than silently
    reporting ``NONE`` for a code this scheme doesn't otherwise produce.
    """
    return _EXIT_CODE_GATE.get(code, PolicyGateDecision.ABI_BREAKING)


class OperationalStatus(str, Enum):
    """A run that never produced a real compatibility verdict at all.

    ``PolicyGateDecision`` alone cannot represent this: it only orders
    *compatibility* categories, and a budget overflow, a hard evidence-
    contract error, an extraction failure, or a comparability refusal are
    real, independent blocking conditions none of those categories cover
    (ADR-063 D6). The four non-``NONE`` members are mutually exclusive per
    report and equally blocking -- there is no further ordering among them,
    only "blocking vs. not" (see :func:`operational_status_exit_code`).
    """

    NONE = "none"
    #: ``scan``'s own legacy exit 5 -- a deadline expired before analysis
    #: completed.
    BUDGET_OVERFLOW = "budget_overflow"
    #: ADR-050 D2's comparability refusal (``verdict: null``) -- also
    #: ``scan``'s own legacy exit 6.
    NOT_COMPARABLE = "not_comparable"
    #: ADR-037 D5's evidence-contract check failing outright (a pinned
    #: depth whose evidence contract could not be satisfied).
    EVIDENCE_CONTRACT_ERROR = "evidence_contract_error"
    #: ``compare-release``'s own ``verdict: "ERROR"`` sentinel -- a library
    #: failed to dump/extract/compare.
    EXTRACTION_ERROR = "extraction_error"
    #: ADR-065 D7: the selected scope produced no valid comparison at all
    #: (a release fan-out with zero matched pairs, or one whose every
    #: selected member failed/was unsupported). Never success, whatever the
    #: completeness policy says -- a permissive ``warn`` setting can
    #: downgrade *missing members* to a warning, never *nothing compared*.
    NO_COMPARISON_COMPLETED = "no_comparison_completed"


def operational_status_exit_code(operational: OperationalStatus) -> int:
    """*operational*'s own contribution to the shared 0/1/2/4 scheme.

    ``NONE`` contributes ``0``; every other member contributes ``1`` --
    mirroring ``workflows.aggregate.gate.COVERAGE_INCOMPLETE_EXIT`` and the
    identical orthogonal-axis fold ADR-049 Phase 7's contract-coverage
    contribution already uses (a real, independent failure that must never
    be masked by, nor mask, the compatibility gate's own contribution).
    """
    return 0 if operational is OperationalStatus.NONE else 1


def fold_gate_and_operational(
    gate: PolicyGateDecision, operational: OperationalStatus
) -> int:
    """The combined exit-code contribution of both axes, by ``max()`` over
    the shared 0/1/2/4 scheme -- neither axis is allowed to mask the other.

    This is the one fold ADR-063 D6 assigns to ``gate.py``'s own readers
    (:mod:`abicheck.workflows.aggregate.gate`): two independent axes folded
    once per target, rather than two separate values every later consumer
    has to remember to fold itself.
    """
    return max(
        policy_gate_decision_exit_code(gate), operational_status_exit_code(operational)
    )


class TargetLifecycle(str, Enum):
    """A target's own lifecycle state within an ``aggregate`` baseline-set.

    Grounded in vocabulary ``workflows.aggregate.contracts.py`` already
    distinguishes (``_BOOTSTRAP_VERDICT``/``_NEW_TARGET_VERDICT``), not
    invented from nothing. Meaningful only where a "target" exists at all
    -- a single-pair ``compare`` invocation always reports ``EXISTING``,
    the fixed default every non-``aggregate`` ``RunOutcome`` construction
    uses.
    """

    #: The ordinary case: a target this baseline-set already knows, with a
    #: real prior baseline to compare against.
    EXISTING = "existing"
    #: ``load.py``'s own "no baseline published yet" synthesis.
    BOOTSTRAP = "bootstrap"
    #: ``load.py``'s own "target new to this baseline-set" synthesis.
    NEW_TARGET = "new_target"


class ScopeCompleteness(str, Enum):
    """ADR-065 D6's completeness axis: was every *selected, expected*
    member of the comparison scope actually compared?

    Independent of :class:`PolicyGateDecision` (what the compared members
    showed) and of :class:`OperationalStatus` (whether the run itself
    failed): a matrix with one clean pair and one unsupported or unmatched
    selected member reads ``INCOMPLETE`` while its gate reads ``NONE`` --
    an incompletely checked scope, never a clean pass, and never an ABI
    finding either. ``COMPLETE`` is the fixed value for every scalar
    comparison (the one pair it ran is the whole scope) and for a release
    whose every selected member reached a completed comparison. The
    per-member evidence behind ``INCOMPLETE`` lives in the typed
    :class:`~abicheck.model.scope_acquisition.ScopeAcquisitionRecord`;
    whether it *blocks* is :mod:`abicheck.policy.scope_completeness`'s
    question, folded into :class:`~abicheck.policy.exit_decision.
    ExitDecision` exactly like ADR-049 Phase 7's coverage axis.
    """

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class RunOutcome:
    """One report's independent-axis outcome (ADR-063 D6).

    ``compatibility`` is ``None`` only for a report that never ran a real
    comparison at all (the three synthetic ``buildsource.check_report``
    builders) -- every ordinary single-pair ``compare``, ``scan --against``,
    and release/bundle comparison populates a real, non-``None`` value.

    ``assurance`` is stored untyped (``object | None``) for the same
    circular-import reason ``checker_types.DiffResult.analysis_assurance``
    is -- see :func:`analysis_assurance_dict` to unwrap it safely. It is
    ``None`` for a writer that has no ``AnalysisAssurance`` rollup of its
    own to report (every synthetic builder; ``scan``'s own writers, which
    predate this axis's wiring into the scan pipeline).

    ``scope`` (ADR-065 D6) defaults to :attr:`ScopeCompleteness.COMPLETE`:
    every pre-existing constructor site is a scalar comparison or a
    synthetic report whose scope is the one pair it describes, so the
    default states what was already true rather than a new claim. Only the
    release fan-out (``outcome_release.run_outcome_dict_for_release``)
    computes it from a real acquisition record.
    """

    compatibility: Verdict | None
    assurance: object | None
    gate: PolicyGateDecision
    operational: OperationalStatus
    lifecycle: TargetLifecycle = TargetLifecycle.EXISTING
    scope: ScopeCompleteness = ScopeCompleteness.COMPLETE

    def exit_code_contribution(self) -> int:
        """This outcome's own contribution to the shared 0/1/2/4 scheme --
        the two typed axes folded together via :func:`fold_gate_and_operational`.
        """
        return fold_gate_and_operational(self.gate, self.operational)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RUN_OUTCOME_SCHEMA_VERSION,
            "compatibility": self.compatibility.value
            if self.compatibility is not None
            else None,
            "assurance": analysis_assurance_dict(self.assurance),
            "gate": self.gate.value,
            "operational": self.operational.value,
            "lifecycle": self.lifecycle.value,
            "scope": self.scope.value,
        }

    @classmethod
    def from_dict(cls, data: object) -> RunOutcome | None:
        """Parse a ``run_outcome`` report block, or ``None`` when *data* is
        absent, not an object, or its required ``gate``/``operational``
        fields don't parse -- the "read once, decode for legacy, never for
        fresh" backfill shape Phase 0 already established for ``Fact[...]``
        against the old reliability flags.

        Deliberately does not attempt to reconstruct :attr:`assurance` (a
        full ``AnalysisAssurance`` round-trip is not needed by any current
        reader -- readers only ever fold :attr:`gate`/:attr:`operational`)
        or :attr:`compatibility` beyond a best-effort parse; a malformed
        value there does not fail the whole block, since it isn't required
        by any current consumer of this method.
        """
        if not isinstance(data, dict):
            return None
        try:
            gate = PolicyGateDecision(data.get("gate"))
            operational = OperationalStatus(data.get("operational"))
        except ValueError:
            return None
        lifecycle_raw = data.get("lifecycle")
        try:
            lifecycle = (
                TargetLifecycle(lifecycle_raw)
                if lifecycle_raw is not None
                else TargetLifecycle.EXISTING
            )
        except ValueError:
            lifecycle = TargetLifecycle.EXISTING
        # A pre-1.1 block reads an absent `scope` as COMPLETE (every such
        # writer described the one pair it ran); later ones must carry it.
        required = run_outcome_scope_required(data.get("schema_version"))
        try:
            scope = ScopeCompleteness(data.get("scope"))
        except ValueError:
            if required:
                return None
            scope = ScopeCompleteness.COMPLETE
        compatibility: Verdict | None = None
        compat_raw = data.get("compatibility")
        if isinstance(compat_raw, str):
            try:
                compatibility = Verdict(compat_raw)
            except ValueError:
                compatibility = None
        return cls(
            compatibility=compatibility,
            assurance=None,
            gate=gate,
            operational=operational,
            lifecycle=lifecycle,
            scope=scope,
        )


#: The non-``Verdict`` strings a ``scan`` writer's own ``verdict`` field can
#: carry -- ADR-063 D6's grounding for :class:`OperationalStatus`'s
#: ``BUDGET_OVERFLOW``/``EVIDENCE_CONTRACT_ERROR``/``NOT_COMPARABLE``
#: members (``scan_abort_result.py``, ``service_scan.ScanSetResult``'s own
#: ``_SCAN_SET_COMPAT_ORDER`` gap for exactly these three strings).
#: ``BUNDLE_INCOMPLETE`` (Codex review) is ``ScanSetResult.run_scan_set``'s
#: own sentinel for "the cross-library bundle audit itself never ran because
#: a discovered member dropped out of resolution" -- the same "something
#: failed to be extracted/analyzed" shape :attr:`OperationalStatus.
#: EXTRACTION_ERROR` already names, not a real compatibility verdict.
_SCAN_ABORT_VERDICT_OPERATIONAL: dict[str, OperationalStatus] = {
    "BUDGET_OVERFLOW": OperationalStatus.BUDGET_OVERFLOW,
    "EVIDENCE_CONTRACT_ERROR": OperationalStatus.EVIDENCE_CONTRACT_ERROR,
    "NOT_COMPARABLE": OperationalStatus.NOT_COMPARABLE,
    "BUNDLE_INCOMPLETE": OperationalStatus.EXTRACTION_ERROR,
}

#: ``scan``'s own legacy top-level exit codes that mean "the compatibility
#: axis contributed nothing; this is an operational condition" -- 5 (budget
#: overflow) and 6 (not-comparable), the two codes
#: ``workflows.aggregate.gate.GateInfo.from_scan_report`` already treats as
#: real, independent blocking conditions outside its own 0/2/4 native scheme.
_SCAN_EXIT_CODE_OPERATIONAL: dict[int, OperationalStatus] = {
    5: OperationalStatus.BUDGET_OVERFLOW,
    6: OperationalStatus.NOT_COMPARABLE,
}


def _is_valid_coverage_contribution(raw: object) -> bool:
    """Whether *raw* is a usable ``0``/``1`` contract-coverage contribution.

    Mirrors ``workflows.aggregate.gate._is_valid_contribution``'s own
    bool-before-int check exactly (``True == 1`` in Python) -- duplicated
    here since that module may depend on this leaf, never the reverse.
    """
    return not isinstance(raw, bool) and isinstance(raw, int) and raw in (0, 1)


def _contributes(raw: object) -> bool:
    """Whether *raw* is a confirmed ``1`` orthogonal-axis contribution."""
    return _is_valid_coverage_contribution(raw) and raw == 1


def run_outcome_for_scan_fields(
    verdict: str,
    exit_code: int,
    *,
    severity_exit_code: int | None = None,
    contract_coverage_contribution: object = None,
    analysis_assurance_contribution: object = None,
    member_evidence_contract_error: bool = False,
    member_not_comparable: bool = False,
    bundle_incomplete: bool = False,
    assurance: object | None = None,
    member_compatibility_verdict: str | None = None,
    lifecycle: TargetLifecycle = TargetLifecycle.EXISTING,
) -> RunOutcome:
    """Build a :class:`RunOutcome` for one of ``scan``'s ``(verdict,
    exit_code)`` report shapes (:class:`~abicheck.scan_engine.ScanOutcome`,
    :class:`~abicheck.service_scan.ScanResult`,
    :class:`~abicheck.service_scan.ScanSetResult`).

    *severity_exit_code*, when given, is the nested compatibility-only exit
    code from a severity-scheme scan's own ``diff.severity.exit_code``
    block -- preferred over deriving the compatibility axis from the
    top-level *exit_code*, which under the severity scheme already folds in
    the orthogonal contract-coverage/analysis-assurance contributions
    (mirrors ``workflows.aggregate.gate._scan_severity_gate``'s own
    preference for the identical nested block).

    *contract_coverage_contribution*, when given, is the report's own
    declared ADR-049 Phase 7 contract-coverage exit contribution (``0``/
    ``1``, schema field ``contract_coverage_exit_contribution``). Under the
    *legacy* scan scheme (no *severity_exit_code*), a raw top-level
    ``exit_code`` of ``1`` is ambiguous on its own -- legacy scan's own
    native codes are 0/2/4/5/6, so a bare ``1`` can only be this orthogonal
    axis folded onto an otherwise-compatible ``0`` by ``max()``, never a
    real compatibility contribution (Codex review: without this, a scan
    that only failed contract coverage read as an ``ADDITION_QUALITY``
    compatibility gate here, and ``GateInfo.from_scan_report``'s
    structured-first read then treated the target as a compatibility
    blocker). Confirmed via the report's own declared contribution, never
    guessed: an unconfirmed ``1`` stays a compatibility-gate contribution,
    fail-closed, like the reader this mirrors
    (``workflows.aggregate.gate._contract_coverage_exit``).

    *analysis_assurance_contribution*, when given, is the report's own P0.4
    ``analysis_assurance_exit_contribution`` (``0``/``1``) -- the sibling
    orthogonal axis a legacy-scheme ``--require-complete-analysis`` scan
    folds onto a bare ``exit_code`` of ``1`` the same way
    *contract_coverage_contribution* does (Codex review). Same fail-closed
    validation, same "confirmed, never guessed" rule.

    *member_evidence_contract_error*/*member_not_comparable*, when ``True``,
    fold ``EVIDENCE_CONTRACT_ERROR``/``NOT_COMPARABLE`` in even though
    *verdict*/*exit_code* don't name either directly --
    ``_aggregate_scan_set_verdict`` deliberately lets a *stronger* member's
    ``API_BREAK``/``BREAKING`` win the reported verdict over a *different*
    member's abort, which otherwise left that member's abort with no signal
    in ``run_outcome`` at all. Never overrides an operational status
    already derived from *verdict*/*exit_code* (e.g. a set-level
    ``BUDGET_OVERFLOW``, which already dominates every member).

    *bundle_incomplete*, when ``True``, folds ``EXTRACTION_ERROR`` in under
    the identical "only when otherwise ``NONE``" rule -- the sibling gap
    where a *stronger* member wins the reported ``verdict`` while the
    cross-library bundle audit itself never ran.

    *assurance*, when given, is the report's own already-serialized
    ``analysis_assurance`` block (``cli_scan_baseline.py``'s
    ``diff_summary["analysis_assurance"]``) -- stored as-is, accepted
    directly by :func:`analysis_assurance_dict`.

    *member_compatibility_verdict*, when given, is the worst REAL
    per-member verdict a caller with no ``report=`` already computed -- it
    resolves *compatibility* precisely when *verdict* is a sentinel, since
    exit code alone can't tell NO_CHANGE/COMPATIBLE/COMPATIBLE_WITH_RISK apart.
    """
    operational = _SCAN_ABORT_VERDICT_OPERATIONAL.get(verdict, OperationalStatus.NONE)
    if operational is OperationalStatus.NONE:
        operational = _SCAN_EXIT_CODE_OPERATIONAL.get(exit_code, OperationalStatus.NONE)
    if operational is OperationalStatus.NONE and member_evidence_contract_error:
        operational = OperationalStatus.EVIDENCE_CONTRACT_ERROR
    if operational is OperationalStatus.NONE and member_not_comparable:
        operational = OperationalStatus.NOT_COMPARABLE
    if operational is OperationalStatus.NONE and bundle_incomplete:
        operational = OperationalStatus.EXTRACTION_ERROR

    compatibility: Verdict | None
    try:
        compatibility = Verdict(verdict)
    except ValueError:
        compatibility = None
        # *verdict* is an operational-only sentinel (e.g. a late abort) --
        # but a persisted *severity_exit_code* or a precise *member_
        # compatibility_verdict* can still say a real comparison completed
        # before the abort fired (Codex review, x2): `null` must not claim
        # nothing was compared when something was. Only severity_exit_code
        # 2/4 are unambiguous on their own (0 can't be told apart from
        # NO_CHANGE/COMPATIBLE/COMPATIBLE_WITH_RISK), hence the verdict-
        # string fallback below.
        if member_compatibility_verdict is not None:
            try:
                compatibility = Verdict(member_compatibility_verdict)
            except ValueError:
                compatibility = None
        if compatibility is None:
            if severity_exit_code == 2:
                compatibility = Verdict.API_BREAK
            elif severity_exit_code == 4:
                compatibility = Verdict.BREAKING

    compat_exit_code = (
        severity_exit_code if severity_exit_code is not None else exit_code
    )
    if severity_exit_code is None and verdict in _SCAN_ABORT_VERDICT_OPERATIONAL:
        # *verdict* itself names an operational-only condition (Codex
        # review) -- its own top-level exit_code is never a genuine
        # compatibility contribution unless a validated one was explicitly
        # given via *severity_exit_code* (the abort-report's own persisted
        # `exit.compatibility_contribution`, above). Without this,
        # BUNDLE_INCOMPLETE's own exit-code-1 floor (`run_scan_set`'s
        # `max(exit_code, 1)`, with no `report=` to read a real
        # contribution from) read as a real ADDITION_QUALITY compatibility
        # gate despite `compatibility` already being `None` for the
        # identical reason (the verdict string itself doesn't parse as a
        # `Verdict`). Never fires for an *ordinary* verdict (e.g.
        # `API_BREAK`) that merely also carries `member_evidence_contract_
        # error=True` -- that flag folds into `operational` above without
        # ever touching *verdict*, so this membership check alone cannot
        # mistake a real break for an operational-only report.
        compat_exit_code = 0
    if severity_exit_code is None and compat_exit_code == 1:
        if _contributes(contract_coverage_contribution) or _contributes(
            analysis_assurance_contribution
        ):
            compat_exit_code = 0
    if compat_exit_code not in _GATE_EXIT_CODE.values():
        # Operational-only code (5/6, etc.) -- compatibility contributed
        # nothing; `operational` carries the real signal.
        compat_exit_code = 0
    gate = policy_gate_decision_for_exit_code(compat_exit_code)

    return RunOutcome(
        compatibility=compatibility,
        assurance=assurance,
        gate=gate,
        operational=operational,
        lifecycle=lifecycle,
    )


def scan_report_severity_exit_code(report: object) -> int | None:
    """A scan report dict's own nested ``diff.severity.exit_code`` (a
    severity-scheme ``scan --against``'s real, policy-aware compatibility
    exit code), or ``None`` when absent/malformed.

    Shared by every scan-shaped writer with a ``report``/``diff`` payload to
    read this out of -- pulled into this leaf module to keep each writer's
    own ``to_dict()`` to one call.
    """
    severity = _scan_report_diff_field(report, "severity")
    if not isinstance(severity, dict):
        return None
    exit_code = severity.get("exit_code")
    return (
        exit_code
        if isinstance(exit_code, int) and not isinstance(exit_code, bool)
        else None
    )


def scan_report_abort_compatibility_contribution(report: object) -> int | None:
    """A scan *abort* report's own persisted ``exit.compatibility_
    contribution``, or ``None`` when absent/malformed/out-of-scheme.

    ``workflows.scan_abort_result.scan_abort_result_fields`` shapes a
    ``run_scan_core`` abort (budget overflow, evidence-contract error) as
    ``report = {"scan_schema_version": ..., "exit": {...}}`` -- no ``diff``
    key, so :func:`scan_report_severity_exit_code` never applies here.
    ``exit.compatibility_contribution`` is the pure, pre-operational-fold
    contribution a *late* abort preserves from whatever gate decision
    already ran (``attach_prior_on_budget_overflow``/``audit_prior_
    decision``) -- reading it lets a late ``BUDGET_OVERFLOW`` that already
    found a real ABI break keep reporting ``gate: abi_breaking`` instead of
    the abort's own out-of-scheme top-level exit code (5) zeroing the axis.

    Rejects a present-but-out-of-scheme value (e.g. ``99``) too, not just a
    missing/non-int one: accepted unchecked, it normalized straight to
    ``gate: none``, silently turning a real ``BREAKING`` scan nonblocking.
    ``None`` falls back to the root verdict/exit code, as a missing value does.
    """
    exit_block = report.get("exit") if isinstance(report, dict) else None
    contribution = (
        exit_block.get("compatibility_contribution")
        if isinstance(exit_block, dict)
        else None
    )
    if (
        not isinstance(contribution, int)
        or isinstance(contribution, bool)
        or contribution not in _GATE_EXIT_CODE.values()
    ):
        return None
    return contribution


def _scan_report_diff_field(report: object, key: str) -> object:
    """A scan report dict's own nested ``diff.<key>``, or ``None`` when
    absent -- the identical ``report.get("diff")`` traversal
    :func:`scan_report_severity_exit_code` uses, shared by every sibling
    flat field reader below. Returned unvalidated: each field's own
    consumer validates it (:func:`_is_valid_coverage_contribution` for the
    two ``*_contribution`` fields, :func:`analysis_assurance_dict` for the
    ``analysis_assurance`` block) -- fail-closed-on-read-not-write, like
    every other reader in this module.
    """
    diff = report.get("diff") if isinstance(report, dict) else None
    return diff.get(key) if isinstance(diff, dict) else None


def scan_report_coverage_contribution(report: object) -> object:
    """The report's ``diff.contract_coverage_exit_contribution`` (ADR-049
    Phase 7, raw ``0``/``1``); see :func:`run_outcome_for_scan_fields`."""
    return _scan_report_diff_field(report, "contract_coverage_exit_contribution")


def scan_report_assurance_block(report: object) -> object:
    """The report's already-serialized ``diff.analysis_assurance`` block
    (``cli_scan_baseline.py``'s ``analysis_assurance_report_dict(diff).
    to_dict()``)."""
    return _scan_report_diff_field(report, "analysis_assurance")


def scan_report_assurance_contribution(report: object) -> object:
    """The report's P0.4 ``diff.analysis_assurance_exit_contribution`` --
    the sibling reader to :func:`scan_report_coverage_contribution` for the
    identical orthogonal-axis special case (Codex review)."""
    return _scan_report_diff_field(report, "analysis_assurance_exit_contribution")


def run_outcome_dict_for_scan_outcome(
    verdict: str, exit_code: int, diff_summary: object
) -> dict[str, Any]:
    """One-call convenience for :class:`~abicheck.scan_engine.ScanOutcome`
    specifically: its own ``diff_summary`` carries ``severity`` directly
    (unlike the ``{"diff": {"severity": ...}}`` shape
    :func:`scan_report_severity_exit_code` reads), so this reads one level
    shallower rather than reusing that helper against the wrong nesting.
    """

    def _field(key: str) -> object:
        return diff_summary.get(key) if isinstance(diff_summary, dict) else None

    severity = _field("severity")
    severity_exit_code = (
        severity.get("exit_code")
        if isinstance(severity, dict) and isinstance(severity.get("exit_code"), int)
        else None
    )
    return run_outcome_for_scan_fields(
        verdict,
        exit_code,
        severity_exit_code=severity_exit_code,
        contract_coverage_contribution=_field("contract_coverage_exit_contribution"),
        analysis_assurance_contribution=_field("analysis_assurance_exit_contribution"),
        assurance=_field("analysis_assurance"),
    ).to_dict()


def worst_real_verdict(candidates: Iterable[object]) -> Verdict | None:
    """Worst real :class:`Verdict` among *candidates* (declaration order),
    skipping every non-parseable entry (``None``, an operational sentinel)
    -- ``None`` if nothing parses. Sibling of ``cli_compare_release_
    helpers._release_completed_compatibility_verdict`` (a `frontends`
    module `policy`/`buildsource` may not import) shared by the scan-set
    (below) and legacy-release-backfill callers instead of each re-deriving
    "worst real verdict, sentinels excluded".
    """
    order = list(Verdict)
    worst: Verdict | None = None
    worst_rank = -1
    for c in candidates:
        if not isinstance(c, str):
            continue
        try:
            v = Verdict(c)
        except ValueError:
            continue
        rank = order.index(v)
        if rank >= worst_rank:
            worst_rank = rank
            worst = v
    return worst


def run_outcome_dict_for_scan(
    verdict: str,
    exit_code: int,
    *,
    report: object = None,
    member_evidence_contract_error: bool = False,
    member_not_comparable: bool = False,
    bundle_incomplete: bool = False,
    member_verdicts: Iterable[object] | None = None,
    lifecycle: TargetLifecycle = TargetLifecycle.EXISTING,
) -> dict[str, Any]:
    """One-call convenience wrapping :func:`run_outcome_for_scan_fields` +
    :func:`scan_report_severity_exit_code` + ``.to_dict()`` -- what every
    scan-shaped writer's own ``to_dict()`` actually wants, so each keeps its
    ``run_outcome`` entry to a single line rather than repeating this same
    three-call sequence.

    An ordinary (non-abort) report's nested ``diff.severity.exit_code`` is
    preferred when present; an *abort* report has no ``diff`` key at all, so
    :func:`scan_report_abort_compatibility_contribution`'s own separate
    ``exit.compatibility_contribution`` nesting is consulted next -- the two
    are mutually exclusive report shapes, never both present at once.

    *member_verdicts*, when given, is the raw per-member (+ bundle) verdict
    strings a writer with no ``report=`` already has
    (:class:`~abicheck.service_scan.ScanSetResult`'s set-level abort case):
    the last-resort fallback, via :func:`worst_real_verdict`, deriving both
    the compat-exit contribution and the precise ``compatibility`` verdict
    -- not reducible to a bare int, since NO_CHANGE/COMPATIBLE/COMPATIBLE_
    WITH_RISK all share exit code ``0``.

    *member_evidence_contract_error*/*member_not_comparable*/*bundle_
    incomplete* forward unchanged -- see that function's own docstring.
    """
    compat_exit_code = scan_report_severity_exit_code(report)
    if compat_exit_code is None:
        compat_exit_code = scan_report_abort_compatibility_contribution(report)
    worst_member = (
        worst_real_verdict(member_verdicts) if member_verdicts is not None else None
    )
    if compat_exit_code is None and worst_member is not None:
        from .severity import legacy_exit_code

        compat_exit_code = legacy_exit_code(worst_member)
    return run_outcome_for_scan_fields(
        verdict,
        exit_code,
        severity_exit_code=compat_exit_code,
        contract_coverage_contribution=scan_report_coverage_contribution(report),
        analysis_assurance_contribution=scan_report_assurance_contribution(report),
        member_evidence_contract_error=member_evidence_contract_error,
        member_not_comparable=member_not_comparable,
        bundle_incomplete=bundle_incomplete,
        assurance=scan_report_assurance_block(report),
        member_compatibility_verdict=getattr(worst_member, "value", None),
        lifecycle=lifecycle,
    ).to_dict()


def analysis_assurance_dict(assurance: object | None) -> dict[str, Any] | None:
    """``assurance.to_dict()``, or ``None`` when absent/wrong type.

    Mirrors ``analysis_assurance.analysis_assurance_report_dict``'s own
    narrowing exactly, for the identical circular-import reason: this
    module may not import ``analysis_assurance`` at module scope (it is not
    one of the leaves ``policy/`` may depend on), so the check is done with
    a function-local import instead.

    A plain ``dict`` is passed through unchanged (Codex review, fresh
    evidence): ``scan``'s own writers never hold a live ``AnalysisAssurance``
    object at the point they build ``RunOutcome`` -- only its own already-
    serialized ``diff_summary["analysis_assurance"]`` block
    (``cli_scan_baseline.py``'s ``analysis_assurance_report_dict(diff).
    to_dict()`` result) -- so :attr:`RunOutcome.assurance` legitimately holds
    either shape depending on the writer, and this is the one place both are
    unwrapped to the same report-JSON shape.
    """
    from ..analysis_assurance import AnalysisAssurance

    if isinstance(assurance, AnalysisAssurance):
        return assurance.to_dict()
    if isinstance(assurance, dict):
        return assurance
    return None
