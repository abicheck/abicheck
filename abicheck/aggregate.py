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

"""Fan-in aggregation of per-target ABI reports (multi-target CI gate).

A project that ships several ABI-relevant targets (``linux-x86_64``,
``windows-x86_64``, ``macos-arm64``, ...) builds and compares each one on its
own CI matrix leg, and each leg emits a ``compare``/``scan`` JSON report. This
module folds those per-target reports into one gate decision under a single
invariant:

    An expected target with no report is UNAVAILABLE (unknown), never folded
    into the verdict as compatible.

It replaces the hand-written post-matrix "ABI gate" heredoc shown in the
GitHub Action recipes: that loop iterated over *whatever report files happened
to be present*, so a target whose build failed before uploading its report was
silently dropped and the gate could pass as "all platforms compatible" when a
required platform was never analyzed at all.

The expected-target set is not a stored entity with its own identity — it is
the CI matrix's own target list, passed in at gate time (``--expect``). Each
report already self-describes its ``verdict``; a target id is just the report
file's stem (matching the artifact name the matrix chose). Two conclusions are
computed and kept orthogonal, exactly so a build-infrastructure failure is
never reported as an ABI regression:

* **findings** — the worst ABI verdict over the *analyzed* targets only.
* **coverage** — did every *required* expected target actually report?
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .change_registry_types import Verdict

#: Verdict → gate severity, mirroring ``compare``'s 0/2/4 exit scheme
#: (see docs/reference/exit-codes.md). NO_CHANGE / COMPATIBLE /
#: COMPATIBLE_WITH_RISK are all non-blocking (0); API_BREAK is a source-level
#: break (2); BREAKING is an ABI break (4). Used for the exit code only.
_SEVERITY: dict[Verdict, int] = {
    Verdict.NO_CHANGE: 0,
    Verdict.COMPATIBLE: 0,
    Verdict.COMPATIBLE_WITH_RISK: 0,
    Verdict.API_BREAK: 2,
    Verdict.BREAKING: 4,
}

#: Total ordering over verdicts for *reporting* the worst analyzed verdict.
#: Unlike ``_SEVERITY`` (which collapses the three non-blocking verdicts to
#: 0 for the exit code), this keeps ``COMPATIBLE_WITH_RISK`` strictly above
#: ``COMPATIBLE`` so the reported ``findings_verdict`` never hides a risk one
#: target flagged. It stays monotonic with ``_SEVERITY``, so the worst verdict
#: by this rank also has the worst severity — the exit code is unchanged.
_VERDICT_RANK: dict[Verdict, int] = {
    Verdict.NO_CHANGE: 0,
    Verdict.COMPATIBLE: 1,
    Verdict.COMPATIBLE_WITH_RISK: 2,
    Verdict.API_BREAK: 3,
    Verdict.BREAKING: 4,
}

#: Default report-filename prefix the matrix recipe uses
#: (``abi-report-<target>.json``). Stripped when deriving a target id from a
#: report file's stem.
DEFAULT_REPORT_PREFIX = "abi-report-"


class CoverageStatus(str, Enum):
    """Was every *required* expected target actually analyzed?"""

    COMPLETE = "complete"  # every required target reported a verdict
    PARTIAL = "partial"  # at least one required target is unavailable
    EMPTY = "empty"  # no target could be analyzed at all


class OnMissingRequired(str, Enum):
    """Gate policy for a required target that never reported."""

    FAIL = "fail"  # incomplete required coverage fails the gate (default)
    WARN = "warn"  # report the gap but do not fail on coverage alone


@dataclass(frozen=True)
class TargetReport:
    """One expected target's contribution to the aggregate.

    ``verdict`` is ``None`` exactly when the target is *unavailable* — its
    report was expected (the matrix declared the target) but never arrived, or
    arrived unreadable. An unavailable target carries a ``reason`` instead of a
    verdict, and never contributes to the findings verdict.
    """

    target_id: str
    required: bool
    verdict: Verdict | None  # None ⟺ unavailable
    report_path: str | None = None
    library: str | None = None
    old_version: str | None = None
    new_version: str | None = None
    reason: str | None = None  # populated only when unavailable

    @property
    def analyzed(self) -> bool:
        return self.verdict is not None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "target_id": self.target_id,
            "required": self.required,
            "analyzed": self.analyzed,
            "verdict": self.verdict.value if self.verdict is not None else None,
        }
        for key in ("report_path", "library", "old_version", "new_version", "reason"):
            value = getattr(self, key)
            if value is not None:
                d[key] = value
        return d


@dataclass(frozen=True)
class AggregateResult:
    """The finalized fan-in view over one commit's per-target reports."""

    #: Every expected target, in stable id order — analyzed or unavailable.
    targets: tuple[TargetReport, ...]
    #: Reports found for ids *not* in the expected set (RFC §7 "new/unbaselined
    #: target"): a candidate matrix leg with no corresponding expected entry.
    #: Surfaced for review, never counted toward coverage of the expected set.
    unbaselined: tuple[TargetReport, ...] = ()

    @property
    def analyzed(self) -> tuple[TargetReport, ...]:
        return tuple(t for t in self.targets if t.analyzed)

    @property
    def unavailable(self) -> tuple[TargetReport, ...]:
        return tuple(t for t in self.targets if not t.analyzed)

    @property
    def findings_verdict(self) -> Verdict | None:
        """Worst verdict over *analyzed* targets, or ``None`` if none analyzed.

        Ranked by :data:`_VERDICT_RANK` (a total order), not by exit severity,
        so a ``COMPATIBLE_WITH_RISK`` reported by one target is never hidden
        behind another's ``COMPATIBLE``.
        """
        analyzed = self.analyzed
        if not analyzed:
            return None
        return max((t.verdict for t in analyzed), key=lambda v: _VERDICT_RANK[v])  # type: ignore[index,arg-type]

    @property
    def findings_severity(self) -> int:
        verdict = self.findings_verdict
        return _SEVERITY[verdict] if verdict is not None else 0

    @property
    def required_gap(self) -> bool:
        """A *required* target that did not report — the coverage fail condition.

        Optional targets never count: a run that declares only optional
        targets (or whose only unavailable targets are optional) has no
        required gap and so never fails the coverage gate.
        """
        return any(not t.analyzed and t.required for t in self.targets)

    @property
    def coverage(self) -> CoverageStatus:
        if not self.required_gap:
            return CoverageStatus.COMPLETE
        # A required target is unavailable. Distinguish "nothing analyzed at
        # all" (EMPTY) from "some analyzed, some required missing" (PARTIAL)
        # for rendering; both fail the gate.
        return CoverageStatus.EMPTY if not self.analyzed else CoverageStatus.PARTIAL

    @property
    def is_partial(self) -> bool:
        return self.coverage is not CoverageStatus.COMPLETE

    def exit_code(
        self, *, on_missing_required: OnMissingRequired = OnMissingRequired.FAIL
    ) -> int:
        """Gate exit code: worst of findings severity and coverage policy.

        Findings map to ``compare``'s 0/2/4 scheme. Under the default
        ``FAIL`` policy, a *required* coverage gap (a required target that did
        not report) fails the gate at 4 — a gate must not pass green while a
        required platform is unknown. An unavailable *optional* target never
        contributes. Under ``WARN``, coverage never affects the exit code
        (findings alone decide), but the gap is still rendered.
        """
        code = self.findings_severity
        if (
            on_missing_required is OnMissingRequired.FAIL
            and self.coverage is not CoverageStatus.COMPLETE
        ):
            code = max(code, 4)
        return code

    def render_text(self) -> str:
        required_total = sum(1 for t in self.targets if t.required)
        required_analyzed = sum(1 for t in self.targets if t.required and t.analyzed)

        lines: list[str] = []
        header = {
            CoverageStatus.COMPLETE: "Complete",
            CoverageStatus.PARTIAL: "Partial",
            CoverageStatus.EMPTY: "No coverage",
        }[self.coverage]
        lines.append(f"ABI aggregate: {header}")
        lines.append(
            f"Analyzed {required_analyzed} of {required_total} required targets"
        )
        lines.append("")

        for target in self.targets:
            tag = "" if target.required else " (optional)"
            if target.analyzed:
                assert target.verdict is not None
                lines.append(f"  {target.target_id}{tag}: {target.verdict.value}")
            else:
                reason = target.reason or "no report was produced"
                lines.append(f"  {target.target_id}{tag}: ⚠ unavailable — {reason}")

        for extra in self.unbaselined:
            verdict = extra.verdict.value if extra.verdict is not None else "unreadable"
            lines.append(
                f"  {extra.target_id} (unbaselined): {verdict} — "
                "not in the expected target set"
            )

        lines.append("")
        lines.append("Findings:")
        findings = self.findings_verdict
        if findings is None:
            lines.append("  No targets were analyzed — no ABI verdict.")
        elif findings is Verdict.COMPATIBLE_WITH_RISK:
            risky = sorted(
                t.target_id
                for t in self.analyzed
                if t.verdict is Verdict.COMPATIBLE_WITH_RISK
            )
            lines.append(
                "  No ABI regressions, but compatible-with-risk on: "
                f"{', '.join(risky)}."
            )
        elif _SEVERITY[findings] == 0:
            lines.append("  No ABI regressions in the analyzed targets.")
        else:
            regressed = sorted(
                t.target_id
                for t in self.analyzed
                if t.verdict is not None and _SEVERITY[t.verdict] > 0
            )
            lines.append(f"  {findings.value} on: {', '.join(regressed)}.")

        lines.append("Coverage:")
        if self.coverage is CoverageStatus.COMPLETE:
            lines.append("  Complete — every required target was analyzed.")
        else:
            unknown = (
                ", ".join(t.target_id for t in self.unavailable if t.required)
                or "(none)"
            )
            lines.append(f"  Incomplete — required target(s) unknown: {unknown}.")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        verdict = self.findings_verdict
        return {
            "coverage": self.coverage.value,
            "findings_verdict": verdict.value if verdict is not None else None,
            "targets": [t.to_dict() for t in self.targets],
            "unbaselined": [t.to_dict() for t in self.unbaselined],
        }


