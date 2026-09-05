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

"""Reusable building blocks for diff detectors.

Detectors repeat two structural patterns:

* **Boolean attribute transitions** — "flag went from off→on / on→off"
  (e.g. ``noexcept`` added/removed, ``virtual`` added/removed). Each site
  used to hand-roll an ``if/elif`` pair around two near-identical
  ``Change`` constructions. :func:`bool_transition` collapses that into a
  single declarative call while preserving the bespoke wording and the
  tri-state (``None`` means "not recorded in this snapshot") skip rule.

* **Keyed map diffs** — "what was removed / added / present on both sides"
  over two ``{key: record}`` maps. :func:`diff_by_key` factors out the
  removed/added/common scaffold so a detector only supplies the per-bucket
  logic.

These helpers are deliberately small and behavior-preserving: they encode
the shape that was already duplicated across the ``diff_*`` modules, not
new policy.
"""

from __future__ import annotations

import re
from collections.abc import Callable, ItemsView, Iterable, Iterator, Mapping, ValuesView
from typing import Any, Protocol, TypeVar, cast

from .change_registry import REGISTRY
from .checker_policy import ChangeKind
from .checker_types import Change
from .compare.dedup_key import hashable_value
from .fact_provenance import (
    both_known_backed_fact_qualified,
    same_producer_backed_fact_qualified,
)
from .model import AbiSnapshot

# Imported directly from the canonical model-layer location (ADR-061 D9's
# target owner for this catalog logic) rather than via change_registry's own
# re-export, so this doesn't grow change_registry.py past its 2000-line
# adoption-debt ceiling for a name nothing external currently imports
# through that path.
from .model.change_catalog.registry import TEMPLATE_VOCAB as TEMPLATE_VOCAB
from .model.identity import EntityId
from .model.qualified_name_split import iter_top_level_chars
from .qualified_name_segments import strip_inline_abi_namespaces

K = TypeVar("K")
V = TypeVar("V")
W = TypeVar("W")

# Sentinel detection for enum members is name-pattern based, not value based:
# a max-value heuristic accidentally downgrades an ordinary member that merely
# happens to hold the largest value in an evolving enum.
_SENTINEL_SUFFIXES = ("_last", "_max", "_count")
_SENTINEL_NAMES = frozenset({"last", "max", "count"})


def is_sentinel_enum_member(member_name: str) -> bool:
    """True for a conventional enum *sentinel* member (``*_LAST``/``*_MAX``/``*_COUNT``).

    Shared by the enum-member detectors in ``diff_types`` (header/DWARF enums)
    and ``diff_platform`` (platform enums) so both classify the same names as
    sentinels; each previously carried its own byte-identical copy, redefined
    once per loop iteration.
    """
    n = member_name.lower()
    return n.endswith(_SENTINEL_SUFFIXES) or n in _SENTINEL_NAMES


def make_change(
    kind: ChangeKind,
    *,
    symbol: str,
    name: str | None = None,
    old: str | None = None,
    new: str | None = None,
    detail: str | None = None,
    description: str | None = None,
    **change_kwargs: Any,
) -> Change:
    """Build a :class:`Change`, formatting its description from the registry.

    The C6 *change factory*: a thin wrapper over the :class:`Change` dataclass
    that keeps a kind's description wording next to its verdict/impact in
    ``change_registry`` instead of hand-rolled at the call site.

    * When ``description`` is given it is used verbatim — the *bespoke* path,
      first-class for findings whose text embeds computed offsets, demangled
      signatures, vtable slot indices, counts, … that no fixed template fits.
    * Otherwise the kind's ``description_template`` is looked up and formatted
      from the ``{symbol} {name} {old} {new} {detail}`` vocabulary. A kind with
      neither a template nor an explicit ``description`` is a programming error
      and raises :class:`ValueError`.

    ``old`` / ``new`` also populate ``Change.old_value`` / ``Change.new_value``
    unless those keys are passed explicitly in ``change_kwargs``. Any remaining
    ``change_kwargs`` (``caused_by_type``, ``confidence``, ``affected_symbols``,
    …) are forwarded to :class:`Change` unchanged.
    """
    if description is None:
        template = REGISTRY.description_template_for(kind.value)
        if template is None:
            raise ValueError(
                f"make_change({kind.value!r}) requires an explicit description= "
                "(no description_template registered for this kind)"
            )
        description = template.format(
            symbol=symbol, name=name, old=old, new=new, detail=detail
        )
    change_kwargs.setdefault("old_value", old)
    change_kwargs.setdefault("new_value", new)
    return Change(kind=kind, symbol=symbol, description=description, **change_kwargs)


