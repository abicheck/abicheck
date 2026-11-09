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

"""The alignment an exported ELF symbol's address is evidence of.

Moved out of ``elf_metadata.py`` (at its no-growth ratchet). An address shows
the alignment it happens to have, ``st_value & -st_value``. Two things bound
how much of that is evidence about the object:

* the page size: ``PT_LOAD`` segments map page-aligned, so larger
  alignment is a load-address accident;
* the alignment of the section that holds the symbol (``sh_addralign``). The
  linker gives an output section the largest alignment any of its input
  sections asked for, and an input section the largest any of its objects
  asked for, so no object in it can require more. Address alignment beyond
  that is where the linker happened to place it. An ``-O0`` -> ``-O2``
  rebuild moves ``int g_counter`` from ``0x4010`` to ``0x4008`` in a
  4-aligned ``.bss`` (i386, the edit corpus's ``rebuild_optimized`` on
  ``c_core``), which read as ``exported_object_alignment_reduced`` 16 -> 8.
"""

from __future__ import annotations

from typing import Any

__all__ = ["PAGE_ALIGNMENT_CAP", "symbol_value_alignment", "value_alignment"]

#: Alignment derived from a symbol address is only meaningful up to the page.
PAGE_ALIGNMENT_CAP = 4096


def value_alignment(st_value: int) -> int:
    """Power-of-two alignment of *st_value*, capped at the page size.

    0 means unknown (st_value of 0 carries no alignment information).
    """
    if st_value <= 0:
        return 0
    return min(st_value & -st_value, PAGE_ALIGNMENT_CAP)


def symbol_value_alignment(
    elffile: Any, st_value: int, st_shndx: object, cache: dict[int, int]
) -> int:
    """:func:`value_alignment`, further capped at the containing section's
    ``sh_addralign``. A symbol not in a regular section (``SHN_*`` names, a
    section that cannot be read) keeps the address-only answer; *cache* maps
    a section index to its alignment for the walk over one table."""
    align = value_alignment(st_value)
    if align == 0 or not isinstance(st_shndx, int) or elffile is None:
        return align
    if st_shndx not in cache:
        try:
            cache[st_shndx] = int(elffile.get_section(st_shndx)["sh_addralign"])
        except Exception:  # noqa: BLE001 -- an unreadable header is "no bound"
            cache[st_shndx] = -1
    section_align = cache[st_shndx]
    if section_align < 0:
        return align
    # sh_addralign 0 and 1 both mean "no alignment constraint".
    return min(align, max(section_align, 1))
