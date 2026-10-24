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

The *facts* live in :mod:`abicheck.policy.disposition_ledger` -- one conserved
record per atomically detected change, with the rule that touched it captured
at the application point that fired -- and the per-rule rows are computed by
:mod:`abicheck.policy.disposition_rules` from the **configured** rule set
(P1: every configured rule has exactly one row, zero-count rows included).
This module is the report half: a ``compute_*`` function returning a frozen
struct of already-resolved plain values (:func:`compute_disposition_audit`)
and ``render_*``/``add_*`` functions that only format or serialize it, per
this package's own compute/render split (``abicheck/report/AGENTS.md``).

Workstream G's report invariant is why every projection gets this and not
just the JSON one: *"every view -- compact, review digest, one-line, PR comment
included -- carries the detected total, the effective (gating) total, and the
per-disposition counts with rule provenance. Collapsing detail is fine;
dropping these counts is not."* The renderers here therefore differ only in
how much rule detail they show, never in whether the counts appear. Human
views collapse zero-count rows into one line; the JSON keeps every row.

The JSON suppression ledger itself stays in ``reporter.py``
(``add_suppression``/``suppressed_change_entry``): it needs that module's
root-cause lookups and the ``impact`` engine, neither of which this layer may
import (``architecture/modules.yaml``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, cast

from ..policy.disposition_close import (
    accepted_breaks as _accepted_breaks,
    acknowledged_total as _acknowledged_total,
    ledger_for,
    reclassified_total as _reclassified_total,
    scope_reasons as _scope_reasons,
)
from ..policy.disposition_ledger import Disposition
from ..policy.disposition_rules import configured_rule_rows
from ..policy.rule_inventory import (
    KIND_ACKNOWLEDGMENT,
    KIND_RECLASSIFY,
    KIND_SUPPRESSION,
    STATUS_ACTIVE,
    STATUS_EXPIRED,
    STATUS_NOT_EVALUATED,
    STATUS_SHADOWED_ONLY,
    STATUS_UNUSED,
    RuleAuditRow,
    RuleRef,
)
from ..policy.unused_rules_gate import (
    UnusedRulesReview,
    render_not_evaluated_rules,
    resolve_unused_rules_mode,
    strictest_mode,
    unused_rules_review,
)
from .disposition_rule_rows import (
    acknowledgment_row_json,
    reclassify_row_json,
    row_from_json,
    suppression_row_json,
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
_REVIEW_RULE_CAP = 4


@dataclass(frozen=True)
class NotEvaluatedDetector:
    """A detector that did not run, and the support gate's reason."""

    name: str
    reason: str | None

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "reason": self.reason}


@dataclass(frozen=True)
class DispositionAudit:
    """Frozen, plain-value summary -- the one struct every renderer formats."""

    detected_total: int
    effective_total: int
    #: ``(disposition value, count)`` pairs, every disposition present, in
    #: :class:`~abicheck.policy.disposition_ledger.Disposition` declaration
    #: order so two reports of the same run read identically.
    counts: tuple[tuple[str, int], ...]
    #: ADR-067 D3: one row per configured *suppression* rule -- zero-count rules
    #: included (P1). ``matched_count`` is findings the rule disposed of.
    rules: tuple[RuleAuditRow, ...]
    #: ADR-067 D3's other half: capability that was never exercised reads as
    #: *not evaluated*, never as a finding count of zero.
    not_evaluated_detectors: tuple[NotEvaluatedDetector, ...]
    #: Policy-generated diagnostics (a withheld-suppression advisory), which
    #: are in :attr:`effective_total` but in neither :attr:`detected_total`
    #: nor :attr:`counts` -- they are findings the gate can score, not
    #: observations.
    policy_overlays: int = 0
    #: ADR-067 C-S2: findings a ``reclassify:`` rule moved to another verdict
    #: class -- an overlay fact independent of `counts`.
    reclassified_total: int = 0
    #: One row per configured ``reclassify:`` rule (P1). ``matched_count`` and
    #: ``changed_count`` are the findings whose verdict the rule moved;
    #: ``selector_matched_count`` also counts the findings it matched without
    #: moving them (a no-op, or shadowed by an earlier rule).
    reclassifications: tuple[RuleAuditRow, ...] = ()
    #: ``(contract-relevance reason code, matched count)`` behind every
    #: ``out_of_contract``/``unresolved_relevance`` record.
    scope_reasons: tuple[tuple[str, int], ...] = ()
    #: ADR-067 D5/C-S3: findings an acknowledgment record covered.
    acknowledged_total: int = 0
    #: One row per configured acknowledgment record (P1), zero-count included.
    acknowledgments: tuple[RuleAuditRow, ...] = ()
    #: ADR-067 D6/C-S3: the additions-review gate's own evaluated result, or
    #: ``None`` when this run never supplied an acknowledgment document.
    unacknowledged_additions_review: dict[str, object] | None = None
    #: P1: the ``unused_rules`` gate, evaluated over the rows above. Always
    #: present; its ``gate_contribution`` is ``0`` unless the mode is ``block``.
    unused_rules_review: dict[str, object] | None = None
    #: ADR-067 D5: ``{kind, symbol, verdict_class}`` per break ``breaks: accept`` made non-gating.
    accepted_breaks: tuple[dict[str, object], ...] = ()

    @property
    def configured_rules(self) -> tuple[RuleAuditRow, ...]:
        """Every configured rule across the three kinds, in JSON order."""
        return (*self.rules, *self.reclassifications, *self.acknowledgments)

    def to_dict(self) -> dict[str, object]:
        return {
            "detected_total": self.detected_total,
            "effective_total": self.effective_total,
            "counts": dict(self.counts),
            "rules": [suppression_row_json(row) for row in self.rules],
            "not_evaluated_detectors": [
                det.to_dict() for det in self.not_evaluated_detectors
            ],
            "policy_overlays": self.policy_overlays,
            "reclassified_total": self.reclassified_total,
            "reclassifications": [
                reclassify_row_json(row) for row in self.reclassifications
            ],
            "scope_reasons": [
                {"reason_code": reason, "matched_count": count}
                for reason, count in self.scope_reasons
            ],
            "acknowledged_total": self.acknowledged_total,
            "acknowledgments": [
                acknowledgment_row_json(row) for row in self.acknowledgments
            ],
            "unacknowledged_additions_review": self.unacknowledged_additions_review,
            "accepted_breaks": [dict(row) for row in self.accepted_breaks],
            "unused_rules_review": self.unused_rules_review,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> DispositionAudit:
        """Rebuild an audit from :meth:`to_dict`'s own output.

        One wire shape, round-tripped -- not a second, lossy projection: the
        Markdown views reach their renderer through a ``ReportDocument``
        (JSON values only), so a struct that could not be reconstructed from
        its own mapping would have to be recomputed inside the renderer.
        """
        counts = d.get("counts") or {}
        return cls(
            detected_total=int(d["detected_total"]),
            effective_total=int(d["effective_total"]),
            counts=tuple((str(k), int(v)) for k, v in counts.items()),
            rules=tuple(
                row_from_json(KIND_SUPPRESSION, row) for row in d.get("rules") or ()
            ),
            not_evaluated_detectors=tuple(
                NotEvaluatedDetector(name=row["name"], reason=row.get("reason"))
                for row in d.get("not_evaluated_detectors") or ()
            ),
            policy_overlays=int(d.get("policy_overlays") or 0),
            reclassified_total=int(d.get("reclassified_total") or 0),
            reclassifications=tuple(
                row_from_json(KIND_RECLASSIFY, row)
                for row in d.get("reclassifications") or ()
            ),
            scope_reasons=tuple(
                (str(row["reason_code"]), int(row.get("matched_count", 0)))
                for row in d.get("scope_reasons") or ()
            ),
            acknowledged_total=int(d.get("acknowledged_total") or 0),
            acknowledgments=tuple(
                row_from_json(KIND_ACKNOWLEDGMENT, row)
                for row in d.get("acknowledgments") or ()
            ),
            unacknowledged_additions_review=cast(
                "dict[str, object] | None", d.get("unacknowledged_additions_review")
            ),
            unused_rules_review=cast(
                "dict[str, object] | None", d.get("unused_rules_review")
            ),
            accepted_breaks=tuple(dict(r) for r in d.get("accepted_breaks") or ()),
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
    result: DiffResult,
    severity_config: object | None = None,
    *,
    today: date | None = None,
) -> DispositionAudit:
    """Resolve *result*'s audit facts. Decides nothing; reads the ledger.

    *severity_config*, when the caller has one, is what makes ``gating`` mean
    the gate this run was actually scored on rather than the raw verdict
    class -- ``checker.compare()`` never sees the resolved severity
    configuration (ADR-064 resolves it in the front end, strictly later). It
    is applied to the run's one shared ledger, so passing it in one
    projection and not another cannot make two views disagree. *today*,
    forwarded to :func:`~abicheck.policy.disposition_close.ledger_for`, keeps
    this agreeing with an already-frozen ``ReportEnvelope`` (Codex review,
    fresh evidence).
    """
    ledger = ledger_for(result, severity_config, today=today)
    counts = ledger.counts()
    rows = configured_rule_rows(result, ledger=ledger, today=today)
    review = unused_rules_review(rows, mode=resolve_unused_rules_mode(result))
    return DispositionAudit(
        detected_total=ledger.detected_total,
        effective_total=ledger.effective_total,
        counts=tuple((d.value, counts[d.value]) for d in Disposition),
        rules=_of_kind(rows, KIND_SUPPRESSION),
        not_evaluated_detectors=tuple(
            NotEvaluatedDetector(name=det.name, reason=det.coverage_gap)
            # ``getattr`` for the same reason the ledger reads its inputs
            # that way: a report path may hand this a duck-typed stand-in.
            for det in getattr(result, "detector_results", None) or ()
            if getattr(det, "not_evaluated", False)
        ),
        policy_overlays=ledger.policy_overlay_total,
        reclassified_total=_reclassified_total(ledger),
        reclassifications=_of_kind(rows, KIND_RECLASSIFY),
        scope_reasons=_scope_reasons(ledger),
        acknowledged_total=_acknowledged_total(ledger),
        acknowledgments=_of_kind(rows, KIND_ACKNOWLEDGMENT),
        unacknowledged_additions_review=_additions_review_dict(result),
        unused_rules_review=review.to_dict(),
        accepted_breaks=_accepted_breaks(ledger),
    )


def _of_kind(rows: Iterable[RuleAuditRow], kind: str) -> tuple[RuleAuditRow, ...]:
    return tuple(row for row in rows if row.kind == kind)


def fold_rule_status(statuses: Iterable[str]) -> str:
    """One status for a rule that several members of a release each report.

    Strongest evidence first: a rule that disposed of anything in *any* member
    is ``active``; one that only shadowed findings is ``shadowed_only``. A
    member whose status is ``not_evaluated`` could not evaluate the rule, so it
    is ignored here. The rule is ``unused`` when every member that could
    evaluate it left it unused (the release gate's "unused in every comparison
    where it was evaluable" rule). It is ``not_evaluated`` only when no member
    could evaluate it. ``expired`` keeps its place among the evaluable
    statuses. Never ``unused`` when no member actually ran a check.
    """
    seen = set(statuses)
    if STATUS_ACTIVE in seen:
        return STATUS_ACTIVE
    if STATUS_SHADOWED_ONLY in seen:
        return STATUS_SHADOWED_ONLY
    evaluable = seen - {STATUS_NOT_EVALUATED}
    if evaluable == {STATUS_UNUSED}:
        return STATUS_UNUSED
    if STATUS_EXPIRED in evaluable:
        return STATUS_EXPIRED
    return STATUS_NOT_EVALUATED


def fold_rule_rows(
    member_rows: Iterable[Iterable[RuleAuditRow]],
) -> tuple[RuleAuditRow, ...]:
    """Merge several members' rule rows into one row per configured rule.

    Keyed by :class:`~abicheck.policy.rule_inventory.RuleRef`, so the same
    document entry in two members is one row with summed counts, and two
    entries that merely look alike stay two rows.
    """
    order: list[RuleRef] = []
    grouped: dict[RuleRef, list[RuleAuditRow]] = {}
    for rows in member_rows:
        for row in rows:
            if row.ref not in grouped:
                order.append(row.ref)
                grouped[row.ref] = []
            grouped[row.ref].append(row)
    folded: list[RuleAuditRow] = []
    for ref in order:
        group = grouped[ref]
        first = group[0]
        status = fold_rule_status(row.status for row in group)
        folded.append(
            RuleAuditRow(
                ref=ref,
                rule_id=first.rule_id,
                label=first.label,
                reason=first.reason,
                expires=first.expires,
                status=status,
                matched_count=sum(row.matched_count for row in group),
                shadowed_count=sum(row.shadowed_count for row in group),
                selector_matched_count=sum(row.selector_matched_count for row in group),
                intent=first.intent,
                allow_public_break=first.allow_public_break,
                rule_identity=first.rule_identity,
                missing_evidence=(
                    next(
                        (r.missing_evidence for r in group if r.missing_evidence), None
                    )
                    if status == "not_evaluated"
                    else None
                ),
            )
        )
    return tuple(folded)


def fold_disposition_audits(audits: Iterable[DispositionAudit]) -> DispositionAudit:
    """ADR-067 C-S2: fold N independent per-comparison audits into one total.

    The release/bundle fan-out and ``aggregate`` each run several independent
    comparisons (one per library, one per matrix target) and need one
    summary-level raw-versus-effective statement rather than N per-member
    blocks with no rollup.

    Every count here is a plain sum, so the folded total is itself conserved
    (``sum(folded.counts) == folded.detected_total`` holds whenever it held for
    every member). Rule rows are merged by configured-rule ref
    (:func:`fold_rule_rows`), with the status folded by :func:`fold_rule_status`.
    ``not_evaluated_detectors`` is deduplicated by name for the same reason a
    homogeneous release's members legitimately share one missing-evidence
    detector. The ``unused_rules`` gate is re-evaluated over the folded rows at
    the strictest member mode -- a release-level decision, not a sum of
    per-member ones.

    ``()`` in gives back the zero audit (every count ``0``, every sequence
    empty) rather than raising.
    """
    members = list(audits)
    detected_total = 0
    effective_total = 0
    policy_overlays = 0
    reclassified_total = 0
    acknowledged_total = 0
    counts: dict[str, int] = {d.value: 0 for d in Disposition}
    reason_order: list[str] = []
    reason_tally: dict[str, int] = {}
    detector_order: list[str] = []
    detectors: dict[str, NotEvaluatedDetector] = {}
    unacknowledged_gate_contribution = 0
    any_additions_review = False
    unacknowledged_entries: list[dict[str, object]] = []
    additions_policies: set[str] = set()
    modes: list[str] = []
    for audit in members:
        detected_total += audit.detected_total
        effective_total += audit.effective_total
        policy_overlays += audit.policy_overlays
        reclassified_total += audit.reclassified_total
        acknowledged_total += audit.acknowledged_total
        for name, count in audit.counts:
            counts[name] = counts.get(name, 0) + count
        for reason, count in audit.scope_reasons:
            if reason not in reason_tally:
                reason_order.append(reason)
                reason_tally[reason] = 0
            reason_tally[reason] += count
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
        if audit.unused_rules_review is not None:
            modes.append(str(audit.unused_rules_review.get("policy") or "allow"))
        for det in audit.not_evaluated_detectors:
            if det.name not in detectors:
                detector_order.append(det.name)
                detectors[det.name] = det
    rules = fold_rule_rows(audit.rules for audit in members)
    reclassifications = fold_rule_rows(audit.reclassifications for audit in members)
    acknowledgments = fold_rule_rows(audit.acknowledgments for audit in members)
    folded_rows = (*rules, *reclassifications, *acknowledgments)
    mode = strictest_mode(modes) if modes else "allow"
    return DispositionAudit(
        detected_total=detected_total,
        effective_total=effective_total,
        counts=tuple((d.value, counts[d.value]) for d in Disposition),
        rules=rules,
        not_evaluated_detectors=tuple(detectors[name] for name in detector_order),
        policy_overlays=policy_overlays,
        reclassified_total=reclassified_total,
        reclassifications=reclassifications,
        scope_reasons=tuple((reason, reason_tally[reason]) for reason in reason_order),
        acknowledged_total=acknowledged_total,
        acknowledgments=acknowledgments,
        # A folded, orthogonal ``0``/``1`` `gate_contribution` -- matching how
        # every other axis here (counts, totals) is a plain sum-or-max fold
        # of a conserved per-member fact. `policy` is the single value when
        # every member agreed, or ``"mixed"`` when they configured different
        # allow/warn/block settings -- never silently defaulted to ``"allow"``.
        # `None` (never a synthetic zero dict) when no member carried a review
        # at all, so "no member ever evaluated this" stays distinguishable from
        # "every member evaluated it and found nothing".
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
        unused_rules_review=(
            unused_rules_review(folded_rows, mode=mode).to_dict() if members else None
        ),
        accepted_breaks=tuple(r for a in members for r in a.accepted_breaks),
    )


def add_disposition_audit(
    d: dict[str, object],
    result: DiffResult,
    severity_config: object | None = None,
    *,
    today: date | None = None,
) -> None:
    """Attach the ``disposition_audit`` block to a JSON report (schema 2.51).

    Unconditional and unsuppressible by construction: it is derived from the
    conserved ledger rather than from the post-disposition ``changes`` list,
    so a rule cannot remove its own audit record (the same structural pattern
    ADR-049 Phase 5's coverage ledger uses). *today*, forwarded to
    :func:`compute_disposition_audit`, keeps this agreeing with an
    already-frozen ``ReportEnvelope``.
    """
    d["disposition_audit"] = compute_disposition_audit(
        result, severity_config, today=today
    ).to_dict()


def disposition_audit_reusing_document(
    result: DiffResult,
    severity_config: object | None,
    report_document: ReportDocument | None,
) -> DispositionAudit:
    """The typed counterpart of :func:`disposition_audit_dict_reusing_document`:
    the audit a shared ``ReportDocument`` already holds, reconstructed, or an
    independent :func:`compute_disposition_audit` for a caller with no
    document. Same ledger either way, so a direct caller and an envelope
    render cannot disagree about what was disposed.
    """
    if report_document is None:
        return compute_disposition_audit(result, severity_config)
    return DispositionAudit.from_dict(
        cast("Mapping[str, Any]", report_document.to_mapping()["disposition_audit"])
    )


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


def _active_rules(rows: Iterable[RuleAuditRow]) -> list[RuleAuditRow]:
    return [row for row in rows if row.matched_count > 0]


def _unused_names(audit: DispositionAudit) -> list[str]:
    return [
        row.rule_id for row in audit.configured_rules if row.status == STATUS_UNUSED
    ]


def render_disposition_audit_note(audit: DispositionAudit) -> str:
    """The compact one-line/`--stat` form: counts only, never dropped.

    Empty only when there is genuinely nothing to state: no change was
    detected *and* every detector ran, and no configured rule is unused.
    """
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
    unused = _unused_names(audit)
    if unused:
        parts.append(f"{len(unused)} unused rule(s)")
    if audit.not_evaluated_detectors:
        parts.append(f"{len(audit.not_evaluated_detectors)} detector(s) not evaluated")
    if (
        audit.detected_total == 0
        and not audit.not_evaluated_detectors
        and not audit.policy_overlays
        and not audit.reclassified_total
        and not audit.acknowledged_total
        and not unused
    ):
        # Nothing detected, every detector ran, and no rule is unused: the
        # counts are true but say nothing the line beside them does not.
        return ""
    return " [audit: " + ", ".join(parts) + "]"


def render_disposition_audit_lines(audit: DispositionAudit) -> list[str]:
    """The Markdown form (review digest / PR comment): the same counts as a
    table, plus the rules that account for the difference.

    Rules that disposed of nothing collapse to one line: the JSON carries every
    row, a human view names the ones that did something (ADR-067 P1).
    """
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
    active = _active_rules(audit.rules)
    if active:
        lines.append("**Rules applied:**")
        lines.append("")
        for rule in active[:_REVIEW_RULE_CAP]:
            detail = rule.reason or rule.label or "no reason given"
            source = f" (`{rule.source_file}`)" if rule.source_file else ""
            expiry = f", expires {rule.expires.isoformat()}" if rule.expires else ""
            lines.append(
                f"- `{rule.rule_id or 'rule'}`{source} — {detail} "
                f"[intent: {rule.intent}{expiry}] — {rule.matched_count} finding(s)"
            )
        if len(active) > _REVIEW_RULE_CAP:
            lines.append(
                f"- … {len(active) - _REVIEW_RULE_CAP} more rule(s) omitted; "
                "export JSON for the complete audit"
            )
        lines.append("")
    idle = [row for row in audit.rules if row.matched_count == 0]
    if idle:
        names = ", ".join(f"`{row.rule_id}`" for row in idle[:_REVIEW_RULE_CAP])
        more = (
            f", … and {len(idle) - _REVIEW_RULE_CAP} more"
            if len(idle) > _REVIEW_RULE_CAP
            else ""
        )
        lines.append(
            f"**Configured rules with no finding:** {len(idle)} — {names}{more}"
        )
        lines.append("")
    if audit.policy_overlays:
        lines.append(
            f"**Policy overlays:** {audit.policy_overlays} "
            "(diagnostics the gate can score; not counted as detections)"
        )
        lines.append("")
    reclass_active = _active_rules(audit.reclassifications)
    if reclass_active:
        lines.append(
            f"**Reclassified:** {audit.reclassified_total} "
            "(verdict moved by a `reclassify:` rule; disposition unchanged)"
        )
        lines.append("")
        for row in reclass_active:
            lines.append(f"- `{row.rule_id}` — {row.matched_count} finding(s)")
        lines.append("")
    if audit.scope_reasons:
        lines.append("**Out-of-contract/scope reasons:**")
        lines.append("")
        for reason, count in audit.scope_reasons:
            lines.append(f"- `{reason}` — {count} finding(s)")
        lines.append("")
    ack_active = _active_rules(audit.acknowledgments)
    if ack_active:
        lines.append(
            f"**Acknowledged:** {audit.acknowledged_total} "
            "(keeps its verdict class and gate contribution)"
        )
        lines.append("")
        for row in ack_active[:_REVIEW_RULE_CAP]:
            lines.append(f"- `{row.rule_id}` — {row.matched_count} finding(s)")
        if len(ack_active) > _REVIEW_RULE_CAP:
            lines.append(f"- … {len(ack_active) - _REVIEW_RULE_CAP} more omitted")
        lines.append("")
    if audit.unacknowledged_additions_review:
        review = audit.unacknowledged_additions_review
        policy = review.get("policy", "allow")
        unacked = cast("list[dict[str, object]]", review.get("unacknowledged") or [])
        if unacked:
            lines.append(
                f"**Unacknowledged public additions ({policy}):** {len(unacked)} "
                "(an orthogonal review gate; never reclassifies "
                "the addition)"
            )
            lines.append("")
            for entry in unacked[:_REVIEW_RULE_CAP]:
                symbol = entry.get("symbol") or "?"
                lines.append(f"- `{entry.get('kind')}`: `{symbol}`")
            if len(unacked) > _REVIEW_RULE_CAP:
                lines.append(f"- … {len(unacked) - _REVIEW_RULE_CAP} more omitted")
            lines.append("")
    unused = _unused_names(audit)
    if unused:
        mode = (audit.unused_rules_review or {}).get("policy", "allow")
        lines.append(
            f"**Unused rules ({mode}):** {len(unused)} configured rule(s) matched "
            "no finding in this run"
        )
        lines.append("")
        for name in unused[:_REVIEW_RULE_CAP]:
            lines.append(f"- `{name}`")
        if len(unused) > _REVIEW_RULE_CAP:
            lines.append(f"- … {len(unused) - _REVIEW_RULE_CAP} more omitted")
        lines.append("")
    lines += render_not_evaluated_rules(audit.unused_rules_review, _REVIEW_RULE_CAP)
    if audit.not_evaluated_detectors:
        # Collapsed to one line on purpose: D3 requires the *state* and its
        # count in every view, and each detector's own reason is already the
        # ``detectors`` block's ``coverage_gap``.
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
        # row is the only place the reader learns a detector could not run.
        tail += f" · {len(audit.not_evaluated_detectors)} detector(s) not evaluated"
    unused = _unused_names(audit)
    if unused:
        tail += f" · {len(unused)} unused rule(s)"
    lines = [
        f"> 📊 **Audit:** {audit.detected_total} detected · "
        f"{audit.effective_total} gating{tail}",
    ]
    active = _active_rules(audit.rules)
    for rule in active[:_COMMENT_RULE_CAP]:
        detail = rule.reason or rule.label or "no reason given"
        source = f" (`{rule.source_file}`)" if rule.source_file else ""
        lines.append(
            f"> · `{rule.rule_id or 'rule'}`{source} — {detail} "
            f"[intent: {rule.intent}] — {rule.matched_count} finding(s)"
        )
    remaining = len(active) - _COMMENT_RULE_CAP
    if remaining > 0:
        lines.append(f"> · … and {remaining} more rule(s)")
    lines.append("")
    return lines


__all__ = [
    "DispositionAudit",
    "NotEvaluatedDetector",
    "UnusedRulesReview",
    "add_disposition_audit",
    "compute_disposition_audit",
    "disposition_audit_dict_reusing_document",
    "fold_disposition_audits",
    "fold_rule_rows",
    "fold_rule_status",
    "render_disposition_audit_comment_lines",
    "render_disposition_audit_lines",
    "render_disposition_audit_note",
    "render_disposition_audit_section",
]