# Sentinel distinguishing "key absent" from "key present with value None".
# Typed as Any so it can stand in for a ``W`` in the get() default without
# upsetting the type checker.
_MISSING: Any = object()

# A (ChangeKind, description) pair describing one direction of a transition.
TransitionSpec = tuple[ChangeKind, str]


def bool_transition(
    old_val: bool | None,
    new_val: bool | None,
    symbol: str,
    *,
    added: TransitionSpec | None = None,
    removed: TransitionSpec | None = None,
    added_values: tuple[str | None, str | None] = (None, None),
    removed_values: tuple[str | None, str | None] = (None, None),
    skip_none: bool = False,
    caused_by_type: str | None = None,
    entity_id: EntityId | None = None,
) -> list[Change]:
    """Emit a :class:`Change` for a boolean attribute transition.

    ``added`` fires on a ``False → True`` transition, ``removed`` on
    ``True → False``. Each is an optional ``(kind, description)`` pair; a
    direction with no spec is simply not reported.

    ``added_values`` / ``removed_values`` supply the ``(old_value,
    new_value)`` strings recorded on the emitted change for that direction
    (defaulting to ``(None, None)`` for flags whose before/after wording is
    carried entirely by the description).

    When ``skip_none`` is set, a ``None`` on *either* side suppresses
    emission. This models tri-state attributes (e.g. ``is_explicit``,
    ``is_hidden_friend``) where ``None`` means the value was not recorded in
    one snapshot — typically an older snapshot predating the field — and
    must not be mistaken for ``False``.

    ``caused_by_type`` is recorded on the emitted change's ``caused_by_type``
    field when given — used by hidden-friend transitions to carry the
    befriending class's qualified name, so surface classification can key
    demotion off the *owner's* header origin.

    ``entity_id`` (ADR-063 Phase 2) passes through to ``Change.entity_id``
    unchanged -- resolving one is entirely the caller's job.
    """
    if skip_none and (old_val is None or new_val is None):
        return []
    if not old_val and new_val and added is not None:
        kind, description = added
        ov, nv = added_values
        return [
            Change(
                kind=kind,
                symbol=symbol,
                description=description,
                old_value=ov,
                new_value=nv,
                caused_by_type=caused_by_type,
                entity_id=entity_id,
            )
        ]
    if old_val and not new_val and removed is not None:
        kind, description = removed
        ov, nv = removed_values
        return [
            Change(
                kind=kind,
                symbol=symbol,
                description=description,
                old_value=ov,
                new_value=nv,
                caused_by_type=caused_by_type,
                entity_id=entity_id,
            )
        ]
    return []


def diff_by_key(
    old_map: Mapping[K, V],
    new_map: Mapping[K, W],
    *,
    on_removed: Callable[[K, V], Iterable[Change]] | None = None,
    on_added: Callable[[K, W], Iterable[Change]] | None = None,
    on_common: Callable[[K, V, W], Iterable[Change]] | None = None,
) -> list[Change]:
    """Diff two keyed maps, dispatching to per-bucket callbacks.

    For every key present only in ``old_map`` ``on_removed(key, old)`` is
    invoked; for keys only in ``new_map`` ``on_added(key, new)``; for keys
    in both ``on_common(key, old, new)``. Each callback returns an iterable
    of :class:`Change` (or nothing); omitted callbacks skip that bucket.

    Removed/common keys are visited in ``old_map`` iteration order and
    added keys in ``new_map`` order, matching the hand-written loops this
    replaces so change ordering is unchanged.
    """
    changes: list[Change] = []
    for key, old_val in old_map.items():
        new_val = new_map.get(key, _MISSING)
        if new_val is _MISSING:
            if on_removed is not None:
                changes.extend(on_removed(key, old_val))
        elif on_common is not None:
            changes.extend(on_common(key, old_val, cast(W, new_val)))
    for key, new_val in new_map.items():
        if key not in old_map and on_added is not None:
            changes.extend(on_added(key, new_val))
    return changes


# ── Type-level old/new matching (moved out of diff_types.py, PR #608) ── Generalized (PR #608 follow-up) over any entity kind that has the same bare-``name`` / optional-``qualified_name`` split — ``RecordType`` was the original motivating case, ``EnumType`` shares the identical ambiguity (two distinct enums sharing a bare leaf name in different namespaces) and the identical fix, so both are expressed as one generic implementation via the ``_QualifiedNamed`` structural protocol rather than duplicating ``TypeMap`` per entity kind.


class _QualifiedNamed(Protocol):
    name: str
    qualified_name: str | None


Q = TypeVar("Q", bound=_QualifiedNamed)


