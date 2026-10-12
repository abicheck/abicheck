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

"""Correlate ``.gnu.version`` entries onto already-parsed ELF symbols.

The second full walk of a symbol table a parse performs: the first builds
the ``ElfSymbol``/``ElfImport`` lists, this one attaches each entry's GNU
symbol version and default/hidden binding by walking the table again in
ordinal order.

Split out of ``elf_metadata.py`` rather than left beside its caller because
that module carries an ``architecture/debt.yaml`` no-growth baseline, and
the right way to stay under one is to move responsibility out (AGENTS.md:
"the way to shrink an entry is to move responsibility out to a properly-
owned module, never to trim the file to fit"). Reading a binary is
``extract``'s job, so this is where it belongs regardless.
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from elftools.elf.enums import ENUM_VERSYM

if TYPE_CHECKING:  # pragma: no cover - typing only
    from elftools.elf.gnuversions import GNUVerDefSection, GNUVerSymSection
    from elftools.elf.sections import SymbolTableSection

    from ..model.elf_facts import ElfMetadata

from ..model.elf_facts import ElfVersionBinding, SymbolBinding
from .elf_string_table import buffered_string_table, string_table_of
from .elf_symbol_tables import _BINDING_MAP, _HIDDEN_VISIBILITIES
from .parse_failures import note_export_read_failure

__all__ = [
    "apply_versions_to_symbols",
    "build_verdef_index",
    "correlate_symbol_versions",
    "decode_versym",
    "parse_ver_entries",
    "symbols_of",
]

log = logging.getLogger(__name__)

#: ``VER_NDX_GLOBAL``: the index the ``VER_FLG_BASE`` definition carries, and
#: the one every exported symbol a version script names in no node gets.
_BASE_INDEX = 1

#: Attribute holding a symbol table's fully-walked entries on the section
#: object itself. A parse walks ``.dynsym`` twice -- once to build the
#: symbol lists, once to attach versions -- and every ``iter_symbols`` entry
#: is a pyelftools ``construct`` parse plus a string-table read. Stored on
#: the section (whose class defines ``__eq__`` without ``__hash__``, so it
#: cannot key a mapping), the cache lives exactly as long as the ``ELFFile``
#: that owns it.
_SYMBOLS_ATTR = "_abicheck_walked_symbols"


def symbols_of(section: Any) -> Iterator[Any]:
    """``section.iter_symbols()``, parsed at most once per section object.

    Lazy on the first walk -- entries are yielded as they are decoded, so a
    table that fails part-way raises at the same entry as before -- and
    cached only once that walk completes.
    """
    cached = getattr(section, _SYMBOLS_ATTR, None)
    if type(cached) is list:  # only what this function stored, never a stand-in
        yield from cached
        return
    walked: list[Any] = []
    for sym in section.iter_symbols():
        walked.append(sym)
        yield sym
    setattr(section, _SYMBOLS_ATTR, walked)


#: ``.gnu.version`` values pyelftools names rather than returning as an int.
_NAMED_VERSYM = {v: k for k, v in ENUM_VERSYM.items() if isinstance(v, int)}


def decode_versym(section: Any, count: int) -> list[tuple[int, bool]] | None:
    """``(version_index, is_hidden)`` for the first *count* ``.gnu.version``
    entries, decoded from the section's bytes in one ``struct`` call.

    The same mapping the per-entry pyelftools read gave: a named value is
    ``(0, False)`` for ``VER_NDX_LOCAL`` and ``(1, False)`` for every other
    name; an unnamed one splits into its index and hidden bit. ``None`` when
    the section is not a plain array of 2-byte entries covering *count*, or
    cannot be read at all, for the caller's per-entry path to handle.
    """
    try:
        header = section.header
        if header["sh_entsize"] not in (0, 2) or header["sh_size"] < 2 * count:
            return None
        data = section.data()
        order = "<" if section.elffile.little_endian else ">"
    except Exception:  # noqa: BLE001 - a truncated/corrupt section
        # Declined, not raised: the per-entry path reads it again and owns
        # the warning-and-skip a malformed `.gnu.version` has always got.
        return None
    if len(data) < 2 * count:
        return None
    out: list[tuple[int, bool]] = []
    for raw in struct.unpack_from(f"{order}{count}H", data):
        name = _NAMED_VERSYM.get(raw)
        if name is not None:
            out.append((0, False) if name == "VER_NDX_LOCAL" else (1, False))
        else:
            out.append((raw & 0x7FFF, bool(raw & 0x8000)))
    return out


def _is_import_sym(sym: Any) -> bool:
    """Check if a dynsym entry is a counted import symbol."""
    if sym.entry.st_shndx != "SHN_UNDEF":
        return False
    return bool(
        sym.name
        and _BINDING_MAP.get(sym.entry.st_info.bind, SymbolBinding.OTHER)
        != SymbolBinding.LOCAL
    )


def _is_export_sym(sym: Any) -> bool:
    """Check if a dynsym entry is a counted export symbol."""
    if sym.entry.st_shndx in ("SHN_UNDEF", "SHN_ABS"):
        return False
    binding = _BINDING_MAP.get(sym.entry.st_info.bind, SymbolBinding.OTHER)
    vis_str = sym.entry.st_other.visibility
    return binding != SymbolBinding.LOCAL and vis_str not in _HIDDEN_VISIBILITIES


def apply_versions_to_symbols(
    dynsym: SymbolTableSection,
    ver_entries: list[tuple[int, bool]],
    ver_index_map: dict[int, tuple[str, str, bool]],
    meta: ElfMetadata,
) -> None:
    """Attach each ``.gnu.version`` label onto the already-parsed entries.

    Walks *dynsym* in ordinal order alongside *ver_entries* (one
    ``(version_index, is_hidden)`` per symbol, positionally aligned with the
    table) and writes the resolved version name and default/hidden binding
    onto ``meta.symbols``/``meta.imports``.

    The alignment is positional on *both* sides: this walk counts exports
    and imports independently, in the same order the first walk appended
    them, so the two indexes stay in step. A symbol beyond the end of
    *ver_entries* stops the walk rather than being guessed at -- a
    truncated ``.gnu.version`` cannot be repaired by pairing the remainder
    with the wrong versions.
    """
    export_idx = 0
    import_idx = 0
    for sym_ordinal, sym in enumerate(symbols_of(dynsym)):
        if sym_ordinal >= len(ver_entries):
            break
        ver_idx, is_hidden = ver_entries[sym_ordinal]
        export_idx, import_idx = _apply_version_to_symbol(
            sym,
            ver_idx,
            is_hidden,
            ver_index_map,
            meta,
            export_idx,
            import_idx,
        )


def _apply_version_to_symbol(
    sym: Any,
    ver_idx: int,
    is_hidden: bool,
    ver_index_map: dict[int, tuple[str, str, bool]],
    meta: ElfMetadata,
    export_idx: int,
    import_idx: int,
) -> tuple[int, int]:
    """Apply version info to a single symbol, returning updated indices.

    An export always gets its ``version_binding``; a version *name* is
    written only for an index >= 2 that resolves -- the base definition
    ``build_verdef_index`` records at index 1 is a binding, never a name.
    """
    entry = ver_index_map.get(ver_idx)
    if _is_import_sym(sym):
        if ver_idx >= 2 and entry is not None and import_idx < len(meta.imports):
            lib_name, ver_name, _is_defined = entry
            meta.imports[import_idx].version = ver_name
            meta.imports[import_idx].is_default = not is_hidden
            # Record the verneed provider soname so consumers can resolve which
            # DSO satisfies this import even when a version label collides.
            meta.imports[import_idx].version_soname = lib_name
        return export_idx, import_idx + 1
    if not _is_export_sym(sym):
        return export_idx, import_idx
    if export_idx < len(meta.symbols):
        out = meta.symbols[export_idx]
        if ver_idx >= 2:
            if entry is not None:
                out.version = entry[1]
                out.is_default = not is_hidden
                out.version_binding = ElfVersionBinding.NAMED
            # else: dangling index -- left UNKNOWN, never guessed.
        elif ver_idx == _BASE_INDEX and entry is not None:
            out.version_binding = ElfVersionBinding.BASE
        else:
            out.version_binding = ElfVersionBinding.UNVERSIONED
    return export_idx + 1, import_idx


def build_verdef_index(
    section: GNUVerDefSection,
    ver_index_map: dict[int, tuple[str, str, bool]],
) -> None:
    """Build version-index → (lib="", version_name, is_defined=True) from .gnu.version_d.

    The ``VER_FLG_BASE`` definition (the SONAME node) is recorded too, at
    ``_BASE_INDEX`` only: its presence is what makes an index-1 symbol
    *base-bound* rather than unversioned. ``_apply_version_to_symbol`` never
    writes it as a version name.
    """
    VER_FLG_BASE = 0x1  # noqa: N806
    for verdef, verdaux_iter in section.iter_versions():
        is_base = bool(verdef.entry.vd_flags & VER_FLG_BASE)
        idx = verdef.entry.vd_ndx
        for verdaux in verdaux_iter:
            name = verdaux.name
            if name and (idx >= 2 or (is_base and idx == _BASE_INDEX)):
                ver_index_map[idx] = ("", name, True)
            break  # only first verdaux is the version name


def parse_ver_entries(
    ver_sym_section: Any,
    num_vers: int,
    so_path: Path,
) -> list[tuple[int, bool]] | None:
    """Parse .gnu.version into a list of (version_index, is_hidden) per symbol."""
    ver_entries: list[tuple[int, bool]] = []
    try:
        for i in range(num_vers):
            entry = ver_sym_section.get_symbol(i)
            raw = entry.entry["ndx"]
            if isinstance(raw, str):
                if raw == "VER_NDX_LOCAL":
                    ver_entries.append((0, False))
                else:
                    ver_entries.append((1, False))
                continue
            is_hidden = bool(raw & 0x8000)
            idx = raw & 0x7FFF
            ver_entries.append((idx, is_hidden))
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "parse_elf_metadata: failed to read .gnu.version from %s: %s", so_path, exc
        )
        note_export_read_failure(f"malformed .gnu.version section: {exc}")
        return None
    return ver_entries


def correlate_symbol_versions(
    ver_sym_section: GNUVerSymSection | None,
    dynsym: SymbolTableSection | None,
    meta: ElfMetadata,
    ver_index_map: dict[int, tuple[str, str, bool]],
    so_path: Path,
) -> None:
    """Correlate .gnu.version entries with exports and imports.

    The .gnu.version section contains one Elf_Half per .dynsym entry,
    mapping each symbol to a version index. Index 0 = VER_NDX_LOCAL,
    1 = VER_NDX_GLOBAL (the base definition when the library has one).
    Higher indices come from .gnu.version_d (defined) or .gnu.version_r
    (required). Bit 15 (0x8000) indicates a hidden (non-default) version.

    With no ``.gnu.version`` at all every export is ``UNVERSIONED``; a
    section that cannot be read leaves every export ``UNKNOWN``.
    """
    if ver_sym_section is None:
        for sym in meta.symbols:
            sym.version_binding = ElfVersionBinding.UNVERSIONED
        return

    try:
        num_vers = ver_sym_section.num_symbols()
    except Exception as exc:  # noqa: BLE001
        note_export_read_failure(f"malformed .gnu.version section: {exc}")
        return

    ver_entries = decode_versym(ver_sym_section, num_vers)
    if ver_entries is None:
        ver_entries = parse_ver_entries(ver_sym_section, num_vers, so_path)
    if ver_entries is None or dynsym is None:
        return

    # Same buffering as `_parse_dynsym`: this is a second full walk of the
    # identical symbol table, so it pays the identical per-name stream cost.
    with buffered_string_table(string_table_of(dynsym)):
        apply_versions_to_symbols(dynsym, ver_entries, ver_index_map, meta)
