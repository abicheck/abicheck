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
A Mach-O ``nlist`` carries an address and a section ordinal but no size. A
span can be recovered from the layout, but it is only as good as the label
that closes it, so a size is produced only under that condition::

    size(sym) = address of the next defined label in the same section - sym.address

A section end is **not** a boundary: it closes a section's tail, which holds
whatever the linker placed after the object (padding, unlabelled data, a
later section's neighbours), so a span closed by it is never reported. The
result also records the closing label's name. The detector compares two
spans only when both name the same closing label, because a span closed by a
different label is a different measurement, not a changed object.

"Next" counts every defined symbol of the section, external, local and
private-extern alike, because each one occupies an address the object cannot
extend across. Debugging (stab) entries are not symbols: the caller passes
only ``N_SECT`` entries that are not stab-bit entries.

Limits, which keep this evidence rather than ``st_size``:

* Alignment padding between the object and its closing label is counted into
  the span. Itanium aligns vtables and typeinfo to the pointer size, so the
  padding is normally zero, but a span can still move with the padding of its
  closing label. This is the residual uncertainty the detector cannot close.
* Only the ``_ZTV``, ``_ZTI`` and ``_ZTT`` families are sized.
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
) -> dict[str, tuple[int, str]]:
    """Map each sized object name to ``(derived size, closing label name)``.

    *defined* holds ``(n_sect, n_value, name)`` for every defined ``N_SECT``
    symbol that is not a stab entry, with the leading underscore already
    stripped. *sections* maps the 1-based section ordinal to
    ``(address, size)``. An object is omitted unless a following label in its
    own section closes it at a higher address; a section end never closes one.
    """
    by_section: dict[int, list[tuple[int, str]]] = {}
    for n_sect, value, name in defined:
        by_section.setdefault(n_sect, []).append((value, name))

    out: dict[str, tuple[int, str]] = {}
    for n_sect, entries in by_section.items():
        extent = sections.get(n_sect)
        if extent is None:
            continue
        sec_addr, sec_size = extent
        sec_end = sec_addr + sec_size
        ordered = sorted(entries)
        addresses = [value for value, _name in ordered]
        for value, name in entries:
            if not name.startswith(SIZED_OBJECT_PREFIXES):
                continue
            if not sec_addr <= value < sec_end:
                continue
            following = bisect_right(addresses, value)
            if following >= len(addresses):
                continue  # closed only by the section end: no boundary
            closer_addr = addresses[following]
            if closer_addr >= sec_end:
                continue
            closer_name = next(n for v, n in ordered if v == closer_addr)
            if closer_addr - value > 0:
                out.setdefault(name, (closer_addr - value, closer_name))
    return out
