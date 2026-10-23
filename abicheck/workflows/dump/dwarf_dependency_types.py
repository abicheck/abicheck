# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Dependency-internal record types of a DWARF-only snapshot that no public
declaration reaches.

A header-AST dump is scoped by :func:`resolve_dependency_scope`, which drops
every declaration whose own header is a dependency. A binary-plus-DWARF
snapshot has no header attribution, so nothing is scoped. A header-only
dependency compiled into the library then reports every template instance its
own code happened to emit -- ``basic_vec<float, avx2_tag>``, ``scratch_pad<T>``
and the tag structs -- as ``TYPE_ADDED``/``TYPE_REMOVED``, burying the changes
the library actually made.

This module answers, per snapshot, which record names are dependency internals
that no public declaration reaches. It does not drop them: the snapshot stays
whole, and :mod:`abicheck.policy.contract_dependency_origin` moves the matching
presence findings out of contract with a visible reason.

What the snapshot can say, and what it cannot:

* The public surface is the **ELF export table**, read through each exported
  symbol's demangled parameter list. A DWARF-only snapshot does not carry the
  exported functions' parameters itself (the DWARF function DIEs are
  declarations the extractor does not keep), so the mangled name is the record.
  Only top-level parameters are named by a signature; a template argument is
  reached through its template.
* "Dependency" means the declaring file (``source_location``, resolved for
  DWARF 4 and 5) is a toolchain/system header, or lies outside every *own*
  root. Own roots are the directories of non-template records an exported
  parameter names directly: the library's own declarations. With no own root
  nothing is classified (weaker evidence keeps the finding).
* A field can name a typedef alias (``V`` for ``lib::V``). The DWARF-only
  fallback keeps no typedef map, so the alias target is unknown. An unresolved
  by-value alias is bounded by its DWARF byte size: its target must have that
  size, so every dependency record of that size is kept. An unresolved pointer
  or reference could name any dependency type, so none is excluded.

