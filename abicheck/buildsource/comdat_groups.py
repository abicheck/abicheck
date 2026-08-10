# Copyright 2026 Nikolay Petrov
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

"""COMDAT-group introspection over L3 object files — proof of *vague linkage*.

The question this answers is one no other evidence source in the tree can:
**did the compiler emit this symbol as a vague-linkage (COMDAT) definition?**
That is the difference between an entity every using translation unit defines
for itself — an inline function, a template instantiation, an implicit special
member, a vtable with no key function — and an ordinary strong definition that
only the library provides.

It matters because an export disappearing means two materially different
things in those two cases, and the export table cannot tell them apart. Both
land in ``.dynsym`` as ``WEAK``:

    FUNC WEAK _Z5vaguei            <- inline, vague linkage: consumers have a copy
    FUNC WEAK _Z14weak_outoflinei  <- __attribute__((weak)): consumers have none

Verified against real g++/clang++ output, which is also how the alternatives
were ruled out and why this module reads *object* files rather than anything
cheaper:

* **The linked ``.so`` does not carry it.** COMDAT groups are resolved and
  discarded by the linker — ``readelf -g`` on a shared library reports "There
  are no section groups in this file". The fact exists only before linking.
* **Headers do not carry it.** castxml (checked through 0.7.0, its only output
  format version) emits a declaration and a definition byte-identically apart
  from ``line``; there is no body or definition attribute at all.
* **DWARF is unreliable for it.** ``DW_AT_inline`` appeared on g++ ``-O2`` and
  on none of g++ ``-O0``, clang++ ``-O0``, clang++ ``-O2`` — so its absence
  means "this compiler at this optimisation level said nothing", not "not
  vague". Keying a demotion on it would be the absence-of-evidence trap.

Two details the empirical check settled, both of which the naive
implementation gets wrong:

1. **Read defined symbols, not group signatures.** A destructor's group is
   signed ``_ZN7DerivedD5Ev`` (the "D5" alias, which is never an ELF symbol),
   while the symbols that actually reach ``.dynsym`` are ``D0``/``D1``/``D2``.
   Signature-only collection misses every one of them.
2. **A group holds more than ``.text``.** Members can include ``.rodata``,
   ``.data.rel.ro`` and EH sections for the same entity, so the scan walks
   every member section rather than assuming one.

**ELF only.** ``SHT_GROUP`` is an ELF construct; a Mach-O or PE/COFF object
reads as "not an ELF file" and is counted as a failure, so ``resolvable``
stays false and nothing is established. That is the correct outcome rather
than a gap to paper over — but it does mean any consumer of this evidence is
inert on macOS and Windows builds. The other two formats express the same
idea through different structures (Mach-O coalesced sections plus
``N_WEAK_DEF``; PE/COFF ``IMAGE_COMDAT_SELECT_*`` in a section's auxiliary
symbol record), so supporting them is a separate extractor, not a flag here.

Pure parsing: no subprocess, no compiler. Degrades to a diagnostic per object
file (missing, unreadable, non-ELF, malformed) rather than raising — ADR-028
D3's rule that optional build evidence never breaks a comparison.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

#: ELF ``GRP_COMDAT`` — the only flag defined for a section group, and the one
#: that marks the group as de-duplicated at link time.
GRP_COMDAT = 0x1

#: Bindings a vague-linkage definition can carry. ``STB_LOCAL`` is excluded:
#: a local symbol is not a candidate for cross-TU de-duplication and can never
#: be the export whose disappearance is under discussion.
_EXPORTABLE_BINDINGS = frozenset({"STB_GLOBAL", "STB_WEAK", "STB_GNU_UNIQUE"})


@dataclass(frozen=True)
class ComdatScan:
    """What a sweep over one side's object files established.

    ``symbols`` is *positive* evidence only. An empty set from zero readable
    objects means "not established", which is why ``objects_scanned`` is
    carried alongside rather than left for the caller to infer — a consumer
    must be able to tell "scanned, found none" from "nothing was scanned".
    """

    symbols: frozenset[str] = frozenset()
    objects_scanned: int = 0
    objects_failed: int = 0
    diagnostics: list[str] = field(default_factory=list)

    @property
    def resolvable(self) -> bool:
        """True when at least one object file was actually read.

        The gate any consumer must check before treating an absent symbol as
        meaningful.
        """
        return self.objects_scanned > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbols": sorted(self.symbols),
            "objects_scanned": self.objects_scanned,
            "objects_failed": self.objects_failed,
            "diagnostics": list(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ComdatScan:
        """Tolerant of a malformed payload: a bad field loads as "not
        established" rather than raising, since this rides an *optional*
        evidence pack that must never break a comparison (ADR-028 D3)."""

        def _seq(key: str) -> list[str]:
            raw = d.get(key)
            return [str(x) for x in raw] if isinstance(raw, list) else []

        def _count(key: str) -> int:
            raw = d.get(key)
            return raw if isinstance(raw, int) and raw >= 0 else 0

        return cls(
            symbols=frozenset(_seq("symbols")),
            objects_scanned=_count("objects_scanned"),
            objects_failed=_count("objects_failed"),
            diagnostics=_seq("diagnostics"),
        )


def _comdat_member_sections(elf: ELFFile) -> set[int]:
    """Section indices belonging to a ``GRP_COMDAT`` group.

    Group words are plain ``Elf_Word``s in the file's own ``EI_DATA`` byte
    order, so the prefix is taken from the ELF rather than assumed. Hardcoding
    little-endian decodes ``GRP_COMDAT`` (1) as ``0x01000000`` on s390x/ppc64
    and silently skips every group — an empty result that reads exactly like
    "this build has nothing vague" (Codex review).
    """
    members: set[int] = set()
    for sec in elf.iter_sections():
        if sec.header["sh_type"] != "SHT_GROUP":
            continue
        members.update(decode_group(sec.data(), little_endian=elf.little_endian))
    return members


def decode_group(data: bytes, *, little_endian: bool) -> frozenset[int]:
    """Section indices of one ``SHT_GROUP`` payload, empty unless ``GRP_COMDAT``.

    Split out of the ELF walk purely so the byte-order handling is directly
    testable without fabricating a big-endian object file.
    """
    if len(data) < 4:
        return frozenset()
    endian = "<" if little_endian else ">"
    (flag,) = struct.unpack(f"{endian}I", data[:4])
    if not flag & GRP_COMDAT:
        return frozenset()
    # The remainder is a flat array of section indices. A truncated tail
    # (malformed input) is dropped rather than raising.
    count = (len(data) - 4) // 4
    if not count:
        return frozenset()
    return frozenset(struct.unpack(f"{endian}{count}I", data[4 : 4 + count * 4]))


def scan_object_comdat_symbols(path: Path | str) -> frozenset[str] | None:
    """Defined exportable symbols living in a COMDAT group of one object file.

    ``None`` means the file could not be read as ELF at all — distinct from an
    empty set, which means "read it, this object defines nothing vague".
    """
    try:
        with open(path, "rb") as fh:
            elf = ELFFile(fh)
            comdat = _comdat_member_sections(elf)
            if not comdat:
                return frozenset()
            found: set[str] = set()
            for sec in elf.iter_sections():
                if not isinstance(sec, SymbolTableSection):
                    continue
                for sym in sec.iter_symbols():
                    if not sym.name:
                        continue
                    shndx = sym["st_shndx"]
                    # Special indices (SHN_UNDEF/SHN_ABS/...) arrive as strings
                    # from pyelftools and are never group members.
                    if not isinstance(shndx, int) or shndx not in comdat:
                        continue
                    if sym["st_info"]["bind"] in _EXPORTABLE_BINDINGS:
                        found.add(sym.name)
            return frozenset(found)
    except (OSError, ELFError, struct.error, KeyError, ValueError):
        return None


def collect_vague_linkage_symbols(objects: list[Path | str]) -> ComdatScan:
    """Sweep *objects*, unioning every COMDAT-defined symbol they carry.

    A union is the correct fold: a symbol emitted vaguely by any translation
    unit of the build is vague for the library, and the same entity is
    routinely emitted by several TUs (that is the whole point of COMDAT).
    """
    symbols: set[str] = set()
    scanned = 0
    failed = 0
    diagnostics: list[str] = []
    for obj in objects:
        found = scan_object_comdat_symbols(obj)
        if found is None:
            failed += 1
            if len(diagnostics) < 20:
                diagnostics.append(f"unreadable object file: {Path(obj).name}")
            continue
        scanned += 1
        symbols |= found
    return ComdatScan(
        symbols=frozenset(symbols),
        objects_scanned=scanned,
        objects_failed=failed,
        diagnostics=diagnostics,
    )