def type_map_key(t: _QualifiedNamed) -> str:
    """Key a ``RecordType``/``EnumType`` for old/new matching by its
    namespace-qualified identity, not its bare declaration name.

    The header-mode dumpers (castxml, clang) keep ``t.name`` bare (see its
    docstring in model.py) and carry the real namespace path in
    ``t.qualified_name`` instead; DWARF has no such split and already
    stores the qualified spelling directly in ``name``. Matching by bare
    ``t.name`` alone lets two unrelated types sharing only a short/leaf
    spelling collide and diff against each other, producing spurious
    field/base-class findings. Falls back to ``t.name`` when
    ``qualified_name`` is unset (global-scope/DWARF-only snapshots).
    """
    return t.qualified_name or t.name


class TypeMap(Mapping[str, Q]):
    """An old/new matching map (``RecordType`` or ``EnumType``) keyed by
    :func:`type_map_key`, with a collision-safe bare-``name`` alias used
    only for lookups.

    The alias exists for schema-evolution compatibility: an older snapshot
    that predates (or never populates) ``qualified_name`` keys its entries
    by the bare name alone, so without an alias, matching it against a
    freshly-dumped side that DOES carry ``qualified_name`` would key the two
    sides differently (``Foo`` vs. ``ns::Foo``) and manufacture a false
    ``TYPE_REMOVED``/``TYPE_ADDED`` pair for an unchanged type (PR #608).

    Only used for ``get``/``in`` — kept OUT of ``items``/``values``/
    iteration (else a dict containing both the qualified key and the bare
    alias for one object would make every ``for name, t in
    old_map.items()``-style loop process it twice) — and only added when
    the bare name is unambiguous within *this* snapshot, so it cannot
    reopen the collision :func:`type_map_key` fixes.
    """

    def __init__(self, types: Iterable[Q]) -> None:
        self._primary: dict[str, Q] = {}
        self._bare_owner: dict[str, str | None] = {}
        for t in types:
            key = type_map_key(t)
            self._primary[key] = t
            bare = t.name
            if bare in self._bare_owner:
                if self._bare_owner[bare] != key:
                    self._bare_owner[bare] = (
                        None  # ambiguous: >1 distinct qualified identity
                    )
            else:
                self._bare_owner[bare] = key
        self._bare_alias: dict[str, str] = {
            bare: key
            for bare, key in self._bare_owner.items()
            if key is not None and bare not in self._primary
        }

    def bare_name_is_unambiguous(self, bare: str) -> bool:
        """True if exactly one distinct qualified identity in this map
        shares the bare declaration name *bare* (including the trivial case
        of a single global-scope type whose own key already equals its bare
        name). False for "no type has this bare name" and "two-or-more
        *distinct* qualified identities share it" alike — both are unsafe to
        treat as a single unambiguous target.
        """
        return self._bare_owner.get(bare) is not None

    def __getitem__(self, key: str) -> Q:
        # get()/__contains__ come from the Mapping mixin, implemented in
        # terms of this — alias resolution lives in exactly one place.
        t = self._primary.get(key)
        if t is not None:
            return t
        alias_key = self._bare_alias.get(key)
        if alias_key is not None:
            # _bare_alias values are always keys already present in
            # _primary (built from it, see __init__) -- a plain indexing
            # KeyError here would indicate a construction bug, not a normal
            # "key absent" case.
            return self._primary[alias_key]
        raise KeyError(key)

    def __len__(self) -> int:
        return len(self._primary)

    def __iter__(self) -> Iterator[str]:
        return iter(self._primary)

    def items(self) -> ItemsView[str, Q]:
        return self._primary.items()

    def values(self) -> ValuesView[Q]:
        return self._primary.values()


def build_type_map(types: Iterable[Q]) -> TypeMap[Q]:
    return TypeMap(types)


