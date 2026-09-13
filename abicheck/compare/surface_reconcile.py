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

from collections.abc import Callable, Container
from typing import TYPE_CHECKING, TypeVar

from .export_transition import surface_exit_is_evidence_gap

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..model import Function, Variable

__all__ = ["reconcile_surfaces"]

_Decl = TypeVar("_Decl", "Function", "Variable")

#: Resolve one side's key to the other side's declaration when the two sides
#: spell it differently, using the caller's own ambiguity-safe identity tier
#: (``SymbolIdentityIndex``). Passed in rather than imported: the identity
#: machinery lives outside this package, and the rule for *which* alias is
#: legitimate belongs to the symbol join, not here.
_Resolver = Callable[[str, "_Decl"], "_Decl | None"]


def reconcile_surfaces(
    old_map: Mapping[str, _Decl],
    new_map: Mapping[str, _Decl],
    *,
    old_all: Mapping[str, _Decl],
    new_all: Mapping[str, _Decl],
    old_exported: Container[str] = frozenset(),
    new_exported: Container[str] = frozenset(),
    resolve_in_old: _Resolver[_Decl] | None = None,
    resolve_in_new: _Resolver[_Decl] | None = None,
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
    _admit(
        src=old_map,
        dst=reconciled_new,
        other=new_map,
        other_all=new_all,
        exported=old_exported,
        resolve=resolve_in_new,
    )
    _admit(
        src=new_map,
        dst=reconciled_old,
        other=old_map,
        other_all=old_all,
        exported=new_exported,
        resolve=resolve_in_old,
    )
    return reconciled_old, reconciled_new


def _admit(
    *,
    src: Mapping[str, _Decl],
    dst: dict[str, _Decl],
    other: Mapping[str, _Decl],
    other_all: Mapping[str, _Decl],
    exported: Container[str],
    resolve: _Resolver[_Decl] | None,
) -> None:
    """Re-admit, into *dst*, each of *src*'s declarations the other side
    dropped from its surface purely for want of contract evidence.

    One direction of :func:`reconcile_surfaces`, written once and called
    twice: the rule is symmetric, and two hand-mirrored copies are how the
    addition half of this very defect survived a round of review.
    """
    # Collect first, admit second: a peer claimed by more than one source key
    # is admitted for none of them. The alias tier answers "the single peer
    # carrying this name", which is unique *per lookup* and says nothing about
    # the reverse direction -- an overload set on the evidence-bearing side
    # (`foo(int)`, `foo(double)`, ...) against one unexported `extern "C" foo`
    # on the other resolves every overload key to that same declaration, and
    # admitting each would put one object under every overload key: the join
    # then compares every old overload against it, hiding genuine removals and
    # inventing signature/linkage changes (Codex review, P2). One-to-one or
    # not at all, which is the same ambiguity-safety the alias tier already
    # applies in its own direction.
    proposals: dict[str, _Decl] = {}
    claims: dict[int, int] = {}
    for key in src.keys() - other.keys():
        peer = other_all.get(key)
        if peer is None and resolve is not None:
            # The declaration may be spelled under a different key on the
            # other side -- an `extern "C"` declaration gaining or losing its
            # C++ mangling is the real case (Codex review, P2), and the
            # symbol join already pairs those through an ambiguity-safe
            # alias tier. An exact-key-only lookup missed it, so the pair
            # read as a removal on one side and an addition on the other,
            # instead of the linkage change it is.
            peer = resolve(key, src[key])
            if peer is not None and any(p is peer for p in other.values()):
                # Already in the other side's surface under its own key: the
                # symbol join's own alias tier pairs them, and admitting a
                # second copy here would double-report the same declaration.
                continue
        if peer is not None and surface_exit_is_evidence_gap(
            src[key], peer, old_exported_symbols=exported, key=key
        ):
            proposals[key] = peer
            claims[id(peer)] = claims.get(id(peer), 0) + 1
    for key, peer in proposals.items():
        if claims[id(peer)] == 1:
            dst[key] = peer
