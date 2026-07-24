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
   dependency. The closure follows enum references (as struct-field types,
   typedef targets, or signature types) exactly as it follows record
   references — including resolving a namespaced enum referenced by its
   unqualified short name (``Mode`` for ``ns::Mode``) via the same trailing-``::``
   alias index records use — so an unreferenced internal enum is scoped out
   while a public-reachable one is kept (locked in by the ``enum-reachability``
   axis of the ``scripts/check_fp_rate.py`` corpus).

   Precise by-value-vs-pointer reachability (ADR-024 §D3) is intentionally
   *not* done here: a pointer-reached type whose full definition is public
   is still layout-observable (a consumer can dereference/allocate it by
   value), so demoting it at this stage would hide a real break. The safe
   half of that precision — a pointer-only-reached *opaque* handle whose
   layout consumers cannot see — is delivered downstream by the opaque
   filter (``diff_filtering._filter_opaque_size_changes`` /
   ``_downgrade_opaque_type_changes``), which acts on the layout-observability
   axis rather than the public/private axis. Both polarities are locked in by
   the ``pointer-opaque`` axis of the FP-rate corpus.

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

from .diff_cxx_rules import owner_class_of
from .model import ScopeOrigin, Visibility

if TYPE_CHECKING:
    from .checker_types import Change
    from .model import AbiSnapshot, EnumType, RecordType

