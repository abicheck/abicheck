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

"""Multi-target assessment aggregation — fan-out builds, fan-in assessment.

When a package ships several ABI-relevant targets (e.g. ``linux-x86_64``,
``windows-x86_64``), each target's build and comparison can succeed or fail
independently. This module aggregates their outcomes into a single
:class:`AssessmentResult` under one invariant:

    An unavailable target is unknown, not an empty ABI.

A target contributes findings only when it reports :attr:`TargetState.ANALYZED`
with an actual :class:`~abicheck.checker_types.DiffResult`. Every other terminal
state (a failed build, a missing artifact, a crashed analysis, ...) marks the
target *unavailable* — it is excluded from findings and surfaced separately as
a coverage gap. Nothing here ever synthesizes an empty baseline/candidate pair
and runs it through the diff engine to stand in for a target that could not be
analyzed.

Usage::

    manifest = AssessmentManifest(
        assessment_id="abc123",
        head_sha="0123456789abcdef",
        targets=(TargetSpec("linux-x86_64"), TargetSpec("windows-x86_64")),
    )
    assessment = Assessment(manifest)
    assessment.record(TargetOutcome.analyzed("linux-x86_64", linux_diff))
    assessment.record(TargetOutcome.unavailable(
        "windows-x86_64", TargetState.BUILD_FAILED, reason="compile error"
    ))
    result = assessment.finalize()
    result.is_partial            # True — windows-x86_64 is unavailable
    result.findings_verdict      # FindingsVerdict.SUCCESS or FAILURE, from
                                  # the *analyzed* targets only
    result.coverage_verdict()    # CoverageVerdict.FAILURE — a required
                                  # target could not be analyzed
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .change_registry_types import Verdict
from .checker_types import DiffResult

#: Verdicts that count as an ABI regression for findings-verdict purposes.
_REGRESSION_VERDICTS = frozenset({Verdict.BREAKING, Verdict.API_BREAK})


class TargetState(str, Enum):
    """Terminal (or pending) state of a single target's contribution."""

    PENDING = "pending"
    ANALYZED = "analyzed"
    BUILD_FAILED = "build_failed"
    ARTIFACT_MISSING = "artifact_missing"
    BASELINE_MISSING = "baseline_missing"
    ANALYSIS_FAILED = "analysis_failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    #: Synthesized by :meth:`Assessment.finalize` for an expected target that
    #: never submitted any outcome at all (e.g. a cancelled runner that never
    #: reached its reporting step). Never appears on a recorded outcome.
    INCOMPLETE = "incomplete"


class FindingsVerdict(str, Enum):
    """Among analyzed targets, did we observe an ABI regression?"""

    SUCCESS = "success"
    FAILURE = "failure"
    #: No target could be analyzed — there is nothing to report a verdict on.
    NEUTRAL = "neutral"


class CoverageVerdict(str, Enum):
    """Did we successfully analyze every target policy says we should?"""

    SUCCESS = "success"
    NEUTRAL = "neutral"
    FAILURE = "failure"


@dataclass(frozen=True)
class TargetSpec:
    """One expected assessment target, declared before builds fan out."""

    id: str
    required: bool = True


@dataclass(frozen=True)
class AssessmentManifest:
    """The expected-target manifest for one assessment (one commit).

    Declaring targets up front is what lets the aggregator tell "1 of 1
    targets analyzed" apart from "1 of 2" — a lone result is meaningless
    without knowing how many targets were expected.
    """

    assessment_id: str
    head_sha: str
    targets: tuple[TargetSpec, ...]

    @property
    def target_ids(self) -> frozenset[str]:
        return frozenset(t.id for t in self.targets)

    @property
    def required_target_ids(self) -> frozenset[str]:
        return frozenset(t.id for t in self.targets if t.required)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AssessmentManifest:
        """Parse from a dictionary (e.g. the assessment-init JSON payload).

        Raises:
            TypeError: If *data* is not a dict.
            ValueError: If required fields are missing or malformed.
        """
        if not isinstance(data, dict):
            raise TypeError(
                f"AssessmentManifest expects a dict, got {type(data).__name__}"
            )

        assessment_id = data.get("assessment_id")
        head_sha = data.get("head_sha")
        if not assessment_id or not head_sha:
            raise ValueError("'assessment_id' and 'head_sha' are required")

        raw_targets = data.get("targets", [])
        if not isinstance(raw_targets, list) or not raw_targets:
            raise ValueError("'targets' must be a non-empty list")

        targets: list[TargetSpec] = []
        seen: set[str] = set()
        for entry in raw_targets:
            if not isinstance(entry, dict) or not entry.get("id"):
                raise ValueError(f"each target needs an 'id': {entry!r}")
            target_id = str(entry["id"])
            if target_id in seen:
                raise ValueError(f"duplicate target id: {target_id!r}")
            seen.add(target_id)
            targets.append(
                TargetSpec(id=target_id, required=bool(entry.get("required", True)))
            )

        return cls(
            assessment_id=str(assessment_id),
            head_sha=str(head_sha),
            targets=tuple(targets),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "assessment_id": self.assessment_id,
            "head_sha": self.head_sha,
            "targets": [{"id": t.id, "required": t.required} for t in self.targets],
        }


