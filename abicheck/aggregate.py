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
GitHub Action recipes, whose ``for path in glob('*.json')`` loop silently
dropped any target whose build failed before uploading its report — passing
green while a required platform was never analyzed.

Three orthogonal axes, kept separate on purpose (ADR-042):

* **compatibility** — the worst ABI *verdict* over the analyzed targets, for
  reporting. This is *not* the gate: a policy can make a ``COMPATIBLE`` report
  block (``addition=error``) or a ``BREAKING`` report pass (a demoted preset).
* **gate** — whether CI should fail. Each report already carries its own gate
  decision (``severity.{exit_code,blocking,blocking_categories}``, computed by
  ``reporter._build_severity_json`` → ``severity.compute_gate_decision``);
  ``aggregate`` combines those, it never recomputes a gate from the verdict.
  Reports produced without a ``--severity-*`` policy carry no gate block, so
  they fall back to the legacy verdict→exit mapping.
* **coverage** — did every *required* target actually report? A required gap is
  a *coverage* failure (exit ``1``), never masqueraded as an ABI break.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .change_registry_types import Verdict

#: Machine-readable schema version of the ``to_dict()`` / ``--format json``
#: output. Bump on any incompatible change to that structure.
AGGREGATE_SCHEMA_VERSION = "1.0"

#: Legacy verdict → gate exit code, used only for reports that carry no
#: ``severity`` gate block (i.e. produced without a ``--severity-*`` policy).
#: Mirrors ``compare``'s legacy scheme: NO_CHANGE/COMPATIBLE/COMPATIBLE_WITH_RISK
#: are non-blocking (0), API_BREAK is a source break (2), BREAKING an ABI break
#: (4). A report *with* a gate block uses that block's own ``exit_code`` — the
#: authoritative, policy-aware value — instead.
_LEGACY_SEVERITY: dict[Verdict, int] = {
    Verdict.NO_CHANGE: 0,
    Verdict.COMPATIBLE: 0,
    Verdict.COMPATIBLE_WITH_RISK: 0,
    Verdict.API_BREAK: 2,
    Verdict.BREAKING: 4,
}

#: Total ordering over verdicts for *reporting* the worst analyzed
#: compatibility verdict. Unlike the exit scheme (which collapses the three
#: non-blocking verdicts), this keeps ``COMPATIBLE_WITH_RISK`` strictly above
#: ``COMPATIBLE`` so a risk one target flagged is never hidden in the summary.
_VERDICT_RANK: dict[Verdict, int] = {
    Verdict.NO_CHANGE: 0,
    Verdict.COMPATIBLE: 1,
    Verdict.COMPATIBLE_WITH_RISK: 2,
    Verdict.API_BREAK: 3,
    Verdict.BREAKING: 4,
}

#: Exit code contributed by an incomplete *required* coverage gap. Deliberately
#: NOT 4 (an ABI break) or 2 (a source break) — a missing build is an
#: infrastructure/coverage problem, and an external wrapper reading exit 4 as
#: "ABI break" must never be handed one for a build that simply never ran.
COVERAGE_INCOMPLETE_EXIT = 1

#: Default report-filename prefix the matrix recipe uses
#: (``abi-report-<target>.json``). Stripped when deriving a target id from a
#: report file's stem, if the report does not self-identify a ``target_id``.
DEFAULT_REPORT_PREFIX = "abi-report-"


class CoverageStatus(str, Enum):
    """Was every *required* expected target actually analyzed?"""

    COMPLETE = "complete"  # every required target reported
    PARTIAL = "partial"  # at least one required target is unavailable
    EMPTY = "empty"  # no target could be analyzed at all


class OnMissingRequired(str, Enum):
    """Gate policy for a required target that never reported."""

    FAIL = "fail"  # incomplete required coverage fails the gate (default)
    WARN = "warn"  # report the gap but do not fail on coverage alone


class OnUnexpectedTarget(str, Enum):
    """Gate policy for a report whose target is not in the expected set."""

    INCLUDE = "include"  # count its real findings in the gate, not in coverage
    WARN = "warn"  # surface it and warn, but never fail the gate on it
    FAIL = "fail"  # any unexpected target fails the gate
    IGNORE = "ignore"  # drop it entirely