Reachability reuses :func:`dependency_retention._directly_referenced_dependency_names`,
the same single-hop walk the header-AST path applies: a dependency type named
by a kept signature, field or base is reached; one reachable only through
another dependency's own internals is not.
"""

from __future__ import annotations

import copy
import posixpath
import re
from collections.abc import Iterable, Mapping, Sequence

from ...demangle import demangle
from ...extract.declaration_provenance import header_from_location
from ...extract.dependency_header_roots import is_dependency_header
from ...model import AbiSnapshot, EnumType, RecordType
from ...model.dwarf_facts import StructLayout
from .dependency_retention import (
    _candidate_identity,
    _directly_referenced_dependency_names,
)

__all__ = ["unreachable_dependency_type_names"]

_BUILTIN_WORDS = frozenset(
    {
        "void",
        "bool",
        "char",
        "signed",
        "unsigned",
        "short",
        "int",
        "long",
        "float",
        "double",
        "wchar_t",
        "char8_t",
        "char16_t",
        "char32_t",
        "auto",
    }
)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_QUALIFIER_SUFFIXES = ("&&", "&", "*", "const", "volatile")


def _is_under(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory + "/")


def _is_dependency_location(location: str | None, own_dirs: Sequence[str]) -> bool:
    header = header_from_location(location) or ""
    if is_dependency_header(header, None):
        return True
    return not any(_is_under(header, d) for d in own_dirs)


def _split_top_level(text: str, separator: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char in "<(":
            depth += 1
        elif char in ">)":
            depth -= 1
        elif char == separator and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return [part.strip() for part in parts if part.strip()]


def _strip_qualifiers(spelling: str) -> str:
    """*spelling* with trailing ``const``/``&``/``*`` and leading ``const``
    removed, template arguments kept: ``const basic_vec<float, T>&`` ->
    ``basic_vec<float, T>``."""
    text = spelling.strip()
    changed = True
    while changed:
        changed = False
        if text.startswith("const "):
            text = text[len("const ") :].strip()
            changed = True
        for suffix in _QUALIFIER_SUFFIXES:
            if text.endswith(suffix):
                text = text[: -len(suffix)].rstrip()
                changed = True
    return text


def _base_name(spelling: str) -> str:
    """*spelling* without its template arguments: ``basic_vec<float, T>`` ->
    ``basic_vec``."""
    return spelling.split("<", 1)[0].strip()


def _exported_parameter_lists(symbols: Iterable[str]) -> list[str]:
    """The parameter list (the text inside the outermost parentheses) of each
    exported C++ symbol that demangles."""
    lists: list[str] = []
    for symbol in symbols:
        if not symbol.startswith("_Z"):
            continue
        demangled = demangle(symbol)
        if not demangled or "(" not in demangled:
            continue
        head = demangled.split("(", 1)[0]
        arguments = demangled[len(head) + 1 :]
        lists.append(arguments[: arguments.rfind(")")])
    return lists


def _exported_names(snap: AbiSnapshot) -> tuple[set[str], set[str]]:
    """``(full, base)`` names of the top-level parameter types of every
    exported C++ function: the full spelling keeps its template arguments, the
    base name drops them."""
    full: set[str] = set()
    base: set[str] = set()
    elf = snap.elf
    symbols = elf.symbols if elf is not None else []
    for arguments in _exported_parameter_lists(sym.name for sym in symbols):
        for parameter in _split_top_level(arguments, ","):
            spelled = _strip_qualifiers(parameter)
            if not spelled or spelled == "void":
                continue
            full.add(spelled)
            base.add(_base_name(spelled))
    return full, base


def _own_directories(
    located: Sequence[RecordType | EnumType], exported_base: set[str]
) -> set[str]:
    dirs: set[str] = set()
    for record in located:
        if record.name not in exported_base or "<" in record.name:
            continue
        directory = posixpath.dirname(
            header_from_location(record.source_location) or ""
        )
        if directory:
            dirs.add(directory)
    return dirs


def _alias_targets(typedefs: Mapping[str, str]) -> dict[str, str]:
    """Spelling -> target for each typedef, by qualified key and, when the leaf
    names exactly one typedef, by that bare leaf too. A leaf shared by two
    typedefs is left out rather than guessed. Unknown targets (``?``) are
    dropped."""
    by_leaf: dict[str, list[str]] = {}
    for key in typedefs:
        by_leaf.setdefault(key.rsplit("::", 1)[-1], []).append(key)
    targets = {key: target for key, target in typedefs.items() if target != "?"}
    for leaf, keys in by_leaf.items():
        target = typedefs[keys[0]]
        if len(keys) == 1 and leaf not in targets and target != "?":
            targets[leaf] = target
    return targets


def _alias_resolved(record: RecordType, targets: Mapping[str, str]) -> RecordType:
    """*record* with each field spelled as a known typedef replaced by its
    target. A shallow copy: the snapshot is not modified."""
    if not any(f.type in targets for f in record.fields):
        return record
    view = copy.copy(record)
    view.fields = []
    for fld in record.fields:
        resolved = copy.copy(fld)
        resolved.type = targets.get(fld.type, fld.type)
        view.fields.append(resolved)
    return view


def _dwarf_structs(snap: AbiSnapshot) -> Mapping[str, StructLayout]:
    layout = snap.declarations.debug_layout
    return layout.structs if layout is not None else {}


def _record_size_bytes(snap: AbiSnapshot, record: RecordType | EnumType) -> int | None:
    if isinstance(record, RecordType) and record.size_bits:
        return record.size_bits // 8
    struct = _dwarf_structs(snap).get(record.name)
    return struct.byte_size if struct is not None and struct.byte_size else None


def _unresolved_alias_bounds(
    snap: AbiSnapshot, own_records: Sequence[RecordType], known: set[str]
) -> tuple[bool, set[int]]:
    """``(unbounded, sizes)`` for the own records' field spellings that name no
    known type: ``unbounded`` when one is a pointer or reference (its target is
    unconstrained), otherwise the byte sizes a by-value alias target must have."""
    unbounded = False
    sizes: set[int] = set()
    for record in own_records:
        struct = _dwarf_structs(snap).get(record.name)
        dwarf_fields = {f.name: f for f in struct.fields} if struct else {}
        for fld in record.fields:
            core = _strip_qualifiers(fld.type)
            if not core or "::" in core or "<" in core or core in known:
                continue
            if not _IDENTIFIER.fullmatch(core) or core in _BUILTIN_WORDS:
                continue
            if fld.type.rstrip().endswith(("*", "&", "&&")):
                unbounded = True
                continue
            dwarf_field = dwarf_fields.get(fld.name)
            if dwarf_field is None or not dwarf_field.byte_size:
                unbounded = True
            else:
                sizes.add(dwarf_field.byte_size)
    return unbounded, sizes


def unreachable_dependency_type_names(snap: AbiSnapshot) -> frozenset[str]:
    """Bare names of *snap*'s dependency-internal records no public declaration
    reaches. Empty when the snapshot is header-derived (already scoped by
    :func:`resolve_dependency_scope`) or when no own root can be established.

    Names are returned, not identities, because the presence findings the
    policy rule matches carry only the bare ``symbol``. A name that is also
    the name of an own record is withheld: the finding could be either.
    """
    if snap.from_headers:
        return frozenset()
    decls = snap.declarations
    records: list[RecordType | EnumType] = [*decls.types, *decls.enums]
    located = [r for r in records if r.source_location]
    if not located:
        return frozenset()

    exported_full, exported_base = _exported_names(snap)
    own_dirs = sorted(_own_directories(located, exported_base))
    if not own_dirs:
        return frozenset()

    dependency: list[RecordType | EnumType] = [
        r for r in located if _is_dependency_location(r.source_location, own_dirs)
    ]
    if not dependency:
        return frozenset()
    dependency_ids = {id(r) for r in dependency}
    own_types = [t for t in decls.types if id(t) not in dependency_ids]
    own_enums = [e for e in decls.enums if id(e) not in dependency_ids]

    targets = _alias_targets(decls.typedefs)
    known = {r.name for r in records} | exported_base | _BUILTIN_WORDS
    unbounded, alias_sizes = _unresolved_alias_bounds(snap, own_types, known)

    reached = _directly_referenced_dependency_names(
        decls.functions,
        decls.variables,
        [_alias_resolved(t, targets) for t in own_types],
        own_enums,
        dependency,
        decls.typedefs,
    )
    for record in dependency:
        if record.name in exported_full:
            reached.add(_candidate_identity(record))
        if unbounded or _record_size_bytes(snap, record) in alias_sizes:
            reached.add(_candidate_identity(record))

    own_names = {r.name for r in records if id(r) not in dependency_ids}
    return frozenset(
        r.name
        for r in dependency
        if _candidate_identity(r) not in reached and r.name not in own_names
    )