def typedef_diff_maps(
    old: AbiSnapshot, new: AbiSnapshot
) -> tuple[dict[str, str], dict[str, str]]:
    """Return the ``(old, new)`` alias->underlying-type maps to diff typedefs
    over, preferring the qualified-name-keyed twin when both sides carry one.

    ``AbiSnapshot.typedefs`` is keyed by *bare* (unqualified) name on both
    header backends, so two distinct member/nested typedefs that happen to
    share a bare spelling in different classes/namespaces (e.g. two unrelated
    ``impl_value_t`` member aliases on different classes) silently collapse
    onto one dict entry, whichever declaration the backend visits last
    winning (see that field's own docstring in ``model.py`` for the full
    incident history). Diffing that collapsed dict directly means an
    unrelated class gaining or losing its own same-named alias can flip the
    surviving entry's recorded value, fabricating a spurious
    ``TYPEDEF_BASE_CHANGED`` for a typedef that never itself changed.

    ``AbiSnapshot.typedefs_qualified`` (schema v25) carries the identical set
    of typedef declarations keyed by qualified name instead, unique per
    declaration and therefore immune to this collision. A side "trusts" its
    own qualified map when that map is non-empty, OR its legacy bare map is
    itself empty (a side with zero typedefs total loses nothing by reporting
    zero qualified ones either, whether or not it actually populates the
    field) -- this is what lets an old side with real qualified typedefs
    still enumerate every one of them as removed when the new side has
    genuinely stripped all typedefs (rather than merely never populating the
    field), instead of collapsing to the legacy bare map and losing
    per-declaration granularity purely because the empty side's qualified
    dict is indistinguishable, by non-emptiness alone, from "unsupported"
    (Codex review). Used only when *both* sides trust their own map this
    way; falls back to the legacy bare maps otherwise (a DWARF-only or
    pre-v25 snapshot with real typedefs) so that comparison path is
    unaffected.
    """
    if typedef_side_trusts_qualified(old) and typedef_side_trusts_qualified(new):
        return old.typedefs_qualified, new.typedefs_qualified
    return old.typedefs, new.typedefs


def typedef_side_trusts_qualified(snapshot: AbiSnapshot) -> bool:
    """One side of `typedef_diff_maps`'s trust rule, split out (Codex review, PR #1078) for `compare.typedefs.typedef_index_pair` to reuse."""
    return bool(snapshot.typedefs_qualified) or not snapshot.typedefs


def lookup_matched_type(own: TypeMap[Q], other: TypeMap[Q], t: Q) -> Q | None:
    """Look up *t*'s counterpart in *other* (the opposite old/new ``TypeMap``
    from the one *t* itself came from, ``own``), trying both *t*'s own
    qualified matching key and its bare declaration name.

    ``TypeMap``'s bare-name alias only maps bare -> qualified (see its
    docstring): a legacy snapshot keyed by the bare name resolves fine
    against a *fresh* qualified-keyed counterpart, because the fresh side's
    map carries that alias. But there is no reverse qualified -> bare
    mapping, so when *t* itself comes from the *fresh* (qualified-keyed) side
    and *other* is the *legacy* one, looking ``other`` up by ``type_map_key(t)``
    alone misses — ``other`` only has the bare key, never learns the
    qualified spelling. Retrying with the bare name makes the schema-
    evolution compatibility symmetric regardless of which side is legacy
    (Codex review, PR #608).

    That bare-name retry is only safe when *t*'s own bare name is itself
    unambiguous within ``own`` — i.e. *t* is the one and only type in its own
    snapshot with that bare spelling. Without this check, a genuine
    same-leaf-name collision on the probing side (e.g. old ``ns1::Impl`` +
    ``ns2::Impl`` vs. a new side that only kept ``ns2::Impl``) would retry
    ``ns1::Impl``'s failed qualified lookup with the bare name ``Impl``,
    hit ``other``'s alias for the *unrelated* surviving ``ns2::Impl``, and
    diff two distinct types against each other — reopening the exact
    short/leaf-name collision ``type_map_key`` was introduced to fix, this
    time through the compatibility fallback instead of naive bare matching
    (Codex review, PR #608, second round).

    That unambiguity check is necessary but **not sufficient**, and the
    missing half is what this function's second fix closes. The retry exists
    for exactly one situation — *other* is a legacy side that never recorded
    a qualified identity for this type — but it used to fire whenever the
    qualified lookup missed, *including* when both sides carry full,
    genuinely different qualified identities. A real namespace move
    (oneTBB 2022's flow graph, ``tbb::detail::d1::graph`` ->
    ``tbb::detail::d2::graph``: distinct types, distinct mangled vtable
    symbols, no declaration in common) is precisely that shape: each side
    holds exactly one type whose bare name is ``graph``, so both bare names
    are "unambiguous" within their own snapshot, the retry hits the other
    side's bare alias, and two unrelated types are diffed against each other
    — manufacturing ``TYPE_SIZE_CHANGED`` / ``TYPE_FIELD_OFFSET_CHANGED`` /
    ``TYPE_VTABLE_CHANGED`` for a pair that is really one removal plus one
    addition. Unambiguity cannot see this: it answers "is this bare spelling
    claimed once here", never "did the other side actually fail to record a
    qualified name".

    So the candidate the retry finds is accepted in exactly two shapes:

    * the candidate is *legacy-shaped* — its own matching key equals its bare
      declaration name, i.e. it carries no distinct qualified identity. That
      is the schema-evolution case the alias was introduced for.
    * the two qualified identities are equal once *inline ABI-tag
      namespaces* are stripped from both
      (:func:`qualified_name_segments.strip_inline_abi_namespaces`) —
      ``std::basic_string<...>`` vs. libstdc++'s dual-ABI
      ``std::__cxx11::basic_string<...>``, or a versioned ``ns::v1::X``
      vs. ``ns::X``. An inline namespace is transparent for name lookup, so
      the two spellings name one entity and a real layout change between
      them (the dual-ABI size change) is a mutation, not a removal plus an
      addition. The stripping is deliberately narrow: only the version-shaped
      (``v1``/``__1``) and named toolchain (``__cxx11``/``__ndk1``) tags
      qualify, never an ordinary implementation namespace such as
      ``detail``, ``impl`` or oneTBB's ``d1`` — renaming one of those really
      does move every declaration inside it.

    Anything else — a side that DID record ``ns::Foo`` and simply does not
    contain *t* — is reported as a non-match, which is the truth.
    """
    key = type_map_key(t)
    found = other.get(key)
    if found is not None:
        return found
    if t.name != key and own.bare_name_is_unambiguous(t.name):
        candidate = other.get(t.name)
        if candidate is None:
            return None
        candidate_key = type_map_key(candidate)
        if candidate_key == candidate.name or strip_inline_abi_namespaces(
            key
        ) == strip_inline_abi_namespaces(candidate_key):
            return candidate
    return None


