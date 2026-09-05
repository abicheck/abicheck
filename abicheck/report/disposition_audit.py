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

"""ADR-067 D3's raw-versus-effective audit, projected into every report view.

The *facts* live in :mod:`abicheck.policy.disposition_ledger` — one conserved
record per atomically detected change, with the suppression rule's provenance
captured at the application point that fired. This module is the report half:
a ``compute_*`` function returning a frozen struct of already-resolved plain
values (:func:`compute_disposition_audit`) and ``render_*``/``add_*``
functions that only format or serialize it, per this package's own
compute/render split (``abicheck/report/AGENTS.md``).

Workstream G's report invariant is why every projection gets this and not
just the JSON one: *"every view — compact, review digest, one-line, PR comment
included — carries the detected total, the effective (gating) total, and the
per-disposition counts with rule provenance. Collapsing detail is fine;
dropping these counts is not."* The renderers here therefore differ only in
how much rule detail they show, never in whether the counts appear.

The JSON suppression ledger itself stays in ``reporter.py``
(``_add_suppression``/``_suppressed_change_entry``): it needs that module's
root-cause lookups and the ``impact`` engine, neither of which this layer may
import (``architecture/modules.yaml``). What it gained from ADR-067 is the
rule that hid each finding — id, source file, reason, expiry — read off the
same ledger this module projects, which the run computed and then dropped
before this slice.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..policy.disposition_ledger import Disposition, RuleProvenance, ledger_for

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..checker_types import DiffResult


#: Detector names listed inline before the "Not evaluated" line collapses. A run
#: without ELF/DWARF/PE/Mach-O/SYCL evidence legitimately leaves a dozen
#: detectors unevaluated; the *count* is the fact D3 requires, the per-row
#: detail is convenience.
_NOT_EVALUATED_ROW_CAP = 6

#: Rules listed individually in a sticky PR comment before the list collapses.
_COMMENT_RULE_CAP = 3


@dataclass(frozen=True)
class NotEvaluatedDetector:
    """A detector that did not run, and the support gate's reason."""

    name: str
    reason: str | None

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "reason": self.reason}


