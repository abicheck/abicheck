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

"""Cheap debug-presence helpers for binary-depth scans."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def cheap_dwarf_presence_metadata(so_path: Path) -> tuple[Any, Any]:
    """Return empty DWARF metadata objects carrying only cheap presence.

    ``scan --depth binary`` needs to report whether debug info exists, but must
    not walk every DWARF DIE. Section lookup is enough for the L1 coverage bit.
    """
    from elftools.elf.elffile import ELFFile

    from .dwarf_advanced import AdvancedDwarfMetadata
    from .dwarf_metadata import DwarfMetadata
    from .dwarf_utils import has_real_dwarf_info

    try:
        with open(so_path, "rb") as f:
            has_dwarf = has_real_dwarf_info(ELFFile(f))  # type: ignore[no-untyped-call]
    except Exception:  # noqa: BLE001 - debug presence is advisory here
        has_dwarf = False
    return DwarfMetadata(has_dwarf=has_dwarf), AdvancedDwarfMetadata(has_dwarf=has_dwarf)