def fact_known_qualified(
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_map: TypeMap[Any],
    new_map: TypeMap[Any],
    name: str,
    old_qualified_key: str,
    new_qualified_key: str,
    bare_key: str,
) -> bool:
    """:func:`fact_provenance.both_known_backed_fact_qualified`, deriving its
    ambiguity flags from *old_map*/*new_map* (``TypeMap.bare_name_is_unambiguous``)
    — same bare-name-retry shape as :func:`lookup_matched_type` above, applied
    to a fact-provenance dict key instead of an old/new type match. Takes
    *old_qualified_key*/*new_qualified_key* separately (not derived from
    *name* alone) since a matched pair's two sides can carry different
    qualified identities."""
    return both_known_backed_fact_qualified(
        old,
        new,
        old_qualified_key,
        new_qualified_key,
        bare_key,
        old_bare_unambiguous=old_map.bare_name_is_unambiguous(name),
        new_bare_unambiguous=new_map.bare_name_is_unambiguous(name),
    )


def fact_same_producer_qualified(
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_map: TypeMap[Any],
    new_map: TypeMap[Any],
    name: str,
    old_qualified_key: str,
    new_qualified_key: str,
    bare_key: str,
) -> bool:
    """:func:`fact_provenance.same_producer_backed_fact_qualified`, deriving
    its ambiguity flags exactly as :func:`fact_known_qualified` above does.

    The gate for a fact both header backends populate with values that are
    NOT cross-comparable (``TypeField.default``) rather than one whose values
    are (``deprecated``/``is_scoped``) — see that function's own docstring for
    why the two need different answers.
    """
    return same_producer_backed_fact_qualified(
        old,
        new,
        old_qualified_key,
        new_qualified_key,
        bare_key,
        old_bare_unambiguous=old_map.bare_name_is_unambiguous(name),
        new_bare_unambiguous=new_map.bare_name_is_unambiguous(name),
    )


def depth_aware_bare_name(qualified: str) -> str:
    """The innermost, fully-unqualified leaf of a ``::``-qualified name.

    Splits only on a top-level ``"::"`` -- via
    :func:`~abicheck.model.qualified_name_split.iter_top_level_chars`,
    which tracks a bracket-KIND-aware stack over ``()``/``[]``/``<>`` and
    quoted literals -- so ``"Wrapper<dep::Tag>"``'s own ``::`` isn't
    mistaken for the outer boundary, and neither is one inside a non-type
    template argument's own parenthesized/bracketed/quoted expression
    (Codex review on PR #1041, several rounds). A small, local caller of
    that shared primitive rather than a second copy of the splitting loop
    itself: ``type_reachability_spelling._bare_type_name`` would be the
    natural sibling, but that module imports ``diff_cxx_rules``, which
    imports this one, so importing it here would add a real cycle."""
    last_split = 0
    for i, ch in iter_top_level_chars(qualified):
        if ch == ":" and qualified[i + 1 : i + 2] == ":":
            last_split = i + 2
    return qualified[last_split:]


