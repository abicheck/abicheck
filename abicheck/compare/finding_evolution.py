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

"""Generic OLD-vs-NEW evolution matcher for a one-sided check (ADR-068 D3,
plan §5 prerequisite P2).

A one-sided check (``buildsource.crosscheck``'s eleven cross-source checks,
today) runs independently over each side and produces zero or more
:class:`~abicheck.checker_types.Change` objects per side, plus a boolean
"could this check even run" evidence signal per side (its own coverage
status: ``"present"`` — evidence sufficient, findings maybe empty — vs
``"skipped"`` — insufficient evidence, never a finding). This module is the
one, check-agnostic place that turns those two independent per-side outcomes
into a single evolution-stated finding set (:func:`evolve_check_findings`),
so every migrated check shares one matching algorithm instead of each
re-deriving its own "is this new" logic.

**The algorithm, stated as the invariant it must hold (this is the
correctness crux — see ``tests/parity/test_evolution_state_gap.py``):**
a check never emits a finding without sufficient evidence (this holds for
every existing ``crosscheck`` check — see each one's own coverage-honesty
docstring), so ``has_finding(side) => evaluated(side)`` always. That
correlation is what makes the four-way state table collapse to exactly the
cases below, with no case left where a finding exists but its own side's
evidence was insufficient to have produced it:

- absent on both sides -> nothing to report (no candidate identity exists);
- present on both -> ``persistent``;
- present on NEW only -> ``introduced`` when OLD *was* evaluated (evidence
  positively showed nothing there), else ``not_evaluated`` — OLD's evidence
  could not answer "was this already there", so this is never allowed to
  read as new;
- present on OLD only -> ``resolved`` when NEW *was* evaluated, else
  ``not_evaluated`` — NEW's evidence could not answer "is this now gone",
  so this is never allowed to read as silently fixed.

Import contract (ADR-061 D1): this module is ``compare``-classified per
``architecture/modules.yaml`` (``may_import: [model]`` only) — it imports
:class:`~abicheck.checker_types.Change` (a ``model``-classified legacy
module) and :class:`~abicheck.model.finding_evolution.FindingEvolution`,
nothing else. It knows nothing about ``buildsource.crosscheck`` or any
other check's own evidence/finding shape — that per-check glue (running the
check twice and calling this matcher) is a ``workflows``-layer concern; see
:mod:`abicheck.workflows.crosscheck_evolution`.

Authority is unchanged (ADR-028 D3 / ADR-035 D1): this module only ever
copies an existing ``Change`` (via :func:`dataclasses.replace`) and stamps
its ``evolution`` field — it never touches ``kind``, so a finding's
``RISK``/``API_BREAK`` category (and therefore ``BREAKING_KINDS``
membership) is exactly what the underlying check already assigned. No
finding is ever promoted by evolution.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence
from dataclasses import replace

from ..checker_types import Change
from ..model.finding_evolution import FindingEvolution


def default_check_identity(change: Change) -> Hashable:
    """The default per-finding identity a check's OLD/NEW instances pair on.

    ``(kind, symbol, new_value)`` -- every crosscheck finding names the
    affected symbol in ``symbol`` and, when relevant, the offending value
    (e.g. the leaked private type) in ``new_value``; both are stable across
    an otherwise-unrelated description-text change. A check whose identity
    needs a different projection may pass its own ``identity=`` callable to
    :func:`evolve_check_findings` instead.
    """
    return (change.kind, change.symbol, change.new_value)


def evolve_check_findings(
    *,
    old_evaluated: bool,
    old_findings: Sequence[Change],
    new_evaluated: bool,
    new_findings: Sequence[Change],
    identity: Callable[[Change], Hashable] = default_check_identity,
) -> list[Change]:
    """Pair one check's OLD/NEW findings into evolution-stated ``Change``s.

    ``old_evaluated``/``new_evaluated`` are that side's own "the check had
    enough evidence to run" signal (independent of whether it found
    anything) — pass the check's own per-side coverage status, never derive
    it from ``bool(old_findings)``/``bool(new_findings)`` (an evaluated side
    that legitimately found nothing must still count as evaluated, so a
    finding appearing only on the other side reads as a real ``introduced``/
    ``resolved``, not ``not_evaluated``).

    Returns one ``Change`` per identity that appears in ``old_findings`` or
    ``new_findings`` (never one for an identity found on neither side, even
    when both sides ran the check with sufficient evidence) — see the
    module docstring for the full four-case table. Order is deterministic
    (sorted by the string form of each identity key) so a caller can rely on
    stable output.
    """
    old_by_id: dict[Hashable, Change] = {identity(c): c for c in old_findings}
    new_by_id: dict[Hashable, Change] = {identity(c): c for c in new_findings}

    results: list[Change] = []
    for key in sorted(set(old_by_id) | set(new_by_id), key=repr):
        has_old = key in old_by_id
        has_new = key in new_by_id
        if has_new and has_old:
            base = new_by_id[key]
            evolution = FindingEvolution.PERSISTENT
        elif has_new:
            base = new_by_id[key]
            evolution = (
                FindingEvolution.INTRODUCED
                if old_evaluated
                else FindingEvolution.NOT_EVALUATED
            )
        elif has_old:
            base = old_by_id[key]
            evolution = (
                FindingEvolution.RESOLVED
                if new_evaluated
                else FindingEvolution.NOT_EVALUATED
            )
        else:  # pragma: no cover - key always comes from one of the two maps
            continue
        results.append(replace(base, evolution=evolution))
    return results


__all__ = ["default_check_identity", "evolve_check_findings"]
