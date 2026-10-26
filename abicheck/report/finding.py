# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""One pre-resolved verdict + issue category per :class:`Change`.

ADR-061 Phase 2 item 4b: ``junit_report.py``, ``html_report.py``, and
``reporter_markdown.py`` each independently called
``effective_verdict_for_change``/``classify_effective_change`` at their own
call sites -- sometimes more than once for the same change (``junit_report.
_is_failure``/``_failure_type`` each re-derived it). ``ReportFinding`` is
the value every renderer should read instead: resolved once per
:class:`~abicheck.checker_types.Change`, not reconstructed per call site,
matching this ADR's "each fact and decision is computed once" principle.

``Change`` is a mutable dataclass with no ``__hash__`` (``__hash__`` is
``None``), so a finding is never cached in a ``dict`` keyed by the ``Change``
itself. :func:`build_report_findings` instead returns a plain tuple built by
one pass over the caller's own change sequence; a caller needing random
access builds its own ``{id(f.change): f for f in findings}`` lookup, valid
only while those exact ``Change`` objects stay in scope (e.g. for the
duration of one render call).

Classified ``report`` (ADR-061 D1): every symbol this module needs --
``Verdict``, ``KindSets``, ``effective_verdict_for_change``,
``classify_effective_change``, ``IssueCategory`` -- is reached through
``policy.severity`` alone, not ``checker_policy``/``reclassify`` directly
(both unclassified `legacy_root_modules`), so this module stays inside
``report``'s declared ``[model, compare, policy, workflows]`` import set.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import cast

from ..checker_types import DiffResult
from ..model.change import Change
from ..model.change_catalog.assembled import REGISTRY
from ..model.change_catalog.kinds import HasKind
from ..policy.classification import evidence_status_for_result, impact_for
from ..policy.evaluate import effective_kind_sets
from ..policy.reclassify import active_reclassify_rules
from ..policy.severity import (
    IssueCategory,
    KindSets,
    Verdict,
    classify_effective_change,
    effective_verdict_for_change,
    reclassify_rule_for_change,
)
from .change_operation import entity_for_change, operation_for_change

#: The three display buckets every renderer groups a finding into.
BUCKET_ADDED = "added"
BUCKET_REMOVED = "removed"
BUCKET_CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class ReportFinding:
    """One change's pre-resolved verdict, issue category and display facts.

    ``bucket`` is the *effective* display bucket (``added``/``removed``/
    ``changed``, see :func:`effective_bucket`), ``entity`` the canonical
    :class:`~abicheck.model.change_catalog.assembled.ChangeEntity` value (``None`` for an
    unregistered kind), and ``reclassified_by`` the deciding
    ``reclassify:`` rule's audit label -- the same value the JSON report
    stamps on the change -- or ``None``. Resolved here once so HTML,
    Markdown and JSON cannot each re-derive them differently.
    """

    change: Change
    verdict: Verdict
    category: IssueCategory
    bucket: str = BUCKET_CHANGED
    entity: str | None = None
    reclassified_by: str | None = None


def effective_bucket(change: object, verdict: Verdict | None) -> str:
    """The display bucket for *change* given its resolved *verdict*.

    Starts from the canonical, registry-declared observed operation
    (:func:`~abicheck.report.change_operation.operation_for_change`, the
    same value the JSON report serializes as ``operation``): ``removed``
    stays ``removed``; ``modified``/``unchanged`` read ``changed``. An
    observed *addition* is ``added`` only while its effective verdict is
    ``COMPATIBLE`` -- a policy that escalates e.g. ``func_added`` to any
    other verdict makes it a finding to review, and the "Added" section
    reads as safe. ``verdict=None`` (no policy resolution available) falls
    back to the kind's declared default verdict.
    """
    kind_obj = getattr(change, "kind", "")
    kind_val = str(getattr(kind_obj, "value", kind_obj))
    operation = operation_for_change(change, kind_val)
    if operation == BUCKET_REMOVED:
        return BUCKET_REMOVED
    if operation != BUCKET_ADDED:
        return BUCKET_CHANGED
    if verdict is None:
        meta = REGISTRY.get(kind_val)
        verdict = getattr(meta, "default_verdict", Verdict.COMPATIBLE)
    return BUCKET_ADDED if verdict == Verdict.COMPATIBLE else BUCKET_CHANGED


def reclassified_by_label(
    change: object, policy_file: object | None, today: date | None = None
) -> str | None:
    """The ``reclassify:`` rule label that decided *change*, or ``None``.

    The single owner of the JSON report's per-change ``reclassified_by``
    value (``change_json.change_to_dict`` delegates here). Falls back to
    ``rule.to_verdict.value`` rather than ``rule.to``, since a directly-
    constructed ``ReclassifyRule`` could leave the latter empty.
    """
    if policy_file is None:
        return None
    rule = reclassify_rule_for_change(cast(HasKind, change), policy_file, today)
    if rule is None:
        return None
    return str(rule.label or rule.reason or rule.to_verdict.value)