def record_canonical_names(snap: AbiSnapshot | None) -> dict[str, str]:
    """Bare/qualified record-type name -> canonical (qualified-if-known)
    spelling, mirroring ``diff_filtering._enum_canonical_names`` exactly
    but for ``RecordType`` (struct/class/union) instead of ``EnumType``.

    Bridges two independent, individually-correct conventions this
    codebase's two struct/type detectors use for a ``Change.symbol``:
    ``diff_types._diff_type_pair`` (L2 header/castxml tier) always keys off
    ``RecordType.name`` — deliberately bare (see that call site's own
    comment: "Keeping emitted symbols bare preserves the identity every
    other consumer already keys on") — while
    ``diff_platform._diff_struct_layouts``/``_process_struct`` (L1 DWARF
    tier) keys off ``dwarf_metadata``'s own dict, whose keys are always
    fully qualified (``_process_struct``: ``f"{scope_prefix}::{name}"``).
    Without this bridge, a namespaced type's ``STRUCT_SIZE_CHANGED``/
    ``STRUCT_ALIGNMENT_CHANGED`` (DWARF, qualified) and
    ``TYPE_SIZE_CHANGED``/``TYPE_ALIGNMENT_CHANGED`` (header, bare) never
    resolve to the same identity, so neither
    ``diff_filtering._dedup_cross_kind``'s exact-symbol match nor
    ``_deduplicate_cross_detector``'s ``resolve_change_identity``-keyed
    dedup can recognize them as the same finding — exactly the class of
    duplicate the enum bridge above was built to close, left open for
    every other kind pair ``_DWARF_TO_AST_EQUIV`` maps (struct size/
    alignment AND the three field-level kinds, since a field-qualified
    symbol's own type-name prefix carries the identical mismatch).

    A bare name is registered only when it uniquely identifies one
    qualified type — the same disambiguation rule
    ``_enum_canonical_names`` uses, for the identical reason: two distinct
    types sharing a bare name (e.g. ``a::Widget`` and ``b::Widget``) must
    never have one silently bridged to the other's qualified spelling. An
    unqualified (global-namespace) record sharing that bare name is itself
    a competing identity for this check -- skipping it entirely, as an
    earlier revision did, let a global ``Widget`` alongside a namespaced
    ``ns::Widget`` silently register ``Widget -> ns::Widget``, which could
    then wrongly canonicalize the global type's own finding onto the
    unrelated namespaced one (Codex review, fresh evidence). This same
    competitor check also scans ``snap.dwarf.structs``' own keys directly:
    a record that DWARF sees but the header surface never exposes (private,
    not header-declared) contributes no ``snap.types`` entry at all, so a
    bare global name there is exactly as competing as an unqualified
    ``RecordType`` (Codex review, fresh evidence -- reachable when supplied
    headers omit a private record that remains in binary debug metadata).
    """
    if snap is None:
        return {}
    by_bare: dict[str, set[str | None]] = {}
    out: dict[str, str] = {}
    for t in getattr(snap, "types", None) or ():
        if t.qualified_name:
            by_bare.setdefault(t.name, set()).add(t.qualified_name)
            out[t.qualified_name] = t.qualified_name
        else:
            by_bare.setdefault(t.name, set()).add(None)
    dwarf = getattr(snap, "dwarf", None)
    for key in getattr(dwarf, "structs", None) or ():
        bare = depth_aware_bare_name(key)
        if bare != key:
            by_bare.setdefault(bare, set()).add(key)
        else:
            by_bare.setdefault(key, set()).add(None)
    for bare, qualified_names in by_bare.items():
        if len(qualified_names) == 1:
            candidate = next(iter(qualified_names))
            if candidate is not None:
                out[bare] = candidate
    return out


def canonicalize_record_symbol(
    symbol: str,
    record_names: Mapping[str, str],
    qualified_hint: str | None = None,
    field_name: str | None = None,
) -> str:
    """Canonicalize a struct/type-kind ``Change.symbol`` via *record_names*
    (see :func:`record_canonical_names`), so a DWARF-tier qualified
    spelling and an AST-tier bare spelling for the same type resolve to
    the same string before an exact-match dedup compares them.

    Handles both a whole-type symbol (``"Widget"``) and a field-qualified
    one (``"Widget::x"``, for the three field-level kinds in
    ``diff_filtering._DWARF_TO_AST_EQUIV``) — only the type-name portion
    is ever rewritten, never the field name itself.

    *field_name* (``Change.field_name``) is the ONLY signal used to decide
    whether *symbol* is field-qualified — never a bare ``"::" in symbol``
    guess (Codex review, fresh evidence): a scoped *whole-type* symbol like
    a template specialization over a namespaced argument
    (``"Wrapper<dep::Tag>"``) also contains ``"::"`` without being
    field-qualified at all, and the old guess corrupted it into
    ``"Wrapper<dep::Tag>::Tag>"``. When *field_name* is given, only the
    literal ``f"::{field_name}"`` suffix (if present) is ever split off;
    when it's ``None``, *symbol* is never split, regardless of how many
    ``"::"`` it contains.

    *qualified_hint* (``Change.qualified_name``, when the emitting detector
    already knows exactly which type it matched) takes priority over the
    *record_names* table and is tried first: two distinct types sharing an
    ambiguous bare name (``a::Widget``/``b::Widget`` both bare ``Widget``)
    make *record_names* correctly decline to bridge that bare name at all
    (see :func:`record_canonical_names`), which would otherwise leave a
    perfectly well-identified AST-tier finding un-bridgeable to its
    DWARF-tier counterpart purely because of an unrelated type elsewhere in
    the snapshot (Codex review). A symbol with no bridging information at
    all (no hint, and an unrecognized or ambiguous bare name) is returned
    unchanged, the same conservative default :func:`record_canonical_names`
    uses.
    """
    suffix = f"::{field_name}" if field_name is not None else None
    parent = (
        symbol[: -len(suffix)]
        if suffix is not None and symbol.endswith(suffix)
        else symbol
    )
    if qualified_hint is not None:
        canonical_parent = qualified_hint
    else:
        canonical_parent = record_names.get(parent, parent)
    return f"{canonical_parent}{suffix}" if suffix is not None else canonical_parent


