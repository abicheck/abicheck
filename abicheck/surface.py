# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Public-ABI surface resolution (ADR-024, Phase 2).

Derives the *public* ABI surface of a snapshot from information already
captured at dump time, then classifies individual diff findings as
in-surface (public) or out-of-surface (private / internal).

The surface is computed from two facts that the dumper already records:

1. **Linkage + header scope** — :class:`~abicheck.model.Visibility`. A
   function/variable is :data:`Visibility.PUBLIC` only when it is *both*
   exported *and* declared in one of the user-provided public headers
   (see ADR-016). ``ELF_ONLY`` / ``HIDDEN`` symbols are therefore not part
   of the public surface.
2. **Type reachability** — a record/enum/typedef is public iff it is
   reachable from a public function/variable through return types,
   parameter types, data members, base classes, or typedef targets. The
   closure deliberately follows *all* data members (including private and
   pointer-typed ones): this over-keeps rather than risks hiding a layout
   dependency, and precise by-value-vs-pointer reachability is left to a
   later phase (ADR-024 §D3).

This module performs *no* deletion on its own; it only answers "is this
finding about the public surface?".  The pipeline step that consumes it
(``FilterNonPublicSurface``) moves out-of-surface findings to an audit
ledger rather than dropping them silently — see ADR-024 §D4/D5.

Design constraints (ADR-024 §D5, anti-hiding):

* Internal-leak findings are **never** treated as out-of-surface — a
  private type reachable from a public API is exactly the signal scoping
  must not hide.
* When the surface cannot be resolved (no headers were provided, so every
  symbol is ``ELF_ONLY``), scoping is a no-op: we keep every finding.
* Type names we cannot place are kept (conservative — never hide an
  unknown).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .model import ScopeOrigin, Visibility

if TYPE_CHECKING:
    from .checker_types import Change
    from .model import AbiSnapshot, RecordType

# Findings whose whole purpose is to surface a *private* entity leaking into
# the public ABI. Scoping must never filter these (ADR-024 §D5.2).
_NEVER_FILTER_KIND_NAMES: frozenset[str] = frozenset(
    {
        "internal_type_leaks_via_public_api",
        "internal_template_leaks_via_public_api",
        "visibility_leak",
        # Preprocessor / const-constant findings. Their ``symbol`` is a
        # constant name, not an exported symbol or a reachable type, so the
        # normal symbol/type reachability classifier would always demote them.
        # The dumper only extracts constants whose declaring header classifies
        # as PUBLIC_HEADER (dumper_castxml._decl_is_public), so they are
        # public-contract by construction and must not be scoped out.
        "constant_changed",
        "constant_removed",
        "constant_added",
    }
)

# Findings whose ``symbol`` field identifies a type (or a member under a type)
# rather than a function/variable symbol. These must be classified through
# type reachability before consulting the symbol universe: in C++ especially,
# a public type name can collide with a hidden constructor/destructor or helper
# symbol of the same spelling.
_TYPE_LEVEL_KIND_NAMES: frozenset[str] = frozenset(
    {
        "type_size_changed",
        "type_alignment_changed",
        "type_field_removed",
        "type_field_added",
        "type_field_offset_changed",
        "type_field_type_changed",
        "type_base_changed",
        "type_vtable_changed",
        "type_added",
        "type_removed",
        "type_field_added_compatible",
        "enum_member_removed",
        "enum_member_added",
        "enum_member_value_changed",
        "enum_last_member_value_changed",
        "typedef_removed",
        "typedef_base_changed",
        "field_bitfield_changed",
        "union_field_added",
        "union_field_removed",
        "union_field_type_changed",
        "struct_size_changed",
        "struct_field_offset_changed",
        "struct_field_removed",
        "struct_field_type_changed",
        "struct_alignment_changed",
        "enum_underlying_size_changed",
        "struct_packing_changed",
        "type_visibility_changed",
        "type_became_final",
        "type_lost_final",
        # Fine-grained class-layout descriptor findings (layout-closure work):
        # each carries the owner *type* name in Change.symbol, so they must take
        # the type-level surface path (reclassify by type reachability) rather
        # than being read as a function/variable symbol and demoted as
        # not-exported (Codex review #345).
        "base_class_offset_changed",
        "vptr_introduced",
        "trivially_copyable_lost",
        "standard_layout_lost",
        "tail_padding_reuse_changed",
        "layout_unverifiable",
    }
)