class AggregateError(ValueError):
    """A malformed input the caller must fix (usage error / exit 64)."""


@dataclass(frozen=True)
class GateInfo:
    """One target's own CI gate decision, as it recorded it.

    ``exit_code`` is in ``compare``'s severity-aware scheme (0 pass / 1
    addition-or-quality error / 2 potential-breaking error / 4 abi-breaking
    error). ``blocking_categories`` names which severity categories are
    failing. This is read from the report's ``severity`` block when present
    (the policy-aware, authoritative value), or synthesized from the verdict
    via :data:`_LEGACY_SEVERITY` for reports produced without a policy.
    """

    exit_code: int
    blocking: bool
    blocking_categories: tuple[str, ...] = ()
    from_report: bool = True  # False when legacy-derived from the verdict

    @classmethod
    def from_report_data(cls, data: Mapping[str, Any]) -> GateInfo | None:
        sev = data.get("severity")
        if not isinstance(sev, dict):
            return None
        exit_code = sev.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            return None
        cats = sev.get("blocking_categories")
        categories = tuple(str(c) for c in cats) if isinstance(cats, list) else ()
        return cls(
            exit_code=exit_code,
            blocking=bool(sev.get("blocking", exit_code > 0)),
            blocking_categories=categories,
            from_report=True,
        )

    @classmethod
    def legacy_from_verdict(cls, verdict: Verdict | None) -> GateInfo:
        code = _LEGACY_SEVERITY.get(verdict, 0) if verdict is not None else 0
        return cls(exit_code=code, blocking=code > 0, from_report=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "blocking": self.blocking,
            "blocking_categories": list(self.blocking_categories),
            "from_report": self.from_report,
        }


@dataclass(frozen=True)
class TargetReport:
    """One target's contribution to the aggregate.

    ``compatibility_verdict`` is ``None`` exactly when the target is
    *unavailable* — its report was expected but never arrived or was
    unreadable — in which case ``gate`` is also ``None`` and ``reason``
    explains why. ``unexpected`` marks a report whose target was not in the
    expected set (RFC §7 "new/unbaselined target").
    """

    target_id: str
    required: bool
    compatibility_verdict: Verdict | None  # None ⟺ unavailable
    gate: GateInfo | None = None
    report_path: str | None = None
    library: str | None = None
    reason: str | None = None  # populated only when unavailable
    unexpected: bool = False

    @property
    def analyzed(self) -> bool:
        return self.compatibility_verdict is not None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "target_id": self.target_id,
            "required": self.required,
            "state": "analyzed" if self.analyzed else "unavailable",
            "compatibility_verdict": (
                self.compatibility_verdict.value
                if self.compatibility_verdict is not None
                else None
            ),
            "gate": self.gate.to_dict() if self.gate is not None else None,
        }
        if self.unexpected:
            d["unexpected"] = True
        for key in ("report_path", "library", "reason"):
            value = getattr(self, key)
            if value is not None:
                d[key] = value
        return d


