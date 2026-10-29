# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""A dependency's internals that no public declaration of the library reaches.

One answer per snapshot, for both evidence paths, consumed by
:mod:`abicheck.policy.contract_dependency_origin`, which moves the matching
findings out of contract with a visible reason. Nothing is dropped here: the
snapshot stays whole.

* A **DWARF-only** snapshot has no header attribution;
  :func:`~abicheck.workflows.dump.dwarf_dependency_types.unreachable_dependency_type_names`
  answers for its records (ADR-089 D2-D4).
* A **header-derived** snapshot is answered here (ADR-089's 2026-10-25
  amendment). Dump-time scoping (``resolve_dependency_scope``) drops only
  toolchain/system declarations, so a header-only dependency reached through
  ``-I`` (``xvec/include``) is kept whole: its template instances, hidden
  friends, inline variables and member typedefs then change with every
  ``-mavx2``/``-mavx512f`` rebuild.

**Which declarations are the dependency's.** Two tests, both required:

1. *Path.* The library's public headers (``ScopeOrigin.PUBLIC_HEADER``, the
   ``-H`` set) are its own. Every other declaring header is placed under its
   *root*: the deepest of the parse's recorded include directories
   (``-I``/``-isystem``/``-iquote``/``-idirafter`` in
   ``AbiSnapshot.ast_compile_args``) and the public headers' own directories
   that contains it. A header whose root also holds a public header is the
   library's; one whose root holds none, or that lies under no root at all,
   is a dependency header. So ``xvec/include`` nested under the library's own
   ``-I .`` is still a dependency root, and without recorded include
   directories a header outside every public header's directory is one.
2. *Namespace.* The declaration's outermost namespace is not one the
   library's own declarations use. A library's private header outside its
   public directories (a generated ``config.h``) therefore never loses its
   ``lib::`` entities on path evidence alone, and a dependency declaring into
   the global namespace is not classified when the library also does.

With no public header nothing is classified (weaker evidence keeps the
finding).

**What "reached" means, entity by entity.**

* A dependency **record** is reached when a public declaration of the library
  names it directly (``dependency_retention.directly_referenced_dependency_names``,
  the single-hop walk dump-time scoping already applies, typedef aliases
  resolved). ``basic_vec<float>`` named by ``State::acc``'s ``V`` is reached
  and stays in contract, layout and presence; ``simd_fixed_size_abi<8>``,
  reachable only as its template argument, is not.
