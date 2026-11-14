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

"""Sizes of vtable, typeinfo and VTT objects in a Mach-O image.

An ELF ``.dynsym`` entry carries ``st_size``, the exact extent of the object.
A Mach-O ``nlist`` carries an address and a section ordinal but no size, so
the size is recovered from the layout the linker wrote, by the method below::

    size(sym) = next defined address in the same section - sym.address
    size(last symbol of a section) = section end - sym.address

"Next" counts every defined symbol of the section, external, local and
private-extern alike, because each one occupies an address the object cannot
extend across. Debugging (stab) entries are not symbols and are excluded by
the caller, which passes only ``N_SECT`` entries.

Limits, which keep this evidence rather than ``st_size``:

* The value is the distance to the next label, so padding the linker inserts
  between two objects is counted into the first. The Itanium ABI aligns
  vtables and typeinfo objects to the pointer size, so for the compilers this
  targets the padding is zero, but a section whose last object shares its
  section with unlabelled data reads the data as part of the object.
* Only the ``_ZTV``, ``_ZTI`` and ``_ZTT`` families are sized: thunks are
  identified by name alone, and no other consumer reads a size.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping

#: Itanium object families whose size the layout detectors read, spelled as
#: they appear after abicheck strips the Mach-O C leading underscore.
SIZED_OBJECT_PREFIXES: tuple[str, ...] = ("_ZTV", "_ZTI", "_ZTT")


def derive_object_sizes(
    defined: Iterable[tuple[int, int, str]],
    sections: Mapping[int, tuple[int, int]],
) -> dict[str, int]:
    """Map each sized object name to its derived size in bytes.

    *defined* holds ``(n_sect, n_value, name)`` for every defined ``N_SECT``
    symbol, with the leading underscore already stripped. *sections* maps the
    1-based section ordinal to ``(address, size)``. A name whose derived size
    is not positive, or whose address lies outside its section, is omitted:
    no size is better than a wrong one.
    """
    by_section: dict[int, list[tuple[int, str]]] = {}
    for n_sect, value, name in defined:
        by_section.setdefault(n_sect, []).append((value, name))

    sizes: dict[str, int] = {}
    for n_sect, entries in by_section.items():
        extent = sections.get(n_sect)
        if extent is None:
            continue
        sec_addr, sec_size = extent
        sec_end = sec_addr + sec_size
        addresses = sorted({value for value, _name in entries})
        for value, name in entries:
            if not name.startswith(SIZED_OBJECT_PREFIXES):
                continue
            if not sec_addr <= value < sec_end:
                continue
            following = bisect_right(addresses, value)
            end = (
                min(addresses[following], sec_end)
                if following < len(addresses)
                else sec_end
            )
            if end - value > 0:
                sizes.setdefault(name, end - value)
    return sizes