@dataclass(frozen=True)
class AggregateResult:
    """The finalized fan-in view over one commit's per-target reports."""

    #: Every expected target, in stable id order — analyzed or unavailable.
    targets: tuple[TargetReport, ...]
    #: Reports for ids not in the expected set (a candidate matrix leg with no
    #: corresponding expected entry). Graded by ``on_unexpected_target``.
    unbaselined: tuple[TargetReport, ...] = ()
    on_missing_required: OnMissingRequired = OnMissingRequired.FAIL
    on_unexpected_target: OnUnexpectedTarget = OnUnexpectedTarget.INCLUDE
    #: True when the caller ran in explicit discovered-only mode (no declared
    #: expected set), so coverage is not gated.
    discovered_only: bool = False

    # --- membership helpers -------------------------------------------------
    @property
    def analyzed(self) -> tuple[TargetReport, ...]:
        return tuple(t for t in self.targets if t.analyzed)

    @property
    def unavailable(self) -> tuple[TargetReport, ...]:
        return tuple(t for t in self.targets if not t.analyzed)

    @property
    def _gated_unexpected(self) -> tuple[TargetReport, ...]:
        """Unexpected targets whose findings count toward the gate."""
        if self.on_unexpected_target in (
            OnUnexpectedTarget.INCLUDE,
            OnUnexpectedTarget.FAIL,
        ):
            return tuple(t for t in self.unbaselined if t.analyzed)
        return ()

    # --- compatibility axis (reporting only) --------------------------------
    @property
    def _compat_targets(self) -> tuple[TargetReport, ...]:
        """Analyzed targets whose verdict feeds the compatibility summary.

        This is *not* just the expected analyzed targets: any unexpected
        target whose findings are gated (``--on-unexpected-target
        include``/``fail``) also contributes, so the reported compatibility can
        never say "clean" while a gated unbaselined break is driving the exit
        code. Non-gated unexpected targets (``warn``/``ignore``) are excluded,
        matching :attr:`_gated_unexpected`.
        """
        return self.analyzed + self._gated_unexpected

    @property
    def compatibility_verdict(self) -> Verdict | None:
        verdicts = [
            t.compatibility_verdict
            for t in self._compat_targets
            if t.compatibility_verdict is not None
        ]
        if not verdicts:
            return None
        return max(verdicts, key=lambda v: _VERDICT_RANK[v])

    # --- coverage axis ------------------------------------------------------
    @property
    def required_gap(self) -> bool:
        """A *required* target that did not report — the coverage fail signal."""
        return any(not t.analyzed and t.required for t in self.targets)

    @property
    def coverage(self) -> CoverageStatus:
        if not self.required_gap:
            return CoverageStatus.COMPLETE
        return CoverageStatus.EMPTY if not self.analyzed else CoverageStatus.PARTIAL

    @property
    def missing_required(self) -> tuple[str, ...]:
        return tuple(t.target_id for t in self.targets if not t.analyzed and t.required)

    @property
    def coverage_blocking(self) -> bool:
        return (
            self.required_gap
            and self.on_missing_required is OnMissingRequired.FAIL
            and not self.discovered_only
        )

    # --- gate axis (the CI decision) ----------------------------------------
    @property
    def blocking_targets(self) -> tuple[str, ...]:
        gated = list(self.analyzed) + list(self._gated_unexpected)
        return tuple(
            sorted(
                t.target_id
                for t in gated
                if t.gate is not None and (t.gate.exit_code > 0 or t.gate.blocking)
            )
        )

    def exit_code(self) -> int:
        """The single CI gate exit code.

        The max of every gated target's own ``severity.exit_code`` (so a
        target's policy-blocked addition contributes ``1``, an API break ``2``,
        an ABI break ``4`` — never recomputed from the verdict) and a coverage
        contribution of ``1`` when a required target is missing. ``64`` /
        malformed-input errors are raised as :class:`AggregateError`, never
        returned here.
        """
        gated = list(self.analyzed) + list(self._gated_unexpected)
        code = max((t.gate.exit_code for t in gated if t.gate is not None), default=0)
        if self.coverage_blocking:
            code = max(code, COVERAGE_INCOMPLETE_EXIT)
        # ``fail`` fails the gate on *any* unexpected report — including one that
        # is unreadable/verdictless (so has no gate to contribute above) — since
        # the policy is "no target outside the expected set is tolerated".
        if self.on_unexpected_target is OnUnexpectedTarget.FAIL and self.unbaselined:
            code = max(code, COVERAGE_INCOMPLETE_EXIT)
        return code

    @property
    def passed(self) -> bool:
        return self.exit_code() == 0

    # --- rendering ----------------------------------------------------------
    def render_text(self) -> str:
        required_total = sum(1 for t in self.targets if t.required)
        required_analyzed = sum(1 for t in self.targets if t.required and t.analyzed)

        lines: list[str] = []
        header = "Passed" if self.passed else "Failed"
        cov = {
            CoverageStatus.COMPLETE: "complete",
            CoverageStatus.PARTIAL: "partial",
            CoverageStatus.EMPTY: "no coverage",
        }[self.coverage]
        lines.append(f"ABI aggregate gate: {header} (coverage: {cov})")
        if not self.discovered_only:
            lines.append(
                f"Analyzed {required_analyzed} of {required_total} required targets"
            )
        lines.append("")

        for target in self.targets:
            lines.append("  " + self._render_target_line(target))
        for extra in self.unbaselined:
            lines.append("  " + self._render_target_line(extra))

        lines.append("")
        lines.append("Compatibility:")
        lines.append("  " + self._render_compatibility_line())

        lines.append("Coverage:")
        if self.coverage is CoverageStatus.COMPLETE:
            lines.append("  Complete — every required target was analyzed.")
        else:
            missing = ", ".join(self.missing_required) or "(none)"
            gated = "" if self.coverage_blocking else " (advisory)"
            lines.append(
                f"  Incomplete — required target(s) unknown: {missing}.{gated}"
            )

        lines.append("Gate:")
        if self.passed:
            lines.append("  Passed — no blocking findings, required coverage complete.")
        else:
            blockers = ", ".join(self.blocking_targets) or "(none)"
            parts = [f"exit {self.exit_code()}"]
            if self.blocking_targets:
                parts.append(f"blocking: {blockers}")
            if self.coverage_blocking:
                parts.append("required coverage incomplete")
            lines.append("  Failed — " + "; ".join(parts) + ".")

        return "\n".join(lines)

    def _render_target_line(self, t: TargetReport) -> str:
        tag = "" if t.required else " (optional)"
        if t.unexpected:
            tag = " (unbaselined)"
        if not t.analyzed:
            return f"{t.target_id}{tag}: ⚠ unavailable — {t.reason or 'no report'}"
        assert t.compatibility_verdict is not None
        verdict = t.compatibility_verdict.value
        if t.gate is not None and t.gate.blocking:
            cats = ", ".join(t.gate.blocking_categories)
            gate = f" [gate: blocking{f' ({cats})' if cats else ''}]"
        else:
            gate = ""
        return f"{t.target_id}{tag}: {verdict}{gate}"

    def _render_compatibility_line(self) -> str:
        verdict = self.compatibility_verdict
        if verdict is None:
            return "No targets were analyzed — no compatibility verdict."
        if verdict is Verdict.COMPATIBLE_WITH_RISK:
            risky = sorted(
                t.target_id
                for t in self._compat_targets
                if t.compatibility_verdict is Verdict.COMPATIBLE_WITH_RISK
            )
            return f"No ABI regressions; compatible-with-risk on: {', '.join(risky)}."
        rank = _VERDICT_RANK[verdict]
        if rank <= _VERDICT_RANK[Verdict.COMPATIBLE]:
            return "No ABI regressions in the analyzed targets."
        by_verdict = []
        for v in (Verdict.BREAKING, Verdict.API_BREAK):
            hits = sorted(
                t.target_id
                for t in self._compat_targets
                if t.compatibility_verdict is v
            )
            if hits:
                by_verdict.append(f"{v.value} on: {', '.join(hits)}")
        return "; ".join(by_verdict) + "."

    def to_dict(self) -> dict[str, Any]:
        verdict = self.compatibility_verdict
        required_total = sum(1 for t in self.targets if t.required)
        required_analyzed = sum(1 for t in self.targets if t.required and t.analyzed)
        return {
            "aggregate_schema_version": AGGREGATE_SCHEMA_VERSION,
            "status": "pass" if self.passed else "fail",
            "compatibility": {
                "verdict": verdict.value if verdict is not None else None,
                "analyzed_targets": len(self._compat_targets),
            },
            "coverage": {
                "status": self.coverage.value,
                "required_targets": required_total,
                "analyzed_required_targets": required_analyzed,
                "missing_required_targets": list(self.missing_required),
                "blocking": self.coverage_blocking,
            },
            "gate": {
                "passed": self.passed,
                "exit_code": self.exit_code(),
                "blocking_targets": list(self.blocking_targets),
                "coverage_blocking": self.coverage_blocking,
            },
            "targets": [t.to_dict() for t in self.targets],
            "unbaselined": [t.to_dict() for t in self.unbaselined],
        }