@dataclass(frozen=True)
class DispositionAudit:
    """Frozen, plain-value summary — the one struct every renderer formats."""

    detected_total: int
    effective_total: int
    #: ``(disposition value, count)`` pairs, every disposition present, in
    #: :class:`~abicheck.policy.disposition_ledger.Disposition` declaration
    #: order so two reports of the same run read identically.
    counts: tuple[tuple[str, int], ...]
    #: ``(rule provenance, number of findings it disposed of)``.
    rules: tuple[tuple[RuleProvenance, int], ...]
    #: ADR-067 D3's other half: capability that was never exercised reads as
    #: *not evaluated*, never as a finding count of zero.
    not_evaluated_detectors: tuple[NotEvaluatedDetector, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "detected_total": self.detected_total,
            "effective_total": self.effective_total,
            "counts": dict(self.counts),
            "rules": [
                {**rule.to_dict(), "matched_count": count} for rule, count in self.rules
            ],
            "not_evaluated_detectors": [
                det.to_dict() for det in self.not_evaluated_detectors
            ],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> DispositionAudit:
        """Rebuild an audit from :meth:`to_dict`'s own output.

        One wire shape, round-tripped -- not a second, lossy projection: the
        Markdown views reach their renderer through a ``ReportDocument``
        (JSON values only), so a struct that could not be reconstructed from
        its own mapping would have to be recomputed inside the renderer,
        which is exactly the compute/render split this package forbids.
        """
        counts = d.get("counts") or {}
        return cls(
            detected_total=int(d["detected_total"]),
            effective_total=int(d["effective_total"]),
            counts=tuple((str(k), int(v)) for k, v in counts.items()),
            rules=tuple(
                (
                    RuleProvenance(
                        **{k: v for k, v in row.items() if k != "matched_count"}
                    ),
                    int(row.get("matched_count", 0)),
                )
                for row in d.get("rules") or ()
            ),
            not_evaluated_detectors=tuple(
                NotEvaluatedDetector(name=row["name"], reason=row.get("reason"))
                for row in d.get("not_evaluated_detectors") or ()
            ),
        )


def compute_disposition_audit(
    result: DiffResult, severity_config: object | None = None
) -> DispositionAudit:
    """Resolve *result*'s audit facts. Decides nothing; reads the ledger.

    *severity_config*, when the caller has one, is what makes ``gating`` mean
    the gate this run was actually scored on rather than the raw verdict
    class — ``checker.compare()`` never sees the resolved severity
    configuration (ADR-064 resolves it in the front end, strictly later). It
    is applied to the run's one shared ledger, so passing it in one
    projection and not another cannot make two views disagree.
    """
    ledger = ledger_for(result, severity_config)
    counts = ledger.counts()
    return DispositionAudit(
        detected_total=ledger.detected_total,
        effective_total=ledger.effective_total,
        counts=tuple((d.value, counts[d.value]) for d in Disposition),
        rules=ledger.rules(),
        not_evaluated_detectors=tuple(
            NotEvaluatedDetector(name=det.name, reason=det.coverage_gap)
            # ``getattr`` for the same reason the ledger reads its inputs
            # that way: a report path may hand this a duck-typed stand-in.
            for det in getattr(result, "detector_results", None) or ()
            if getattr(det, "not_evaluated", False)
        ),
    )


def add_disposition_audit(
    d: dict[str, object], result: DiffResult, severity_config: object | None = None
) -> None:
    """Attach the ``disposition_audit`` block to a JSON report (schema 2.50).

    Unconditional and unsuppressible by construction: it is derived from the
    conserved ledger rather than from the post-disposition ``changes`` list,
    so a rule cannot remove its own audit record (the same structural pattern
    ADR-049 Phase 5's coverage ledger uses).
    """
    d["disposition_audit"] = compute_disposition_audit(
        result, severity_config
    ).to_dict()


def render_disposition_audit_note(audit: DispositionAudit) -> str:
    """The compact one-line/`--stat` form: counts only, never dropped.

    Empty string only when nothing was detected at all — there is then no
    raw-versus-effective distinction to state. Carries the counts and nothing
    else: the not-evaluated detector list is detail this view collapses (it
    stays in full in the JSON and Markdown projections), which D3 permits;
    dropping the *counts* is what it forbids.
    """
    if audit.detected_total == 0:
        return ""
    parts = [f"{audit.detected_total} detected", f"{audit.effective_total} gating"]
    parts += [
        f"{count} {name}"
        for name, count in audit.counts
        if count and name != Disposition.GATING.value
    ]
    return " [audit: " + ", ".join(parts) + "]"


def render_disposition_audit_lines(audit: DispositionAudit) -> list[str]:
    """The Markdown form (review digest / PR comment): the same counts as a
    table, plus the rules that produced the non-gating dispositions."""
    lines = [
        "| Disposition | Count |",
        "|---|---|",
        f"| Detected (raw) | {audit.detected_total} |",
        f"| Effective (gating) | {audit.effective_total} |",
    ]
    lines += [
        f"| … {name.replace('_', ' ')} | {count} |"
        for name, count in audit.counts
        if name != Disposition.GATING.value
    ]
    lines.append("")
    if audit.rules:
        lines.append("**Rules applied:**")
        lines.append("")
        for rule, count in audit.rules:
            detail = rule.reason or rule.label or "no reason given"
            source = f" (`{rule.source_file}`)" if rule.source_file else ""
            expiry = f", expires {rule.expires}" if rule.expires else ""
            lines.append(
                f"- `{rule.rule_id or 'rule'}`{source} — {detail} "
                f"[intent: {rule.intent}{expiry}] — {count} finding(s)"
            )
        lines.append("")
    if audit.not_evaluated_detectors:
        # Collapsed to one line on purpose: D3 requires the *state* and its
        # count in every view, and each detector's own reason is already the
        # ``detectors`` block's ``coverage_gap`` -- repeating a dozen of them
        # inside a digest would bury the counts this section exists for.
        names = ", ".join(
            f"`{det.name}`"
            for det in audit.not_evaluated_detectors[:_NOT_EVALUATED_ROW_CAP]
        )
        remaining = len(audit.not_evaluated_detectors) - _NOT_EVALUATED_ROW_CAP
        more = f", … and {remaining} more" if remaining > 0 else ""
        lines.append(
            f"**Not evaluated:** {len(audit.not_evaluated_detectors)} detector(s) "
            f"— {names}{more}"
        )
        lines.append("")
    return lines


def render_disposition_audit_comment_lines(audit: DispositionAudit) -> list[str]:
    """The sticky PR comment's form: one blockquote line of counts, plus the
    rules that account for the difference.

    ADR-067 D3 promotes this from ``pr_comment_render``'s trailing suppression
    blockquote — which stated only *how many* findings a rule withheld — to a
    row that also names the raw total, the gating total, and which rule (with
    its reason) did the withholding.
    """
    if audit.detected_total == 0:
        return []
    counts = ", ".join(
        f"{count} {name.replace('_', ' ')}"
        for name, count in audit.counts
        if count and name != Disposition.GATING.value
    )
    tail = f" · {counts}" if counts else ""
    lines = [
        f"> 📊 **Audit:** {audit.detected_total} detected · "
        f"{audit.effective_total} gating{tail}",
    ]
    for rule, count in audit.rules[:_COMMENT_RULE_CAP]:
        detail = rule.reason or rule.label or "no reason given"
        source = f" (`{rule.source_file}`)" if rule.source_file else ""
        lines.append(
            f"> · `{rule.rule_id or 'rule'}`{source} — {detail} "
            f"[intent: {rule.intent}] — {count} finding(s)"
        )
    remaining = len(audit.rules) - _COMMENT_RULE_CAP
    if remaining > 0:
        lines.append(f"> · … and {remaining} more rule(s)")
    lines.append("")
    return lines
