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

from collections.abc import Callable, ItemsView, Iterable, Iterator, Mapping, ValuesView
from typing import Any, Protocol, TypeVar, cast

from .change_registry import REGISTRY
from .checker_policy import ChangeKind
from .checker_types import Change

K = TypeVar("K")
V = TypeVar("V")
W = TypeVar("W")

# Fixed placeholder vocabulary a ``ChangeKind.description_template`` may use
# (C6). ``make_change`` formats the template from exactly these structured
# fields, so the wording for a kind is owned by the registry rather than
# reinvented at each call site:
#   {symbol} — the mangled / exported symbol (or type) name (the Change.symbol)
#   {name}   — the human-facing declared name (demangled, e.g. ``f_old.name``)
#   {old}    — old value (also populates Change.old_value unless overridden)
#   {new}    — new value (also populates Change.new_value unless overridden)
#   {detail} — any extra computed snippet the template wants to interpolate
TEMPLATE_VOCAB = frozenset({"symbol", "name", "old", "new", "detail"})


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


# ── Type-level old/new matching (moved out of diff_types.py, PR #608) ──────
#
# Generalized (PR #608 follow-up) over any entity kind that has the same
# bare-``name`` / optional-``qualified_name`` split — ``RecordType`` was the
# original motivating case, ``EnumType`` shares the identical ambiguity
# (two distinct enums sharing a bare leaf name in different namespaces) and
# the identical fix, so both are expressed as one generic implementation
# via the ``_QualifiedNamed`` structural protocol rather than duplicating
# ``TypeMap`` per entity kind.


class _QualifiedNamed(Protocol):
    name: str
    qualified_name: str | None


Q = TypeVar("Q", bound=_QualifiedNamed)


def type_map_key(t: _QualifiedNamed) -> str:
    """Key a ``RecordType``/``EnumType`` for old/new matching by its
    namespace-qualified identity, not its bare declaration name.

    The header-mode dumpers (castxml, clang) deliberately keep ``t.name``
    bare (see its docstring in model.py) and carry the real namespace path in
    ``t.qualified_name`` instead; the DWARF backend has no such split and
    already stores the qualified spelling directly in ``name``. Matching
    old/new maps by bare ``t.name`` alone lets two unrelated types that only
    share a short/leaf spelling (e.g. two distinct ``std::*::_Impl`` template
    internals pulled in transitively) collide and diff against each other,
    producing spurious field/base-class findings. Falling back to ``t.name``
    when ``qualified_name`` is unset (global-scope types, DWARF-only
    snapshots) keeps existing behaviour unchanged there.
    """
    return t.qualified_name or t.name


class TypeMap(Mapping[str, Q]):
    """An old/new matching map (``RecordType`` or ``EnumType``) keyed by
    :func:`type_map_key`, with a collision-safe bare-``name`` alias used
    only for lookups.

    The alias exists for schema-evolution compatibility: an older serialized/
    header snapshot that predates ``qualified_name`` (or a producer that
    never populates it) keys its own map entries by the bare name alone.
    Without an alias, matching that against a freshly-dumped snapshot side
    where the same namespaced type *does* carry ``qualified_name`` would key
    the two sides differently (``Foo`` vs. ``ns::Foo``) and manufacture a
    false ``TYPE_REMOVED``/``TYPE_ADDED`` pair for an unchanged type (Codex
    review, PR #608).

    The alias is only used for ``get``/``in`` — deliberately kept OUT of
    ``items``/``values``/iteration, which stay one entry per type under its
    canonical key. A dict literally containing both the qualified key and a
    bare-name alias for the same object would make every ``for name, t in
    old_map.items()``-style detector loop process that type (and emit its
    finding) twice. It is also only added when the bare name is unambiguous
    within *this* snapshot — not already claimed by a distinct qualified
    identity — so it cannot reopen the short/leaf-name collision
    :func:`type_map_key` itself was introduced to fix.
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
    """
    key = type_map_key(t)
    found = other.get(key)
    if found is not None:
        return found
    if t.name != key and own.bare_name_is_unambiguous(t.name):
        return other.get(t.name)
    return None
