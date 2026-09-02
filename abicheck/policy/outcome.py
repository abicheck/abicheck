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
  mirroring the ``IssueCategory`` ordering ``severity.compute_exit_code``
  already uses internally). Not a reuse of ``severity.GateDecision``, which
  carries exactly the scheme-encoded ``exit_code``/``blocking`` data this
  axis exists to keep out of domain objects -- that type remains what the
  boundary encoders convert *to*, never what a domain object holds.
* ``operational`` -- :class:`OperationalStatus`, the axis
  ``PolicyGateDecision`` alone cannot represent: a run that never produced a
  real compatibility verdict at all (a budget overflow, a hard evidence-
  contract error, an extraction failure) or a comparability refusal, as
  opposed to one that did and scored it ``ABI_BREAKING``.
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

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..change_registry_types import Verdict

__all__ = [
    "OperationalStatus",
    "PolicyGateDecision",
    "RunOutcome",
    "RUN_OUTCOME_SCHEMA_VERSION",
    "TargetLifecycle",
    "analysis_assurance_dict",
    "fold_gate_and_operational",
    "operational_status_exit_code",
    "policy_gate_decision_exit_code",
    "policy_gate_decision_for_exit_code",
]

#: Versioned independently of ``REPORT_SCHEMA_VERSION``/``SCAN_SCHEMA_
#: VERSION`` (which each still gain their own bump for the new top-level
#: ``run_outcome`` report key) -- the same self-contained-sub-object
#: convention ``analysis_assurance.ANALYSIS_ASSURANCE_SCHEMA_VERSION`` and
#: ``buildsource.model.BUILD_SOURCE_PACK_VERSION`` already use.
RUN_OUTCOME_SCHEMA_VERSION = "1.0"


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
    """

    compatibility: Verdict | None
    assurance: object | None
    gate: PolicyGateDecision
    operational: OperationalStatus
    lifecycle: TargetLifecycle = TargetLifecycle.EXISTING

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
    bool-before-int check exactly (``True == 1`` in Python, so the ``bool``
    exclusion has to come first) -- duplicated here rather than imported
    since that module may depend on this leaf, never the reverse.
    """
    return not isinstance(raw, bool) and isinstance(raw, int) and raw in (0, 1)