def impact_text_for(change: object, evidence_tiers: Sequence[str] = ()) -> str:
    """The per-change impact text: the kind's registry impact, caveated by
    the evidence the run actually had for it.

    ``evidence_tiers`` lets an UNATTRIBUTED finding's impact text carry the
    same evidence caveat the JSON/Markdown views already do. Empty for a
    change with no kind (a stub or a non-``Change`` value).
    """
    kind = getattr(change, "kind", None)
    if not kind:
        return ""
    evidence_status = evidence_status_for_result(cast(HasKind, change), evidence_tiers)
    return impact_for(kind, evidence_status) or ""


def active_reclassify_labels(
    policy_file: object | None, today: date | None
) -> tuple[str, ...]:
    """The ``reclassify:`` rules still in effect on *today*, as display text.

    Expired rules are dropped rather than disclosed as though they applied:
    the disclosure states the run's active policy, not every rule the file
    ever named.
    """
    rules = getattr(policy_file, "reclassify", None) if policy_file else None
    if not rules:
        return ()
    return tuple(rule.describe() for rule in active_reclassify_rules(rules, today))


def kind_sets_for_result(result: DiffResult) -> KindSets:
    """The effective kind sets a projection resolves its verdicts against.

    Projections read this seam rather than ``policy.evaluate`` directly, so a
    renderer-adjacent compute module can state its dependence on policy in
    one place. The resolution itself is unchanged: this returns exactly
    :func:`~abicheck.policy.evaluate.effective_kind_sets` for *result*.
    """
    return effective_kind_sets(result)


def build_report_findings(
    changes: Sequence[Change],
    *,
    policy: str | None = None,
    kind_sets: KindSets | None = None,
    policy_file: object | None = None,
    today: date | None = None,
) -> tuple[ReportFinding, ...]:
    """Resolve one :class:`ReportFinding` per change in *changes*.

    *policy*/*kind_sets*/*policy_file*/*today* are forwarded verbatim to
    ``effective_verdict_for_change``/``classify_effective_change`` -- pass
    the same values a caller would have passed to either function directly
    (typically ``result.policy``/``policy.evaluate.effective_kind_sets(result)``/
    ``result.policy_file``) so the resolved verdict/category agree with
    what the rest of the report already computes. Each change's verdict is
    resolved once and handed to ``classify_effective_change`` rather than
    left for it to re-derive internally (Codex review).
    """
    findings = []
    for change in changes:
        verdict = effective_verdict_for_change(
            change,
            policy=policy,
            kind_sets=kind_sets,
            policy_file=policy_file,
            today=today,
        )
        category = classify_effective_change(
            change,
            policy=policy,
            kind_sets=kind_sets,
            policy_file=policy_file,
            today=today,
            verdict=verdict,
        )
        kind_val = str(getattr(change.kind, "value", change.kind))
        findings.append(
            ReportFinding(
                change=change,
                verdict=verdict,
                category=category,
                bucket=effective_bucket(change, verdict),
                entity=entity_for_change(change, kind_val),
                reclassified_by=reclassified_by_label(change, policy_file, today),
            )
        )
    return tuple(findings)


def findings_by_change_id(
    findings: Sequence[ReportFinding],
) -> dict[int, ReportFinding]:
    """Index *findings* by ``id(change)`` for O(1) lookup within one render.

    Only valid while the exact ``Change`` objects *findings* was built from
    remain alive and unchanged -- see the module docstring.
    """
    return {id(finding.change): finding for finding in findings}


def report_findings_for(result: DiffResult) -> tuple[ReportFinding, ...]:
    """:func:`build_report_findings` over ``result.changes``.

    Not memoized: ``DiffResult`` is a mutable dataclass, and ``model`` may
    not own this as a method anyway (it is ``model``-classified; ``model``
    may not import ``report``/``policy``, the dependency direction ADR-061
    D1 forbids). A prior revision cached the result on the instance
    (``result._report_findings_cache``) -- caching here was strictly a
    cross-call convenience, since every current caller already invokes this
    once per render, so it bought nothing; it also went stale silently if a
    caller mutated ``result`` (its ``changes``, ``policy_file``, or a
    ``Change.effective_verdict``) and called this again on the same
    instance -- e.g. a caller rendering twice with a demotion in between
    would keep serving the first render's verdicts (Codex review). Recomputed
    every call instead: this function is one pass over ``result.changes``,
    called at most twice per report (primary + `-o` secondary), so the
    cost is negligible next to the correctness this trades for it.

    A caller holding only a duck-typed stub result (``html_report.py``
    accepts one without ``policy_file``) should confirm the object looks
    like a real ``DiffResult`` (e.g. ``hasattr(result, "policy_file")``)
    before calling this, the same way it already guards other
    ``DiffResult``-only calls.
    """
    return build_report_findings(
        result.changes,
        policy=result.policy,
        kind_sets=effective_kind_sets(result),
        policy_file=result.policy_file,
    )
