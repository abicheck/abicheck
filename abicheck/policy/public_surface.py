# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors
"""The public-surface relevance query's own declaration/type *indexing* and
*origin/ambiguity bookkeeping* (ADR-063 Phase 3 D5) -- the ``PublicSurface``
result type, plus the leaf half of the migrated closure-walk algorithm.

This is a **leaf module** with respect to ``surface.py``/``export_surface.py``
alike: it imports from neither, so both of them can safely import from here
(directly, or via the sibling module below) without forming a cycle.

**Split across two sibling files purely to stay under this repo's 800-line
new-file production cap** (mechanical extraction, not a redesign -- mirrors
the ``fact_detector_misuse.py``/``fact_detector_misuse_aliases.py`` split
``scripts/CLAUDE.md`` documents): this file owns the ``PublicSurface``
dataclass and ``_index_surface_types``' indexing/bookkeeping;
``policy/public_surface_closure.py`` owns the actual reachability walk
(``_seed_public_roots``/``_walk_type_closure``/``_walk_exact_type_closure``
and siblings) plus the real entry point, :func:`~abicheck.policy.
public_surface_closure.resolve_public_surface` -- import it from there, not
here. See that module's own docstring for the full accounting of what the
migration actually changed (the closure-walk's data source, moved from
independently re-parsing ``fn.return_type``/``rec.fields``/``rec.bases``/
typedef targets to reading ``compare/surface_graph.py``'s
``referenced_identifiers_by_node()`` -- a pure function of the snapshot's
own current declarations, computed fresh on every call, deliberately
**not** ``AbiSnapshot.surface_graph``'s own persisted node attrs, after two
further review rounds found the persisted graph could not be trusted for
this computation at all; see that module's own docstring for the full
security history) and what deliberately did not (this file's own
indexing/bookkeeping, still snapshot-field-derived -- see the docstring on
:func:`_index_surface_types` for why).

``surface.py``'s own ``compute_public_surface()`` is now a thin wrapper
delegating to ``policy/public_surface_closure.py``'s ``resolve_public_
surface()``, kept for its existing callers' call shape; ``surface.py``
re-exports this file's own ``PublicSurface`` (``PublicSurface as
PublicSurface``) so every existing ``from .surface import PublicSurface``/
``from abicheck.surface import PublicSurface`` call site is unaffected.

``policy -> compare``/``model`` is an already-allowed ADR-061 import edge.
This module itself imports nothing from ``surface.py``/``export_surface.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..model.vocabulary import ScopeOrigin

if TYPE_CHECKING:
    from ..model.entities import EnumType, RecordType
    from ..model.snapshot import AbiSnapshot

__all__ = [
    "PublicSurface",
    "PublicSurfaceQuery",  # noqa: F822 -- resolved by the __getattr__ shim below
    "PublicSurfaceResolution",
    "resolve_public_surface",  # noqa: F822 -- resolved by the __getattr__ shim below
]

# ── Leaf-local duplicate of surface._type_identifiers ───────────────────────
# Needed for exactly one remaining purpose in this module: extracting
# candidate name tokens from a public method's *owner class* spelling
# (``_seed_public_roots``'s owner-class seeding below), a plain string
# derived from mangled-name demangling, not a declaration/type's own
# signature -- so it has no ``referenced_identifiers`` graph entry to read
# instead -- plus the collision fallback in
# ``_referenced_identifiers_for_function``/``_for_variable``/``_for_record``.
# Duplicated rather than imported from ``surface.py`` (which still needs its
# own copy for classifying *findings*' type-name text), matching the same
# "leaf-safe duplicate" precedent ``compare/surface_graph.py`` already
# established for this identical function.
_TYPE_NOISE: frozenset[str] = frozenset(
    {
        "const",
        "volatile",
        "unsigned",
        "signed",
        "struct",
        "class",
        "union",
        "enum",
        "typename",
        "mutable",
        "restrict",
        "register",
        "void",
        "bool",
        "char",
        "short",
        "int",
        "long",
        "float",
        "double",
        "wchar_t",
        "char8_t",
        "char16_t",
        "char32_t",
    }
)
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_:]*")


def _type_identifiers(type_str: str | None) -> set[str]:
    if not type_str:
        return set()
    out: set[str] = set()
    for tok in _IDENT_RE.findall(type_str):
        if tok in _TYPE_NOISE:
            continue
        out.add(tok)
        if "::" in tok:
            out.add(tok.rsplit("::", 1)[1])
    return out


@dataclass
class PublicSurface:
    """Resolved public-ABI surface of a single snapshot.

    ``public_*`` sets are the public surface; ``all_*`` sets are the full
    universe (used to decide whether a finding is *about* a symbol vs a
    type at all). ``resolvable`` is ``False`` when no header-derived
    visibility exists, in which case scoping is skipped entirely.

    Moved here from ``surface.py`` (ADR-063 Phase 3 D5) alongside the
    closure-walk algorithm that fills it -- ``surface.py`` re-exports this
    class (``PublicSurface as PublicSurface``) so every existing
    ``from .surface import PublicSurface``/``from abicheck.surface import
    PublicSurface`` call site is unaffected.
    """

    public_symbols: set[str] = field(default_factory=set)
    all_symbols: set[str] = field(default_factory=set)
    public_types: set[str] = field(default_factory=set)
    all_types: set[str] = field(default_factory=set)
    # Typedef keys (``snapshot.typedefs``) actually resolved while walking the
    # type closure from a public root — a strict subset of ``snapshot.typedefs``.
    # Used by dump-time public-surface scoping (``dumper_scoping.py``) to decide
    # which typedef entries to keep; unrelated to classification/demotion, so it
    # has no analogue in the per-finding scoping this dataclass otherwise serves.
    public_typedefs: set[str] = field(default_factory=set)
    resolvable: bool = False
    # Origin (ADR-024 D1 / ADR-015 v6) keyed by every symbol key and type
    # name. Only populated when the snapshot was dumped with a public-header
    # set; otherwise every value is UNKNOWN and provenance reasons never fire.
    origin_by_key: dict[str, ScopeOrigin] = field(default_factory=dict)
    # Origin keyed by a type's *qualified* name (``RecordType.qualified_name``
    # / ``EnumType.qualified_name``), populated only for types that actually
    # carry one. ``origin_by_key`` is keyed by the deliberately-bare ``name``
    # (see model.py), so two distinct types sharing a leaf name in different
    # namespaces (``pub::Foo`` vs. ``priv::Foo``) collide there and their
    # origins merge conservatively (public wins). This index lets a caller
    # holding a fully-qualified owner identity (e.g. a hidden friend's
    # ``befriending`` class) resolve it exactly instead of falling into that
    # collision (Codex review).
    origin_by_qualified_key: dict[str, ScopeOrigin] = field(default_factory=dict)
    # Names (bare or full) that resolve to *more than one* record/enum in
    # this snapshot — the same collision ``origin_by_qualified_key`` exists
    # to route around, but that only helps when a qualified name was
    # actually recorded. When it wasn't (a producer that doesn't populate
    # ``qualified_name`` at all), a caller must know the plain ``origin_by_key``
    # lookup for such a name is unreliable (merged across unrelated types,
    # public wins conservatively) rather than trust it outright — see
    # :func:`_hidden_friend_owner_effective_origin` (Codex review). Computed
    # across records *and* enums combined, not per-kind: a private record
    # and an unrelated public enum sharing a bare name each look unique
    # within their own kind, but still collide in the single ``origin_by_key``
    # both kinds share (Codex review, thirteenth round).
    ambiguous_type_names: set[str] = field(default_factory=set)
    # Qualified (or, when a type carries no ``qualified_name``, bare) identity
    # of every record/enum reached by :func:`_walk_exact_type_closure` -- a
    # chain from a seed where *every* step resolved to exactly one candidate,
    # never through an ambiguous ``::``-tail fork (see that function's own
    # docstring for why this needs its own, separate, ambiguity-vetoing walk
    # rather than a flag computed inside :func:`_walk_type_closure`).
    # Distinguishes "this qualified identity was itself directly,
    # unambiguously referenced" from "this qualified identity was only swept
    # in because an unrelated sibling shares its bare tail" — the anti-hiding
    # rule means both routes leave an *identical* footprint in ``public_types``/
    # ``ambiguous_type_names`` alone, which is exactly the missing provenance
    # ``_confirmed_type_matches``'s docstring (contract_evaluation.py) named as
    # needed for a real fix (Codex review, fifteenth round's known gap; ADR-049).
    # Monotonic: once a spelling resolves an identity exactly, it stays here
    # even if that same identity is *also* later swept in ambiguously via a
    # different, colliding spelling (during the *other*, ambiguity-tolerant
    # walk -- this set itself never records an ambiguous route at all).
    exact_type_identities: set[str] = field(default_factory=set)
    # True when *any* declaration carried a non-UNKNOWN origin — i.e. the
    # snapshot was dumped with a public-header set so provenance is available.
    # Lets the classifier distinguish a confident reachability demotion from one
    # made without provenance to confirm it (ADR-024 §D5.1 ``no-provenance``).
    has_provenance: bool = False
    # True when at least one public root carried real signature type info
    # (a parameter or a return/variable type other than the export-only
    # sentinel ``"?"``). When False the snapshot is export-table-only (e.g. a
    # PE binary whose header scoping fell back), so the type-reachability
    # closure has no roots and **cannot** be trusted to demote a type as
    # "unreachable" — doing so would hide a real break (ADR-024 §D5.2). Only
    # confident provenance (private/system header) may demote in that case.
    has_typed_roots: bool = False


# Back-compat alias: `PublicSurfaceQuery`'s pre-migration home imported
# `PublicSurface` from `surface.py` under this name (`from ..surface import
# PublicSurface as PublicSurfaceResolution`, back when `PublicSurface` itself
# still lived there). A plain, direct assignment -- unlike `resolve_public_
# surface`/`PublicSurfaceQuery` below, `PublicSurface` is defined in this
# same module, so there is no cycle to route around with a lazy shim
# (Codex review, PR #979).
PublicSurfaceResolution = PublicSurface


def _is_real_type(type_str: str | None) -> bool:
    """True when *type_str* is a parsed type, not the export-only sentinel
    (see ``surface.py``'s original docstring for the full rationale --
    unchanged, just relocated)."""
    return bool(type_str) and type_str != "?"


def _symbol_keys(name: str, mangled: str) -> set[str]:
    """All identifier encodings under which a symbol may appear in a Change."""
    keys = {k for k in (name, mangled) if k}
    if name and "::" in name:
        keys.add(name.rsplit("::", 1)[1])
    return keys


# Origins that justify demoting a finding out of the public surface.
_DEMOTE_ORIGINS: frozenset[ScopeOrigin] = frozenset(
    {ScopeOrigin.PRIVATE_HEADER, ScopeOrigin.SYSTEM_HEADER}
)


def _merge_origin(existing: ScopeOrigin | None, new: ScopeOrigin) -> ScopeOrigin:
    """Combine origins sharing a key. A non-demote origin (public/unknown/…)
    always wins so we never demote a key that *any* public-header declaration
    contributes to (conservative, ADR-024 §D5)."""
    if existing is None or existing in _DEMOTE_ORIGINS:
        return new if existing is None or new not in _DEMOTE_ORIGINS else existing
    return existing


def _record_origin(
    surface: PublicSurface, keys: set[str], origin: ScopeOrigin
) -> None:
    for k in keys:
        surface.origin_by_key[k] = _merge_origin(surface.origin_by_key.get(k), origin)


def _index_surface_types(
    snap: AbiSnapshot, surface: PublicSurface
) -> tuple[dict[str, list[RecordType]], dict[str, list[EnumType]]]:
    """Populate ``surface.all_types`` and return name -> record / enum indexes.

    Records *and* enums are indexed by both their full name and (for namespaced
    types) the trailing ``::`` segment, so the closure walk can resolve either
    encoding — a namespaced enum referenced unqualified from a public signature
    or field (``Mode`` for ``ns::Mode``) must still be marked public, exactly as
    records are.

    A tail segment can be *ambiguous*: two namespaces may both define
    ``ns1::Mode`` and ``ns2::Mode``. Without namespace context on the reference
    we cannot tell which the public API meant, so each name maps to a *list* of
    all matching types and the closure marks every one public — over-keeping is
    the safe direction (never hide a real break behind snapshot order).

    Deliberately keyed off the flat ``snapshot.types``/``.enums`` lists, not
    the (deduplicating) graph node set — see this module's own docstring for
    why that distinction matters for ``ambiguous_type_names`` specifically.
    """
    record_by_name: dict[str, list[RecordType]] = {}
    for rec in snap.types:
        surface.all_types.add(rec.name)
        keys = {rec.name}
        record_by_name.setdefault(rec.name, []).append(rec)
        if "::" in rec.name:
            tail = rec.name.rsplit("::", 1)[1]
            record_by_name.setdefault(tail, []).append(rec)
            keys.add(tail)
        # castxml/clang convention: `.name` is deliberately bare (the
        # record's own leaf), with the qualified spelling recorded
        # separately in `.qualified_name` -- index that too, so a queued
        # spelling that IS the qualified form (a genuinely exact reference,
        # from whichever producer supplies one) resolves here the same way
        # DWARF's `.name` -- which already *is* the qualified string --
        # always could. Guarded against the value already being a
        # registered key for this record so a genuinely unique record is
        # never double-counted into `combined_counts` below and wrongly
        # marked ambiguous.
        if rec.qualified_name and rec.qualified_name not in keys:
            record_by_name.setdefault(rec.qualified_name, []).append(rec)
        origin = getattr(rec, "origin", ScopeOrigin.UNKNOWN)
        _record_origin(surface, keys, origin)
        if rec.qualified_name:
            surface.origin_by_qualified_key[rec.qualified_name] = _merge_origin(
                surface.origin_by_qualified_key.get(rec.qualified_name), origin
            )
    enum_by_name: dict[str, list[EnumType]] = {}
    for en in snap.enums:
        surface.all_types.add(en.name)
        keys = {en.name}
        enum_by_name.setdefault(en.name, []).append(en)
        if "::" in en.name:
            tail = en.name.rsplit("::", 1)[1]
            enum_by_name.setdefault(tail, []).append(en)
            keys.add(tail)
        if en.qualified_name and en.qualified_name not in keys:
            enum_by_name.setdefault(en.qualified_name, []).append(en)
        origin = getattr(en, "origin", ScopeOrigin.UNKNOWN)
        _record_origin(surface, keys, origin)
        if en.qualified_name:
            surface.origin_by_qualified_key[en.qualified_name] = _merge_origin(
                surface.origin_by_qualified_key.get(en.qualified_name), origin
            )
    for alias in snap.typedefs:
        surface.all_types.add(alias)
    # Combine both kinds before counting: a bare name ambiguous *across*
    # records and enums (one record entry, one enum entry -- neither list
    # individually looks ambiguous) collides in ``origin_by_key`` exactly
    # the same way a within-kind collision does, since that dict is shared
    # by both kinds.
    combined_counts: dict[str, int] = {}
    for name_map in (record_by_name, enum_by_name):
        for name, entries in name_map.items():
            combined_counts[name] = combined_counts.get(name, 0) + len(entries)
    surface.ambiguous_type_names.update(
        name for name, count in combined_counts.items() if count > 1
    )
    return record_by_name, enum_by_name


# ── Back-compat re-export shim (lazy, to avoid an import cycle) ─────────────
# Before this migration, `resolve_public_surface`/`PublicSurfaceQuery` lived
# directly in this module (this file's own prior docstring called it "the
# new, forward-facing entry point every Phase 3 consumer threads through").
# They moved to `public_surface_closure.py`/`public_surface_query.py` when
# the actual closure-walk algorithm and the orchestrator were split out.
# A *static* `from .public_surface_closure import resolve_public_surface` /
# `from .public_surface_query import PublicSurfaceQuery` here would form a
# `public_surface -> public_surface_closure -> public_surface` (and
# `-> public_surface_query -> export_surface -> public_surface_closure`)
# import cycle -- both sibling modules import from *this* one. This
# module-level `__getattr__` (PEP 562) resolves the two names lazily via
# `importlib.import_module` instead (a runtime call, not a static import
# edge), preserving the historical path `from abicheck.policy.public_surface
# import resolve_public_surface, PublicSurfaceQuery` (Codex review, PR #979)
# without coupling the three modules. New code should import from
# `public_surface_closure`/`public_surface_query` directly.
_MOVED_REEXPORTS = {
    "resolve_public_surface": "abicheck.policy.public_surface_closure",
    "PublicSurfaceQuery": "abicheck.policy.public_surface_query",
}


def __getattr__(name: str) -> Any:
    module_name = _MOVED_REEXPORTS.get(name)
    if module_name is not None:
        import importlib

        return getattr(importlib.import_module(module_name), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