# Synthesized placeholder names for anonymous/unnamed aggregate member types, which differ across DWARF / castxml / PDB readers (``<unnamed-tag>``, ``<unnamed-type-u>``, ``<anonymous union>``, ``<unnamed struct at …>``, …). The aggregate *kind* (when the placeholder names one) is captured so a real union→struct change is preserved while the unstable identifier suffix is not.
_ANON_TYPE_RE = re.compile(
    r"<\s*(?:unnamed|anonymous)(?:\s+(union|struct|class|enum)\b)?", re.IGNORECASE
)


def _normalize_type_name(name: str) -> str:
    """Normalize a C/C++ type name for stable DWARF↔castxml comparison: strips whitespace, CV-qualifiers, pointer/reference decorations, and 'struct'/'class'/'union' tag keywords ("struct Foo" -> "Foo", "const struct Foo *" -> "Foo"). Lossy by design, for comparison only -- Change.old_value/new_value keep the original spelling. Moved here from diff_platform.py (Codex review): cross_tier_transition below needs a sibling of it, and diff_platform.py already imports from this module, so importing back from there would grow the import-cycle-growth gate's tracked SCC."""
    return _normalize_type_spelling(name, strip_indirection=True)


def _normalize_type_spelling(name: str, *, strip_indirection: bool) -> str:
    """Shared implementation behind ``_normalize_type_name`` and the cross-tier type-spelling comparison in ``cross_tier_transition``.

    *strip_indirection* controls whether trailing pointer/reference sigils are dropped. ``_normalize_type_name``'s own same-tier callers want them dropped (a pointee cv-qualifier change like ``char *`` -> ``const char *`` is source churn, not a layout break). Cross-tier comparison must NOT drop them (Codex review, fresh evidence): stripping ``*``/``&`` made ``Foo * -> Bar *`` (DWARF) and ``Foo -> Bar`` (header) compare equal, silently hiding a genuine indirection-level disagreement between the two tiers' own evidence -- exactly the class of bug this whole value-agreement gate exists to catch.
    """
    s = name.strip()
    # Collapse whitespace directly touching a pointer/reference sigil ("Foo *" / "Foo* " / "Foo * *") to a single canonical spelling *before* any other step, regardless of strip_indirection -- when indirection is kept (cross-tier comparison), this is the difference between correctly matching "Foo*" (header) against "Foo *" (DWARF) and wrongly treating a pure spacing difference as a real indirection-level disagreement (Codex review, fresh evidence: preserving "*"/"&" for the indirection fix above still needs their surrounding whitespace normalized).
    s = re.sub(r"\s*([*&])\s*", r"\1", s).strip()
    if strip_indirection:
        # Remove trailing pointer/reference decorators and CV-qualifiers
        s = re.sub(r"[\s*&]+$", "", s).strip()
    # Remove leading CV-qualifiers
    s = re.sub(r"^(const|volatile)(\s+(const|volatile))?\s+", "", s).strip()
    # Remove struct/class/union tag keyword, remembering it: for an anonymous
    # placeholder spelled with a *leading* tag ("union <anonymous>") the tag
    # carries the aggregate kind, which must survive the collapse below.
    lead = re.match(r"^(struct|class|union)\s+", s)
    lead_kind = lead.group(1) if lead else None
    if lead:
        s = s[lead.end() :].strip()
    # Anonymous/unnamed member types have no stable *name* across DWARF / castxml / PDB extraction — the same anonymous union can be spelled "<unnamed-tag>" by one reader and "Parent::<unnamed-type-u>" by another (observed on the Windows SDK _TP_CALLBACK_ENVIRON_V3::u between two MSVC builds). Collapse those placeholders to a token keyed on the aggregate *kind* — taken from the placeholder itself ("<anonymous union>") or the leading tag ("union <anonymous>") — so the unstable identifier suffix no longer drives a false positive while a genuine kind change (anonymous union → anonymous struct) is still reported. Size drift remains caught by the separate byte_size comparison.
    anon = _ANON_TYPE_RE.search(s)
    if anon is not None:
        kind = anon.group(1) or lead_kind
        return f"<anonymous {kind.lower()}>" if kind else "<anonymous>"
    return s


