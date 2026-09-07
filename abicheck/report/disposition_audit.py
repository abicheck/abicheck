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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from ..policy.disposition_close import (
    acknowledged_total as _acknowledged_total,
    acknowledgments as _acknowledgments,
    ledger_for,
    reclassifications as _reclassifications,
    reclassified_total as _reclassified_total,
    scope_reasons as _scope_reasons,
)
from ..policy.disposition_ledger import (
    Disposition,
    RuleProvenance,
)
from .document import ReportDocument

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
    #: Policy-generated diagnostics (a withheld-suppression advisory), which
    #: are in :attr:`effective_total` but in neither :attr:`detected_total`
    #: nor :attr:`counts` -- they are findings the gate can score, not
    #: observations. Carried through every projection because without it a
    #: consumer reconciling the three sees an effective finding that appears
    #: in no raw total and no disposition count, and cannot tell whether that
    #: is an overlay or a bug (Codex review).
    policy_overlays: int = 0
    #: ADR-067 C-S2: findings a ``reclassify:`` rule moved to another verdict
    #: class -- an overlay fact independent of `counts`, like
    #: :attr:`policy_overlays` (a reclassified finding keeps its own terminal
    #: disposition).
    reclassified_total: int = 0
    #: ``(reclassify rule id, matched count)`` -- :attr:`rules`'s counterpart
    #: for reclassification.
    reclassifications: tuple[tuple[str, int], ...] = ()
    #: ``(contract-relevance reason code, matched count)`` behind every
    #: ``out_of_contract``/``unresolved_relevance`` record -- :attr:`rules`'s
    #: counterpart for a scope/contract exclusion.
    scope_reasons: tuple[tuple[str, int], ...] = ()
    #: ADR-067 D5/C-S3: findings an :class:`~abicheck.policy.acknowledgment.
    #: Acknowledgment` record matched -- an overlay fact independent of
    #: `counts`, like :attr:`reclassified_total` (an acknowledged finding
    #: keeps its own terminal disposition).
    acknowledged_total: int = 0
    #: ``(acknowledgment record id, matched count)`` -- :attr:`rules`'s
    #: counterpart for acknowledgment.
    acknowledgments: tuple[tuple[str, int], ...] = ()
    #: ADR-067 D6/C-S3: the additions-review gate's own evaluated result
    #: (``AdditionsReviewResult.to_dict()``), or ``None`` when this run never
    #: supplied an acknowledgment document to evaluate against -- the
    #: "capability that was never exercised" convention this module already
    #: applies to `not_evaluated_detectors`.
    unacknowledged_additions_review: dict[str, object] | None = None

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
            "policy_overlays": self.policy_overlays,
            "reclassified_total": self.reclassified_total,
            "reclassifications": [
                {"rule_id": rule_id, "matched_count": count}
                for rule_id, count in self.reclassifications
            ],
            "scope_reasons": [
                {"reason_code": reason, "matched_count": count}
                for reason, count in self.scope_reasons
            ],
            "acknowledged_total": self.acknowledged_total,
            "acknowledgments": [
                {"record_id": record_id, "matched_count": count}
                for record_id, count in self.acknowledgments
            ],
            "unacknowledged_additions_review": self.unacknowledged_additions_review,
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
            policy_overlays=int(d.get("policy_overlays") or 0),
            reclassified_total=int(d.get("reclassified_total") or 0),
            reclassifications=tuple(
                (str(row["rule_id"]), int(row.get("matched_count", 0)))
                for row in d.get("reclassifications") or ()
            ),
            scope_reasons=tuple(
                (str(row["reason_code"]), int(row.get("matched_count", 0)))
                for row in d.get("scope_reasons") or ()
            ),
            acknowledged_total=int(d.get("acknowledged_total") or 0),
            acknowledgments=tuple(
                (str(row["record_id"]), int(row.get("matched_count", 0)))
                for row in d.get("acknowledgments") or ()
            ),
            unacknowledged_additions_review=cast(
                "dict[str, object] | None", d.get("unacknowledged_additions_review")
            ),
        )


