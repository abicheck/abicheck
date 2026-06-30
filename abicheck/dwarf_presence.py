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

from .dwarf_advanced import AdvancedDwarfMetadata
from .dwarf_metadata import DwarfMetadata


def cheap_dwarf_presence_metadata(so_path: Path) -> tuple[DwarfMetadata, AdvancedDwarfMetadata]:
    """Return empty DWARF metadata objects carrying only cheap presence.

    ``scan --depth binary`` needs to report whether debug info exists, but must
    not walk every DWARF DIE. Section lookup is enough for the L1 coverage bit.
    """
    from elftools.elf.elffile import ELFFile

    from .dwarf_utils import has_real_dwarf_info

    try:
        with open(so_path, "rb") as f:
            has_dwarf = has_real_dwarf_info(ELFFile(f))
    except Exception:  # noqa: BLE001 - debug presence is advisory here
        has_dwarf = False
    return DwarfMetadata(has_dwarf=has_dwarf), AdvancedDwarfMetadata(has_dwarf=has_dwarf)


def cheap_debug_presence_metadata(
    so_path: Path,
    *,
    debug_format: str | None = None,
) -> tuple[DwarfMetadata, AdvancedDwarfMetadata]:
    """Cheaply mirror ELF debug-format selection without parsing type records."""
    if debug_format == "dwarf":
        return cheap_dwarf_presence_metadata(so_path)
    if debug_format == "btf":
        return _section_presence_metadata(_has_btf(so_path))
    if debug_format == "ctf":
        return _section_presence_metadata(_has_ctf(so_path))
    if debug_format is not None:
        raise ValueError(
            f"Invalid debug_format {debug_format!r}; expected 'dwarf', 'btf', or 'ctf'."
        )

    if _is_kernel_binary(so_path) and _has_btf(so_path):
        return _section_presence_metadata(True)

    dwarf_meta, dwarf_adv = cheap_dwarf_presence_metadata(so_path)
    if dwarf_meta.has_dwarf:
        return dwarf_meta, dwarf_adv
    if _has_btf(so_path):
        return _section_presence_metadata(True)
    if _has_ctf(so_path):
        return _section_presence_metadata(True)
    return dwarf_meta, dwarf_adv


def _section_presence_metadata(present: bool) -> tuple[DwarfMetadata, AdvancedDwarfMetadata]:
    return DwarfMetadata(has_dwarf=present), AdvancedDwarfMetadata(has_dwarf=present)


def _has_btf(so_path: Path) -> bool:
    from .btf_metadata import has_btf_section

    try:
        return has_btf_section(so_path)
    except Exception:  # noqa: BLE001 - debug presence is advisory here
        return False


def _has_ctf(so_path: Path) -> bool:
    from .ctf_metadata import has_ctf_section

    try:
        return has_ctf_section(so_path)
    except Exception:  # noqa: BLE001 - debug presence is advisory here
        return False


def _is_kernel_binary(path: Path) -> bool:
    try:
        from elftools.elf.elffile import ELFFile

        with open(path, "rb") as f:
            elf = ELFFile(f)
            return elf.get_section_by_name(".modinfo") is not None
    except Exception:  # noqa: BLE001 - debug presence is advisory here
        return False
