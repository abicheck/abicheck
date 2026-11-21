# SPDX-License-Identifier: Apache-2.0
"""The logical target of every entry of an exported Itanium VTT (``_ZTT``).

A VTT is an array of pointers to vtable *address points*; constructors of a
class with virtual bases install them while the object is being built. Its
size says how many entries it holds, not where they point, so two VTTs of
equal size can make an old client dispatch a construction-time virtual call
to a different override. This module reads the entries.

Each entry is a pointer-sized word covered by a dynamic relocation. The
relocation is resolved to an absolute target address, then to the table
whose symbol covers that address and the offset inside it
(:class:`~abicheck.model.elf_facts.VttEntryTarget`). Supported forms, all
x86-64 (``EM_X86_64``, ``ELFCLASS64``, RELA):

* ``R_X86_64_64`` / ``R_X86_64_GLOB_DAT`` against a defined symbol:
  ``S + A``, then mapped to its covering table;
* the same against an *undefined* symbol: ``(S, A)`` as is -- the table
  lives in another object and is identified by its name;
* ``R_X86_64_RELATIVE``: the addend is the address;
* a ``DT_RELR`` packed relative entry: the address is the word stored in
  the file.

Two forms that reach the same address point give the same value, so a
``-Bsymbolic`` or ``pack-relative-relocs`` relink is not a change. An entry
stays unknown (``None``) when it has no relocation, another relocation type,
or an address no single named object covers -- typically a local
construction vtable (``_ZTC``) whose ``.symtab`` entry was stripped. Any
other target is None for the whole map (never read), so it is not
confused with "no change".
"""

from __future__ import annotations

import bisect
import logging
from typing import Any

from ..model.elf_facts import ElfMetadata, VttEntryTarget
from .parse_failures import export_read_failed

log = logging.getLogger(__name__)

__all__ = ["read_vtt_entries"]

_VTT_PREFIX = "_ZTT"
#: x86-64 relocation types whose value is ``S + A``.
_R_X86_64_64 = 1  # S + A
_R_X86_64_GLOB_DAT = 6  # S (the addend is not applied)
_R_X86_64_RELATIVE = 8
_WORD = 8

#: One resolved relocation: ``("addr", address)`` or ``("sym", name, addend)``.
_Resolved = tuple[Any, ...]


def read_vtt_entries(
    elf: Any, meta: ElfMetadata
) -> dict[str, tuple[VttEntryTarget | None, ...]] | None:
    """``{_ZTT symbol: entry targets}`` for each exported VTT of *elf*.

    *meta* is the metadata parsed so far; its ``.dynsym`` exports name the
    VTTs. ``None`` for a non-x86-64 image or an unreadable one (unknown);
    an empty map when the library exports no VTT.
    """
    if elf["e_machine"] != "EM_X86_64" or elf.elfclass != 64:
        return None
    if export_read_failed():
        # The export table was not read in full: "no VTT" (or a VTT list
        # missing the ones the failed read never reached) would be a claim
        # the parse cannot support, so the map stays unknown.
        return None
    vtts = {
        s.name: s.size
        for s in meta.symbols
        if s.name.startswith(_VTT_PREFIX) and s.size > 0
    }
    if not vtts:
        return {}
    try:
        return _read(elf, vtts)
    except Exception as exc:  # noqa: BLE001 - any read failure is "unknown"
        log.debug("VTT entries not read: %s", exc)
        return None


