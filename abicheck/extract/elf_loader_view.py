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

"""The dynamic symbol table as the dynamic loader sees it (``PT_DYNAMIC``).

The section header table is a link-time index. ``ld.so`` never reads it: it
maps the ``PT_LOAD`` segments, reads the ``PT_DYNAMIC`` array, and finds the
dynamic symbol table through ``DT_SYMTAB``, its names through ``DT_STRTAB``
(bounded by ``DT_STRSZ``), its entry size through ``DT_SYMENT``, its count
through the hash table (``DT_GNU_HASH`` or ``DT_HASH``), and the GNU symbol
versions through ``DT_VERSYM`` / ``DT_VERDEF`` / ``DT_VERNEED``. A stripped
shared object (``sstrip``, a zeroed section header table) keeps all of that,
so reading the same tables this way yields the same export surface as the
intact file.

:func:`read_loader_view` returns those tables as objects shaped like the
pyelftools section objects the existing parsers consume (``iter_symbols``,
``iter_versions``, ``num_symbols``/``data``), so the one symbol walk and the
one version correlation serve both views instead of a second copy.

Anything the loader would not be able to use raises :class:`DynamicTableUnreadable`;
the caller turns that into the export-read refusal. Nothing here guesses a
count or substitutes a section that was not named by a dynamic tag.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from elftools.common.utils import struct_parse
from elftools.elf.hash import ELFHashTable, GNUHashTable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from elftools.elf.elffile import ELFFile

__all__ = ["DynamicTableUnreadable", "LoaderView", "read_loader_view"]

#: Upper bound on a single dynamic string table read. Real tables are a few
#: megabytes; a larger declared size is corruption and is refused.
_MAX_STRTAB_BYTES = 256 * 1024 * 1024


class DynamicTableUnreadable(Exception):
    """The dynamic table cannot be read the way the loader reads it."""


@dataclass(frozen=True)
class LoaderView:
    """The loader's view of one shared object's dynamic tables.

    Every field is shaped like the section object the same data would come
    from in an intact file (see the module docstring); they are typed ``Any``
    because they are duck-typed stand-ins, not pyelftools sections.

    * ``dynamic``  -- the ``PT_DYNAMIC`` segment (``iter_tags``).
    * ``symbols``  -- the dynamic symbol table (``iter_symbols``, ``elffile``).
    * ``versym``   -- ``DT_VERSYM`` (``num_symbols``, ``header``, ``data``), or
      ``None`` when the object carries no version array.
    * ``verdef`` / ``verneed`` -- ``DT_VERDEF`` / ``DT_VERNEED`` records
      (``iter_versions``), or ``None`` when the tag is absent.
    * ``hash_styles`` -- the hash table kinds present (``"sysv"``/``"gnu"``).
    """

    dynamic: Any
    symbols: Any
    versym: Any
    verdef: Any
    verneed: Any
    hash_styles: frozenset[str]


# ---------------------------------------------------------------------------
# Reading bytes the way the loader maps them
# ---------------------------------------------------------------------------


def _read_vaddr(elf: ELFFile, vaddr: int, size: int) -> bytes:
    """*size* bytes at virtual address *vaddr*, through the ``PT_LOAD`` map.

    The region must lie entirely inside one loadable segment's file image;
    a region that is not mapped, or runs past the end of the file, is
    unreadable.
    """
    offset = next(elf.address_offsets(vaddr, max(size, 1)), None)
    if offset is None:
        raise DynamicTableUnreadable(f"address {vaddr:#x} is not in a PT_LOAD segment")
    elf.stream.seek(offset)
    data = elf.stream.read(size)
    if len(data) != size:
        raise DynamicTableUnreadable(f"{size} bytes at {vaddr:#x} run past end of file")
    return bytes(data)


def _order(elf: ELFFile) -> str:
    """The struct byte-order prefix for *elf*."""
    return "<" if elf.little_endian else ">"


def _read_offset(elf: ELFFile, offset: int, size: int) -> bytes:
    elf.stream.seek(offset)
    data = elf.stream.read(size)
    if len(data) != size:
        raise DynamicTableUnreadable(
            f"{size} bytes at file offset {offset:#x} unreadable"
        )
    return bytes(data)


def _string_at(strtab: bytes, offset: int) -> str:
    """The NUL-terminated name at *offset*, or ``""`` when it is not in the table."""
    if offset < 0 or offset >= len(strtab):
        return ""
    end = strtab.find(b"\x00", offset)
    if end < 0:
        return ""
    return strtab[offset:end].decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Dynamic tags
# ---------------------------------------------------------------------------


def _tags(dynamic: Any) -> dict[str, int]:
    """The first value of each dynamic tag, keyed by its name.

    ``DT_NEEDED`` and the other repeatable tags are not needed here; the
    tags this module reads each occur once.
    """
    out: dict[str, int] = {}
    for tag in dynamic.iter_tags():
        out.setdefault(str(tag.entry.d_tag), int(tag.entry.d_val))
    return out


def _required(tags: dict[str, int], name: str) -> int:
    if name not in tags:
        raise DynamicTableUnreadable(f"no {name} in the dynamic table")
    return tags[name]


# ---------------------------------------------------------------------------
# Symbol count (DT_GNU_HASH or DT_HASH) and the symbol table itself
# ---------------------------------------------------------------------------


def _symbol_count(elf: ELFFile, tags: dict[str, int]) -> int:
    """Number of dynamic symbols, from the hash table the loader would use."""
    # The count reads only the hash table's own header and chains; the symbol
    # lookup table the constructor takes is never consulted.
    no_lookup: Any = None
    if "DT_GNU_HASH" in tags:
        offset = next(elf.address_offsets(tags["DT_GNU_HASH"], 1), None)
        if offset is None:
            raise DynamicTableUnreadable("DT_GNU_HASH is not in a PT_LOAD segment")
        return int(GNUHashTable(elf, offset, no_lookup).get_number_of_symbols())
    if "DT_HASH" in tags:
        offset = next(elf.address_offsets(tags["DT_HASH"], 1), None)
        if offset is None:
            raise DynamicTableUnreadable("DT_HASH is not in a PT_LOAD segment")
        return int(ELFHashTable(elf, offset, None, no_lookup).get_number_of_symbols())
    raise DynamicTableUnreadable(
        "neither DT_GNU_HASH nor DT_HASH sizes the symbol table"
    )


class LoaderSymbolTable:
    """The dynamic symbol table, read from ``DT_SYMTAB`` through ``DT_STRTAB``.

    ``iter_symbols`` yields objects with ``name`` and ``entry`` -- the shape
    pyelftools' ``Symbol`` has -- where ``entry`` is the same construct
    container pyelftools parses from a ``.dynsym`` entry. ``elffile`` lets the
    code hasher resolve function bytes.
    """

    def __init__(self, elffile: ELFFile, symbols: list[Any]) -> None:
        self.elffile = elffile
        self._symbols = symbols

    def iter_symbols(self) -> Any:
        yield from self._symbols

    def num_symbols(self) -> int:
        return len(self._symbols)


@dataclass(frozen=True)
class _LoaderSymbol:
    name: str
    entry: Any


def _read_symbols(elf: ELFFile, tags: dict[str, int]) -> LoaderSymbolTable:
    syment = _required(tags, "DT_SYMENT")
    if syment != elf.structs.Elf_Sym.sizeof():
        raise DynamicTableUnreadable(
            f"DT_SYMENT {syment} is not the symbol entry size {elf.structs.Elf_Sym.sizeof()}"
        )
    count = _symbol_count(elf, tags)
    symtab = (
        _read_vaddr(elf, _required(tags, "DT_SYMTAB"), count * syment) if count else b""
    )
    strsz = _required(tags, "DT_STRSZ")
    if not 0 <= strsz <= _MAX_STRTAB_BYTES:
        raise DynamicTableUnreadable(f"DT_STRSZ {strsz} is out of range")
    strtab = _read_vaddr(elf, _required(tags, "DT_STRTAB"), strsz) if strsz else b""

    entries = io.BytesIO(symtab)
    symbols: list[_LoaderSymbol] = []
    for index in range(count):
        entry = struct_parse(elf.structs.Elf_Sym, entries, stream_pos=index * syment)
        symbols.append(_LoaderSymbol(_string_at(strtab, int(entry["st_name"])), entry))
    return LoaderSymbolTable(elf, symbols)


# ---------------------------------------------------------------------------
# GNU symbol versions
# ---------------------------------------------------------------------------


class _VersymArray:
    """``DT_VERSYM``, exposing the surface ``decode_versym`` reads from a section.

    ``decode_versym`` and ``correlate_symbol_versions`` read ``header``,
    ``data()``, ``elffile`` and ``num_symbols()``; this presents the version
    array under those names, one 2-byte ``Elf_Half`` per dynamic symbol.
    """

    def __init__(self, elffile: ELFFile, raw: bytes, count: int) -> None:
        self.elffile = elffile
        self.header = {"sh_entsize": 2, "sh_size": len(raw)}
        self._raw = raw
        self._count = count

    def data(self) -> bytes:
        return self._raw

    def num_symbols(self) -> int:
        return self._count


class _VersionRecords:
    """Records of ``DT_VERDEF`` or ``DT_VERNEED``, in the shape the section parsers consume.

    ``iter_versions`` yields ``(head, aux_iter)`` pairs exactly as the
    pyelftools ``GNUVerDefSection``/``GNUVerNeedSection`` does: ``head.name``
    is the library (verneed) and ``head.entry`` the record's fields; each
    ``aux.name`` is a version name and ``aux.entry`` its fields.
    """

    def __init__(self, records: list[tuple[Any, list[Any]]]) -> None:
        self._records = records

    def iter_versions(self) -> Any:
        for head, auxes in self._records:
            yield head, iter(auxes)


def _read_verdef(elf: ELFFile, tags: dict[str, int]) -> _VersionRecords:
    """Walk ``DT_VERDEF`` (``Elf_Verdef`` chain; ``Elf_Verdaux`` names)."""
    offset = next(elf.address_offsets(_required(tags, "DT_VERDEF"), 1), None)
    if offset is None:
        raise DynamicTableUnreadable("DT_VERDEF is not in a PT_LOAD segment")
    count = _required(tags, "DT_VERDEFNUM")
    strtab = _strtab_bytes(elf, tags)
    records: list[tuple[Any, list[Any]]] = []
    for _ in range(count):
        raw = _read_offset(elf, offset, 20)
        _ver, vd_flags, vd_ndx, vd_cnt, _hash, vd_aux, vd_next = struct.unpack(
            f"{_order(elf)}HHHHIII", raw
        )
        auxes: list[Any] = []
        aux_offset = offset + vd_aux
        for _aux in range(vd_cnt):
            vda_name, vda_next = struct.unpack(
                f"{_order(elf)}II", _read_offset(elf, aux_offset, 8)
            )
            auxes.append(SimpleNamespace(name=_string_at(strtab, vda_name)))
            aux_offset += vda_next
        head = SimpleNamespace(
            name=None, entry=SimpleNamespace(vd_flags=vd_flags, vd_ndx=vd_ndx)
        )
        records.append((head, auxes))
        if vd_next == 0:
            break
        offset += vd_next
    return _VersionRecords(records)


def _read_verneed(elf: ELFFile, tags: dict[str, int]) -> _VersionRecords:
    """Walk ``DT_VERNEED`` (``Elf_Verneed`` chain; ``Elf_Vernaux`` names)."""
    offset = next(elf.address_offsets(_required(tags, "DT_VERNEED"), 1), None)
    if offset is None:
        raise DynamicTableUnreadable("DT_VERNEED is not in a PT_LOAD segment")
    count = _required(tags, "DT_VERNEEDNUM")
    strtab = _strtab_bytes(elf, tags)
    records: list[tuple[Any, list[Any]]] = []
    for _ in range(count):
        _ver, vn_cnt, vn_file, vn_aux, vn_next = struct.unpack(
            f"{_order(elf)}HHIII", _read_offset(elf, offset, 16)
        )
        auxes: list[Any] = []
        aux_offset = offset + vn_aux
        for _aux in range(vn_cnt):
            _hash, _flags, vna_other, vna_name, vna_next = struct.unpack(
                f"{_order(elf)}IHHII", _read_offset(elf, aux_offset, 16)
            )
            auxes.append(
                SimpleNamespace(
                    name=_string_at(strtab, vna_name),
                    entry=SimpleNamespace(vna_other=vna_other),
                )
            )
            aux_offset += vna_next
        head = SimpleNamespace(name=_string_at(strtab, vn_file), entry=None)
        records.append((head, auxes))
        if vn_next == 0:
            break
        offset += vn_next
    return _VersionRecords(records)


def _strtab_bytes(elf: ELFFile, tags: dict[str, int]) -> bytes:
    strsz = _required(tags, "DT_STRSZ")
    if not 0 <= strsz <= _MAX_STRTAB_BYTES:
        raise DynamicTableUnreadable(f"DT_STRSZ {strsz} is out of range")
    return _read_vaddr(elf, _required(tags, "DT_STRTAB"), strsz) if strsz else b""


def _read_versym(elf: ELFFile, tags: dict[str, int], count: int) -> _VersymArray | None:
    if "DT_VERSYM" not in tags:
        return None
    raw = _read_vaddr(elf, tags["DT_VERSYM"], 2 * count) if count else b""
    return _VersymArray(elf, raw, count)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def read_loader_view(elf: ELFFile) -> LoaderView:
    """The loader's view of *elf*'s dynamic tables, or :class:`DynamicTableUnreadable`.

    Needs only the program headers, so it works on an object whose section
    header table is zeroed, absent or stripped. Raises when there is no
    ``PT_DYNAMIC``, or when a table it needs is missing, unmapped, truncated or
    internally inconsistent.
    """
    dynamic = next(
        (seg for seg in elf.iter_segments() if seg.header.p_type == "PT_DYNAMIC"),
        None,
    )
    if dynamic is None:
        raise DynamicTableUnreadable("no PT_DYNAMIC segment")
    tags = _tags(dynamic)
    symbols = _read_symbols(elf, tags)
    count = symbols.num_symbols()
    verdef = _read_verdef(elf, tags) if "DT_VERDEF" in tags else None
    verneed = _read_verneed(elf, tags) if "DT_VERNEED" in tags else None
    hash_styles = frozenset(
        style
        for style, tag in (("sysv", "DT_HASH"), ("gnu", "DT_GNU_HASH"))
        if tag in tags
    )
    return LoaderView(
        dynamic=dynamic,
        symbols=symbols,
        versym=_read_versym(elf, tags, count),
        verdef=verdef,
        verneed=verneed,
        hash_styles=hash_styles,
    )
