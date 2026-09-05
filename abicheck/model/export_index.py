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

"""ADR-063 T7 — one canonical raw export index, with named projections.

Before this module, at least six call sites each kept their own copy of "read
a snapshot's/binary's platform export table" (``policy.depth_projection.
_exported_symbol_names``, ``buildsource.crosscheck_base._exported_symbol_names``
/``_linked_export_symbols``, ``buildsource.snapshot_exports.
exported_symbols_from_snapshot``, ``post_manifest._exported_symbol_names``,
``diff_unnamed_types._exported_symbol_names``, ``buildsource.poi._exported_names``)
— each re-reading ``snap.elf``/``snap.pe``/``snap.macho`` (or a raw
``ElfMetadata``) itself and each drifting slightly from the others on real
distinctions: whether a non-default ELF version alias counts, whether a
Mach-O name gets its leading underscore stripped once or left alone, whether
an ELF symbol's callable-vs-data type matters, and whether "no platform table
at all" is distinguished from "a table that parsed to zero entries."

The fix is not one universal set-of-strings helper — that would erase exactly
the distinctions each call site individually earned (Codex review comments
across several separate PRs). Instead: :func:`build_raw_export_index` is the
*one* place that reads a snapshot's or a raw platform-metadata object's export
table, into :class:`RawExportIndex` — unfiltered, unnormalized, one row per
raw table entry. Every caller's own distinction becomes a small, named,
independently testable *projection* function over that one raw shape, so a
change to how ELF/PE/Mach-O tables are read (a new field, a parsing fix)
lands on every consumer at once instead of needing each duplicate edited
independently.

**Missing vs. confirmed-empty is structural, not a convention callers must
remember.** ``build_raw_export_index`` returns ``None`` when *no* platform
export table exists at all (a synthetic/incomplete snapshot, or a source-only
one) and a real ``RawExportIndex`` — with ``entries == ()`` when the table
genuinely parsed to nothing — otherwise. A real hidden-only library
legitimately exports nothing and that must still read as "confirmed empty,
this binary has no public surface," never conflated with "cannot confirm
either way." Every projection below inherits this: they all take an already
non-``None`` ``RawExportIndex`` (the caller decided what "no table" means for
its own purpose, same as each of the five originals did), never re-derive the
missing/empty distinction themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .elf_facts import ElfMetadata
    from .macho_facts import MachoMetadata
    from .pe_facts import PeMetadata
    from .snapshot import AbiSnapshot

__all__ = [
    "RawExportEntry",
    "RawExportIndex",
    "all_export_names",
    "build_raw_export_index",
    "build_raw_export_index_from_elf",
    "build_raw_export_index_from_macho",
    "build_raw_export_index_from_pe",
    "callable_export_names",
    "default_versioned_names",
    "export_names_or_modeled_fallback",
    "linked_export_names",
    "macho_callable_names",
    "named_pe_exports",
    "ordinal_only_pe_exports",
]

#: Which platform table a :class:`RawExportIndex` was read from.
Platform = Literal["elf", "pe", "macho"]


@dataclass(frozen=True)
class RawExportEntry:
    """One raw platform export-table row — no filtering, no normalization.

    ``name`` may be empty (a PE export-by-ordinal carries no name at all —
    see :func:`ordinal_only_pe_exports`). ``is_default`` is only a real
    ELF/COFF-versioning distinction on ELF (``True`` for every PE/Mach-O
    entry — those formats have no symbol-versioning concept, so nothing to
    demote). ``ordinal`` is populated for PE only. ``sym_type`` carries the
    ELF ``SymbolType`` member *name* (``"FUNC"``/``"OBJECT"``/``"NOTYPE"``/…,
    matching ``ElfSymbol.sym_type.name`` — the same spelling
    ``post_manifest``'s callable-type filter already compared against) —
    ``None`` for PE/Mach-O, which carry no equivalent function-vs-data
    distinction in their export directories. ``is_data`` is populated for
    Mach-O only (``True`` for a ``__DATA``-segment/global-variable export) —
    ``None`` for ELF/PE, which use ``sym_type``/no such facet at all instead.
    """

    name: str
    is_default: bool = True
    ordinal: int | None = None
    sym_type: str | None = None
    is_data: bool | None = None


@dataclass(frozen=True)
class RawExportIndex:
    """A snapshot's (or a raw platform-metadata object's) unfiltered export table."""

    platform: Platform
    entries: tuple[RawExportEntry, ...]


def build_raw_export_index_from_elf(elf_meta: ElfMetadata) -> RawExportIndex:
    """*elf_meta*'s dynamic symbol table as a :class:`RawExportIndex`.

    Every ``.gnu.dynsym`` entry becomes a row, default-versioned or not,
    named or not, callable or not — filtering is a projection's job, not
    this constructor's.
    """
    return RawExportIndex(
        platform="elf",
        entries=tuple(
            RawExportEntry(
                name=s.name,
                is_default=s.is_default,
                sym_type=s.sym_type.name,
            )
            for s in elf_meta.symbols
        ),
    )


def build_raw_export_index_from_pe(pe_meta: PeMetadata) -> RawExportIndex:
    """*pe_meta*'s export directory as a :class:`RawExportIndex`.

    An ordinal-only export (no name in the export directory) still becomes a
    row, with ``name == ""`` — see :func:`ordinal_only_pe_exports`.
    """
    return RawExportIndex(
        platform="pe",
        entries=tuple(
            RawExportEntry(name=e.name, ordinal=e.ordinal) for e in pe_meta.exports
        ),
    )


def build_raw_export_index_from_macho(macho_meta: MachoMetadata) -> RawExportIndex:
    """*macho_meta*'s export list as a :class:`RawExportIndex`.

    Names are exactly as ``macho_metadata`` parsed them — still carrying the
    platform's own single leading underscore (``_foo``, ``__Z3fooi``) at this
    layer; stripping it is :func:`default_versioned_names`'s job, not this
    constructor's.
    """
    return RawExportIndex(
        platform="macho",
        entries=tuple(
            RawExportEntry(name=e.name, is_data=e.is_data) for e in macho_meta.exports
        ),
    )


def build_raw_export_index(snap: AbiSnapshot) -> RawExportIndex | None:
    """*snap*'s raw platform export table, or ``None`` with no table at all.

    Reads whichever of ``snap.elf``/``snap.pe``/``snap.macho`` is populated
    (a real snapshot carries at most one — ELF, PE, and Mach-O are mutually
    exclusive container formats). ``None`` only when none of the three is
    set — never an index with ``entries == ()`` standing in for "no
    evidence"; see this module's own docstring for why that distinction is
    structural rather than a per-caller convention.
    """
    if snap.elf is not None:
        return build_raw_export_index_from_elf(snap.elf)
    if snap.pe is not None:
        return build_raw_export_index_from_pe(snap.pe)
    if snap.macho is not None:
        return build_raw_export_index_from_macho(snap.macho)
    return None


# ---------------------------------------------------------------------------
# Named projections
# ---------------------------------------------------------------------------


def _strip_macho_leading_underscore(name: str) -> str:
    return name[1:] if name.startswith("_") else name


def default_versioned_names(
    index: RawExportIndex, *, normalize_macho: bool = True
) -> frozenset[str]:
    """The "is this symbol a real, unversioned export" projection.

    Only **default/unversioned** ELF exports count: a symbol that exists
    *only* as a non-default version alias (``foo@LIB_1``, ``is_default ==
    False``) does not satisfy an unversioned consumer link (which needs
    ``foo@@…``) — including it would mask the exact missing-export case this
    set is meant to catch. Every named PE export counts (PE has no
    equivalent versioning concept). Mach-O names get the dumper's own
    normalization applied by default (``_foo`` → ``foo``, ``__Z...`` →
    ``_Z...``) so the result matches ``Function.mangled``/``Variable.mangled``
    spelling instead of flagging every C/C++ symbol as missing; pass
    ``normalize_macho=False`` for a caller that needs the once-stripped,
    still-platform-native spelling instead (see :func:`linked_export_names`).

    Formerly ``policy.depth_projection._exported_symbol_names`` /
    ``buildsource.crosscheck_base._exported_symbol_names``.
    """
    if index.platform == "elf":
        return frozenset(e.name for e in index.entries if e.name and e.is_default)
    if index.platform == "pe":
        return frozenset(e.name for e in index.entries if e.name)
    if normalize_macho:
        return frozenset(
            _strip_macho_leading_underscore(e.name) for e in index.entries if e.name
        )
    return frozenset(e.name for e in index.entries if e.name)


def linked_export_names(index: RawExportIndex) -> frozenset[str]:
    """Exported names in the **L4 source-linker's** own keyspace.

    Identical to :func:`default_versioned_names` except Mach-O names are
    *not* re-normalized: ``macho_metadata`` already strips the platform's
    one leading underscore, and the L4 linker keeps that once-stripped form
    (a C++ export is stored as ``_Z…``, and stripping again — as the default
    projection does, to match the dumper's *doubly*-stripped ``Function.
    mangled`` — would make a correctly relinked macOS C++ surface intersect
    nothing). Formerly ``buildsource.crosscheck_base._linked_export_symbols``.
    """
    return default_versioned_names(index, normalize_macho=False)


def named_pe_exports(index: RawExportIndex) -> frozenset[str]:
    """PE exports that carry a real name, excluding ordinal-only entries."""
    return frozenset(e.name for e in index.entries if e.name)


def ordinal_only_pe_exports(index: RawExportIndex) -> frozenset[int]:
    """Ordinals of PE exports with **no** name at all (export/import-by-ordinal).

    A PE export directory entry always carries an ordinal; a subset carry no
    name (``ImportByOrdinal``-style consumption on the importing side, or a
    deliberately unnamed export on the exporting side). Those never satisfy a
    *named*-export lookup (:func:`named_pe_exports`) and need their own view.
    """
    return frozenset(
        e.ordinal for e in index.entries if not e.name and e.ordinal is not None
    )


def macho_callable_names(index: RawExportIndex) -> frozenset[str]:
    """Mach-O exports that are callable (not ``__DATA``-segment) symbols.

    No leading-underscore normalization is applied here at all — this is the
    convention ``post_manifest``'s own binary-path validation path used
    instead of either of :func:`default_versioned_names`'s or
    :func:`linked_export_names`'s underscore handling, since a data export
    (``is_data``) is what it needs to exclude, not the export's spelling.
    Formerly ``post_manifest._exported_names_for_binary``'s Mach-O branch.
    """
    return frozenset(e.name for e in index.entries if e.name and not e.is_data)


def callable_export_names(
    index: RawExportIndex, callable_sym_types: frozenset[str]
) -> frozenset[str]:
    """Default-versioned ELF exports whose symbol type is in *callable_sym_types*.

    A data ``OBJECT`` symbol sharing a name with a promised callable export
    does not satisfy a client compiled to call it — this is the projection an
    ABI-commitment consumer (POST's ``pp_*`` C-callable contract) needs
    instead of a bare name set. Formerly ``post_manifest._exported_symbol_names``.
    """
    return frozenset(
        e.name
        for e in index.entries
        if e.name and e.is_default and e.sym_type in callable_sym_types
    )


def all_export_names(index: RawExportIndex) -> frozenset[str]:
    """Every named export, regardless of default-version status.

    For a consumer that cares whether a spelling was exported *at all* — a
    non-default version alias included — not just the unversioned/default
    one (e.g. ``diff_unnamed_types``, which must catch a newly-introduced
    unnamed-type mangling leaking through any exported alias, versioned or
    not).
    """
    return frozenset(e.name for e in index.entries if e.name)


def export_names_or_modeled_fallback(snap: AbiSnapshot) -> tuple[str, ...]:
    """Exported (mangled) symbol names already parsed into *snap* — no re-dump.

    The authoritative export set is the raw platform table
    (:func:`build_raw_export_index`), read in the L4 source-linker's own
    keyspace (:func:`linked_export_names`) — the modeled ``functions``/
    ``variables`` lists are a *narrower*, DWARF-shaped view that covers only
    a fraction of the exports and can carry non-ABI ctor/dtor linkage tags
    (GCC's unified ``C4``/``D4``) that are not real exports, so they are used
    **only** as a fallback for a snapshot with no raw table at all (a
    source-only snapshot, or a format whose export table did not parse). A
    parsed platform table is authoritative even when empty: a hidden-only
    library genuinely exports nothing, and its DWARF-modeled ``functions``
    must not be relinked as if they were.

    Formerly ``buildsource.snapshot_exports.exported_symbols_from_snapshot``.
    """
    index = build_raw_export_index(snap)
    if index is not None:
        return tuple(sorted(linked_export_names(index)))
    syms = {fn.mangled for fn in snap.functions if fn.mangled}
    syms |= {v.mangled for v in snap.variables if getattr(v, "mangled", "")}
    syms.discard("")
    return tuple(sorted(syms))