def _additions_review_dict(result: DiffResult) -> dict[str, object] | None:
    """*result*'s own persisted additions-review, read duck-typed.

    ``None`` for every run that never supplied ``acknowledgments=...`` to
    ``checker.compare()`` -- the same "capability never exercised" convention
    the ``not_evaluated_detectors`` field already applies.
    """
    review = getattr(result, "unacknowledged_additions_review", None)
    to_dict = getattr(review, "to_dict", None)
    return to_dict() if callable(to_dict) else None


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
        policy_overlays=ledger.policy_overlay_total,
        reclassified_total=_reclassified_total(ledger),
        reclassifications=_reclassifications(ledger),
        scope_reasons=_scope_reasons(ledger),
        acknowledged_total=_acknowledged_total(ledger),
        acknowledgments=_acknowledgments(ledger),
        unacknowledged_additions_review=_additions_review_dict(result),
    )


def fold_disposition_audits(audits: Iterable[DispositionAudit]) -> DispositionAudit:
    """ADR-067 C-S2: fold N independent per-comparison audits into one total.

    The release/bundle fan-out and ``aggregate`` each run several independent
    comparisons (one per library, one per matrix target) and need one
    summary-level raw-versus-effective statement rather than N per-member
    blocks with no rollup -- the same D3 invariant scalar `compare` already
    states, one level up.

    Every field here is a plain count (or a count keyed by rule/reason/
    detector), never a state, so summing is the correct fold at any level:
    the sum of N conserved per-comparison totals is itself a conserved total
    (``sum(folded.counts) == folded.detected_total`` holds whenever it held
    for every member). Rule/reclassification/scope-reason tallies are merged
    by key (rule id / reason code), in first-appearance order across the
    members in the order given, with counts summed across whichever members
    matched; ``not_evaluated_detectors`` is deduplicated by name for the same
    reason a homogeneous release's members legitimately share one missing-
    evidence detector and should not report it N times.

    ``()`` in gives back the zero audit (every count ``0``, every sequence
    empty) rather than raising -- a release/aggregate run with zero members
    still owes every consumer an audit block it can render unconditionally,
    the same "state zero, don't omit" rule :func:`render_disposition_audit_note`
    already applies at the single-comparison level.
    """
    detected_total = 0
    effective_total = 0
    policy_overlays = 0
    reclassified_total = 0
    acknowledged_total = 0
    counts: dict[str, int] = {d.value: 0 for d in Disposition}
    rule_order: list[RuleProvenance] = []
    rule_tally: dict[RuleProvenance, int] = {}
    reclass_order: list[str] = []
    reclass_tally: dict[str, int] = {}
    reason_order: list[str] = []
    reason_tally: dict[str, int] = {}
    ack_order: list[str] = []
    ack_tally: dict[str, int] = {}
    detector_order: list[str] = []
    detectors: dict[str, NotEvaluatedDetector] = {}
    unacknowledged_gate_contribution = 0
    any_additions_review = False
    unacknowledged_entries: list[dict[str, object]] = []
    additions_policies: set[str] = set()
    for audit in audits:
        detected_total += audit.detected_total
        effective_total += audit.effective_total
        policy_overlays += audit.policy_overlays
        reclassified_total += audit.reclassified_total
        acknowledged_total += audit.acknowledged_total
        for name, count in audit.counts:
            counts[name] = counts.get(name, 0) + count
        for rule, count in audit.rules:
            if rule not in rule_tally:
                rule_order.append(rule)
                rule_tally[rule] = 0
            rule_tally[rule] += count
        for rule_id, count in audit.reclassifications:
            if rule_id not in reclass_tally:
                reclass_order.append(rule_id)
                reclass_tally[rule_id] = 0
            reclass_tally[rule_id] += count
        for reason, count in audit.scope_reasons:
            if reason not in reason_tally:
                reason_order.append(reason)
                reason_tally[reason] = 0
            reason_tally[reason] += count
        for record_id, count in audit.acknowledgments:
            if record_id not in ack_tally:
                ack_order.append(record_id)
                ack_tally[record_id] = 0
            ack_tally[record_id] += count
        if audit.unacknowledged_additions_review is not None:
            any_additions_review = True
            review = audit.unacknowledged_additions_review
            unacknowledged_gate_contribution = max(
                unacknowledged_gate_contribution,
                int(cast("int", review.get("gate_contribution") or 0)),
            )
            additions_policies.add(str(review.get("policy") or "allow"))
            unacknowledged_entries.extend(
                cast("list[dict[str, object]]", review.get("unacknowledged") or [])
            )
        for det in audit.not_evaluated_detectors:
            if det.name not in detectors:
                detector_order.append(det.name)
                detectors[det.name] = det
    return DispositionAudit(
        detected_total=detected_total,
        effective_total=effective_total,
        counts=tuple((d.value, counts[d.value]) for d in Disposition),
        rules=tuple((rule, rule_tally[rule]) for rule in rule_order),
        not_evaluated_detectors=tuple(detectors[name] for name in detector_order),
        policy_overlays=policy_overlays,
        reclassified_total=reclassified_total,
        reclassifications=tuple(
            (rule_id, reclass_tally[rule_id]) for rule_id in reclass_order
        ),
        scope_reasons=tuple((reason, reason_tally[reason]) for reason in reason_order),
        acknowledged_total=acknowledged_total,
        acknowledgments=tuple((rid, ack_tally[rid]) for rid in ack_order),
        # A folded, orthogonal ``0``/``1`` `gate_contribution` -- matching how
        # every other axis here (counts, totals) is a plain sum-or-max fold
        # of a conserved per-member fact. `unacknowledged` concatenates every
        # member's own list rather than dropping it (Codex review: an
        # aggregate report must still be able to name which additions were
        # unacknowledged, not just how many). `policy` is the single value
        # when every member agreed, or ``"mixed"`` when they configured
        # different allow/warn/block settings -- never silently defaulted to
        # ``"allow"`` for a fold that never asked the question. `None` (never
        # a synthetic zero dict) when no member carried a review at all, so
        # "no member ever evaluated this" stays distinguishable from "every
        # member evaluated it and found nothing".
        unacknowledged_additions_review=(
            {
                "policy": (
                    next(iter(additions_policies))
                    if len(additions_policies) == 1
                    else "mixed"
                ),
                "unacknowledged": unacknowledged_entries,
                "gate_contribution": unacknowledged_gate_contribution,
            }
            if any_additions_review
            else None
        ),
    )


