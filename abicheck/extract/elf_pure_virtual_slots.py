# SPDX-License-Identifier: Apache-2.0
"""Which vtable slots a library's emitted vtables fill with ``__cxa_pure_virtual``.

GCC writes ``DW_VIRTUALITY_virtual`` for a pure virtual member (checked
against gcc 13, in a class with and without a key function), so its DWARF
never says a member is pure; clang writes ``DW_VIRTUALITY_pure_virtual``. The
binary still carries the fact whenever the class's vtable is emitted: the
Itanium C++ ABI fills a pure virtual's slot with ``__cxa_pure_virtual``, a
dynamic relocation the loader resolves. Read here as ``{vtable symbol: slot
indexes}``, where slot 0 is the first virtual function (the two header words,
offset-to-top and the typeinfo pointer, are skipped) -- the numbering
``DW_AT_vtable_elem_location`` uses.

Only a class whose vtable this library emits is covered: a class whose only
virtual is pure has no key function, its vtable is emitted nowhere, and no
binary evidence of the pure virtual exists. Slots of a secondary vtable in the
same ``_ZTV`` object are numbered past the primary one and never match a
primary-vtable member slot, so they are left out rather than misattributed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..model.elf_facts import ElfMetadata

PURE_VIRTUAL_SYMBOL = "__cxa_pure_virtual"
#: Offset-to-top and typeinfo pointer precede the first virtual function.
_HEADER_WORDS = 2

__all__ = ["PURE_VIRTUAL_SYMBOL", "PureVirtualEvidence", "pure_virtual_slots"]


def pure_virtual_slots(
    elf_path: Path, imports: Iterable[str]
) -> dict[str, frozenset[int]]:
    """``{_ZTV symbol: pure slot indexes}`` of *elf_path*'s emitted vtables.

    Empty without opening the file when *imports* (the library's undefined
    dynamic symbols) does not name ``__cxa_pure_virtual``: no emitted vtable
    can then hold a pure slot. Any read failure yields an empty map -- no
    evidence, never a claim that no member is pure.
    """
    if PURE_VIRTUAL_SYMBOL not in set(imports):
        return {}
    try:
        from elftools.elf.elffile import ELFFile
        from elftools.elf.relocation import RelocationSection
        from elftools.elf.sections import SymbolTableSection

        with open(elf_path, "rb") as f:
            elf = ELFFile(f)  # type: ignore[no-untyped-call]
            word = elf.elfclass // 8
            symtab = elf.get_section_by_name(".symtab") or elf.get_section_by_name(
                ".dynsym"
            )
            if not isinstance(symtab, SymbolTableSection):
                return {}
            vtables = [
                (s.name, s["st_value"], s["st_size"])
                for s in symtab.iter_symbols()
                if s.name.startswith("_ZTV")
                and s["st_size"]
                and s["st_shndx"] != "SHN_UNDEF"
            ]
            slots: dict[str, set[int]] = {}
            for section in elf.iter_sections():
                if not isinstance(section, RelocationSection):
                    continue
                link = elf.get_section(section["sh_link"])
                if not isinstance(link, SymbolTableSection):
                    continue
                for rel in section.iter_relocations():
                    idx = rel["r_info_sym"]
                    if not idx or link.get_symbol(idx).name != PURE_VIRTUAL_SYMBOL:
                        continue
                    off = rel["r_offset"]
                    for name, start, size in vtables:
                        if start <= off < start + size:
                            slot = (off - start) // word - _HEADER_WORDS
                            if slot >= 0:
                                slots.setdefault(name, set()).add(slot)
                            break
            return {k: frozenset(v) for k, v in slots.items()}
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return {}


@dataclass(frozen=True)
class PureVirtualEvidence:
    """One library's pure-virtual evidence for the DWARF reader.

    *slots* (:func:`pure_virtual_slots`) and *vtables* (the ``_ZTV`` symbols
    the library defines) are per library; *marks_pure* (whether the current
    compile unit's producer writes ``DW_VIRTUALITY_pure_virtual``) is reset
    per compile unit. An empty instance claims nothing.
    """

    slots: Mapping[str, frozenset[int]] = field(default_factory=dict)
    vtables: frozenset[str] = frozenset()
    marks_pure: bool = False

    @classmethod
    def of(cls, elf_path: Path, elf_meta: ElfMetadata) -> PureVirtualEvidence:
        imports = (i.name for i in elf_meta.imports)
        defined = frozenset(
            s.name for s in elf_meta.symbols if s.name.startswith("_ZTV")
        )
        return cls(pure_virtual_slots(elf_path, imports), defined)

    def for_unit(self, marks_pure: bool) -> PureVirtualEvidence:
        """This evidence for a compile unit whose producer does (or does not)
        mark pure virtuals -- a new object, so a shared default never
        changes."""
        return replace(self, marks_pure=marks_pure)

    def member_is_pure(self, die: Any) -> bool:
        """Whether member *die* is a pure virtual: marked so (clang), or its
        vtable slot is a pure slot of the emitted vtable (GCC)."""
        from .dwarf_member_functions import is_pure_virtual_declaration, slot_is_pure

        return is_pure_virtual_declaration(die) or slot_is_pure(die, self.slots)

    def record_abstract(
        self, die: Any, children: Any, has_bases: object, qualified: str
    ) -> bool | None:
        """The record's ``is_abstract`` fact (``record_is_abstract``)."""
        from .dwarf_member_functions import class_vtable_symbol, record_is_abstract

        emitted = class_vtable_symbol(qualified.split("::")) in self.vtables
        return record_is_abstract(
            die, children, has_bases, self.marks_pure, self.slots, emitted
        )
