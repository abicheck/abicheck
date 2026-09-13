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
from typing import TYPE_CHECKING, Any, TypeVar

from .export_transition import surface_exit_is_evidence_gap

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ..model import AbiSnapshot, Function, Variable

__all__ = [
    "RECONCILED_FUNCTIONS",
    "RECONCILED_VARIABLES",
    "cached_reconciliation",
    "invalidate_reconciliation",
    "reconcile_declaration_lists",
    "reconcile_surfaces",
    "store_reconciliation",
]

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


#: Cache keys for the two per-pair reconciliations below.
RECONCILED_FUNCTIONS = "_abicheck_reconciled_functions"
RECONCILED_VARIABLES = "_abicheck_reconciled_variables"

_ReconciledPair = tuple[dict[str, _Decl], dict[str, _Decl]]


def cached_reconciliation(
    old: AbiSnapshot, new: AbiSnapshot, slot: str
) -> _ReconciledPair[Any] | None:
    """The reconciliation already computed for exactly this pair, if any.

    Every per-pair detector asks for the same reconciled surfaces, and
    building them is not free: it resolves a canonical identity for every
    declaration in both FULL maps. Recomputing that per detector made a
    comparison 25-70% slower across the scaling benchmarks (the PR-vs-base
    performance gate caught it), so the result is memoised on the OLD
    snapshot, keyed by the NEW one.

    The cache entry holds a strong reference to *new* and is matched with
    ``is``, so a recycled object address can never alias two different
    snapshots -- the same reasoning as ``scripts/fact_detector_misuse_scope``
    's ``_memoize_per_tree``, which caches on the node rather than in a
    module-level ``id()`` dict. Scoped to one comparison in practice: the
    snapshots are read-only while detectors run.
    """
    cached = old.__dict__.get(slot)
    if cached is not None and cached[0] is new:
        return cached[1]  # type: ignore[no-any-return]
    return None


def store_reconciliation(
    old: AbiSnapshot,
    new: AbiSnapshot,
    slot: str,
    result: _ReconciledPair[_Decl],
) -> _ReconciledPair[_Decl]:
    old.__dict__[slot] = (new, result)
    return result


def reconcile_declaration_lists(
    old_decls: Sequence[_Decl],
    new_decls: Sequence[_Decl],
    *,
    old_all: Sequence[_Decl],
    new_all: Sequence[_Decl],
    key: Callable[[_Decl], str],
    alias_key: Callable[[_Decl], str] | None = None,
    old_exported: Container[str] = frozenset(),
    new_exported: Container[str] = frozenset(),
) -> tuple[list[_Decl], list[_Decl]]:
    """:func:`reconcile_surfaces` for a caller whose surface is a *list*.

    ``diff_symbols`` keys its surfaces by mangled name, but two sibling
    detectors -- ``diff_templates``'s internal-template-leak pass and its
    lambda-closure demotion pass -- select a list straight off
    ``AbiSnapshot.functions`` with their own predicate. They are per-pair
    joins all the same (an OLD instantiation absent from NEW is the whole
    finding), so the same evidence asymmetry manufactures the same false
    break there, and routing them through a second hand-written copy of the
    rule is what this module exists to avoid (Codex review, P1).

    *key* is the caller's own match key; declarations sharing one are kept
    in first-seen order, and the returned lists preserve each input's order
    with any re-admitted declaration appended.

    *alias_key* is the ambiguity-safe second tier, and it is what makes this
    usable for a *mangled* key: the realistic change these detectors exist to
    catch alters the mangling itself (``char *`` -> ``char8_t *`` on a
    parameter takes ``_Z1fPKc`` to ``_Z1fPKDu``), so an exact-key lookup finds
    no peer on the evidence-poor side and the finding is lost outright --
    reported as a bare removal or addition instead (Codex review, P2). A
    declaration is resolved through this tier only when exactly one peer
    carries the same alias; "no candidate" and "several candidates" both
    decline, so an overload set is never guessed at. Omit it for a key that
    is already stable across the change.
    """
    old_map = _first_by_key(old_decls, key)
    new_map = _first_by_key(new_decls, key)
    reconciled_old, reconciled_new = reconcile_surfaces(
        old_map,
        new_map,
        old_all=_first_by_key(old_all, key),
        new_all=_first_by_key(new_all, key),
        old_exported=old_exported,
        new_exported=new_exported,
        resolve_in_old=_alias_resolver(old_all, alias_key),
        resolve_in_new=_alias_resolver(new_all, alias_key),
    )
    return (
        _appended(old_decls, reconciled_old, old_map),
        _appended(new_decls, reconciled_new, new_map),
    )


def _alias_resolver(
    decls: Sequence[_Decl], alias_key: Callable[[_Decl], str] | None
) -> _Resolver[_Decl] | None:
    """Resolve a declaration to the single peer in *decls* sharing its alias.

    Built lazily on first use, like the symbol join's own index: a pair with
    no evidence-gap candidate at all must not pay to index both full maps.
    """
    if alias_key is None:
        return None
    index: list[dict[str, list[_Decl]]] = []

    def _resolve(key: str, decl: _Decl) -> _Decl | None:
        if not index:
            by_alias: dict[str, list[_Decl]] = {}
            for candidate in decls:
                by_alias.setdefault(alias_key(candidate), []).append(candidate)
            index.append(by_alias)
        matches = index[0].get(alias_key(decl), ())
        return matches[0] if len(matches) == 1 else None

    return _resolve


def _first_by_key(
    decls: Sequence[_Decl], key: Callable[[_Decl], str]
) -> dict[str, _Decl]:
    """*decls* keyed by *key*, first occurrence winning -- so a duplicate key
    never silently replaces the declaration the caller's own list order put
    first."""
    out: dict[str, _Decl] = {}
    for decl in decls:
        out.setdefault(key(decl), decl)
    return out


def _appended(
    original: Sequence[_Decl],
    reconciled: Mapping[str, _Decl],
    before: Mapping[str, _Decl],
) -> list[_Decl]:
    """*original*, plus whatever reconciliation re-admitted.

    Deliberately additive in *position* as well as in content: the caller's
    own ordering decides which of two same-key declarations it already
    treats as canonical, and reordering that would be a behaviour change no
    part of this defect calls for.
    """
    return list(original) + [decl for k, decl in reconciled.items() if k not in before]


def invalidate_reconciliation(old: AbiSnapshot | None) -> None:
    """Drop any memoised reconciliation held on *old*.

    :func:`cached_reconciliation` matches on object identity, which is only
    sound while the snapshots are read-only -- the memo's own stated scope.
    A caller that holds two ``AbiSnapshot`` objects, compares them, *mutates*
    one (flipping a declaration's contract fact from unknown to a
    producer-confirmed ``False``, say) and compares the same objects again
    would otherwise be served the first call's surfaces and see no change at
    all, where an equivalent fresh pair reports one (Codex review, P2). The
    typed API makes that shape reachable: nothing there requires a caller to
    rebuild its snapshots between comparisons.

    So :func:`~abicheck.checker.compare` calls this on entry, which makes the
    memo's scope what its docstring always claimed -- one comparison -- rather
    than the lifetime of the object it happens to hang on. It is deliberately
    keyed to *old* alone (both slots live there) and never inspects the
    surfaces: a validity token derived from the facts would have to visit
    every declaration in both full maps, which is the cost the memo exists to
    avoid.
    """
    if old is None:
        return
    for slot in (RECONCILED_FUNCTIONS, RECONCILED_VARIABLES):
        old.__dict__.pop(slot, None)