def _read(
    elf: Any, vtts: dict[str, int]
) -> dict[str, tuple[VttEntryTarget | None, ...]] | None:
    from elftools.elf.relocation import RelocationSection, RelrRelocationSection
    from elftools.elf.sections import SymbolTableSection

    objects: dict[str, tuple[int, int]] = {}  # name -> (start, size)
    # name -> the (start, size) of the one backed .dynsym VTT, or None when
    # its aliases disagree or its bytes are not in the file: not read.
    vtt_spans: dict[str, tuple[int, int] | None] = {}
    versions = _symbol_versions(elf)
    for section in elf.iter_sections():
        if not isinstance(section, SymbolTableSection):
            continue
        for sym in section.iter_symbols():
            entry = sym.entry
            shndx = entry["st_shndx"]
            if not isinstance(shndx, int) or shndx == 0 or entry["st_size"] <= 0:
                continue
            if entry["st_info"]["type"] != "STT_OBJECT":
                continue
            span = (int(entry["st_value"]), int(entry["st_size"]))
            if section["sh_type"] == "SHT_DYNSYM" and sym.name in vtts:
                if sym.name not in vtt_spans:
                    backed = _backed(elf, shndx, span)
                    vtt_spans[sym.name] = span if backed else None
                elif vtt_spans[sym.name] != span:
                    vtt_spans[sym.name] = None  # aliases disagree: not read
            if sym.name in objects and objects[sym.name] != span:
                # One name, two places (a local in two TUs): no single table.
                objects[sym.name] = (-1, 0)
                continue
            objects[sym.name] = span
    # Only the VTT words are read, sized by the span that passed _backed.
    spans = {name: span for name, span in vtt_spans.items() if span is not None}
    slots = {
        start + i * _WORD
        for start, size in spans.values()
        for i in range(size // _WORD)
    }

    relocs: dict[int, _Resolved | None] = {}
    for section in elf.iter_sections():
        if isinstance(section, RelrRelocationSection):
            for rel in section.iter_relocations():
                off = int(rel["r_offset"])
                if off in slots:
                    relocs[off] = ("addr", _read_word(elf, off))
        elif isinstance(section, RelocationSection):
            if not section.is_RELA():
                return None
            symtab = elf.get_section(section["sh_link"])
            dynamic = section["sh_link"] == _dynsym_index(elf)
            for rel in section.iter_relocations():
                off = int(rel["r_offset"])
                if off in slots:
                    relocs[off] = _resolve(rel, symtab, versions if dynamic else None)

    index = _CoverIndex(objects)
    out: dict[str, tuple[VttEntryTarget | None, ...]] = {}
    for name, (start, size) in sorted(spans.items()):
        out[name] = tuple(
            _target(relocs.get(start + i * _WORD), objects, index)
            for i in range(size // _WORD)
        )
    return out


def _resolve(
    rel: Any, symtab: Any, versions: dict[int, str | None] | None
) -> _Resolved | None:
    """One RELA relocation as an address or an unresolved ``(symbol, addend)``.

    An imported symbol carries its required version (``_ZTV1V@V2``): two
    versions of one name can be two tables. A version that cannot be read
    leaves the target unknown.
    """
    rtype = rel["r_info_type"]
    if rtype == _R_X86_64_RELATIVE:
        return ("addr", int(rel["r_addend"]))
    if rtype == _R_X86_64_64:
        addend = int(rel["r_addend"])
    elif rtype == _R_X86_64_GLOB_DAT:
        addend = 0
    else:
        return None
    if symtab is None:
        return None
    index = rel["r_info_sym"]
    sym = symtab.get_symbol(index)
    shndx = sym.entry["st_shndx"]
    if shndx == "SHN_UNDEF" or shndx == 0:
        if not sym.name:
            return None
        if versions is None:
            return ("sym", sym.name, addend)
        if index not in versions:
            return ("sym", sym.name, addend)  # unversioned import
        version = versions[index]
        if version is None:
            return None
        return ("sym", f"{sym.name}@{version}", addend)
    return ("addr", int(sym.entry["st_value"]) + addend)


def _dynsym_index(elf: Any) -> int | None:
    for i, section in enumerate(elf.iter_sections()):
        if section["sh_type"] == "SHT_DYNSYM":
            return i
    return None


def _symbol_versions(elf: Any) -> dict[int, str | None]:
    """``{.dynsym index: required version name}`` for versioned imports.

    Indexes with no version (``VER_NDX_LOCAL``/``GLOBAL``) are absent; an
    index naming a version this file does not declare maps to None.
    """
    from elftools.elf.gnuversions import GNUVerNeedSection, GNUVerSymSection

    names: dict[int, str] = {}
    versym = None
    for section in elf.iter_sections():
        if isinstance(section, GNUVerNeedSection):
            for _verneed, auxiliaries in section.iter_versions():
                for aux in auxiliaries:
                    names[int(aux["vna_other"])] = aux.name
        elif isinstance(section, GNUVerSymSection):
            versym = section
    out: dict[int, str | None] = {}
    if versym is None:
        return out
    for index in range(versym.num_symbols()):
        ndx = versym.get_symbol(index).entry["ndx"]
        if isinstance(ndx, int) and ndx & 0x7FFF > 1:
            out[index] = names.get(ndx & 0x7FFF)
    return out


def _backed(elf: Any, shndx: int, span: tuple[int, int]) -> bool:
    """Whether the symbol's whole span lies in file-backed section bytes."""
    section = elf.get_section(shndx)
    if section is None or section["sh_type"] == "SHT_NOBITS":
        return False
    start, size = span
    base = int(section["sh_addr"])
    if not (base <= start and start + size <= base + int(section["sh_size"])):
        return False
    # The section header is as untrusted as st_size: the bytes must also
    # exist in the file, or a forged size would drive the slot allocation.
    stream = elf.stream
    here = stream.tell()
    length = int(stream.seek(0, 2))
    stream.seek(here)
    return int(section["sh_offset"]) + (start - base) + size <= length


def _read_word(elf: Any, address: int) -> int | None:
    """The little-endian word stored at virtual *address* (a DT_RELR slot)."""
    for seg in elf.iter_segments():
        if seg["p_type"] != "PT_LOAD":
            continue
        vaddr = int(seg["p_vaddr"])
        if vaddr <= address and address + _WORD <= vaddr + int(seg["p_filesz"]):
            stream = elf.stream
            stream.seek(int(seg["p_offset"]) + address - vaddr)
            raw = stream.read(_WORD)
            return int.from_bytes(raw, "little") if len(raw) == _WORD else None
    return None


class _CoverIndex:
    """Which named object covers an address point."""

    def __init__(self, objects: dict[str, tuple[int, int]]) -> None:
        spans = sorted(
            (start, start + size, name)
            for name, (start, size) in objects.items()
            if start >= 0
        )
        self._starts = [s for s, _e, _n in spans]
        self._spans = spans
        self._longest = max((e - s for s, e, _n in spans), default=0)

    def covering(self, address: int) -> str | None:
        """The one object with ``start < address <= end``, else None.

        An address point is never a table's first byte (offset-to-top and
        the typeinfo pointer precede it) but can be its end (a vtable with
        no virtual functions), so the start is excluded and the end
        included. Two different objects covering it is ambiguous: None.
        """
        found: set[str] = set()
        i = bisect.bisect_left(self._starts, address) - 1
        # Spans are sorted by start, so none that starts more than the
        # longest object's size before *address* can still reach it.
        while i >= 0 and self._spans[i][0] >= address - self._longest:
            start, end, name = self._spans[i]
            if start < address <= end:
                found.add(name)
            i -= 1
        return found.pop() if len(found) == 1 else None


def _target(
    resolved: _Resolved | None,
    objects: dict[str, tuple[int, int]],
    index: _CoverIndex,
) -> VttEntryTarget | None:
    if resolved is None:
        return None
    if resolved[0] == "sym":
        return VttEntryTarget(table=resolved[1], offset=resolved[2])
    address = resolved[1]
    if address is None:
        return None
    table = index.covering(address)
    if table is None:
        return None
    return VttEntryTarget(table=table, offset=address - objects[table][0])
