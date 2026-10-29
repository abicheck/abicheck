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

"""The one outcome projection every pair-report format reads.

A comparison's *outcome* -- the verdict and how it is labelled, the severity
gate's decision, the merge-effect phrase stated from it, the severity
summary table, and a supplied consumer's informational scoped verdict -- used
to be worked out separately by the Markdown, review-digest, ``--stat`` and
HTML compute halves. :func:`project_outcome` builds it once from a completed
:class:`~abicheck.report.envelope.ReportEnvelope` (or, for a direct caller
with none, from the same primitives the envelope uses), so a summary bug fixed
here is fixed in every format.

Exit data is *read*, never derived: the full-library gate is the envelope's
:class:`~abicheck.policy.severity.GateDecision`, and the consumer's scoped exit
code is the envelope's :class:`~abicheck.report.scoped_gate_facts.ScopedGateFacts`.
Nothing here maps a verdict to an exit code.

The tables are plain data so a format can share the glyphs without sharing the
rest (the release and PR-comment tables extend :data:`VERDICT_EMOJI` with
their own non-comparison states).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from ..model.change_catalog.registry import Verdict
from .envelope import resolved_gate, resolved_scoped_gate
from .finding import build_report_findings
from .policy_facts import kind_sets_for

# The severity table's row structs stay beside their Markdown renderer (the
# only format that prints the table), as every compute half's structs do.
from .render_markdown import SeverityRow, SeveritySummary

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from ..policy.severity import GateDecision, SeverityConfig
    from .envelope import ReportEnvelope
    from .finding import ReportFinding
    from .scoped_gate_facts import ScopedGateFacts

__all__ = [
    "SEVERITY_EMOJI",
    "VERDICT_EMOJI",
    "VERDICT_LABEL",
    "VERDICT_MERGE_EFFECT",
    "OutcomeSummary",
    "compute_severity_summary",
    "gate_merge_effect",
    "project_outcome",
    "section_severity_label",
    "section_severity_level",
    "severity_summary_mapping",
]

VERDICT_EMOJI: Mapping[Verdict, str] = {
    Verdict.NO_CHANGE: "✅",
    Verdict.COMPATIBLE: "✅",
    Verdict.COMPATIBLE_WITH_RISK: "⚠️",
    Verdict.API_BREAK: "⚠️",
    Verdict.BREAKING: "❌",
}

VERDICT_LABEL: Mapping[Verdict, str] = {
    Verdict.NO_CHANGE: "NO_CHANGE",
    Verdict.COMPATIBLE: "COMPATIBLE",
    Verdict.COMPATIBLE_WITH_RISK: "COMPATIBLE_WITH_RISK",
    Verdict.API_BREAK: "API_BREAK",
    Verdict.BREAKING: "BREAKING",
}

#: Verdict -> short merge-effect phrase, used when no severity gate is configured.
VERDICT_MERGE_EFFECT: Mapping[Verdict, str] = {
    Verdict.NO_CHANGE: "no ABI/API change — safe to merge",
    Verdict.COMPATIBLE: "backward-compatible — safe to merge",
    Verdict.COMPATIBLE_WITH_RISK: "compatible but carries deployment risk — review advised",
    Verdict.API_BREAK: "source-level (API) break — consumers must recompile",
    Verdict.BREAKING: "binary (ABI) break — blocks merge under a strict gate",
}

SEVERITY_EMOJI: Mapping[str, str] = {
    "error": "❌",
    "warning": "⚠️",
    "info": "ℹ️",
}


def gate_merge_effect(exit_code: int) -> str:
    """The two merge-effect phrases, keyed by the resolved severity exit code."""
    if exit_code == 0:
        return "no error-level findings under the configured severity policy — safe to merge"
    return "blocked by severity policy — review required before merge"


@dataclass(frozen=True)
class OutcomeSummary:
    """One comparison's outcome as every format states it.

    ``gate`` is ``None`` when no severity scheme is in effect; ``scoped_gate``
    is ``None`` for a run with no ``--used-by``/``--required-symbol`` consumer.
    """

    verdict: Verdict
    gate: GateDecision | None
    scoped_gate: ScopedGateFacts | None

    @property
    def verdict_emoji(self) -> str:
        return VERDICT_EMOJI.get(self.verdict, "?")

    @property
    def verdict_label(self) -> str:
        return VERDICT_LABEL.get(self.verdict, self.verdict.value)

    @property
    def merge_effect(self) -> str:
        """The gate's phrase under a severity scheme, else the verdict's."""
        if self.gate is not None:
            return gate_merge_effect(self.gate.exit_code)
        return VERDICT_MERGE_EFFECT.get(self.verdict, "")

    @property
    def gate_exit_code(self) -> int | None:
        """The severity gate's exit code, or ``None`` with no gate in effect."""
        return None if self.gate is None else self.gate.exit_code


