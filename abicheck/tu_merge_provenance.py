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

"""``tu_merge.py``'s provenance-comparison cluster: which of two cross-TU
redeclarations' ``source_location``/``source_header``/``origin``/
``deprecated`` should survive a merge, and whether two declarations are
identical modulo those routinely-differing fields.

Split out of ``tu_merge.py`` (mechanical extraction, unchanged function
bodies) once that file crossed the ADR-061 architecture debt ledger's
no-growth baseline for the second batch of ADR-063 Phase 5's fact/
capability registry conversions -- the same "move responsibility instead of
raising the baseline" discipline ``fact_field_readers_scope.py``/
``qualified_name_segments_walk.py`` already establish elsewhere in this
codebase. ``tu_merge.py`` re-imports every name here, so no call site
outside this file changed.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol, TypeVar

from .model import Fact, ScopeOrigin
from .provenance import classify_origin, header_from_location

__all__ = [
    "_Provenanced",
    "_blank_provenance",
    "_more_public_of",
    "_other_is_strictly_less_public",
    "_pick_deprecated",
    "_with_more_public_provenance",
]


class _Provenanced(Protocol):
    """The ADR-015 schema v6 provenance fields every model type this module
    compares (:class:`~abicheck.model.Function`/:class:`~abicheck.model.
    Variable`/:class:`~abicheck.model.RecordType`/:class:`~abicheck.model.
    EnumType`) shares -- expressing this as a bound on ``_T`` (CodeRabbit
    review, PR #635) lets :func:`_blank_provenance`/:func:`_more_public_of`/
    :func:`_with_more_public_provenance` type-check their real contract
    directly, instead of an unbound ``_T`` plus per-call-site ``# type:
    ignore[attr-defined]`` suppressions.
    """

    source_location: str | None
    source_header: str | None
    origin: ScopeOrigin
    deprecated: str | None


_T = TypeVar("_T", bound=_Provenanced)


def _pick_deprecated(
    primary: _T, secondary: _T, *, secondary_is_private: bool = False
) -> str | None:
    """Pick which side's ``deprecated`` message survives a merge -- prefer
    *primary* (whichever side the caller already selected as the merge's
    representative, e.g. via :func:`_more_public_of`) when it carries one,
    otherwise fall back to *secondary* -- **unless** *secondary_is_private*
    is ``True``, in which case an unset *primary* stays unset rather than
    picking up *secondary*'s message.

    Two TUs' redeclarations carrying *different* non-``None`` messages --
    e.g. ``[[deprecated("a")]] void f();`` in one TU, ``[[deprecated("b")]]
    void f();`` in another -- is **not** a conflict (Codex review, PR #635
    round 13, verified empirically against both GCC and Clang compiling
    exactly that pair under ``-pedantic-errors``: both accept it cleanly).
    A deprecation message is additive diagnostic metadata, not part of a
    declaration's type or an ODR-significant fact, unlike
    ``contract_attributes``/calling-convention tokens
    (``tu_merge._merge_contract_attributes``), which really can describe
    genuinely incompatible ABI-relevant claims. There is therefore nothing
    to reject here -- this never fails, unlike every other optional-fact
    merge in ``tu_merge.py``.

    But when the caller can *prove* *secondary* is the strictly-less-public
    side (:func:`_other_is_strictly_less_public`), an unset *primary* must
    not pick up *secondary*'s message anyway: a private-only redeclaration
    annotating an otherwise-undecorated public declaration as
    ``[[deprecated]]`` does not make the library's actual public consumers
    -- who only ever see the public header -- see that deprecation, and
    later removing/changing that private-only annotation would surface as
    a false ``FUNC_DEPRECATED_REMOVED``/``CHANGED`` (or the variable/type/
    enum equivalent) against a public surface that never carried it (Codex
    review, PR #635 round 18) -- the same leak ``tu_merge._merge_functions``'s
    default-argument union was fixed against in round 17.
    """
    if primary.deprecated is not None:
        return primary.deprecated
    if secondary_is_private:
        return None
    return secondary.deprecated


def _blank_provenance(entity: _T) -> _T:
    """Blank *entity*'s ``source_location``/``source_header``/``origin``/
    ``deprecated`` for an equality comparison.

    Every one of the four model types this module compares
    (:class:`~abicheck.model.Function`/:class:`~abicheck.model.Variable`/
    :class:`~abicheck.model.RecordType`/:class:`~abicheck.model.EnumType`)
    carries these same provenance fields (ADR-015 schema v6). They
    legitimately differ across TUs for what is otherwise the very same
    declaration -- each TU force-includes its own header file, so a
    genuinely identical redeclaration still has a different
    ``source_location``/``source_header`` per side (e.g. ``"a.h:1"`` vs
    ``"b.h:1"``) purely because of *which* TU parsed it, not because the
    declarations disagree. Comparing them directly would make ADR-050 D4's
    own "declaration + redeclaration" trivial-merge case -- the routine
    shape of a real multi-TU manifest, not an edge case -- spuriously
    conflict on every ordinary cross-TU redeclaration.

    ``deprecated`` gets the same treatment for the same reason: one TU
    seeing ``[[deprecated]]`` on an otherwise-identical redeclaration that
    another TU sees without it -- or even a *different* message than
    another TU sees (round 13) -- is exactly as routine as differing
    provenance, never a structural disagreement -- every caller of this
    function picks ``deprecated`` back explicitly via
    :func:`_pick_deprecated` afterwards.
    """
    # ADR-063 Phase 5, Codex review P1: source_header_fact must blank too,
    # or __post_init__'s "explicit Fact wins" rule reasserts the stale
    # value -- hasattr-gated since not every owner has converted it yet.
    extra: dict[str, object] = (
        {"source_header_fact": Fact.not_collected()}
        if hasattr(entity, "source_header_fact")
        else {}
    )
    return replace(  # type: ignore[type-var]
        entity,
        source_location=None,
        source_header=None,
        origin=ScopeOrigin.UNKNOWN,
        deprecated=None,
        **extra,
    )


def _more_public_of(
    a: _T,
    b: _T,
    *,
    header_segs: list[tuple[str, ...]],
    dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> _T:
    """Pick whichever of *a*/*b* -- two already-confirmed-compatible
    declarations -- should lend its ``source_location``/``source_header``/
    ``origin`` to the merged result.

    A merged declaration carries exactly one ``source_location`` (the model
    has no "seen from N headers" field), and
    :func:`abicheck.provenance.apply_provenance` classifies a declaration's
    public/private ``origin`` from that single field, *after* this merge
    already ran. Defaulting to an arbitrary side (e.g. always the
    tu_name-sorted-first one) is a real correctness gap, not a cosmetic
    choice: if TU ``a`` reaches this declaration only through a private
    header while TU ``b`` reaches the identical declaration through a
    declared *public* one, keeping ``a``'s location would make a genuinely
    public API read as private -- silently hiding a real ABI change from
    public-surface scoping (Codex review, PR #635). When exactly one side
    classifies as ``PUBLIC_HEADER``, that side wins; otherwise *a* (the
    deterministic tu_name-ordered default) is kept, unchanged from before.
    """
    if not have_public_set:
        return a
    origin_a = classify_origin(
        header_from_location(a.source_location),
        header_segs,
        dir_segs,
        have_public_set=have_public_set,
    )
    if origin_a == ScopeOrigin.PUBLIC_HEADER:
        return a
    origin_b = classify_origin(
        header_from_location(b.source_location),
        header_segs,
        dir_segs,
        have_public_set=have_public_set,
    )
    return b if origin_b == ScopeOrigin.PUBLIC_HEADER else a


def _other_is_strictly_less_public(
    base: _T,
    other: _T,
    *,
    header_segs: list[tuple[str, ...]],
    dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> bool:
    """Whether *other* is definitively *less* public than *base* -- i.e.
    :func:`_more_public_of` picked *base* specifically because it classifies
    as ``PUBLIC_HEADER`` and *other* does not (not merely an arbitrary
    tu_name-ordered tie-break between two equally-classified, or
    unclassifiable, sides).

    A capability only *other*'s declaration grants -- most concretely, a
    default argument (Codex review, PR #635 round 17) -- must not be
    attributed to the merged, *base*-provenanced declaration when this is
    ``True``: a private-only header redeclaring ``f(int)`` (the public
    signature) as ``f(int = 42)`` does not give the library's actual public
    consumers -- who only ever see the public header -- the ability to call
    ``f()`` with no argument; unioning that default in anyway would make
    the merged snapshot claim a capability the public API never granted,
    and later removing/changing that private-only default would then
    surface as a false ``PARAM_DEFAULT_VALUE_REMOVED``/``CHANGED`` finding
    against the public surface. When public/private status can't be
    determined at all (``have_public_set`` is ``False``) or both sides
    classify the same way, this returns ``False`` and every other
    optional-fact union in ``tu_merge.py`` keeps its existing, symmetric
    behavior -- this narrower check exists only for the one case where we
    can concretely prove *other* is the less-visible side.
    """
    if not have_public_set:
        return False
    origin_base = classify_origin(
        header_from_location(base.source_location),
        header_segs,
        dir_segs,
        have_public_set=have_public_set,
    )
    if origin_base != ScopeOrigin.PUBLIC_HEADER:
        return False
    origin_other = classify_origin(
        header_from_location(other.source_location),
        header_segs,
        dir_segs,
        have_public_set=have_public_set,
    )
    return origin_other != ScopeOrigin.PUBLIC_HEADER


def _with_more_public_provenance(
    winner: _T,
    other: _T,
    *,
    header_segs: list[tuple[str, ...]],
    dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> _T:
    """Return *winner* -- the structurally-complete side of a forward-
    declaration/definition merge (``tu_merge._merge_types``/
    ``tu_merge._merge_enums``) -- with its provenance possibly overridden
    from *other* (the forward declaration) when *other* classifies as more
    public, and its ``deprecated`` picked via :func:`_pick_deprecated`
    (preferring whichever side ends up as the provenance representative).

    :func:`_more_public_of` alone isn't enough here: unlike the
    already-identical-modulo-provenance case it's built for, *winner* and
    *other* are structurally different (fields/members differ by
    construction -- that's the whole point of a forward-decl/definition
    pair), so simply calling it and returning whichever side "wins" would
    silently drop the winner's richer structural facts whenever the forward
    declaration happens to be the public one. A public header commonly
    forward-declares a type whose full definition lives only in a private
    implementation header -- keeping the definition's fields/size/members
    is still correct, but the merged entity's *provenance* must reflect the
    public forward declaration, or ``apply_provenance`` reads a genuinely
    public type as private (Codex review, PR #635 follow-up).

    ``deprecated`` gets the same "don't silently drop the other side's
    fact" treatment (Codex review, PR #635 round 7): a public
    ``class [[deprecated("old")]] X;`` forward declaration merged with an
    undecorated private definition must not lose the deprecation -- picking
    *winner*'s fields wholesale, as before this fix, always did, since
    *winner* is the definition and definitions commonly carry no
    ``[[deprecated]]`` of their own. Two differing non-``None`` messages are
    not a conflict here either (round 13 -- see :func:`_pick_deprecated`),
    so this function can no longer fail and returns ``_T`` unconditionally.
    """
    provenance_source = _more_public_of(
        winner,
        other,
        header_segs=header_segs,
        dir_segs=dir_segs,
        have_public_set=have_public_set,
    )
    provenance_fallback = other if provenance_source is winner else winner
    fallback_is_private = _other_is_strictly_less_public(
        provenance_source,
        provenance_fallback,
        header_segs=header_segs,
        dir_segs=dir_segs,
        have_public_set=have_public_set,
    )
    deprecated = _pick_deprecated(
        provenance_source, provenance_fallback, secondary_is_private=fallback_is_private
    )
    if provenance_source is winner:
        merged = winner
    else:
        merged = replace(  # type: ignore[type-var]
            winner,
            source_location=other.source_location,
            source_header=other.source_header,
            origin=other.origin,
        )
        # ADR-063 Phase 5, Codex review P1: source_header_fact must move
        # with source_header too, or winner's stale Fact wins under
        # __post_init__'s "explicit Fact wins" rule and reverts this swap.
        if hasattr(other, "source_header_fact"):
            merged = replace(  # type: ignore[type-var]
                merged, source_header_fact=other.source_header_fact
            )
    if merged.deprecated != deprecated:
        merged = replace(merged, deprecated=deprecated)  # type: ignore[type-var]
    return merged
