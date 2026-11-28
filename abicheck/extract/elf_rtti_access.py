# SPDX-License-Identifier: Apache-2.0
"""Public direct-base paths in exported x86-64 Itanium VMI type descriptors."""

from __future__ import annotations

import logging
import struct
from typing import Any

from .parse_failures import export_read_failed

log = logging.getLogger(__name__)
_VMI = "_ZTVN10__cxxabiv121__vmi_class_type_infoE"


def read_rtti_access(elf: Any) -> dict[str, dict[str, bool]] | None:
    if (
        elf["e_machine"] != "EM_X86_64"
        or elf.elfclass != 64
        or not elf.little_endian
        or export_read_failed()
    ):
        return None
    try:
        return _edges(elf)
    except Exception as exc:  # noqa: BLE001 - optional malformed metadata is unknown
        log.debug("RTTI access flags unavailable: %s", exc)
        return None


def _edges(elf: Any) -> dict[str, dict[str, bool]] | None:
    syms = elf.get_section_by_name(".dynsym")
    rels = elf.get_section_by_name(".rela.dyn")
    if (
        syms is None
        or rels is None
        or elf.get_section(rels["sh_link"]).name != ".dynsym"
    ):
        return None
    # Restrict the initial table scan to files that actually reference VMI RTTI.
    if not syms.get_symbol_by_name(_VMI):
        return {}
    # A locally bound R_X86_64_RELATIVE addend is the link-time address.
    # Require an exact, unambiguous dynamic RTTI symbol at that address.
    by_address = {}
    for symbol_index, sym in enumerate(syms.iter_symbols()):
        if (
            sym.name.startswith("_ZTI")
            and isinstance(sym["st_shndx"], int)
            and sym["st_info"]["type"] == "STT_OBJECT"
            and sym["st_size"] > 0
        ):
            address = sym["st_value"]
            by_address[address] = symbol_index if address not in by_address else None
    refs = {}
    for rel in rels.iter_relocations():
        addr = rel["r_offset"]
        target = None
        if addr not in refs:
            if rel["r_info_type"] == 1:
                target = (rel["r_info_sym"], rel["r_addend"])
            elif rel["r_info_type"] == 8 and rel["r_info_sym"] == 0:
                index = by_address.get(rel["r_addend"])
                if index is not None:
                    target = (index, 0)
        refs[addr] = target
    # RELR stores the addend in the relocated word itself.
    packed_slots: set[int] = set()
    for section in elf.iter_sections(type="SHT_RELR"):
        for rel in section.iter_relocations():
            address = rel["r_offset"]
            if address in refs or address in packed_slots:
                return None
            packed_slots.add(address)
    result: dict[str, dict[str, bool]] = {}
    seen = set()
    for sym in syms.iter_symbols():
        name = sym.name
        idx = sym["st_shndx"]
        if (
            not name.startswith("_ZTI")
            or not isinstance(idx, int)
            or sym["st_info"]["type"] != "STT_OBJECT"
        ):
            continue
        if name in seen:
            result.pop(name, None)
            continue
        seen.add(name)
        start, size = sym["st_value"], sym["st_size"]
        head = refs.get(start)
        if not head or head[1] != 16 or syms.get_symbol(head[0]).name != _VMI:
            continue
        section = elf.get_section(idx)
        offset = start - section["sh_addr"]
        if (
            section["sh_type"] != "SHT_PROGBITS"
            or offset < 0
            or size < 40
            or offset + size > section["sh_size"]
        ):
            continue
        if any(
            addr in refs or addr in packed_slots for addr in (start + 16, start + 20)
        ):
            continue
        elf.stream.seek(section["sh_offset"] + offset)
        raw = elf.stream.read(size)
        count = struct.unpack_from("<I", raw, 20)[0]
        if len(raw) != size or size != 24 + count * 16:
            continue
        edges = {}
        for i in range(count):
            slot = 24 + 16 * i
            target = refs.get(start + slot)
            if start + slot in packed_slots:
                index = by_address.get(struct.unpack_from("<Q", raw, slot)[0])
                target = (index, 0) if index is not None else None
            flag_address = start + slot + 8
            if (
                not target
                or target[1]
                or flag_address in refs
                or flag_address in packed_slots
            ):
                break
            base = syms.get_symbol(target[0]).name
            if not base.startswith("_ZTI") or base in edges:
                break
            edges[base] = bool(raw[slot + 8] & 2)
        else:
            result[name] = edges
    return result