@dataclass(frozen=True)
class TargetOutcome:
    """A single target's contribution to an assessment.

    An ``ANALYZED`` outcome carries the real :class:`DiffResult`. Every other
    state carries a human-readable *reason* instead — never a synthetic diff.
    ``attempt`` lets a rerun's outcome supersede an earlier attempt's (e.g. a
    Windows retry that succeeds after an earlier ``build_failed``) without a
    late duplicate of the *older* attempt clobbering it back.
    """

    target_id: str
    state: TargetState
    required: bool = True
    attempt: int = 1
    head_sha: str | None = None
    reason: str | None = None
    job_url: str | None = None
    findings: DiffResult | None = None

    def __post_init__(self) -> None:
        if self.state is TargetState.ANALYZED and self.findings is None:
            raise ValueError("an ANALYZED outcome must carry findings")
        if self.state is not TargetState.ANALYZED and self.findings is not None:
            raise ValueError(f"a {self.state.value!r} outcome must not carry findings")

    @property
    def is_available(self) -> bool:
        return self.state is TargetState.ANALYZED

    @classmethod
    def analyzed(
        cls,
        target_id: str,
        findings: DiffResult,
        *,
        required: bool = True,
        attempt: int = 1,
        head_sha: str | None = None,
    ) -> TargetOutcome:
        return cls(
            target_id=target_id,
            state=TargetState.ANALYZED,
            required=required,
            attempt=attempt,
            head_sha=head_sha,
            findings=findings,
        )

    @classmethod
    def unavailable(
        cls,
        target_id: str,
        state: TargetState,
        *,
        required: bool = True,
        attempt: int = 1,
        head_sha: str | None = None,
        reason: str | None = None,
        job_url: str | None = None,
    ) -> TargetOutcome:
        if state is TargetState.ANALYZED:
            raise ValueError("use TargetOutcome.analyzed() for the analyzed state")
        return cls(
            target_id=target_id,
            state=state,
            required=required,
            attempt=attempt,
            head_sha=head_sha,
            reason=reason,
            job_url=job_url,
        )


@dataclass(frozen=True)
class CoveragePolicy:
    """Repository policy for how coverage gaps are graded.

    ``missing_required_target`` controls the case where at least one target
    was analyzed but a required one was not — reasonable repositories
    disagree on this (fail the gate vs. warn-only). ``no_analyzed_targets``
    separately controls a full outage (not one target could be analyzed,
    e.g. every build failed); it applies regardless of whether any of the
    unanalyzed targets were required, so an optional-only manifest is
    governed by it too. Both default to ``FAILURE`` — the stricter choice —
    since silently downgrading a coverage gap to neutral is how it goes
    unnoticed.
    """

    missing_required_target: CoverageVerdict = CoverageVerdict.FAILURE
    no_analyzed_targets: CoverageVerdict = CoverageVerdict.FAILURE


@dataclass(frozen=True)
class TargetSetChange:
    """Difference between two assessments' expected-target manifests.

    Removing a target from CI is a support/coverage change, not a symbol
    removal — the aggregator must never translate a disappeared target into
    a synthetic "all symbols removed" finding. Compare manifests across
    commits with :func:`compare_target_sets` to surface that distinction.
    """

    added: frozenset[str]
    removed: frozenset[str]

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)


def compare_target_sets(
    previous: AssessmentManifest, current: AssessmentManifest
) -> TargetSetChange:
    """Report which targets were added/removed between two manifests."""
    return TargetSetChange(
        added=current.target_ids - previous.target_ids,
        removed=previous.target_ids - current.target_ids,
    )


