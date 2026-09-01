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

from ..checker_types import Change, DiffResult
from ..policy.severity import (
    IssueCategory,
    KindSets,
    Verdict,
    classify_effective_change,
    effective_verdict_for_change,
)


@dataclass(frozen=True, slots=True)
class ReportFinding:
    """One change's pre-resolved verdict and issue category."""

    change: Change
    verdict: Verdict
    category: IssueCategory


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
    (typically ``result.policy``/``result._effective_kind_sets()``/
    ``result.policy_file``) so the resolved verdict/category agree with
    what the rest of the report already computes.
    """
    return tuple(
        ReportFinding(
            change=change,
            verdict=effective_verdict_for_change(
                change,
                policy=policy,
                kind_sets=kind_sets,
                policy_file=policy_file,
                today=today,
            ),
            category=classify_effective_change(
                change,
                policy=policy,
                kind_sets=kind_sets,
                policy_file=policy_file,
                today=today,
            ),
        )
        for change in changes
    )


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
    called at most twice per report (primary + `--write` secondary), so the
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
        kind_sets=result._effective_kind_sets(),
        policy_file=result.policy_file,
    )