# --- parsing / loading ------------------------------------------------------


def parse_report_verdict(data: Mapping[str, Any]) -> Verdict | None:
    """Extract the compatibility verdict from a parsed report."""
    raw = data.get("verdict")
    if not isinstance(raw, str):
        return None
    try:
        return Verdict(raw)
    except ValueError:
        return None


def target_id_from_path(path: Path, *, prefix: str = DEFAULT_REPORT_PREFIX) -> str:
    """Derive a target id from a report file's stem (convenience fallback)."""
    stem = path.stem
    if prefix and stem.startswith(prefix):
        stem = stem[len(prefix) :]
    return stem


@dataclass(frozen=True)
class _LoadedReport:
    target_id: str
    verdict: Verdict | None
    gate: GateInfo | None
    library: str | None
    head_sha: str | None
    reason: str | None
    path: Path


def _load_report_file(path: Path, *, prefix: str) -> _LoadedReport:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return _LoadedReport(
            target_id_from_path(path, prefix=prefix),
            None,
            None,
            None,
            None,
            f"unreadable report ({type(exc).__name__})",
            path,
        )
    if not isinstance(data, dict):
        return _LoadedReport(
            target_id_from_path(path, prefix=prefix),
            None,
            None,
            None,
            None,
            "report is not a JSON object",
            path,
        )
    # Prefer the report's own self-identified target id; fall back to filename.
    own_id = data.get("target_id")
    target_id = (
        str(own_id)
        if isinstance(own_id, str) and own_id
        else target_id_from_path(path, prefix=prefix)
    )
    verdict = parse_report_verdict(data)
    gate: GateInfo | None = None
    if verdict is not None:
        gate = GateInfo.from_report_data(data) or GateInfo.legacy_from_verdict(verdict)
    head_sha = data.get("head_sha")
    return _LoadedReport(
        target_id=target_id,
        verdict=verdict,
        gate=gate,
        library=data.get("library"),
        head_sha=str(head_sha) if isinstance(head_sha, str) else None,
        reason=None if verdict is not None else "report carried no ABI verdict",
        path=path,
    )


