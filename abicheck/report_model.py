# Copyright 2026 Nikolay Petrov
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

"""A render-ready view of a comparison result (C2 — ADR-036).

Output formats used to each re-apply the ``show_only`` filter and re-derive the
verdict-axis buckets (breaking / source-break / risk / compatible) on their own.
:class:`ReportModel` computes those *once* from a :class:`DiffResult` so renderers
become thin projections over a single, canonical classification.

Canonical severity (ADR-036): the **verdict axis** — each finding's
``result._effective_verdict_for_change(c)`` (policy-file overrides + ADR-027 A4
per-finding modulation respected). This is the same partition that drives the
overall verdict and the process exit code, so the report can never disagree with
the gate. The ABICC-style display severity (HIGH/MEDIUM/LOW in
``report_classifications``) and the symbol-origin axis (rtti/internal/public in
``report_summary``) are *separate projections*, deliberately not collapsed into
this one — they answer different questions.

Cycle-safety: this module imports only ``checker_policy`` and ``report_summary``.
The ``show_only`` filter lives in ``reporter`` (``apply_show_only``); callers
apply it and pass the already-filtered change list in, so ``report_model`` never
imports ``reporter`` — ``reporter`` depends on this module one-directionally.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .checker_policy import Verdict
from .report_summary import ReportSummary, build_summary

if TYPE_CHECKING:
    from .checker_types import Change, DiffResult


# ── Canonical cross-axis presentation mapping (ADR-036) ──────────────────────
#
# All output channels share ONE authoritative table mapping the canonical
# verdict to each channel's vocabulary. The axes are deliberately distinct (they
# answer different questions and have different origins) — the point of this
# table is that the *mapping between them* lives in exactly one place, so they
# can be related and verified rather than re-encoded per renderer.
#
#   verdict                | native label | SARIF level | breaking boundary
#   -----------------------|--------------|-------------|------------------
#   BREAKING               | breaking     | error       | yes
#   API_BREAK              | api_break    | error       | yes
#   COMPATIBLE_WITH_RISK   | risk         | warning     | no
#   COMPATIBLE             | compatible   | note        | no
#
# Two further axes are intentionally NOT verdict-derived and keep their own
# single-source mappings (see ADR-036):
#   * ABICC display severity (High/Medium/Low) — kind-based, ABICC parity,
#     in ``report_classifications.severity``.
#   * symbol origin (rtti/internal/public) — name-based, in
#     ``name_classification.symbol_origin``.
# The SARIF *level* here is the override-path mapping; SARIF additionally uses a
# finer per-kind level for non-overridden findings (``policy_for(kind).severity``).


@dataclass(frozen=True)
class VerdictPresentation:
    """How one canonical verdict presents in each verdict-derived channel axis."""

    severity_label: str  # native JSON/text label + PR-comment input
    sarif_level: str  # SARIF level on the override path
    breaking_boundary: bool  # error/failure side of the gate?


VERDICT_PRESENTATION: dict[Verdict, VerdictPresentation] = {
    Verdict.BREAKING: VerdictPresentation("breaking", "error", True),
    Verdict.API_BREAK: VerdictPresentation("api_break", "error", True),
    Verdict.COMPATIBLE_WITH_RISK: VerdictPresentation("risk", "warning", False),
    Verdict.COMPATIBLE: VerdictPresentation("compatible", "note", False),
}
# Verdict.NO_CHANGE is intentionally absent: per-finding classification
# (``_effective_verdict_for_change``) only ever returns the four reportable
# verdicts above — a *finding* is never NO_CHANGE (that is an overall-result
# state, handled by the exit-code path in ``severity.legacy_exit_code``). A
# lookup miss falls back to UNKNOWN_SEVERITY_LABEL / non-breaking, which is the
# safe default.

# Fallback label for a verdict not in the table (defensive; should not occur).
UNKNOWN_SEVERITY_LABEL = "unknown"

# Back-compat projections derived from the single table above, so the existing
# import names in reporter/sarif keep working without a second source of truth.
VERDICT_TO_SEVERITY_LABEL: dict[Verdict, str] = {
    v: p.severity_label for v, p in VERDICT_PRESENTATION.items()
}
VERDICT_TO_SARIF_LEVEL: dict[Verdict, str] = {
    v: p.sarif_level for v, p in VERDICT_PRESENTATION.items()
}


@dataclass(frozen=True)
class ReportModel:
    """Classified, filtered, summarised view of a :class:`DiffResult`.

    ``changes`` is the display set (after any ``show_only`` filter); the four
    bucket lists partition it by canonical verdict; ``summary`` is the headline
    metric roll-up. The verdict counts on ``result`` itself are unfiltered and
    remain the source of truth for the gate — the buckets here are the *display*
    partition of the (possibly filtered) change set.
    """

    result: DiffResult
    changes: list[Change]
    breaking: list[Change]
    source_breaks: list[Change]
    risk: list[Change]
    compatible: list[Change]
    summary: ReportSummary

    @staticmethod
    def classify(
        changes: list[Change],
        result: DiffResult,
    ) -> tuple[list[Change], list[Change], list[Change], list[Change]]:
        """Split *changes* into (breaking, source_breaks, risk, compatible) by the
        effective per-finding verdict (canonical severity, ADR-036)."""
        ev = result._effective_verdict_for_change
        breaking = [c for c in changes if ev(c) == Verdict.BREAKING]
        source_breaks = [c for c in changes if ev(c) == Verdict.API_BREAK]
        risk = [c for c in changes if ev(c) == Verdict.COMPATIBLE_WITH_RISK]
        compatible = [c for c in changes if ev(c) == Verdict.COMPATIBLE]
        return breaking, source_breaks, risk, compatible

    def verdict_of(self, change: Change) -> Verdict:
        """Canonical per-finding verdict (policy + ADR-027 A4 overrides)."""
        return self.result._effective_verdict_for_change(change)

    def severity_label(self, change: Change) -> str:
        """Canonical native severity label for *change* (breaking/api_break/…).

        This is the verdict axis used by the JSON/text reports and consumed by
        the PR comment. SARIF keeps a finer per-kind level (see
        :data:`VERDICT_PRESENTATION`, the override path); the cross-channel
        invariant is the *breaking boundary* and override propagation, not
        identical vocabulary — see ADR-036 and ``tests/test_report_integrity.py``.
        """
        pres = VERDICT_PRESENTATION.get(self.verdict_of(change))
        return pres.severity_label if pres else UNKNOWN_SEVERITY_LABEL

    def is_breaking_boundary(self, change: Change) -> bool:
        """True if *change* is on the breaking side of the gate (BREAKING/API_BREAK).

        The one classification fact every channel must agree on: a finding here
        must read as error/failure in SARIF/JUnit and breaking in JSON/text;
        one not here must never read as error/failure.
        """
        pres = VERDICT_PRESENTATION.get(self.verdict_of(change))
        return pres.breaking_boundary if pres else False

    @classmethod
    def from_result(
        cls,
        result: DiffResult,
        *,
        changes: list[Change] | None = None,
    ) -> ReportModel:
        """Build the model and classify *changes* (defaults to all of
        ``result.changes``).

        The ``show_only`` display filter is applied by the caller (via
        ``reporter.apply_show_only``) and the filtered list passed in here, so
        this module stays free of any ``reporter`` import (no cycle).
        """
        if changes is None:
            changes = list(result.changes)
        breaking, source_breaks, risk, compatible = cls.classify(changes, result)
        return cls(
            result=result,
            changes=changes,
            breaking=breaking,
            source_breaks=source_breaks,
            risk=risk,
            compatible=compatible,
            summary=build_summary(result),
        )