# Findings whose whole purpose is to surface a *private* entity leaking into
# the public ABI. Scoping must never filter these (ADR-024 §D5.2).
_NEVER_FILTER_KIND_NAMES: frozenset[str] = frozenset(
    {
        "internal_type_leaks_via_public_api",
        "internal_template_leaks_via_public_api",
        "visibility_leak",
        # Same "public entity newly reaches an internal one" shape as the two
        # leak kinds above, but produced by the separate L5 source-graph pass
        # (source_graph_findings._internal_dependency_findings) rather than
        # internal_leak.py. ``symbol`` is the *public* entity's own qualified
        # name (e.g. "demo::Public" or "demo::configure") -- a type or a
        # function/variable that legitimately IS on the public surface, but
        # the type-vs-symbol split above still runs it through
        # ``_classify_symbol_level`` (not in ``_TYPE_LEVEL_KIND_NAMES``),
        # which looks it up in the flat ``all_symbols``/``public_symbols``
        # sets. A type name is never in those sets (only functions/variables
        # are), so every type-rooted instance of this finding was silently
        # demoted as "not-exported" -- defeating the one thing this L5 pass
        # exists to report. The graph pass already has its own entry-point
        # reachability gate (header_graph.is_public_dependency_node); this
        # finding must not be re-filtered by a second, incompatible one.
        "public_api_internal_dependency_added",
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

# A hidden friend (an in-class `friend` operator with no namespace-scope
# declaration, found only via ADL) can never produce an exported symbol by
# construction — it is compiled inline into every caller. Requiring
# ELF-export presence for this kind is therefore never satisfiable, so the
# ordinary not-exported gate must never apply to it (examples/
# case96_hidden_friend_removed). That is a reason to skip *that one* gate,
# not a reason to skip header-provenance demotion entirely: a hidden friend
# whose befriending class lives in a system/private header is exactly as
# out-of-surface as any other declaration from that header. These kinds get
# their own classification path (``_classify_hidden_friend_surface``) instead
# of the unconditional keep in ``_NEVER_FILTER_KIND_NAMES`` above.
_HIDDEN_FRIEND_KIND_NAMES: frozenset[str] = frozenset(
    {
        "hidden_friend_removed",
        "hidden_friend_added",
    }
)


def is_hidden_friend_finding(change: Change) -> bool:
    """True when *change* is a ``hidden_friend_removed``/``hidden_friend_added``.

    Used by manifest-scoped comparison (``compare --post-manifest``, in
    ``post_processing.FilterNonPublicSurface._run_allowlist``) to keep a
    hidden friend out of the *concrete exported symbol* demotion check
    (Codex review): a hidden friend can never produce a real export, but its
    mangled name can still appear in a header/L2 snapshot's function list —
    the same list ``_snapshot_export_ids`` reads from, with no visibility
    filter — so it would otherwise be misread as "a real export not in the
    committed manifest" and silently demoted, hiding a genuine public ADL
    break. The allowlist path doesn't go through
    :func:`classify_change_surface`/``_classify_hidden_friend_surface`` at
    all, so ``is_symbol_level_finding`` alone isn't enough there.
    """
    return change.kind.value in _HIDDEN_FRIEND_KIND_NAMES


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
        # Carries the owner *type* name in Change.symbol (e.g. "Point", not
        # "Point::x") — must take the type-level reachability path. Missing
        # this let the symbol-level path run first, where "Point" can
        # collide with an unrelated *symbol* of the same bare name: any C++
        # class has an implicit same-named constructor (castxml represents
        # it unmangled, per the identity() docstring's own caveat), so a
        # public, reachable type could still be wrongly demoted as
        # "not-exported" by matching its own hidden constructor instead of
        # the type reachability graph (case35_field_rename).
        "field_renamed",
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


def is_symbol_level_finding(change: Change) -> bool:
    """True when *change* is a symbol/export-level finding (function/variable).

    False for type-level findings (struct/enum/union layout & member changes)
    and for never-filter findings (internal leaks). This is the single source of
    truth for the type-vs-symbol distinction, shared with
    :func:`classify_change_surface`.

    Used by manifest-scoped comparison (``compare --post-manifest``) to demote
    *only* concrete export findings whose symbol is outside the committed
    surface. Type-level and leak findings stay in-surface (conservative — a type
    change may still affect a committed export's ABI, and scoping must never hide
    a break).
    """
    kv = change.kind.value
    return kv not in _NEVER_FILTER_KIND_NAMES and kv not in _TYPE_LEVEL_KIND_NAMES


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
    # by both kinds (Codex review, thirteenth round).
    combined_counts: dict[str, int] = {}
    for name_map in (record_by_name, enum_by_name):
        for name, entries in name_map.items():
            combined_counts[name] = combined_counts.get(name, 0) + len(entries)
    surface.ambiguous_type_names.update(
        name for name, count in combined_counts.items() if count > 1
    )
    return record_by_name, enum_by_name


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
            # A public *method* makes its enclosing class directly public even
            # when the method's own signature carries no class-typed return/
            # param (e.g. `void process();`) — the class is exported and
            # consumers can declare/allocate/inherit it by value, so its own
            # layout and base-class changes must not be scoped out as
            # "non-public-type" just because no *other* signature happens to
            # reference it.
            owner = owner_class_of(fn)
            if owner:
                seed_types |= _type_identifiers(owner)
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
    record_by_name: dict[str, list[RecordType]],
    enum_by_name: dict[str, list[EnumType]],
    seed_types: set[str],
) -> None:
    """Transitive closure over the record/typedef graph; fills public_types.

    Follows typedef targets, record fields, and base classes from each seed
    type, marking every reachable known type as part of the public surface.
    A name may resolve to *several* types (an ambiguous ``::`` tail shared by
    two namespaces); every match is marked public and walked.
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
        # A short/qualified enum alias (``Mode``) reached from a public signature
        # or field resolves here to its canonical namespaced name (``ns::Mode``),
        # so a scoped enum-member finding is not hidden (mirrors the record alias
        # handling below). Enums have no fields or bases, so nothing is queued.
        # An ambiguous tail may match enums in several namespaces — mark them all.
        for en_node in enum_by_name.get(name, ()):
            surface.public_types.add(en_node.name)
        rec_nodes = record_by_name.get(name)
        if not rec_nodes:
            continue
        # A short alias (``A``) reached inside its namespace resolves here to the
        # namespaced record (``ns::A``); record the *canonical* full name as
        # public so callers that count/scope by ``RecordType.name`` see it
        # (otherwise a reachable namespaced type is silently missed — ADR-027
        # review). ``rec_node.name`` is always in ``all_types``. An ambiguous
        # tail shared by two namespaces resolves to several records — walk each.
        for rec_node in rec_nodes:
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

    # Build the type universe and name -> record / enum indexes for closure walks.
    record_by_name, enum_by_name = _index_surface_types(snap, surface)

    # Seed roots from public symbols; collect the type names they touch.
    seed_types, has_public = _seed_public_roots(snap, surface)

    # A named enum whose declaration textually came from a parsed header
    # (``source_header`` set — populated whenever castxml parsed it from a
    # ``-H``/``--header`` input, independent of the separate opt-in
    # ``--public-header`` provenance classification) is part of the public
    # surface even when no function/variable signature references the enum
    # type by name. Unlike a struct's layout (only observable by a caller
    # that actually names the type), an enum's members are consumer-visible
    # the moment the header is included: `ERROR` is used directly as a
    # compile-time constant, the same as a `#define` (which surface.py
    # already exempts from reachability via `_NEVER_FILTER_KIND_NAMES`).
    # Reachability-only seeding was designed for the struct-layout hazard
    # and is the wrong model for enums (ADR-024; case20 regression).
    #
    # Excludes an enum whose *own* origin is confidently private/system
    # (``--public-header`` was given and this enum came from outside that
    # boundary, or from a system header): seeding it into public_types would
    # make `known & public_types` short-circuit _classify_type_level before
    # _confident_header_reason ever gets to demote it via provenance, so a
    # private-header-only enum's value change would wrongly stay BREAKING
    # instead of landing in the private-header filtered ledger (Codex review).
    seed_types |= {
        en.name
        for en in snap.enums
        if en.source_header and en.origin not in _DEMOTE_ORIGINS
    }

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
    _walk_type_closure(snap, surface, record_by_name, enum_by_name, seed_types)
    return surface


# Scope-level confidence notes (ADR-024 §D5.3). Unlike the per-finding
# exclusion reasons below, these qualify the *whole* surface resolution: they
# flag that the resolved surface (and therefore every demotion decision made
# against it) is less trustworthy than a clean header-scoped run.
SCOPE_NOTE_MANGLING_FALLBACK = "mangling-fallback"  # MSVC C++ name-mangling gap
SCOPE_NOTE_HEADER_BACKEND_UNAVAILABLE = "header-backend-unavailable"
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
    the per-snapshot ``scope_fallback`` signal (backend/mangling); a resolvable
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
# A native C/C++ finding on a CPython extension module whose only public
# contract is its Python-visible API (recovered `.pyi`) plus its load contract
# (imported Py* / abi3). The module exports only `PyInit_`, so churn in its
# other exported symbols and internal type layout cannot be observed by any
# `import` consumer — it is off the real public surface (G23 oracle scoping).
REASON_OFF_PYTHON_SURFACE = "off-python-surface"

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
    # Python-level API and CPython load-contract findings (G23/G14) live on a
    # distinct evidence axis from the C/C++ export surface: their ``symbol`` is a
    # dotted Python name the header-surface classifier cannot place, and demoting
    # them would hide exactly the break they exist to catch. Never scope them out
    # (the authority rule — makes explicit what was previously an implicit
    # survival via the conservative-unknown fallback).
    if change.kind.value.startswith("python_"):
        return True, None
    if not (surf_old.resolvable and surf_new.resolvable):
        # If either side lacks a resolvable surface we cannot confidently
        # place a finding as private on *both* versions — keep everything
        # rather than risk hiding a real change from the unresolved side.
        # Hidden-friend findings must go through this guard too (Codex
        # review): dispatching to _classify_hidden_friend_surface before
        # this check let it demote from whichever side happens to have
        # resolvable origin data, even when the other side (e.g. an
        # ELF-only baseline) offers nothing to cross-check against —
        # exactly the mixed-evidence case this guard exists to protect
        # every other kind of finding from.
        return True, None
    if change.kind.value in _HIDDEN_FRIEND_KIND_NAMES:
        return _classify_hidden_friend_surface(change, surf_old, surf_new)

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


def _hidden_friend_owner_effective_origin(
    surf: PublicSurface, owner: str, bare: str
) -> ScopeOrigin | None:
    """Resolve one side's origin for a qualified hidden-friend owner: an
    exact ``origin_by_qualified_key`` match if present, else an exact match
    on ``owner`` itself in ``origin_by_key`` (a producer that stores the
    owner's ``RecordType.name`` as the full qualified string rather than
    populating ``qualified_name`` separately — legacy/DWARF-style), else
    the bare tail's origin when the type exists on this side at all — even
    if that origin is ``UNKNOWN`` — else ``None`` only when the type is
    genuinely absent from this snapshot (the common add/remove-together
    case). "Present but unclassified" must never collapse to the same
    ``None`` as "absent": the caller treats ``None`` as a side that cannot
    disagree, but a present-and-unclassified side very much can (Codex
    review — a prior version of this fallback used ``None`` for both,
    letting an exact private match on one side demote a friend even when
    the other side's bare-name entry neither confirms nor refutes that,
    because the type is right there, just unclassified). The full-``owner``
    check matters because ``all_types`` only ever indexes a record's own
    ``name`` (never the bare tail extracted from it), so a legacy side
    whose ``name`` *is* the qualified string was otherwise treated as
    absent even though its origin is recorded under that exact key too
    (Codex review, third round).

    Neither the ``owner`` nor the ``bare`` key is trusted when
    ``ambiguous_type_names`` says it names more than one record/enum on
    this side (Codex review, sixth round): an unrelated *public* type
    sharing that bare tail (``pub::Foo`` alongside the actually-private
    ``priv::Foo``) would otherwise merge into ``origin_by_key`` as
    PUBLIC_HEADER (conservative "any side public" merge — safe for
    reachability, but wrong here, since it would hide a genuinely private
    owner's demotion). Reported as ``UNKNOWN`` rather than ``None`` in that
    case — the type genuinely exists on this side, we just can't tell
    which one, which is a real disagreement signal, not absence."""
    exact = surf.origin_by_qualified_key.get(owner)
    if exact is not None:
        return exact
    if owner in surf.all_types:
        if owner in surf.ambiguous_type_names:
            return ScopeOrigin.UNKNOWN
        return surf.origin_by_key.get(owner, ScopeOrigin.UNKNOWN)
    if bare in surf.all_types:
        if bare in surf.ambiguous_type_names:
            return ScopeOrigin.UNKNOWN
        return surf.origin_by_key.get(bare, ScopeOrigin.UNKNOWN)
    return None


def _one_sided_key_origin(
    surf: PublicSurface, key: str, universe: frozenset[str] | set[str]
) -> ScopeOrigin | None:
    """Resolve *key*'s origin on one *surf*, distinguishing "present but
    unclassified" (an actual :class:`ScopeOrigin`, possibly ``UNKNOWN``)
    from "genuinely absent" (``None``) — the same distinction
    :func:`_hidden_friend_owner_effective_origin` makes for a type owner,
    generalized to any flat key already indexed by *universe* (a symbol's
    ``all_symbols``, or a type's ``all_types`` when no ``::``-qualified/
    bare-tail ambiguity needs resolving)."""
    if key not in universe:
        return None
    return surf.origin_by_key.get(key, ScopeOrigin.UNKNOWN)


def _hidden_friend_owner_reason_qualified(
    eff_old: ScopeOrigin | None,
    eff_new: ScopeOrigin | None,
) -> str | None:
    """Combine two per-side *effective* origins (see
    :func:`_hidden_friend_owner_effective_origin`) into a single ledger
    reason, the way :func:`_origin_reason` combines two ``origin_by_key``
    lookups. ``None`` for a side means the owner is genuinely absent from
    that snapshot (removed/added together with the friend); a
    present-but-``UNKNOWN`` side blocks the reason exactly like an ordinary
    origin disagreement would, via ``_ORIGIN_REASON.get`` returning
    ``None`` for it."""
    origins = [o for o in (eff_old, eff_new) if o is not None]
    if not origins:
        return None
    reasons = {_ORIGIN_REASON.get(o) for o in origins}
    if None in reasons:
        return None
    return (
        REASON_PRIVATE_HEADER
        if REASON_PRIVATE_HEADER in reasons
        else REASON_SYSTEM_HEADER
    )


def _classify_hidden_friend_surface(
    change: Change,
    surf_old: PublicSurface,
    surf_new: PublicSurface,
) -> tuple[bool, str | None]:
    """Classify a ``hidden_friend_removed``/``hidden_friend_added`` finding.

    A hidden friend can never produce an exported symbol (it is compiled
    inline into every caller via ADL), so the ordinary not-exported gate must
    never demote it — but its *origin* still decides surface membership,
    exactly like any other declaration. Preference order:

    1. The befriending class (``change.caused_by_type``, resolved from
       castxml's ``befriending`` attribute / clang's friend-scope walk) — if
       every snapshot that actually contains the owner confidently agrees it
       is a system- or private-header declaration, demote. A hidden friend is
       most often added/removed *together with* its owner, so the owner
       legitimately exists on only one side — that side alone decides.
    2. Fall back to the friend function's own recorded origin
       (``change.symbol``) — covers both the case where the owner could not
       be resolved at all (older snapshot, DWARF-only path) *and* the case
       where the owner was found but its origin was inconclusive (present on
       only one side with an unknown origin, or the two sides disagree). For
       an in-class-defined hidden friend the owner and the friend function
       share the same declaration site, so this is often independent
       confirmation rather than a weaker signal.
    3. Otherwise the origin is unknown/unconfirmed: keep the finding
       (conservative — never silently hide a hidden-friend break) rather than
       claim a provenance-confirmed demotion that was never verified.
    """
    owner = change.caused_by_type
    if owner:
        # RecordType.name stays deliberately bare (model.py) — a namespaced
        # owner ("ns::Foo", from castxml/clang's qualified-name walk) would
        # never match origin_by_key's bare-name keys directly, so resolve
        # both the qualified spelling and its trailing ``::`` segment via
        # the same tiered lookup regardless of whether the owner itself is
        # namespaced (a bare owner degenerates to bare == owner). Each
        # side's *effective* origin falls back to its bare-name entry when
        # the type exists there without a qualified match — genuinely
        # distinct from a side that lacks the type entirely (added/removed
        # together with the friend) — so an unclassified-but-present owner
        # on one side can neither be silently ignored (it might disagree)
        # nor mistaken for proof of anything (Codex review).
        bare = owner.rsplit("::", 1)[-1] if "::" in owner else owner
        eff_old = _hidden_friend_owner_effective_origin(surf_old, owner, bare)
        eff_new = _hidden_friend_owner_effective_origin(surf_new, owner, bare)
        if eff_old is not None or eff_new is not None:
            if ScopeOrigin.PUBLIC_HEADER in (eff_old, eff_new):
                # Owner confidently public on either side — never let the
                # friend-function fallback below override that signal
                # (CodeRabbit review).
                return True, None
            reason = _hidden_friend_owner_reason_qualified(eff_old, eff_new)
            if reason is not None:
                return False, reason
            # Owner found but inconclusive (UNKNOWN on the only side that
            # has it, or the two sides disagree) — fall through to the
            # friend function's own origin (step 2) instead of returning
            # (True, None) here, exactly like the owner-not-found case.
    sym = change.symbol or ""
    # A plain both-sides-must-agree _origin_reason lookup treats a symbol
    # genuinely absent from one side (the common case: the friend function
    # itself was added/removed together with the finding, not just its
    # owner) identically to a side that has it but is UNKNOWN — starving
    # this fallback of the same one-sided relaxation already applied to
    # the owner above (Codex review). Resolve each side's own presence
    # first so an absent side can neither block nor fabricate a reason.
    eff_sym_old = _one_sided_key_origin(surf_old, sym, surf_old.all_symbols)
    eff_sym_new = _one_sided_key_origin(surf_new, sym, surf_new.all_symbols)
    if ScopeOrigin.PUBLIC_HEADER in (eff_sym_old, eff_sym_new):
        return True, None
    reason = _hidden_friend_owner_reason_qualified(eff_sym_old, eff_sym_new)
    if reason is not None:
        return False, reason
    return True, None


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
