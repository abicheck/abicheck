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

from .model import Visibility

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
    }
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


def _symbol_keys(name: str, mangled: str) -> set[str]:
    """All identifier encodings under which a symbol may appear in a Change."""
    keys = {k for k in (name, mangled) if k}
    if name and "::" in name:
        keys.add(name.rsplit("::", 1)[1])
    return keys


def _index_surface_types(snap: AbiSnapshot, surface: PublicSurface) -> dict[str, RecordType]:
    """Populate ``surface.all_types`` and return a name -> record index.

    Records are indexed by both their full name and (for namespaced types) the
    trailing ``::`` segment, so the closure walk can match either encoding.
    """
    record_by_name: dict[str, RecordType] = {}
    for rec in snap.types:
        surface.all_types.add(rec.name)
        record_by_name[rec.name] = rec
        if "::" in rec.name:
            record_by_name.setdefault(rec.name.rsplit("::", 1)[1], rec)
    for en in snap.enums:
        surface.all_types.add(en.name)
    for alias in snap.typedefs:
        surface.all_types.add(alias)
    return record_by_name


def _seed_public_roots(snap: AbiSnapshot, surface: PublicSurface) -> tuple[set[str], bool]:
    """Record public symbols on *surface*; return (seed type names, has_public).

    Seeds the type-closure work-list from the return/parameter/variable types of
    every :data:`Visibility.PUBLIC` function and variable.
    """
    seed_types: set[str] = set()
    has_public = False
    for fn in snap.functions:
        keys = _symbol_keys(fn.name, fn.mangled)
        surface.all_symbols |= keys
        if fn.visibility == Visibility.PUBLIC:
            has_public = True
            surface.public_symbols |= keys
            seed_types |= _type_identifiers(fn.return_type)
            for p in fn.params:
                seed_types |= _type_identifiers(getattr(p, "type", None))
    for var in snap.variables:
        keys = _symbol_keys(var.name, var.mangled)
        surface.all_symbols |= keys
        if var.visibility == Visibility.PUBLIC:
            has_public = True
            surface.public_symbols |= keys
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
        for f in rec_node.fields:
            for ident in _type_identifiers(f.type):
                if ident not in seen:
                    queue.append(ident)
        for base in rec_node.bases:
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

    # Scoping only makes sense when we actually have header-derived public
    # visibility. Without headers every symbol is ELF_ONLY (ADR-016) and a
    # surface filter would hide everything — so declare it unresolvable.
    surface.resolvable = has_public and not getattr(snap, "elf_only_mode", False)
    if not surface.resolvable:
        return surface

    # Transitive closure over the record/typedef graph.
    _walk_type_closure(snap, surface, record_by_name, seed_types)
    return surface


def change_in_public_surface(
    change: Change,
    surf_old: PublicSurface,
    surf_new: PublicSurface,
) -> bool:
    """Return ``True`` if *change* concerns the public ABI surface.

    Conservative by construction (ADR-024 §D5): leak findings, unknown
    symbols, and unknown types all return ``True`` so scoping can only
    ever remove findings it is *confident* are private.
    """
    if change.kind.value in _NEVER_FILTER_KIND_NAMES:
        return True
    if not (surf_old.resolvable or surf_new.resolvable):
        return True

    public_symbols = surf_old.public_symbols | surf_new.public_symbols
    all_symbols = surf_old.all_symbols | surf_new.all_symbols
    public_types = surf_old.public_types | surf_new.public_types
    all_types = surf_old.all_types | surf_new.all_types

    sym = change.symbol or ""
    # Symbol-level finding (function/variable): public iff a public symbol.
    if sym in all_symbols:
        return sym in public_symbols
    if sym and "::" in sym and sym.rsplit("::", 1)[1] in all_symbols:
        return sym.rsplit("::", 1)[1] in public_symbols

    # Type-level finding: check the implicated type name(s). A finding is
    # in-surface if *any* implicated type is reachable from the public API.
    candidates = _type_identifiers(sym) | _type_identifiers(change.caused_by_type)

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
        return True

    known = {c for c in candidates if c in all_types}
    if not known:
        # We cannot place this finding — keep it (never hide an unknown).
        return True
    return bool(known & public_types)