def parse_report_verdict(data: Mapping[str, Any]) -> Verdict | None:
    """Extract the ABI verdict from a parsed ``compare``/``scan`` JSON report.

    Returns ``None`` when the payload carries no recognizable ``verdict`` — an
    unreadable/verdict-less report is treated as unavailable (unknown), never
    as a silent pass.
    """
    raw = data.get("verdict")
    if not isinstance(raw, str):
        return None
    try:
        return Verdict(raw)
    except ValueError:
        return None


def target_id_from_path(path: Path, *, prefix: str = DEFAULT_REPORT_PREFIX) -> str:
    """Derive a target id from a report file's stem.

    ``abi-report-linux-x86_64.json`` → ``linux-x86_64``; a bare
    ``linux-x86_64.json`` → ``linux-x86_64``. The id is whatever the matrix
    named its per-target artifact, matching what a caller passes to
    ``--expect``.
    """
    stem = path.stem
    if prefix and stem.startswith(prefix):
        stem = stem[len(prefix) :]
    return stem


def _load_report_file(
    path: Path,
) -> tuple[Verdict | None, dict[str, Any], str | None]:
    """Load one report file → (verdict, identity, reason-if-unavailable)."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return None, {}, f"unreadable report ({type(exc).__name__})"
    if not isinstance(data, dict):
        return None, {}, "report is not a JSON object"
    verdict = parse_report_verdict(data)
    identity = {
        "library": data.get("library"),
        "old_version": data.get("old_version"),
        "new_version": data.get("new_version"),
    }
    reason = None if verdict is not None else "report carried no ABI verdict"
    return verdict, identity, reason


def collect_reports(
    reports_dir: Path, *, prefix: str = DEFAULT_REPORT_PREFIX
) -> dict[str, tuple[Verdict | None, dict[str, Any], str | None, Path]]:
    """Load every ``*.json`` report in *reports_dir*, keyed by derived id.

    When two files derive the same id, the lexicographically last path wins
    (deterministic); this is not expected in practice since the matrix names
    each artifact uniquely.
    """
    found: dict[str, tuple[Verdict | None, dict[str, Any], str | None, Path]] = {}
    for path in sorted(reports_dir.glob("*.json")):
        target_id = target_id_from_path(path, prefix=prefix)
        verdict, identity, reason = _load_report_file(path)
        found[target_id] = (verdict, identity, reason, path)
    return found


def aggregate(
    expected: Mapping[str, bool],
    found: Mapping[str, tuple[Verdict | None, dict[str, Any], str | None, Path]],
) -> AggregateResult:
    """Reconcile expected targets against found reports.

    *expected* maps ``target_id -> required``. *found* maps
    ``target_id -> (verdict, identity, reason, path)`` (as produced by
    :func:`collect_reports`). An expected target absent from *found* is
    unavailable; a found id absent from *expected* is unbaselined.
    """
    targets: list[TargetReport] = []
    for target_id in sorted(expected):
        required = expected[target_id]
        entry = found.get(target_id)
        if entry is None:
            targets.append(
                TargetReport(
                    target_id=target_id,
                    required=required,
                    verdict=None,
                    reason="no report was produced for this expected target",
                )
            )
            continue
        verdict, identity, reason, path = entry
        targets.append(
            TargetReport(
                target_id=target_id,
                required=required,
                verdict=verdict,
                report_path=str(path),
                library=identity.get("library"),
                old_version=identity.get("old_version"),
                new_version=identity.get("new_version"),
                reason=reason,
            )
        )

    unbaselined: list[TargetReport] = []
    for target_id in sorted(set(found) - set(expected)):
        verdict, identity, reason, path = found[target_id]
        unbaselined.append(
            TargetReport(
                target_id=target_id,
                required=False,
                verdict=verdict,
                report_path=str(path),
                library=identity.get("library"),
                old_version=identity.get("old_version"),
                new_version=identity.get("new_version"),
                reason=reason,
            )
        )

    return AggregateResult(targets=tuple(targets), unbaselined=tuple(unbaselined))


def expected_from_lists(
    required: Iterable[str], optional: Iterable[str] = ()
) -> dict[str, bool]:
    """Build an ``expected`` map from a required list plus an optional list.

    A target named in both is treated as required. When *required* is empty,
    the caller intends "aggregate whatever is present" — see
    :func:`aggregate_reports_dir`, which then derives the expected set from the
    reports found (pure worst-of, backward compatible with the old heredoc).
    """
    expected = {tid: False for tid in optional}
    for tid in required:
        expected[tid] = True
    return expected


def aggregate_reports_dir(
    reports_dir: Path,
    *,
    required: Iterable[str] = (),
    optional: Iterable[str] = (),
    prefix: str = DEFAULT_REPORT_PREFIX,
) -> AggregateResult:
    """Convenience: load a reports dir and aggregate against an expected set.

    If *required* is empty, there is no declared required set, so the reports
    actually present become the expected set (pure worst-of aggregation — a
    present ``BREAKING`` still gates). Any *optional* ids are tracked as
    optional on top of that (an explicit ``--optional`` id always stays
    optional, even if a report for it is present). Supplying *required* is what
    turns on the "a missing required target is unknown, not compatible" gate.
    """
    found = collect_reports(reports_dir, prefix=prefix)
    required = list(required)
    optional = list(optional)
    if not required:
        # No required set declared → aggregate whatever reports are present
        # (as the required set for worst-of), plus any declared optional
        # targets. Explicit optional ids win, so a present report for an
        # id the caller marked optional stays optional.
        expected = {tid: False for tid in optional}
        for tid in found:
            expected.setdefault(tid, True)
    else:
        expected = expected_from_lists(required, optional)
    return aggregate(expected, found)