def collect_reports(
    reports_dir: Path, *, prefix: str = DEFAULT_REPORT_PREFIX
) -> dict[str, _LoadedReport]:
    """Load every ``*.json`` report in *reports_dir*, keyed by target id.

    A missing directory is treated as zero reports (a full build outage must
    still produce a coverage result, not a usage error). Two reports resolving
    to the *same* target id are a hard :class:`AggregateError` — silently
    dropping one on a CI gate is unacceptable.
    """
    found: dict[str, _LoadedReport] = {}
    if not reports_dir.is_dir():
        return found
    for path in sorted(reports_dir.glob("*.json")):
        report = _load_report_file(path, prefix=prefix)
        if report.target_id in found:
            raise AggregateError(
                f"duplicate target id {report.target_id!r}: both "
                f"{found[report.target_id].path.name} and {path.name} resolve to "
                "it — give each target a unique report/artifact name"
            )
        found[report.target_id] = report
    return found


# --- expected-set specification --------------------------------------------


@dataclass(frozen=True)
class ExpectedTargets:
    """The declared expected-target set (from a manifest or CLI flags)."""

    #: target_id → required
    targets: Mapping[str, bool]
    head_sha: str | None = None

    @classmethod
    def from_manifest_file(cls, path: Path) -> ExpectedTargets:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise AggregateError(f"cannot read manifest {path}: {exc}") from exc
        return cls.from_manifest_data(data)

    @classmethod
    def from_manifest_data(cls, data: Any) -> ExpectedTargets:
        if not isinstance(data, dict):
            raise AggregateError("manifest must be a JSON object")
        raw = data.get("targets")
        if not isinstance(raw, list) or not raw:
            raise AggregateError("manifest 'targets' must be a non-empty list")
        targets: dict[str, bool] = {}
        for entry in raw:
            if not isinstance(entry, dict):
                raise AggregateError(f"manifest target must be an object: {entry!r}")
            tid = entry.get("id")
            if not isinstance(tid, str) or not tid:
                raise AggregateError(f"manifest target needs a string 'id': {entry!r}")
            if tid in targets:
                raise AggregateError(f"duplicate manifest target id: {tid!r}")
            required = entry.get("required", True)
            if not isinstance(required, bool):
                raise AggregateError(
                    f"manifest target 'required' must be a boolean: {entry!r}"
                )
            targets[tid] = required
        head_sha = data.get("head_sha")
        return cls(
            targets=targets,
            head_sha=str(head_sha) if isinstance(head_sha, str) else None,
        )

    @classmethod
    def from_lists(
        cls, required: Iterable[str], optional: Iterable[str] = ()
    ) -> ExpectedTargets:
        targets: dict[str, bool] = {tid: False for tid in optional}
        for tid in required:
            targets[tid] = True
        if not targets:
            raise AggregateError("no expected targets given")
        return cls(targets=targets)


