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

"""Tell "no DWARF" from "DWARF whose unit section was lost".

The DWARF readers decide whether a binary carries debug info by looking up
``.debug_info`` (``dwarf_utils.has_real_dwarf_info``). A section header whose
name no longer resolves -- one corrupted ``sh_name`` byte -- hides that
section while ``.debug_abbrev`` and the other sections that only exist to
serve it stay visible, and the binary read as having no debug info at all.
An abbreviation table with no unit section to decode is never a toolchain's
output, so it is recorded as a failed debug-info read instead.
"""

from __future__ import annotations

from typing import Any

from .parse_failures import note_debug_info_read_failure

__all__ = ["note_orphaned_debug_sections", "note_unit_past_section"]

#: Sections that describe ``.debug_info``'s units and exist for no other use
#: (``.debug_frame`` and ``.debug_gdb_scripts`` stand alone, so are not here).
_UNIT_COMPANIONS = tuple(
    prefix + name
    for name in (
        "abbrev",
        "aranges",
        "line",
        "line_str",
        "str",
        "str_offsets",
        "addr",
        "loclists",
        "rnglists",
        "loc",
        "ranges",
    )
    for prefix in (".debug_", ".zdebug_")
)


def note_orphaned_debug_sections(elf: Any) -> None:
    """Record a failure when *elf* (with no ``.debug_info``) still carries a
    section only a ``.debug_info`` uses (its abbreviations, lines, strings...).

    A section-name table that cannot be read at all hides every debug section
    with the rest; that is the section-table loss
    ``tests/test_export_table_unreadable_matrix.py`` pins as recovered from the
    loader view without debug info, so it is not recorded here."""
    present = [name for name in _UNIT_COMPANIONS if elf.get_section_by_name(name)]
    if present:
        note_debug_info_read_failure(
            f"{present[0]} is present but .debug_info is not "
            "(a debug section header is unreadable)"
        )


def note_unit_past_section(cu: Any, dwarf: Any) -> None:
    """Record a failure when compilation unit *cu* claims more bytes than its
    ``.debug_info`` holds: a corrupt or cut unit length, whose DIEs the walk
    reads as none rather than raising."""
    section = getattr(dwarf, "debug_info_sec", None)
    size = getattr(section, "size", None)
    if isinstance(size, int) and cu.cu_offset + cu.size > size:
        note_debug_info_read_failure(
            f"CU at {cu.cu_offset} claims {cu.size} bytes; .debug_info holds {size}"
        )