@dataclass
class AssessmentResult:
    """The finalized, reconciled view of an assessment.

    ``outcomes`` covers every expected target — one that never reported
    anything is present with :attr:`TargetState.INCOMPLETE` rather than
    being absent from the mapping.
    """

    manifest: AssessmentManifest
    outcomes: dict[str, TargetOutcome]
    #: Outcomes for target ids not in the manifest — a candidate target that
    #: has no corresponding baseline/expected entry (RFC §7: "new/unbaselined
    #: target"). Tracked separately so it never contaminates coverage math
    #: for the *expected* set.
    additional_outcomes: dict[str, TargetOutcome] = field(default_factory=dict)

    @property
    def analyzed_target_ids(self) -> frozenset[str]:
        return frozenset(
            tid for tid, o in self.outcomes.items() if o.state is TargetState.ANALYZED
        )

    @property
    def unavailable_target_ids(self) -> frozenset[str]:
        return self.manifest.target_ids - self.analyzed_target_ids

    @property
    def findings(self) -> tuple[tuple[str, DiffResult], ...]:
        """``union(compare(baseline[t], candidate[t]) for each analyzed t)``.

        Only ``ANALYZED`` targets contribute — unavailable targets are never
        represented here, synthetically or otherwise.
        """
        return tuple(
            (tid, o.findings)
            for tid, o in sorted(self.outcomes.items())
            if o.state is TargetState.ANALYZED and o.findings is not None
        )

    @property
    def is_partial(self) -> bool:
        return bool(self.unavailable_target_ids)

    @property
    def findings_verdict(self) -> FindingsVerdict:
        analyzed = self.findings
        if not analyzed:
            return FindingsVerdict.NEUTRAL
        if any(diff.verdict in _REGRESSION_VERDICTS for _tid, diff in analyzed):
            return FindingsVerdict.FAILURE
        return FindingsVerdict.SUCCESS

    def coverage_verdict(self, policy: CoveragePolicy | None = None) -> CoverageVerdict:
        policy = policy or CoveragePolicy()
        if not self.analyzed_target_ids:
            return policy.no_analyzed_targets
        if self.manifest.required_target_ids & self.unavailable_target_ids:
            return policy.missing_required_target
        if self.unavailable_target_ids:
            return CoverageVerdict.NEUTRAL
        return CoverageVerdict.SUCCESS

    def render_text(self) -> str:
        """Render a human-readable summary in the RFC §2 style."""
        lines: list[str] = []
        total = len(self.manifest.targets)
        analyzed = len(self.analyzed_target_ids)
        lines.append(f"ABI assessment: {'Partial' if self.is_partial else 'Complete'}")
        lines.append(f"Analyzed {analyzed} of {total} targets")
        lines.append("")

        for spec in self.manifest.targets:
            outcome = self.outcomes[spec.id]
            lines.append(spec.id)
            if outcome.state is TargetState.ANALYZED:
                diff = outcome.findings
                assert diff is not None
                breaking_count = len(diff.breaking) + len(diff.source_breaks)
                compatible_count = len(diff.compatible)
                lines.append("  ✓ Analysis completed")
                lines.append(f"    {breaking_count} breaking changes")
                lines.append(f"    {compatible_count} compatible additions")
            else:
                lines.append("  ⚠ Not analyzed")
                lines.append(
                    f"    {outcome.reason or f'target is {outcome.state.value}'}"
                )
                if outcome.job_url:
                    lines.append(f"    {outcome.job_url}")
            lines.append("")

        lines.append("Observed result:")
        verdict = self.findings_verdict
        if verdict is FindingsVerdict.NEUTRAL:
            lines.append("No targets could be analyzed.")
        elif verdict is FindingsVerdict.SUCCESS:
            lines.append("No ABI regressions were found in the analyzed targets.")
        else:
            regressed = sorted(
                tid
                for tid, diff in self.findings
                if diff.verdict in _REGRESSION_VERDICTS
            )
            lines.append(f"ABI regressions found on: {', '.join(regressed)}.")

        lines.append("")
        lines.append("Coverage:")
        if self.unavailable_target_ids:
            unresolved = ", ".join(sorted(self.unavailable_target_ids))
            lines.append(f"Incomplete. {unresolved} ABI compatibility is unknown.")
        else:
            lines.append("Complete. All expected targets were analyzed.")

        return "\n".join(lines)


class Assessment:
    """Incrementally aggregates per-target outcomes into one assessment.

    A target's result becomes visible as soon as it is recorded — nothing is
    withheld while other targets are still running. :meth:`finalize` is a
    reconciliation step (fill in targets that never reported), not the point
    at which analysis starts.
    """

    def __init__(self, manifest: AssessmentManifest) -> None:
        self.manifest = manifest
        self._outcomes: dict[str, TargetOutcome] = {}
        self._additional_outcomes: dict[str, TargetOutcome] = {}

    def record(self, outcome: TargetOutcome) -> None:
        """Record a target outcome.

        Outcomes for a different commit (``outcome.head_sha`` set and not
        matching ``manifest.head_sha``) are dropped — stale data from a
        superseded commit must never contaminate the current assessment. A
        lower ``attempt`` than one already recorded for the same target is
        also dropped, so a late-arriving retry of an old attempt can't
        clobber a newer result.
        """
        if outcome.head_sha is not None and outcome.head_sha != self.manifest.head_sha:
            return
        bucket = (
            self._outcomes
            if outcome.target_id in self.manifest.target_ids
            else self._additional_outcomes
        )
        existing = bucket.get(outcome.target_id)
        if existing is not None and existing.attempt > outcome.attempt:
            return
        bucket[outcome.target_id] = outcome

    def progress(self) -> tuple[int, int]:
        """``(targets with a recorded outcome so far, total expected)``."""
        return len(self._outcomes), len(self.manifest.targets)

    def finalize(self) -> AssessmentResult:
        """Reconcile into an :class:`AssessmentResult`.

        Every expected target that never recorded an outcome is filled in as
        :attr:`TargetState.INCOMPLETE` — it is still, like every other
        non-``ANALYZED`` state, treated as unknown rather than an empty ABI.
        """
        outcomes = dict(self._outcomes)
        for spec in self.manifest.targets:
            if spec.id not in outcomes:
                outcomes[spec.id] = TargetOutcome(
                    target_id=spec.id,
                    state=TargetState.INCOMPLETE,
                    required=spec.required,
                )
        return AssessmentResult(
            manifest=self.manifest,
            outcomes=outcomes,
            additional_outcomes=dict(self._additional_outcomes),
        )
