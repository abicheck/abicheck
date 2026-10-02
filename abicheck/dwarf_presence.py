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

"""Cheap debug-presence helpers for binary-depth scans.

Each section probe is a producer and returns a ``Fact[bool]``: a probe that
raised is ``FAILED``, never a confirmed "no such section". Auto-detection
combines the probes with the one evidence-merge rule
(``model/evidence_merge.merge_presence``). The returned metadata still
carries a plain ``has_dwarf`` flag, which is "debug evidence in hand"
(``model.dwarf_facts.debug_info_present``): an unknown merge reads as
``False`` there, which understates assurance and never states the artifact
is stripped.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .model.availability import FactStatus
from .model.dwarf_facts import AdvancedDwarfMetadata, DwarfMetadata
from .model.evidence_merge import merge_presence
from .model.fact import Fact


def _probe(read: Callable[[], bool], what: str) -> Fact[bool]:
    try:
        return Fact.present(bool(read()))
    except Exception as exc:  # noqa: BLE001 - a failed probe is an unknown, not "absent"
        return Fact.failed(f"{what} probe failed: {type(exc).__name__}")


def _has_dwarf(so_path: Path) -> Fact[bool]:
    from elftools.elf.elffile import ELFFile

    from .dwarf_utils import has_real_dwarf_info

    def read() -> bool:
        with open(so_path, "rb") as f:
            return bool(has_real_dwarf_info(ELFFile(f)))

    return _probe(read, "DWARF section")


def _confirmed(fact: Fact[bool]) -> bool:
    return fact.status is FactStatus.PRESENT and fact.value is True


def cheap_dwarf_presence_metadata(
    so_path: Path,
) -> tuple[DwarfMetadata, AdvancedDwarfMetadata]:
    """Return empty DWARF metadata objects carrying only cheap presence.

    ``scan --depth binary`` needs to report whether debug info exists, but must
    not walk every DWARF DIE. Section lookup is enough for the L1 coverage bit.
    """
    return _section_presence_metadata(_confirmed(_has_dwarf(so_path)))


def cheap_debug_presence_metadata(
    so_path: Path,
    *,
    debug_format: str | None = None,
) -> tuple[DwarfMetadata, AdvancedDwarfMetadata]:
    """Cheaply mirror ELF debug-format selection without parsing type records."""
    if debug_format == "dwarf":
        return cheap_dwarf_presence_metadata(so_path)
    if debug_format == "btf":
        return _section_presence_metadata(_confirmed(_has_btf(so_path)))
    if debug_format == "ctf":
        return _section_presence_metadata(_confirmed(_has_ctf(so_path)))
    if debug_format is not None:
        raise ValueError(
            f"Invalid debug_format {debug_format!r}; expected 'dwarf', 'btf', or 'ctf'."
        )

    if _confirmed(_is_kernel_binary(so_path)) and _confirmed(_has_btf(so_path)):
        return _section_presence_metadata(True)

    dwarf_meta, dwarf_adv = cheap_dwarf_presence_metadata(so_path)
    if dwarf_meta.has_dwarf:
        return dwarf_meta, dwarf_adv
    btf = _has_btf(so_path)
    if _confirmed(btf):  # a positive needs no further probe
        return _section_presence_metadata(True)
    any_debug = merge_presence(btf, _has_ctf(so_path))
    return _section_presence_metadata(_confirmed(any_debug))


def _section_presence_metadata(
    present: bool,
) -> tuple[DwarfMetadata, AdvancedDwarfMetadata]:
    return DwarfMetadata(has_dwarf=present), AdvancedDwarfMetadata(has_dwarf=present)


def _has_section(so_path: Path, *names: str) -> Fact[bool]:
    """Whether the ELF file carries any of *names*; a read error is ``FAILED``.

    Reads the sections itself rather than through ``btf_metadata.
    has_btf_section``/``ctf_metadata.has_ctf_section``: those helpers answer
    ``False`` when the file cannot be read, which here would be a confirmed
    absence.
    """

    def read() -> bool:
        from elftools.elf.elffile import ELFFile

        with open(so_path, "rb") as f:
            elf = ELFFile(f)
            return any(elf.get_section_by_name(name) is not None for name in names)

    return _probe(read, f"{'/'.join(names)} section")


def _has_btf(so_path: Path) -> Fact[bool]:
    return _has_section(so_path, ".BTF")


def _has_ctf(so_path: Path) -> Fact[bool]:
    # CTF can be in .ctf or .SUNW_ctf (matches ctf_metadata.has_ctf_section).
    return _has_section(so_path, ".ctf", ".SUNW_ctf")


def _is_kernel_binary(path: Path) -> Fact[bool]:
    return _has_section(path, ".modinfo")