def add_disposition_audit(
    d: dict[str, object], result: DiffResult, severity_config: object | None = None
) -> None:
    """Attach the ``disposition_audit`` block to a JSON report (schema 2.51).

    Unconditional and unsuppressible by construction: it is derived from the
    conserved ledger rather than from the post-disposition ``changes`` list,
    so a rule cannot remove its own audit record (the same structural pattern
    ADR-049 Phase 5's coverage ledger uses).
    """
    d["disposition_audit"] = compute_disposition_audit(
        result, severity_config
    ).to_dict()


def disposition_audit_dict_reusing_document(
    result: DiffResult,
    severity_config: object | None,
    report_document: ReportDocument | None,
) -> dict[str, object]:
    """A format's ``disposition_audit`` dict, reused verbatim from a shared
    ``ReportDocument`` when given (ADR-061 Phase 2 gap C) instead of a
    second independent ``compute_disposition_audit`` call -- same ledger,
    same inputs, so the two can never disagree.
    """
    if report_document is None:
        return compute_disposition_audit(result, severity_config).to_dict()
    return cast("dict[str, object]", report_document.to_mapping()["disposition_audit"])


def render_disposition_audit_note(audit: DispositionAudit) -> str:
    """The compact one-line/`--stat` form: counts only, never dropped.

    Empty only when there is genuinely nothing to state: no change was
    detected *and* every detector ran. Carries the counts plus the
    not-evaluated detector *count* — the per-detector list is detail this view
    collapses (it stays in full in the JSON and Markdown projections), which
    D3 permits; dropping the count is what it forbids, and this is the one
    view where a zero-change run would otherwise read "no changes (0 total)"
    with the missing-evidence signal nowhere at all.
    """
    # The two totals are unconditional. An earlier version gated them behind
    # `detected_total or not not_evaluated_detectors`, which read correctly in
    # every state but one -- nothing detected *and* a detector refused -- where
    # it dropped both zeros and left the detector warning standing alone with
    # no raw-versus-effective statement at all. D3's rule is that a view may
    # collapse *detail*, never the counts, and "0 detected, 0 gating" is a
    # statement, not an absence (Codex review).
    parts: list[str] = [
        f"{audit.detected_total} detected",
        f"{audit.effective_total} gating",
    ]
    parts += [
        f"{count} {name}"
        for name, count in audit.counts
        if count and name != Disposition.GATING.value
    ]
    if audit.policy_overlays:
        parts.append(f"{audit.policy_overlays} policy overlay(s)")
    if audit.reclassified_total:
        parts.append(f"{audit.reclassified_total} reclassified")
    if audit.acknowledged_total:
        parts.append(f"{audit.acknowledged_total} acknowledged")
    if audit.unacknowledged_additions_review:
        unacked = cast(
            "list[object]",
            audit.unacknowledged_additions_review.get("unacknowledged") or [],
        )
        if unacked:
            parts.append(f"{len(unacked)} unacknowledged addition(s)")
    if audit.not_evaluated_detectors:
        parts.append(f"{len(audit.not_evaluated_detectors)} detector(s) not evaluated")
    if (
        audit.detected_total == 0
        and not audit.not_evaluated_detectors
        and not audit.policy_overlays
        and not audit.reclassified_total
        and not audit.acknowledged_total
    ):
        # Nothing detected and every detector ran: the counts are true but say
        # nothing the line beside them ("no changes (0 total)") does not
        # already say, so this one view stays silent rather than repeating it.
        return ""
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
    if audit.policy_overlays:
        lines.append(
            f"**Policy overlays:** {audit.policy_overlays} "
            "(diagnostics the gate can score; not counted as detections)"
        )
        lines.append("")
    if audit.reclassifications:
        lines.append(
            f"**Reclassified:** {audit.reclassified_total} "
            "(verdict moved by a `reclassify:` rule; disposition unchanged)"
        )
        lines.append("")
        for rule_id, count in audit.reclassifications:
            lines.append(f"- `{rule_id}` — {count} finding(s)")
        lines.append("")
    if audit.scope_reasons:
        lines.append("**Out-of-contract/scope reasons:**")
        lines.append("")
        for reason, count in audit.scope_reasons:
            lines.append(f"- `{reason}` — {count} finding(s)")
        lines.append("")
    if audit.acknowledgments:
        lines.append(
            f"**Acknowledged:** {audit.acknowledged_total} "
            "(keeps its verdict class and gate contribution; ADR-067 D5)"
        )
        lines.append("")
        for record_id, count in audit.acknowledgments:
            lines.append(f"- `{record_id}` — {count} finding(s)")
        lines.append("")
    if audit.unacknowledged_additions_review:
        review = audit.unacknowledged_additions_review
        policy = review.get("policy", "allow")
        unacked = cast("list[dict[str, object]]", review.get("unacknowledged") or [])
        if unacked:
            lines.append(
                f"**Unacknowledged public additions ({policy}):** {len(unacked)} "
                "(ADR-067 D6 — an orthogonal review gate; never reclassifies "
                "the addition)"
            )
            lines.append("")
            for entry in unacked:
                symbol = entry.get("symbol") or "?"
                lines.append(f"- `{entry.get('kind')}`: `{symbol}`")
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


