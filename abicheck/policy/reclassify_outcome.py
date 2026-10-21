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

"""ADR-067 P1: what every ``reclassify:`` rule did to one finding.

The verdict resolver (:mod:`abicheck.policy.reclassify`) answers "which
verdict"; the audit needs one more answer per finding: which rules *matched*
(selector hits, including a no-op or a shadowed one), which rule *decided*,
and whether that decision *changed* the verdict. This module answers those by
walking the same precedence chain the resolver walks, so the two cannot
disagree. Kept out of ``reclassify.py``, which is at the production line cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import cast

from ..model.change_catalog.kinds import HasKind
from .classification import Verdict, effective_category
from .reclassify import (
    _has_frozen_namespace_violation,
    _matched_kind,
    resolve_kind_sets,
)
from .release_outcome import compatibility_rank


@dataclass(frozen=True)
class ReclassifyOutcome:
    """How every configured ``reclassify:`` rule fared on one finding (P1).

    ``hits`` are the indices (into the document's rule list) of every rule
    whose selectors matched -- the first-match winner, any no-op winner, and
    every later rule that was shadowed. ``decider`` is the index of the rule
    that *decided* the verdict (the first match, when it was not preempted by
    a pipeline ``effective_verdict``), and ``changed`` says whether that
    decision moved the verdict. ``decider`` is ``None`` when nothing decided
    (no match, or a pipeline modulation pre-empted the whole chain).

    Mirrors :func:`effective_verdict_for_change` and
    :func:`reclassify_rule_for_change` step for step; the closing ledger reads
    this instead of re-deriving either one, so the audit's counts cannot
    disagree with the verdict the run actually produced.
    """

    hits: tuple[int, ...] = ()
    decider: int | None = None
    changed: bool = False


def reclassify_outcome_for_change(
    change: HasKind, policy_file: object | None, today: date | None = None
) -> ReclassifyOutcome:
    """Every matching rule, and which one (if any) actually moved *change*."""
    rules = (
        getattr(policy_file, "reclassify", None) if policy_file is not None else None
    )
    if not rules:
        return ReclassifyOutcome()
    kind = change.kind.value
    indexed = [(i, r) for i, r in enumerate(rules) if _matched_kind(r) in (None, kind)]
    hits = tuple(i for i, rule in indexed if rule.matches(change, today))
    if not hits:
        return ReclassifyOutcome()
    if isinstance(getattr(change, "effective_verdict", None), Verdict):
        # A pipeline modulation pre-empts reclassify entirely: the selectors
        # matched, but nothing was decided by them.
        return ReclassifyOutcome(hits=hits)
    first = hits[0]
    rule = rules[first]
    next_priority_v = _next_priority_verdict(change, policy_file)
    base_v = effective_category(
        change, *resolve_kind_sets(getattr(policy_file, "base_policy", None), None)
    )
    reclass_v = rule.to_verdict
    effective_v = (
        base_v
        if (
            _has_frozen_namespace_violation(change)
            and compatibility_rank(reclass_v) < compatibility_rank(base_v)
        )
        else reclass_v
    )
    if effective_v == next_priority_v:
        return ReclassifyOutcome(hits=hits, decider=first, changed=False)
    return ReclassifyOutcome(hits=hits, decider=first, changed=True)


def _next_priority_verdict(change: HasKind, policy_file: object | None) -> Verdict:
    """The verdict a reclassify rule would have to beat: the same-kind
    ``overrides:`` entry (frozen-namespace floored), else the base policy."""
    base_policy = getattr(policy_file, "base_policy", None)
    base_v = effective_category(change, *resolve_kind_sets(base_policy, None))
    overrides = getattr(policy_file, "overrides", None)
    if overrides and change.kind in overrides:
        override_v = cast(Verdict, overrides[change.kind])
        if _has_frozen_namespace_violation(change) and compatibility_rank(
            override_v
        ) < compatibility_rank(base_v):
            return base_v
        return override_v
    return base_v
