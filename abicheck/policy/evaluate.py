"""The one policy entry that classifies a comparison's findings.

:func:`evaluate` turns a :class:`~abicheck.checker_types.DiffResult` -- pure
model data -- into a :class:`ClassifiedDiff`: every compatibility-evaluated
finding placed in exactly one verdict bucket under the active policy profile
and policy file (kind-set overrides, ``reclassify:`` rules, frozen-namespace
guards). ``DiffResult`` itself holds no policy logic (ADR-061): a reader asks
policy, the result does not ask on its own behalf.

Nothing is cached on the result. A caller that edits ``diff.changes`` or
``diff.policy_file`` afterwards calls :func:`evaluate` again and sees the
edit, exactly as the former ``DiffResult.breaking`` properties did.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..model.change_catalog.registry import Verdict
from ..model.contract_finding_relevance import is_evaluated
from .classification import apply_policy_file_overrides, policy_kind_sets
from .reclassify import KindSets, effective_verdict_for_change

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from ..model.change import Change

__all__ = [
    "ClassifiedDiff",
    "effective_kind_sets",
    "effective_verdict",
    "evaluate",
    "evaluated_changes",
]


def effective_kind_sets(diff: DiffResult) -> KindSets:
    """(breaking, api_break, compatible, risk) kind sets with *diff*'s overrides applied."""
    overrides = diff.policy_file.overrides if diff.policy_file else None
    return apply_policy_file_overrides(policy_kind_sets(diff.policy), overrides)


def effective_verdict(
    diff: DiffResult, change: Change, *, kind_sets: KindSets | None = None
) -> Verdict:
    """One finding's verdict under *diff*'s policy, including frozen-namespace guards."""
    return effective_verdict_for_change(
        change,
        policy=diff.policy,
        kind_sets=kind_sets if kind_sets is not None else effective_kind_sets(diff),
        policy_file=diff.policy_file,
    )


def evaluated_changes(diff: DiffResult) -> list[Change]:
    """The findings compatibility policy classifies (ADR-049 D1).

    A finding contract evaluation left ``NOT_EVALUATED`` is in no verdict
    bucket -- it stays in ``diff.changes`` and ``diff.not_evaluated``.
    Without ``--contract`` this is ``diff.changes`` unchanged.
    """
    return [c for c in diff.changes if is_evaluated(c)]


@dataclass(frozen=True)
class ClassifiedDiff:
    """A comparison's findings bucketed by effective verdict, in ``changes`` order.

    ``not_evaluated`` holds the findings contract evaluation left
    ``NOT_EVALUATED`` (ADR-049 D1); they are in no verdict bucket.
    """

    breaking: list[Change]
    source_breaks: list[Change]
    compatible: list[Change]
    risk: list[Change]
    not_evaluated: list[Change]


def evaluate(diff: DiffResult) -> ClassifiedDiff:
    """Classify *diff*'s findings under its own ``policy``/``policy_file``."""
    kind_sets = effective_kind_sets(diff)
    buckets: dict[Verdict, list[Change]] = {
        Verdict.BREAKING: [],
        Verdict.API_BREAK: [],
        Verdict.COMPATIBLE: [],
        Verdict.COMPATIBLE_WITH_RISK: [],
    }
    not_evaluated: list[Change] = []
    for change in diff.changes:
        if not is_evaluated(change):
            not_evaluated.append(change)
            continue
        verdict = effective_verdict(diff, change, kind_sets=kind_sets)
        bucket = buckets.get(verdict)
        if bucket is not None:
            bucket.append(change)
    return ClassifiedDiff(
        breaking=buckets[Verdict.BREAKING],
        source_breaks=buckets[Verdict.API_BREAK],
        compatible=buckets[Verdict.COMPATIBLE],
        risk=buckets[Verdict.COMPATIBLE_WITH_RISK],
        not_evaluated=not_evaluated,
    )