def render_disposition_audit_section(audit: DispositionAudit | None) -> list[str]:
    """The Markdown *section* every report mode appends (D3).

    A heading plus :func:`render_disposition_audit_lines`, so the full,
    leaf and root-cause views carry the identical block the review digest
    does rather than three near-copies. ``None`` renders nothing, which is
    what a document produced before this block existed round-trips to.
    """
    if audit is None:
        return []
    # Leading blank line: this section is appended after whatever the mode
    # rendered last, which is not guaranteed to end in one.
    return ["", "## Disposition audit", "", *render_disposition_audit_lines(audit)]


def render_disposition_audit_comment_lines(audit: DispositionAudit) -> list[str]:
    """The sticky PR comment's form: one blockquote line of counts, plus the
    rules that account for the difference.

    ADR-067 D3 promotes this from ``pr_comment_render``'s trailing suppression
    blockquote — which stated only *how many* findings a rule withheld — to a
    row that also names the raw total, the gating total, and which rule (with
    its reason) did the withholding.
    """
    if audit.detected_total == 0 and not audit.not_evaluated_detectors:
        return []
    counts = ", ".join(
        f"{count} {name.replace('_', ' ')}"
        for name, count in audit.counts
        if count and name != Disposition.GATING.value
    )
    tail = f" · {counts}" if counts else ""
    if audit.policy_overlays:
        tail += f" · {audit.policy_overlays} policy overlay(s)"
    if audit.reclassified_total:
        tail += f" · {audit.reclassified_total} reclassified"
    if audit.not_evaluated_detectors:
        # Same reason as the one-line view: on a zero-delta comparison this
        # row is the only place the reader learns a detector could not run,
        # and "No ABI changes" is exactly the sentence that needs the caveat.
        tail += f" · {len(audit.not_evaluated_detectors)} detector(s) not evaluated"
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