_MEMBER_LEVEL_TYPE_KIND_NAMES: frozenset[str] = frozenset(
    {
        # Struct/union field findings, encoded as ``Type::field``.
        "type_field_removed",
        "type_field_added",
        "type_field_offset_changed",
        "type_field_type_changed",
        "type_field_added_compatible",
        "field_bitfield_changed",
        "union_field_added",
        "union_field_removed",
        "union_field_type_changed",
        "struct_field_offset_changed",
        "struct_field_removed",
        "struct_field_type_changed",
        # Enum member findings, encoded as ``Enum::member`` — same owner-qualified
        # shape, so they must reclassify by the owning enum just like fields do.
        "enum_member_removed",
        "enum_member_added",
        "enum_member_value_changed",
        "enum_last_member_value_changed",
    }
)

# Owner-qualified member findings are a strict subset of type-level findings:
# the owner-type reclassification in classify_change_surface() only runs inside
# the type-level branch, so any member kind missing from _TYPE_LEVEL_KIND_NAMES
# would silently never be reclassified. Guard the invariant at import time so a
# future kind cannot drift out of sync.
assert _MEMBER_LEVEL_TYPE_KIND_NAMES <= _TYPE_LEVEL_KIND_NAMES, (
    "member-level kinds must also be type-level: "
    f"{_MEMBER_LEVEL_TYPE_KIND_NAMES - _TYPE_LEVEL_KIND_NAMES}"
)

# Tokens that are type qualifiers / keywords, not type names.
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


def _is_real_type(type_str: str | None) -> bool:
    """True when *type_str* is a parsed type, not the export-only sentinel.

    Export-table-only dumps (e.g. a PE binary whose header scoping fell back)
    record ``return_type="?"`` and no parameters. Such roots carry no real
    type information, so the reachability closure cannot trust them.
    """
    return bool(type_str) and type_str != "?"


