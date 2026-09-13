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

"""Repair a surface-membership disagreement that is an *evidence* asymmetry
rather than a change in the library, once, where the two surfaces meet.

``diff_symbols`` builds each side's compared public surface from that side's
own facts, and one of them -- ``in_public_contract`` (b) -- is only
*established* when that side's producer was given a public-header set. Two
captures of an unchanged library, one with that set and one without,
therefore disagree about every promised-but-unexported declaration, and the
disagreement is an artifact of how the two runs were configured.

**Why this lives here rather than at each disposition site.** An earlier
revision guarded the removal path, then the addition path, and a reviewer
pointed out the remaining hole (Codex review, P1): every *other* per-pair
detector -- parameter defaults, ``[[deprecated]]`` transitions, parameter
renames, pointer levels, access narrowing, and the variable value/access
siblings -- rebuilds the filtered maps itself, so a pair reconciled at one
call site is invisible to all of them. A run whose only real change was a
parameter default moving from ``1`` to ``2`` on such a declaration reported
nothing at all. Guarding the third site would have left the fourth.

So the asymmetry is repaired in the *surface*, before any detector runs:
both sides get the declaration back, the pair matches like any other, and
every per-pair detector sees it with no knowledge of this module. That is
the same shape ``finding_identity_ctor_dtor.iter_matched_function_pairs``
already took for ctor/dtor key drift, for the same reason -- one join, not
one patch per consumer.

Deliberately *additive only*: a declaration is re-admitted to the side that
lacks the evidence, never removed from the side that has it, so no detector
loses a population it had before.
"""

from __future__ import annotations

from collections.abc import Container
from typing import TYPE_CHECKING, TypeVar

from .export_transition import surface_exit_is_evidence_gap

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..model import Function, Variable

__all__ = ["reconcile_surfaces"]

_Decl = TypeVar("_Decl", "Function", "Variable")


def reconcile_surfaces(
    old_map: Mapping[str, _Decl],
    new_map: Mapping[str, _Decl],
    *,
    old_all: Mapping[str, _Decl],
    new_all: Mapping[str, _Decl],
    old_exported: Container[str] = frozenset(),
    new_exported: Container[str] = frozenset(),
) -> tuple[dict[str, _Decl], dict[str, _Decl]]:
    """Both surfaces, with evidence-gap-only differences reconciled.

    *old_map*/*new_map* are the two compared public surfaces;
    *old_all*/*new_all* the full declaration maps behind them;
    *old_exported*/*new_exported* each side's observed export table (see
    :func:`~abicheck.compare.export_transition.surface_exit_is_evidence_gap`
    for why the table is consulted and not only the declaration's own fact).

    Returns new dicts; the inputs are not mutated. A key present in both, or
    absent from both full maps, is untouched -- so a comparison with
    symmetric evidence gets back exactly what it passed in.
    """
    reconciled_old = dict(old_map)
    reconciled_new = dict(new_map)
    for key in old_map.keys() - new_map.keys():
        peer = new_all.get(key)
        if peer is not None and surface_exit_is_evidence_gap(
            old_map[key], peer, old_exported_symbols=old_exported, key=key
        ):
            reconciled_new[key] = peer
    for key in new_map.keys() - old_map.keys():
        peer = old_all.get(key)
        if peer is not None and surface_exit_is_evidence_gap(
            new_map[key], peer, old_exported_symbols=new_exported, key=key
        ):
            reconciled_old[key] = peer
    return reconciled_old, reconciled_new