def project_outcome(
    result: DiffResult,
    *,
    envelope: ReportEnvelope | None = None,
    severity_config: SeverityConfig | None = None,
) -> OutcomeSummary:
    """The outcome of *result* (the envelope's own snapshot when one is given).

    With an *envelope* every decision is the envelope's; *severity_config* is
    read only by a direct caller with no envelope, which resolves the gate
    once through the same primitive the envelope builder uses.
    """
    if envelope is not None:
        result = envelope.result
    return OutcomeSummary(
        verdict=result.verdict,
        gate=resolved_gate(envelope, result, severity_config),
        scoped_gate=resolved_scoped_gate(envelope, result),
    )


# ---------------------------------------------------------------------------
# Severity configuration summary
# ---------------------------------------------------------------------------


def section_severity_level(
    severity_config: SeverityConfig | None, category_attr: str
) -> object | None:
    """That section's own resolved severity level, or ``None`` when no
    severity setting is in effect.

    The value half of :func:`section_severity_label`, which renders the
    same lookup for a heading. Split out because the rollup needs to *act*
    on the level (a gating section is never summarised) and parsing it back
    out of a rendered label would be a second derivation.
    """
    if severity_config is None:
        return None
    return getattr(severity_config, category_attr, None)


def section_severity_label(
    severity_config: SeverityConfig | None, category_attr: str
) -> str:
    """Return a severity label suffix like ' [ERROR]' for a report section header."""
    if severity_config is None:
        return ""
    level = getattr(severity_config, category_attr, None)
    if level is None:
        return ""
    level_val = level.value if hasattr(level, "value") else str(level)
    emoji = SEVERITY_EMOJI.get(level_val, "")
    return f" {emoji} `{level_val.upper()}`"