# --- the aggregation itself -------------------------------------------------


def aggregate(
    expected: ExpectedTargets | None,
    found: Mapping[str, _LoadedReport],
    *,
    on_missing_required: OnMissingRequired = OnMissingRequired.FAIL,
    on_unexpected_target: OnUnexpectedTarget = OnUnexpectedTarget.INCLUDE,
) -> AggregateResult:
    """Reconcile an expected-target set against the reports found.

    *expected* is ``None`` only in discovered-only mode, where the reports
    present *are* the expected set and coverage is not gated.
    """
    discovered_only = expected is None
    if expected is None:
        expected = ExpectedTargets(targets={tid: True for tid in found}, head_sha=None)

    def _target(tid: str, required: bool, unexpected: bool) -> TargetReport:
        report = found.get(tid)
        if report is None:
            return TargetReport(
                target_id=tid,
                required=required,
                compatibility_verdict=None,
                reason="no report was produced for this expected target",
                unexpected=unexpected,
            )
        # A report for a superseded commit (manifest head_sha set and the
        # report's own head_sha present and different) is stale — unavailable.
        if (
            expected.head_sha is not None
            and report.head_sha is not None
            and report.head_sha != expected.head_sha
        ):
            return TargetReport(
                target_id=tid,
                required=required,
                compatibility_verdict=None,
                report_path=str(report.path),
                library=report.library,
                reason=f"report is for a different commit ({report.head_sha})",
                unexpected=unexpected,
            )
        return TargetReport(
            target_id=tid,
            required=required,
            compatibility_verdict=report.verdict,
            gate=report.gate,
            report_path=str(report.path),
            library=report.library,
            reason=report.reason,
            unexpected=unexpected,
        )

    targets = tuple(
        _target(tid, expected.targets[tid], unexpected=False)
        for tid in sorted(expected.targets)
    )

    unbaselined: tuple[TargetReport, ...] = ()
    if on_unexpected_target is not OnUnexpectedTarget.IGNORE:
        unbaselined = tuple(
            _target(tid, required=False, unexpected=True)
            for tid in sorted(set(found) - set(expected.targets))
        )

    return AggregateResult(
        targets=targets,
        unbaselined=unbaselined,
        on_missing_required=on_missing_required,
        on_unexpected_target=on_unexpected_target,
        discovered_only=discovered_only,
    )


def aggregate_reports_dir(
    reports_dir: Path,
    *,
    expected: ExpectedTargets | None = None,
    discovered_only: bool = False,
    on_missing_required: OnMissingRequired = OnMissingRequired.FAIL,
    on_unexpected_target: OnUnexpectedTarget = OnUnexpectedTarget.INCLUDE,
    prefix: str = DEFAULT_REPORT_PREFIX,
) -> AggregateResult:
    """Load a reports dir and aggregate against an expected set.

    Exactly one of *expected* or *discovered_only* selects the mode. In
    discovered-only mode the reports present become the expected set and
    coverage is not gated — the caller must opt into that explicitly, since it
    cannot detect a missing target. Raises :class:`AggregateError` for
    malformed input (a usage error, exit 64).
    """
    if not discovered_only and expected is None:
        raise AggregateError(
            "no expected-target set: pass a manifest / expected targets, or "
            "opt into discovered-only mode explicitly"
        )
    found = collect_reports(reports_dir, prefix=prefix)
    return aggregate(
        None if discovered_only else expected,
        found,
        on_missing_required=on_missing_required,
        on_unexpected_target=on_unexpected_target,
    )