* A dependency **function or variable** is the library's only when the
  library's own binary defines it: a ``GLOBAL`` ELF export, a Mach-O export
  that is not a weak definition, any PE export (a PE export directory records
  no binding, so on PE the dependency's exported instances stay in contract:
  ADR-089's PE limit). A hidden friend of a reached record is
  no exception: it is the dependency's API on that type, compiled into every
  consumer from the dependency's own header, and the library promises nothing
  about it. What the library does promise about the type -- its identity, size
  and layout wherever its declarations use it -- keeps gating through the
  record's own findings. A ``WEAK``/``GNU_UNIQUE`` ELF export or a Mach-O weak
  definition is a COMDAT copy of such an entity, so it does not make one the
  library's, and one declared in a dependency-only namespace is internal even
  when no header declaration names it (``fakesimd::reduce<float>``, emitted by
  the library's own build).
* A dependency **typedef** (a member of a dependency record, or one in a
  dependency-only namespace) is reached when its name occurs in a public
  declaration's signature, field or default, or in the library's own header
  text (``AbiSnapshot.public_header_identifiers``). A member typedef of a
  reached record follows the hidden-friend rule. Without that header-text
  scan no typedef is classified: a name the headers spell could read
  unreached.

Findings name records and typedefs by bare name, so a bare name shared with
one of the library's own entities is withheld: the finding could be either.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import TypeVar

from ...compare.template_surface import qualified_name_lookup, strip_template_args
from ...extract.flat_map_dependency_scope import kept_reference_text
from ...model import AbiSnapshot, EnumType, Function, RecordType, ScopeOrigin, Variable
from ...model.declaration_headers import declaring_header_set
from ...model.elf_facts import SymbolBinding
from ...model.export_index import build_raw_export_index, linked_export_names
from ...model.surface_facts import in_public_surface
from ...model.synthetic_key import synthetic_ctor_owner
from ...model.vocabulary import NON_PUBLIC_ORIGINS
from ...policy.contract_dependency_origin import DependencyInternals
from .dependency_retention import (
    candidate_spelling,
    directly_referenced_dependency_names,
    kept_signature_haystack,
)
from .dwarf_dependency_types import unreachable_dependency_type_reading

__all__ = ["dependency_internals", "include_roots", "itanium_top_namespace"]

#: Longest first, so ``-isystem`` is not read as ``-i`` + ``system``.
_INCLUDE_FLAGS = ("-idirafter", "-isystem", "-iquote", "-I")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LENGTH = re.compile(r"\d+")

_Declaration = Function | Variable | RecordType | EnumType
_D = TypeVar("_D", Function, Variable, RecordType | EnumType)


def dependency_internals(snap: AbiSnapshot) -> DependencyInternals:
    """*snap*'s dependency internals no public declaration reaches.

    Empty when the snapshot cannot tell its own declarations from a
    dependency's. ``record_names`` is always filled: it is the side's
    presence evidence for a finding raised on a record, internal or not.
    """
    if not snap.from_headers:
        reading = unreachable_dependency_type_reading(snap)
        internals = DependencyInternals(types=reading.names, types_read=reading.read)
    else:
        internals = _header_dependency_internals(snap)
    decls = snap.declarations
    records: list[RecordType | EnumType] = [*decls.types, *decls.enums]
    return replace(internals, record_names=frozenset(r.name for r in records))


def include_roots(compile_args: Sequence[str]) -> tuple[str, ...]:
    """The include-search directories *compile_args* name, normalized, in
    order, de-duplicated: ``-I dir``, ``-Idir``, and the ``-isystem``/
    ``-iquote``/``-idirafter`` forms."""
    roots: list[str] = []
    index = 0
    while index < len(compile_args):
        arg = compile_args[index]
        for flag in _INCLUDE_FLAGS:
            if arg == flag:
                if index + 1 < len(compile_args):
                    roots.append(compile_args[index + 1])
                index += 1
                break
            if arg.startswith(flag):
                roots.append(arg[len(flag) :])
                break
        index += 1
    return tuple(dict.fromkeys(_normalized(r) for r in roots if r))


def itanium_top_namespace(mangled: str) -> str | None:
    """The outermost scope an Itanium-mangled name is declared in (``""``
    for the global namespace), or ``None`` when the prefix is not one this
    reads: ``_ZN8fakesimd6reduceIfEE...`` -> ``fakesimd``."""
    if not mangled.startswith("_Z"):
        return None
    rest = mangled[2:]
    nested = rest.startswith("N")
    if nested:
        rest = rest[1:].lstrip("rVK").removeprefix("R").removeprefix("O")
    if rest.startswith("St"):
        return "std"
    length = _LENGTH.match(rest)
    if length is None:
        return None
    if not nested:
        return ""
    return rest[length.end() : length.end() + int(length.group(0))]


def _normalized(path: str) -> str:
    return posixpath.normpath(path.replace("\\", "/"))


def _is_under(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory.rstrip("/") + "/")


def _root_of(header: str, roots: Sequence[str]) -> str | None:
    """The deepest of *roots* containing *header*, or ``None``."""
    containing = [r for r in roots if _is_under(header, r)]
    return max(containing, key=len) if containing else None


def _dependency_header_predicate(
    snap: AbiSnapshot, public: set[str]
) -> Callable[[str | None], bool]:
    """``is_dependency(source_header)`` by the path test (module docstring)."""
    roots = (
        *include_roots(snap.ast_compile_args),
        *sorted({posixpath.dirname(h) for h in public}),
    )
    own_roots = {r for r in roots if any(_is_under(h, r) for h in public)}
    memo: dict[str, bool] = {}

    def is_dependency(source_header: str | None) -> bool:
        if not source_header:
            return False
        header = _normalized(source_header)
        if header not in memo:
            memo[header] = header not in public and (
                _root_of(header, roots) not in own_roots
            )
        return memo[header]

    return is_dependency


def _split_scope(qualified: str) -> tuple[str, str]:
    """``(scope, leaf)`` of *qualified* at its last top-level ``::``:
    ``ns::A<x::y>::t`` -> ``("ns::A<x::y>", "t")``."""
    depth = 0
    cut = -1
    for index, char in enumerate(qualified):
        if char in "<(":
            depth += 1
        elif char in ">)":
            depth -= 1
        elif depth == 0 and qualified.startswith("::", index):
            cut = index
    if cut < 0:
        return "", qualified
    return qualified[:cut], qualified[cut + 2 :]


def _top_namespace(qualified: str) -> str:
    """The outermost scope of *qualified* (``""`` for the global one)."""
    scope, leaf = _split_scope(qualified)
    if not scope:
        return ""
    while scope:
        scope, leaf = _split_scope(scope)
    return leaf


def _library_provided_names(snap: AbiSnapshot) -> frozenset[str] | None:
    """The names *snap*'s binary defines as its own, or ``None`` when it has
    no export table (a symbol's non-export cannot be proven then).

    A coalesced (COMDAT) export is not one of them: :func:`_coalesced_export_names`.
    """
    elf = snap.elf
    if elf is not None and elf.symbols:
        return frozenset(
            s.name for s in elf.symbols if s.binding is SymbolBinding.GLOBAL
        )
    index = build_raw_export_index(snap)
    if index is None:
        return None
    return linked_export_names(index) - _coalesced_export_names(snap)


def _coalesced_export_names(snap: AbiSnapshot) -> frozenset[str]:
    """Exports *snap*'s binary marks as a coalesced definition, which every
    translation unit that uses the entity may emit: an ELF ``WEAK``/
    ``GNU_UNIQUE`` binding, a Mach-O weak definition (``N_WEAK_DEF``, the
    export trie's ``EXPORT_SYMBOL_FLAGS_WEAK_DEFINITION``). A PE export
    directory records no such flag (the linker resolved COMDAT selection
    away), so none is read there: ADR-089's PE limit.
    """
    if snap.elf is not None:
        return frozenset(
            s.name
            for s in snap.elf.symbols
            if s.binding in (SymbolBinding.WEAK, SymbolBinding.UNIQUE)
        )
    if snap.macho is not None:
        return frozenset(e.name for e in snap.macho.exports if e.is_weak)
    return frozenset()


def _weak_exports_in(snap: AbiSnapshot, namespaces: set[str]) -> frozenset[str]:
    """Coalesced exports declared in one of *namespaces*."""
    return frozenset(
        name
        for name in _coalesced_export_names(snap)
        if itanium_top_namespace(name) in namespaces
    )


def _declaration_namespace(
    decl: Function | Variable, name_of: Callable[[Function | Variable], str]
) -> str:
    """*decl*'s outermost namespace, read from its linkage name where it has
    one: castxml keys an implicit constructor or destructor by a synthetic
    ``__abicheck_ctor__ns::C(...)``/``~ns::C`` spelling, which no qualified-name
    reader resolves."""
    key = _key(decl)
    owner = synthetic_ctor_owner(key)
    if owner is not None:
        return _top_namespace(owner)
    if key.startswith("~"):
        return _top_namespace(key[1:])
    mangled = itanium_top_namespace(key)
    return mangled if mangled is not None else _top_namespace(name_of(decl))


def _key(decl: Function | Variable) -> str:
    return decl.mangled or decl.name


def _overload_stems(
    snap: AbiSnapshot,
    name_of: Callable[[Function | Variable], str],
    internal: frozenset[str],
) -> frozenset[str]:
    """Stems (``OVERLOAD_SET_REROUTED``'s ``symbol``) under which every
    function of *snap* is internal."""
    internal_stems: set[str] = set()
    other_stems: set[str] = set()
    for fn in snap.declarations.functions:
        stem = strip_template_args(name_of(fn))
        (internal_stems if _key(fn) in internal else other_stems).add(stem)
    return frozenset(internal_stems - other_stems)


def _internal_typedefs(
    snap: AbiSnapshot,
    is_dependency: Callable[[str | None], bool],
    own_records: Sequence[RecordType | EnumType],
    dep_only_namespaces: set[str],
    referenced: set[str],
) -> frozenset[str]:
    """Bare names of the dependency typedefs no public declaration names."""
    qualified = snap.declarations.typedefs_qualified
    own_scopes = {candidate_spelling(r) for r in own_records}

    def is_dependency_typedef(key: str) -> bool:
        if _top_namespace(key) not in dep_only_namespaces:
            return False
        headers = declaring_header_set(qualified, key)
        if headers:
            return all(is_dependency(h) for h in headers)
        # A member of one of the library's own records is the library's,
        # whatever namespace it sits in.
        return _split_scope(key)[0] not in own_scopes

    internal: set[str] = set()
    kept: set[str] = set()
    for key in qualified:
        leaf = _split_scope(key)[1]
        if is_dependency_typedef(key) and leaf not in referenced:
            internal.add(leaf)
        else:
            kept.add(leaf)
    # A bare key no qualified entry explains has unknown origin: kept.
    kept |= {key for key in snap.declarations.typedefs if key not in internal}
    return frozenset(internal - kept)


def _header_dependency_internals(snap: AbiSnapshot) -> DependencyInternals:
    decls = snap.declarations
    records: list[RecordType | EnumType] = [*decls.types, *decls.enums]
    every: list[_Declaration] = [*decls.functions, *decls.variables, *records]
    public = {
        _normalized(d.source_header)
        for d in every
        if d.origin is ScopeOrigin.PUBLIC_HEADER and d.source_header
    }
    if not public:
        return DependencyInternals()
    at_dependency_path = _dependency_header_predicate(snap, public)
    name_of = qualified_name_lookup(snap)

    def namespace(decl: _Declaration) -> str:
        if isinstance(decl, Function | Variable):
            return _declaration_namespace(decl, name_of)
        return _top_namespace(candidate_spelling(decl))

    own_namespaces = {
        namespace(d) for d in every if not at_dependency_path(d.source_header)
    }
    dep_only_namespaces = {
        namespace(d) for d in every if at_dependency_path(d.source_header)
    } - own_namespaces

    def is_dependency(decl: _Declaration) -> bool:
        return namespace(decl) in dep_only_namespaces and at_dependency_path(
            decl.source_header
        )

    def split(items: Sequence[_D]) -> tuple[list[_D], list[_D]]:
        dep = [d for d in items if is_dependency(d)]
        dep_ids = {id(d) for d in dep}
        return dep, [d for d in items if id(d) not in dep_ids]

    dep_functions, own_functions = split(decls.functions)
    dep_variables, own_variables = split(decls.variables)
    dep_records, own_records = split(records)
    if not (dep_functions or dep_variables or dep_records):
        return DependencyInternals()

    # The same public roots dump-time scoping retains dependency types for.
    root_functions = [
        f
        for f in own_functions
        if in_public_surface(f) and f.origin not in NON_PUBLIC_ORIGINS
    ]
    root_variables = [
        v
        for v in own_variables
        if in_public_surface(v) and v.origin not in NON_PUBLIC_ORIGINS
    ]
    root_types = [
        t
        for t in own_records
        if isinstance(t, RecordType) and t.origin not in NON_PUBLIC_ORIGINS
    ]
    root_enums = [
        e
        for e in own_records
        if isinstance(e, EnumType) and e.origin not in NON_PUBLIC_ORIGINS
    ]
    reached = directly_referenced_dependency_names(
        root_functions,
        root_variables,
        root_types,
        root_enums,
        dep_records,
        decls.typedefs,
    )
    own_names = {r.name for r in own_records}
    types = frozenset(
        r.name
        for r in dep_records
        if candidate_spelling(r) not in reached and r.name not in own_names
    )

    symbols: frozenset[str] = frozenset()
    stems: frozenset[str] = frozenset()
    provided = _library_provided_names(snap)
    if provided is not None:
        own_keys = {_key(d) for d in own_functions} | {_key(d) for d in own_variables}
        declared = {_key(d) for d in dep_functions} | {_key(d) for d in dep_variables}
        declared -= provided
        symbols = frozenset(
            (declared | _weak_exports_in(snap, dep_only_namespaces)) - own_keys
        )
        stems = _overload_stems(snap, name_of, symbols)

    # A typedef is named by spelling, and the instantiation-independent
    # spelling evidence is the library's own header text. Without that scan
    # (not run, or it could not vouch for completeness) a dependency typedef
    # the headers name could read unreached, so none is classified.
    typedefs: frozenset[str] = frozenset()
    if snap.public_header_identifiers is not None:
        reference_text = kept_reference_text(
            kept_signature_haystack(root_functions, root_variables, root_types),
            root_functions,
            root_types,
            root_enums,
        )
        referenced = set(_IDENTIFIER.findall(reference_text))
        referenced |= snap.public_header_identifiers
        typedefs = _internal_typedefs(
            snap,
            at_dependency_path,
            own_records,
            dep_only_namespaces,
            referenced,
        )
    return DependencyInternals(
        types=types, typedefs=typedefs, symbols=symbols, overload_stems=stems
    )