# DWARF-tier kinds whose old_value/new_value are byte-based (DW_AT_byte_size/
# DW_AT_alignment/byte_offset), unlike their AST-tier equivalent (diff_
# filtering._DWARF_TO_AST_EQUIV), which is always bit-based (RecordType.
# size_bits/alignment_bits, TypeField.offset_bits) -- see diff_platform.py's/
# diff_types.py's own construction sites. A transition comparison across
# tiers must convert one side before comparing, or a genuinely identical
# transition (e.g. 64 -> 96 bytes vs. 512 -> 768 bits) would never match.
_DWARF_BYTE_VALUE_KINDS = frozenset(
    {
        ChangeKind.STRUCT_SIZE_CHANGED,
        ChangeKind.STRUCT_ALIGNMENT_CHANGED,
        ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
    }
)

# Kinds whose old_value/new_value hold a C/C++ type spelling rather than a byte/bit count -- compared via _normalize_type_name (already built for exactly this DWARF<->castxml spelling gap) rather than raw string equality, so "struct Foo *" and "Foo *" aren't treated as disagreeing transitions.
_TYPE_SPELLING_VALUE_KINDS = frozenset(
    {
        ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
        ChangeKind.TYPE_FIELD_TYPE_CHANGED,
    }
)


def _bits_str_from_bytes_str(value: str | None) -> str | None:
    """Convert a plain byte-count string to its bit-count string equivalent.

    Defensive on anything that isn't a plain integer (returns it unchanged)
    -- a value this shape-mismatched can never legitimately equal the AST
    tier's own bit-based value anyway, so failing the comparison closed
    (not matching, i.e. keeping both findings) is the safe outcome.
    """
    if value is None:
        return None
    try:
        return str(int(value) * 8)
    except ValueError:
        return value


def _malformed_unit_typed_transition(
    kind: ChangeKind, old: object, new: object
) -> tuple[object, object] | None:
    """None for the plain str|None shape byte/spelling conversions require, else a kind-tagged fallback so malformed bytes/bits transitions never wrongly compare equal."""
    ok = (old is None or isinstance(old, str)) and (new is None or isinstance(new, str))
    return None if ok else ((kind, hashable_value(old)), (kind, hashable_value(new)))


def cross_tier_transition(c: Change) -> tuple[object, object] | None:
    """The (old_value, new_value) pair to require agreement on across tiers.

    Returns ``None`` for a kind with no independent transition to disagree
    about (a removal reports only that the field is gone, on both tiers
    alike) -- the caller then dedups on kind+symbol alone, same as before
    this value gate existed (Codex review: a cross-tier dedup that only
    matched on kind+symbol could silently drop a DWARF finding whose own
    old/new values genuinely disagreed with the AST finding's -- e.g. header
    evidence reporting a size change 64->128 while DWARF reports 64->96).

    The caller keys on this result through a ``set``, so a value slot
    returned unchanged goes through :func:`~abicheck.compare.dedup_key.
    hashable_value` -- the annotation on those slots is not enforced and
    real detectors do store lists in them.
    """
    if c.kind in (ChangeKind.STRUCT_FIELD_REMOVED, ChangeKind.TYPE_FIELD_REMOVED):
        return None
    old, new = c.old_value, c.new_value
    if c.kind in _DWARF_BYTE_VALUE_KINDS or c.kind in _TYPE_SPELLING_VALUE_KINDS:
        if (m := _malformed_unit_typed_transition(c.kind, old, new)) is not None:
            return m
    if c.kind in _DWARF_BYTE_VALUE_KINDS:
        return _bits_str_from_bytes_str(old), _bits_str_from_bytes_str(new)
    if c.kind in _TYPE_SPELLING_VALUE_KINDS:
        return (
            _normalize_type_spelling(old, strip_indirection=False)
            if old is not None
            else None,
            _normalize_type_spelling(new, strip_indirection=False)
            if new is not None
            else None,
        )
    return hashable_value(old), hashable_value(new)