def _type_identifiers(type_str: str | None) -> set[str]:
    """Extract candidate record/enum/typedef names from a type string.

    Handles pointers, references, ``const``/``volatile``, arrays, and
    template arguments (``A<B, C>`` yields ``A``, ``B``, ``C``). Built-in
    keywords are dropped. Both the fully-qualified name and its trailing
    ``::`` segment are returned so callers can match either encoding.
    """
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
    """

    public_symbols: set[str] = field(default_factory=set)
    all_symbols: set[str] = field(default_factory=set)
    public_types: set[str] = field(default_factory=set)
    all_types: set[str] = field(default_factory=set)
    resolvable: bool = False
    # Origin (ADR-024 D1 / ADR-015 v6) keyed by every symbol key and type
    # name. Only populated when the snapshot was dumped with a public-header
    # set; otherwise every value is UNKNOWN and provenance reasons never fire.
    origin_by_key: dict[str, ScopeOrigin] = field(default_factory=dict)
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


def _record_origin(surface: PublicSurface, keys: set[str], origin: ScopeOrigin) -> None:
    for k in keys:
        surface.origin_by_key[k] = _merge_origin(surface.origin_by_key.get(k), origin)


def _index_surface_types(
    snap: AbiSnapshot, surface: PublicSurface
) -> dict[str, RecordType]:
    """Populate ``surface.all_types`` and return a name -> record index.

    Records are indexed by both their full name and (for namespaced types) the
    trailing ``::`` segment, so the closure walk can match either encoding.
    """
    record_by_name: dict[str, RecordType] = {}
    for rec in snap.types:
        surface.all_types.add(rec.name)
        record_by_name[rec.name] = rec
        keys = {rec.name}
        if "::" in rec.name:
            tail = rec.name.rsplit("::", 1)[1]
            record_by_name.setdefault(tail, rec)
            keys.add(tail)
        _record_origin(surface, keys, getattr(rec, "origin", ScopeOrigin.UNKNOWN))
    for en in snap.enums:
        surface.all_types.add(en.name)
        _record_origin(surface, {en.name}, getattr(en, "origin", ScopeOrigin.UNKNOWN))
    for alias in snap.typedefs:
        surface.all_types.add(alias)
    return record_by_name


def _seed_public_roots(
    snap: AbiSnapshot, surface: PublicSurface
) -> tuple[set[str], bool]:
    """Record public symbols on *surface*; return (seed type names, has_public).

    Seeds the type-closure work-list from the return/parameter/variable types of
    every :data:`Visibility.PUBLIC` function and variable.
    """
    seed_types: set[str] = set()
    has_public = False
    for fn in snap.functions:
        keys = _symbol_keys(fn.name, fn.mangled)
        surface.all_symbols |= keys
        _record_origin(surface, keys, getattr(fn, "origin", ScopeOrigin.UNKNOWN))
        if fn.visibility == Visibility.PUBLIC:
            has_public = True
            surface.public_symbols |= keys
            if fn.params or _is_real_type(fn.return_type):
                surface.has_typed_roots = True
            seed_types |= _type_identifiers(fn.return_type)
            for p in fn.params:
                seed_types |= _type_identifiers(getattr(p, "type", None))
    for var in snap.variables:
        keys = _symbol_keys(var.name, var.mangled)
        surface.all_symbols |= keys
        _record_origin(surface, keys, getattr(var, "origin", ScopeOrigin.UNKNOWN))
        if var.visibility == Visibility.PUBLIC:
            has_public = True
            surface.public_symbols |= keys
            if _is_real_type(var.type):
                surface.has_typed_roots = True
            seed_types |= _type_identifiers(var.type)
    return seed_types, has_public


def _walk_type_closure(
    snap: AbiSnapshot,
    surface: PublicSurface,
    record_by_name: dict[str, RecordType],
    seed_types: set[str],
) -> None:
    """Transitive closure over the record/typedef graph; fills public_types.

    Follows typedef targets, record fields, and base classes from each seed
    type, marking every reachable known type as part of the public surface.
    """
    queue = list(seed_types)
    seen: set[str] = set()
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        if name in surface.all_types:
            surface.public_types.add(name)
        # Follow typedef targets.
        target = snap.typedefs.get(name)
        if target:
            for ident in _type_identifiers(target):
                if ident not in seen:
                    queue.append(ident)
        rec_node = record_by_name.get(name)
        if rec_node is None:
            continue
        # A short alias (``A``) reached inside its namespace resolves here to the
        # namespaced record (``ns::A``); record the *canonical* full name as
        # public so callers that count/scope by ``RecordType.name`` see it
        # (otherwise a reachable namespaced type is silently missed — ADR-027
        # review). ``rec_node.name`` is always in ``all_types``.
        surface.public_types.add(rec_node.name)
        for f in rec_node.fields:
            for ident in _type_identifiers(f.type):
                if ident not in seen:
                    queue.append(ident)
        # Both direct and virtual bases are ABI-reachable through the derived
        # type (virtual inheritance still embeds the base subobject + vtable
        # path), so the public closure must follow both (ADR-025 A3 review).
        for base in (*rec_node.bases, *rec_node.virtual_bases):
            for ident in _type_identifiers(base):
                if ident not in seen:
                    queue.append(ident)


def compute_public_surface(snap: AbiSnapshot) -> PublicSurface:
    """Compute the public-ABI surface of *snap*.

    Public roots are :data:`Visibility.PUBLIC` functions/variables. The
    public type set is the transitive closure over the types they
    reference (returns, params, fields, bases, typedef targets).
    """
    surface = PublicSurface()

    # Build the type universe and a name -> record index for closure walks.
    record_by_name = _index_surface_types(snap, surface)

    # Seed roots from public symbols; collect the type names they touch.
    seed_types, has_public = _seed_public_roots(snap, surface)

    # Provenance is available iff some declaration was classified to a real
    # origin (only happens when the snapshot was dumped with a public-header
    # set). Used by the classifier to emit the ``no-provenance`` ledger reason.
    surface.has_provenance = any(
        o != ScopeOrigin.UNKNOWN for o in surface.origin_by_key.values()
    )

    # Scoping only makes sense when we actually have header-derived public
    # visibility. Without headers every symbol is ELF_ONLY (ADR-016) and a
    # surface filter would hide everything — so declare it unresolvable.
    surface.resolvable = has_public and not getattr(snap, "elf_only_mode", False)
    if not surface.resolvable:
        return surface

    # Transitive closure over the record/typedef graph.
    _walk_type_closure(snap, surface, record_by_name, seed_types)
    return surface


# Scope-level confidence notes (ADR-024 §D5.3). Unlike the per-finding
# exclusion reasons below, these qualify the *whole* surface resolution: they
# flag that the resolved surface (and therefore every demotion decision made
# against it) is less trustworthy than a clean header-scoped run.
SCOPE_NOTE_MANGLING_FALLBACK = "mangling-fallback"  # MSVC C++ name-mangling gap
SCOPE_NOTE_CASTXML_UNAVAILABLE = "castxml-unavailable"  # castxml missing / parse failed
SCOPE_NOTE_NO_PROVENANCE = "no-provenance"  # surface resolved without provenance


def surface_scope_confidence(
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    scope_enabled: bool,
    surf_old: PublicSurface | None = None,
    surf_new: PublicSurface | None = None,
) -> tuple[str, list[str]]:
    """Summarise confidence in the header-scope resolution (ADR-024 §D5.3).

    Returns ``(confidence, notes)`` where *confidence* is ``"high"`` or
    ``"reduced"`` and *notes* is a deduplicated, order-stable list of structured
    note codes. ``"high"`` with no notes is the clean case. The dumper records
    the per-snapshot ``scope_fallback`` signal (castxml/mangling); a resolvable
    surface that nonetheless lacks provenance adds ``no-provenance``.

    ``surf_old`` / ``surf_new`` may be passed when the caller has already run
    :func:`compute_public_surface` (e.g. the ``FilterNonPublicSurface`` pipeline
    step) to avoid repeating the type-closure walk; otherwise they are computed
    on demand.
    """
    notes: list[str] = []

    def _add(code: str | None) -> None:
        if code and code not in notes:
            notes.append(code)

    for snap in (old, new):
        _add(getattr(snap, "scope_fallback", None))

    if scope_enabled:
        s_old = surf_old if surf_old is not None else compute_public_surface(old)
        s_new = surf_new if surf_new is not None else compute_public_surface(new)
        # Flag reduced confidence when *any* resolvable side was scoped without
        # provenance — a mixed comparison (one side has provenance, the other
        # resolvable side does not) is still only half-trustworthy, so the note
        # must fire unless every resolvable side carries provenance.
        if any(s.resolvable and not s.has_provenance for s in (s_old, s_new)):
            _add(SCOPE_NOTE_NO_PROVENANCE)

    return ("reduced" if notes else "high"), notes


def change_in_public_surface(
    change: Change,
    surf_old: PublicSurface,
    surf_new: PublicSurface,
) -> bool:
    """Return ``True`` if *change* concerns the public ABI surface.

    Thin boolean wrapper over :func:`classify_change_surface` for callers
    that only need the in/out decision.
    """
    return classify_change_surface(change, surf_old, surf_new)[0]


# Exclusion reasons recorded on the surface ledger (ADR-024 §D5.1).
# ``private-header`` / ``system-header`` are provenance-driven and only fire
# when the snapshot was dumped with a public-header set (Phase 1, ADR-015 v6);
# ``not-exported`` / ``non-public-type`` are the linkage/reachability reasons
# the resolver can always determine. ``suppressed-by-user`` belongs to the
# separate suppression ledger.
REASON_NOT_EXPORTED = "not-exported"  # symbol known but not in the public export set
REASON_NON_PUBLIC_TYPE = "non-public-type"  # type reachable by no public API root
REASON_PRIVATE_HEADER = (
    "private-header"  # decl originates in a non-public project header
)
REASON_SYSTEM_HEADER = "system-header"  # decl originates in a toolchain/system header
# A type was demoted by reachability while provenance *was* available for the
# snapshot but not for this type — the demotion is reachability-based, not
# provenance-confirmed (reduced confidence; ADR-024 §D5.1 / §D5.3).
REASON_NO_PROVENANCE = "no-provenance"
# An internal-namespace (``detail::``/``impl::``/``internal::``) type's layout
# churn that the internal-leak detector confirmed is NOT reachable from any
# public API root, so it is truly private and must not drive a hard ABI verdict
# (ISSUE-15: oneTBB ``tbb::detail::*`` / ``rml::internal::*`` DWARF-only churn).
REASON_PRIVATE_INTERNAL_UNREACHABLE = "private-internal-unreachable"

# Map a demotable origin to its ledger reason code.
_ORIGIN_REASON: dict[ScopeOrigin, str] = {
    ScopeOrigin.PRIVATE_HEADER: REASON_PRIVATE_HEADER,
    ScopeOrigin.SYSTEM_HEADER: REASON_SYSTEM_HEADER,
}


def _origin_reason(
    surf_old: PublicSurface, surf_new: PublicSurface, key: str
) -> str | None:
    """Return the provenance demotion reason for *key*, or None to defer to
    linkage/reachability. A public-header (or unknown) origin on *either* side
    blocks demotion (conservative)."""
    o_old = surf_old.origin_by_key.get(key, ScopeOrigin.UNKNOWN)
    o_new = surf_new.origin_by_key.get(key, ScopeOrigin.UNKNOWN)
    # Only demote when both sides agree the key is private/system. If either
    # side is public/unknown/generated/export-only, keep deferring.
    if o_old in _ORIGIN_REASON and o_new in _ORIGIN_REASON:
        # Prefer private-header when the two disagree (the stronger signal).
        if ScopeOrigin.PRIVATE_HEADER in (o_old, o_new):
            return REASON_PRIVATE_HEADER
        return REASON_SYSTEM_HEADER
    return None


@dataclass(frozen=True)
class SurfaceUnions:
    """The four old∪new surface universes used to classify a finding.

    These depend only on the surface *pair*, not on the individual change, so
    when classifying many findings against the same surfaces they should be
    computed once and reused — recomputing the unions per change is
    O(findings × surface) and makes large comparisons quadratic. Build with
    :func:`surface_unions` and pass to :func:`classify_change_surface`.
    """

    public_symbols: frozenset[str]
    all_symbols: frozenset[str]
    public_types: frozenset[str]
    all_types: frozenset[str]


def surface_unions(surf_old: PublicSurface, surf_new: PublicSurface) -> SurfaceUnions:
    """Compute the old∪new surface universes once for a surface pair."""
    return SurfaceUnions(
        public_symbols=frozenset(surf_old.public_symbols | surf_new.public_symbols),
        all_symbols=frozenset(surf_old.all_symbols | surf_new.all_symbols),
        public_types=frozenset(surf_old.public_types | surf_new.public_types),
        all_types=frozenset(surf_old.all_types | surf_new.all_types),
    )


def classify_change_surface(
    change: Change,
    surf_old: PublicSurface,
    surf_new: PublicSurface,
    *,
    unions: SurfaceUnions | None = None,
) -> tuple[bool, str | None]:
    """Classify *change* against the public surface.

    Returns ``(in_surface, reason)``. ``reason`` is ``None`` when the change
    is in-surface (kept); otherwise it is a stable ledger reason code
    explaining *why* the finding was demoted (ADR-024 §D5.1).

    Conservative by construction (ADR-024 §D5): leak findings, unknown
    symbols, and unknown types all stay in-surface so scoping can only ever
    remove findings it is *confident* are private.

    When classifying many changes against the same surface pair, pass a
    precomputed *unions* (see :func:`surface_unions`) to avoid recomputing the
    four old∪new set unions on every call — that recomputation is what makes a
    large comparison quadratic in the number of findings.
    """
    if change.kind.value in _NEVER_FILTER_KIND_NAMES:
        return True, None
    if not (surf_old.resolvable and surf_new.resolvable):
        # If either side lacks a resolvable surface we cannot confidently
        # place a finding as private on *both* versions — keep everything
        # rather than risk hiding a real change from the unresolved side.
        return True, None

    if unions is None:
        unions = surface_unions(surf_old, surf_new)
    public_symbols = unions.public_symbols
    all_symbols = unions.all_symbols
    public_types = unions.public_types
    all_types = unions.all_types

    sym = change.symbol or ""
    # Type-level findings must not be classified via the symbol universe first:
    # a public type such as ``Foo`` can legitimately collide with a hidden
    # constructor/destructor/helper symbol named ``Foo``. In that case the
    # layout change's ``symbol`` still denotes the type, so reachability decides.
    type_level_finding = change.kind.value in _TYPE_LEVEL_KIND_NAMES
    if change.kind.value in _MEMBER_LEVEL_TYPE_KIND_NAMES and "::" in sym:
        # Member-level findings are owner-qualified: ``Type::field`` (struct/union
        # field) or ``Enum::member`` (enum member). Classifying the full string as
        # a type keeps a private member's churn in-surface as an "unknown" type;
        # use the owner type for reachability/provenance decisions. (Membership in
        # this set implies type_level_finding — see the import-time assert above —
        # so a qualified *type name* like ``ns::Foo`` is never mis-split here.)
        candidates = {sym.rsplit("::", 1)[0]} | _type_identifiers(change.caused_by_type)
    else:
        candidates = _type_identifiers(sym) | _type_identifiers(change.caused_by_type)

    # Symbol-level finding (function/variable): public iff a public symbol.
    # A confident private/system-header origin demotes even an exported
    # symbol — that is exactly the leaked-private-header case scoping targets.
    if not type_level_finding:
        verdict = _classify_symbol_level(
            sym,
            all_symbols,
            public_symbols,
            surf_old,
            surf_new,
        )
        if verdict is not None:
            return verdict

    return _classify_type_level(
        candidates,
        all_types,
        public_types,
        surf_old,
        surf_new,
    )


def _classify_symbol_level(
    sym: str,
    all_symbols: frozenset[str] | set[str],
    public_symbols: frozenset[str] | set[str],
    surf_old: PublicSurface,
    surf_new: PublicSurface,
) -> tuple[bool, str | None] | None:
    """Classify a symbol-level finding, or return None to fall through to
    type-level reachability (the symbol is unknown to the surface)."""
    if sym in all_symbols:
        reason = _origin_reason(surf_old, surf_new, sym)
        if reason is not None:
            return False, reason
        return (True, None) if sym in public_symbols else (False, REASON_NOT_EXPORTED)
    if sym and "::" in sym and sym.rsplit("::", 1)[1] in all_symbols:
        tail = sym.rsplit("::", 1)[1]
        reason = _origin_reason(surf_old, surf_new, tail)
        if reason is not None:
            return False, reason
        return (True, None) if tail in public_symbols else (False, REASON_NOT_EXPORTED)
    return None


def _classify_type_level(
    candidates: set[str],
    all_types: frozenset[str] | set[str],
    public_types: frozenset[str] | set[str],
    surf_old: PublicSurface,
    surf_new: PublicSurface,
) -> tuple[bool, str | None]:
    """Classify a finding by the implicated type name(s). A finding is
    in-surface if *any* implicated type is reachable from the public API."""
    # Anti-hiding (ADR-024 §D5.2): never filter a change to an
    # internal-namespace type (``detail::``, ``impl::``, …). The internal-leak
    # detector (post_processing.DetectInternalLeaks) runs *after* this step and
    # decides whether such a type leaks through the public API — and it uses a
    # broader set of public roots than this reachability closure (it also seeds
    # from unreferenced public-header types). Deferring to it guarantees a real
    # leak is never silently dropped here; a genuinely-unreachable internal type
    # is simply left for normal handling.
    from .internal_leak import DEFAULT_INTERNAL_NAMESPACES, is_internal_type

    if any(is_internal_type(c, DEFAULT_INTERNAL_NAMESPACES) for c in candidates):
        return True, None

    known = {c for c in candidates if c in all_types}
    if not known:
        # We cannot place this finding — keep it (never hide an unknown).
        return True, None
    if known & public_types:
        return True, None
    # Prefer a provenance reason when every implicated type confidently
    # originates from a private/system header; this is a *confident* demotion
    # and applies even without typed roots (it is the leaked-private case).
    header_reason = _confident_header_reason(known, surf_old, surf_new)
    if header_reason is not None:
        return False, header_reason
    return _demote_by_reachability(known, surf_old, surf_new)


def _confident_header_reason(
    known: set[str],
    surf_old: PublicSurface,
    surf_new: PublicSurface,
) -> str | None:
    """Reason when every implicated type confidently originates from a
    private/system header, else None."""
    type_reasons = {_origin_reason(surf_old, surf_new, c) for c in known}
    if None in type_reasons or not type_reasons:
        return None
    return (
        REASON_PRIVATE_HEADER
        if REASON_PRIVATE_HEADER in type_reasons
        else REASON_SYSTEM_HEADER
    )


def _demote_by_reachability(
    known: set[str],
    surf_old: PublicSurface,
    surf_new: PublicSurface,
) -> tuple[bool, str | None]:
    """Final demotion stage: the only remaining basis is type-reachability."""
    # That is trustworthy *only* when the surface has real typed roots to walk
    # from. An export-table-only snapshot (e.g. a PE binary whose header scoping
    # fell back to the export table — functions are ``return_type="?"``) has
    # none, so every type looks "unreachable". Demoting on that basis would hide
    # a genuine public ABI break, including a change to a PUBLIC_HEADER type
    # recovered from a PDB. Keep the finding in that case (ADR-024 §D5.2).
    if not (surf_old.has_typed_roots and surf_new.has_typed_roots):
        return True, None
    # Reachability demotion. If provenance was available for the snapshot but
    # none of the implicated types carried it, disclose the reduced confidence
    # (ADR-024 §D5.3) rather than implying a provenance-confirmed verdict.
    if (
        surf_old.has_provenance
        and surf_new.has_provenance
        and all(
            surf_old.origin_by_key.get(c, ScopeOrigin.UNKNOWN) == ScopeOrigin.UNKNOWN
            and surf_new.origin_by_key.get(c, ScopeOrigin.UNKNOWN)
            == ScopeOrigin.UNKNOWN
            for c in known
        )
    ):
        return False, REASON_NO_PROVENANCE
    return False, REASON_NON_PUBLIC_TYPE