def run_outcome_for_scan_fields(
    verdict: str,
    exit_code: int,
    *,
    severity_exit_code: int | None = None,
    contract_coverage_contribution: object = None,
    member_evidence_contract_error: bool = False,
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
    that only failed contract coverage was recorded as an
    ``ADDITION_QUALITY``-level compatibility gate here, and
    ``GateInfo.from_scan_report``'s own structured-first read -- which
    trusts this field over re-deriving it -- then treated the target as a
    compatibility blocker, bypassing its raw-code fallback's identical,
    already-correct special case). Confirmed via the report's own declared
    contribution, never guessed: an unconfirmed ``1`` stays a
    compatibility-gate contribution, fail-closed, exactly like the reader
    this mirrors (``workflows.aggregate.gate._contract_coverage_exit``).

    *member_evidence_contract_error*, when ``True``, folds
    :attr:`OperationalStatus.EVIDENCE_CONTRACT_ERROR` in even though *verdict*/
    *exit_code* don't name it directly -- :class:`~abicheck.service_scan.
    ScanSetResult`'s own ``_aggregate_scan_set_verdict`` deliberately lets a
    *stronger* member's ``API_BREAK``/``BREAKING`` compatibility verdict win
    the reported ``verdict``/``exit_code`` over a *different* member's
    ``EVIDENCE_CONTRACT_ERROR`` (an incomplete analysis stays visible in
    ``per_artifact``, but never becomes the set-level verdict once a real
    break outranks it) -- without this, that member abort has no signal left
    in ``run_outcome`` at all (Codex review). Never overrides an operational
    status already derived from *verdict*/*exit_code* (e.g. a set-level
    ``BUDGET_OVERFLOW``, which already dominates every member per that same
    function's own step 1).
    """
    operational = _SCAN_ABORT_VERDICT_OPERATIONAL.get(verdict, OperationalStatus.NONE)
    if operational is OperationalStatus.NONE:
        operational = _SCAN_EXIT_CODE_OPERATIONAL.get(exit_code, OperationalStatus.NONE)
    if operational is OperationalStatus.NONE and member_evidence_contract_error:
        operational = OperationalStatus.EVIDENCE_CONTRACT_ERROR

    compatibility: Verdict | None
    try:
        compatibility = Verdict(verdict)
    except ValueError:
        compatibility = None

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
    if (
        severity_exit_code is None
        and compat_exit_code == 1
        and _is_valid_coverage_contribution(contract_coverage_contribution)
        and contract_coverage_contribution == 1
    ):
        compat_exit_code = 0
    if compat_exit_code not in _GATE_EXIT_CODE.values():
        # An operational-only code (5/6, or anything else this scheme
        # doesn't natively produce) -- the compatibility axis itself
        # contributed nothing; :attr:`operational` carries the real signal.
        compat_exit_code = 0
    gate = policy_gate_decision_for_exit_code(compat_exit_code)

    return RunOutcome(
        compatibility=compatibility,
        assurance=None,
        gate=gate,
        operational=operational,
        lifecycle=lifecycle,
    )


def scan_report_severity_exit_code(report: object) -> int | None:
    """A scan report dict's own nested ``diff.severity.exit_code`` (a
    severity-scheme ``scan --against``'s real, policy-aware compatibility
    exit code), or ``None`` when absent/malformed.

    Shared by every scan-shaped writer that has a ``report``/``diff``
    payload to read this out of (:class:`~abicheck.service_scan.
    ScanResult`'s own ``report`` field) -- pulled into this leaf module
    purely to keep each writer's own ``to_dict()`` to one call instead of
    repeating this same nested-lookup dance.
    """
    diff = report.get("diff") if isinstance(report, dict) else None
    severity = diff.get("severity") if isinstance(diff, dict) else None
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
    contribution``, or ``None`` when absent/malformed.

    ``workflows.scan_abort_result.scan_abort_result_fields`` shapes a
    ``run_scan_core`` abort (budget overflow, evidence-contract error) as
    ``report = {"scan_schema_version": ..., "exit": {...}}`` -- no ``diff``
    key at all, so :func:`scan_report_severity_exit_code` never applies to
    an abort report and this is a genuinely separate nesting, not a variant
    of that lookup. ``exit.compatibility_contribution``
    (``policy.exit_decision.ExitDecision.to_dict()``) is the pure, pre-
    operational-fold compatibility contribution a *late* abort preserves
    from whatever gate decision already ran before it fired
    (``attach_prior_on_budget_overflow``/``audit_prior_decision``) -- reading
    it here is what lets a late ``BUDGET_OVERFLOW`` that already found a real
    ABI break keep reporting ``run_outcome.gate: abi_breaking`` instead of
    the abort's own top-level exit code (5, outside the 0/1/2/4 scheme)
    zeroing the compatibility axis entirely (Codex review).
    """
    exit_block = report.get("exit") if isinstance(report, dict) else None
    contribution = (
        exit_block.get("compatibility_contribution")
        if isinstance(exit_block, dict)
        else None
    )
    return (
        contribution
        if isinstance(contribution, int) and not isinstance(contribution, bool)
        else None
    )


def scan_report_coverage_contribution(report: object) -> object:
    """A scan report dict's own nested ``diff.contract_coverage_exit_
    contribution`` (ADR-049 Phase 7's raw ``0``/``1`` axis value), or
    ``None`` when absent -- the identical ``report.get("diff")`` traversal
    :func:`scan_report_severity_exit_code` uses, for the sibling field.
    Returned unvalidated (``object``, not ``int | None``):
    :func:`run_outcome_for_scan_fields` validates it itself via
    :func:`_is_valid_coverage_contribution`, the same fail-closed check
    ``workflows.aggregate.gate._contract_coverage_exit`` applies on read.
    """
    diff = report.get("diff") if isinstance(report, dict) else None
    return (
        diff.get("contract_coverage_exit_contribution")
        if isinstance(diff, dict)
        else None
    )


def run_outcome_dict_for_scan_outcome(
    verdict: str, exit_code: int, diff_summary: object
) -> dict[str, Any]:
    """One-call convenience for :class:`~abicheck.scan_engine.ScanOutcome`
    specifically: its own ``diff_summary`` carries ``severity`` directly
    (unlike the ``{"diff": {"severity": ...}}`` shape
    :func:`scan_report_severity_exit_code` reads), so this reads one level
    shallower rather than reusing that helper against the wrong nesting.
    """
    severity = diff_summary.get("severity") if isinstance(diff_summary, dict) else None
    severity_exit_code = (
        severity.get("exit_code")
        if isinstance(severity, dict) and isinstance(severity.get("exit_code"), int)
        else None
    )
    coverage_contribution = (
        diff_summary.get("contract_coverage_exit_contribution")
        if isinstance(diff_summary, dict)
        else None
    )
    return run_outcome_for_scan_fields(
        verdict,
        exit_code,
        severity_exit_code=severity_exit_code,
        contract_coverage_contribution=coverage_contribution,
    ).to_dict()


def run_outcome_dict_for_scan(
    verdict: str,
    exit_code: int,
    *,
    report: object = None,
    member_evidence_contract_error: bool = False,
    lifecycle: TargetLifecycle = TargetLifecycle.EXISTING,
) -> dict[str, Any]:
    """One-call convenience wrapping :func:`run_outcome_for_scan_fields` +
    :func:`scan_report_severity_exit_code` + ``.to_dict()`` -- what every
    scan-shaped writer's own ``to_dict()`` actually wants, so each keeps its
    ``run_outcome`` entry to a single line rather than repeating this same
    three-call sequence.

    An ordinary (non-abort) report's nested ``diff.severity.exit_code`` is
    preferred when present; a *abort* report has no ``diff`` key at all, so
    :func:`scan_report_abort_compatibility_contribution`'s own separate
    ``exit.compatibility_contribution`` nesting is consulted next -- the two
    are mutually exclusive report shapes (Codex review), never both present
    at once, so there is no precedence question between them in practice.

    *member_evidence_contract_error* is forwarded to
    :func:`run_outcome_for_scan_fields` unchanged -- see that function's own
    docstring (:class:`~abicheck.service_scan.ScanSetResult`'s own use).
    """
    compat_exit_code = scan_report_severity_exit_code(report)
    if compat_exit_code is None:
        compat_exit_code = scan_report_abort_compatibility_contribution(report)
    return run_outcome_for_scan_fields(
        verdict,
        exit_code,
        severity_exit_code=compat_exit_code,
        contract_coverage_contribution=scan_report_coverage_contribution(report),
        member_evidence_contract_error=member_evidence_contract_error,
        lifecycle=lifecycle,
    ).to_dict()


def analysis_assurance_dict(assurance: object | None) -> dict[str, Any] | None:
    """``assurance.to_dict()``, or ``None`` when absent/wrong type.

    Mirrors ``analysis_assurance.analysis_assurance_report_dict``'s own
    narrowing exactly, for the identical circular-import reason: this
    module may not import ``analysis_assurance`` at module scope (it is not
    one of the leaves ``policy/`` may depend on), so the check is done with
    a function-local import instead.
    """
    from ..analysis_assurance import AnalysisAssurance

    if not isinstance(assurance, AnalysisAssurance):
        return None
    return assurance.to_dict()