def compute_severity_summary(
    severity_config: SeverityConfig,
    *,
    counts: Mapping[str, int],
    exit_counts: Mapping[str, int],
    scoped_counts: dict[str, int] | None = None,
    scoped_blocking_categories: tuple[str, ...] | None = None,
) -> SeveritySummary:
    """Build the severity configuration summary table.

    *counts* are the per-category totals over the (possibly ``--show-only``-
    filtered) changes shown in the ``Count`` column; *exit_counts* are the
    per-category totals over the set the gate scores (ADR-049 D1), which
    drives the ``Exit Impact`` column so filtering the display cannot make
    this table claim "no exit impact" for a category that still fails the
    real gate. Both are keyed by :class:`~abicheck.policy.severity.IssueCategory`
    value and are resolved by the caller from the completed envelope's
    findings -- this function classifies nothing itself.

    *scoped_counts*/*scoped_blocking_categories* (Codex review), when given
    (from ``result.scoped_severity_counts``/``scoped_blocking_categories``
    on a ``--used-by``/``--required-symbol`` run), override both columns
    with the scoped gate's own numbers -- otherwise this table always
    reflects the full-library changes, so a scoped run whose only gating
    issue is a scoped-only change or missing-contract label (neither of which
    is in ``result.changes``) would show every category at 0 and "no exit
    impact" while the report elsewhere names a real, blocking finding.
    """
    from ..policy.severity import SeverityLevel

    category_info: list[tuple[str, str]] = [
        ("ABI/API Incompatibilities", "abi_breaking"),
        ("Potential Incompatibilities", "potential_breaking"),
        ("Quality Issues", "quality_issues"),
        ("Additions", "addition"),
    ]

    rows: list[SeverityRow] = []
    for label, attr in category_info:
        level = getattr(severity_config, attr, SeverityLevel.INFO)
        level_val = level.value if hasattr(level, "value") else str(level)
        emoji = SEVERITY_EMOJI.get(level_val, "")
        count = (
            scoped_counts.get(attr, 0)
            if scoped_counts is not None
            else counts.get(attr, 0)
        )
        impact = (
            "causes non-zero exit"
            if (
                attr in scoped_blocking_categories
                if scoped_blocking_categories is not None
                else level_val == "error" and exit_counts.get(attr, 0) > 0
            )
            else "no exit impact"
        )
        rows.append(
            SeverityRow(
                label=label,
                emoji=emoji,
                level_upper=level_val.upper(),
                count=count,
                impact=impact,
            )
        )
    # ADR-078: the informational category has no configurable level and
    # never gates; it gets a row only when it holds something to disclose.
    info_count = (
        scoped_counts.get("informational", 0)
        if scoped_counts is not None
        else counts.get("informational", 0)
    )
    if info_count:
        rows.append(
            SeverityRow(
                label="Informational (no material change)",
                emoji=SEVERITY_EMOJI.get(SeverityLevel.INFO.value, ""),
                level_upper=SeverityLevel.INFO.value.upper(),
                count=info_count,
                impact="no exit impact",
            )
        )

    return SeveritySummary(rows=tuple(rows))


def _severity_counts_for(
    result: Any,
    changes: list[Any],
    envelope: ReportEnvelope | None,
) -> tuple[dict[str, int], dict[str, int]]:
    """Per-category totals for the severity table: ``(displayed, exit)``.

    *displayed* counts *changes* (the ``--show-only``-filtered set the ``Count``
    column reports); *exit* counts the set the gate scores (ADR-049 D1), which
    drives ``Exit Impact``. Both read each change's already-resolved
    :class:`~abicheck.report.finding.ReportFinding` category, so the table
    never classifies a change itself. A direct caller with no envelope resolves
    the findings here through the same primitive the envelope uses.
    """
    from ..policy.severity import gate_eligible_changes

    def findings_for(cs: Sequence[Any]) -> Sequence[ReportFinding]:
        if envelope is not None:
            return envelope.findings_for(list(cs))
        return build_report_findings(
            list(cs),
            policy=result.policy,
            kind_sets=kind_sets_for(result),
            policy_file=result.policy_file,
        )

    def tally(findings: Sequence[ReportFinding]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in findings:
            key = finding.category.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    return (
        tally(findings_for(changes)),
        tally(findings_for(gate_eligible_changes(list(result.changes)))),
    )


def severity_summary_mapping(
    result: Any,
    changes: list[Any],
    severity_config: Any,
    envelope: ReportEnvelope | None,
    *,
    scoped_gate: bool,
) -> dict[str, Any] | None:
    """The severity table as a JSON-safe mapping, or ``None`` with no severity config.

    *scoped_gate* is the root-cause view's opt-in to the ``--used-by`` /
    ``--required-symbol`` gate numbers; the full view never reads them.
    """
    if severity_config is None:
        return None
    displayed, exit_counts = _severity_counts_for(result, changes, envelope)
    return asdict(
        compute_severity_summary(
            severity_config,
            counts=displayed,
            exit_counts=exit_counts,
            scoped_counts=(
                getattr(result, "scoped_severity_counts", None) if scoped_gate else None
            ),
            scoped_blocking_categories=(
                getattr(result, "scoped_blocking_categories", None)
                if scoped_gate
                else None
            ),
        )
    )
