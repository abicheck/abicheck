"""Independent oracle for named Itanium VMI base edges in x86-64 ELF DSOs."""

import struct
from pathlib import Path

from elftools.elf.elffile import ELFFile


def direct_base_flags(library: Path, symbol: str) -> dict[str, int] | None:
    """Read raw offset_flags for direct bases with named R_X86_64_64 relocations.

    Return None when this narrow oracle cannot establish all edges.
    """
    with library.open("rb") as stream:
        elf = ELFFile(stream)
        if (
            elf["e_machine"] != "EM_X86_64"
            or elf.elfclass != 64
            or not elf.little_endian
        ):
            return None
        symbols = elf.get_section_by_name(".dynsym")
        relas = elf.get_section_by_name(".rela.dyn")
        if (
            symbols is None
            or relas is None
            or elf.get_section(relas["sh_link"]).name != ".dynsym"
        ):
            return None
        matches = symbols.get_symbol_by_name(symbol) or []
        if len(matches) != 1 or not isinstance(matches[0]["st_shndx"], int):
            return None
        obj = matches[0]
        start, size = obj["st_value"], obj["st_size"]
        section = elf.get_section(obj["st_shndx"])
        offset = start - section["sh_addr"]
        if (
            not symbol.startswith("_ZTI")
            or obj["st_info"]["type"] != "STT_OBJECT"
            or section["sh_type"] != "SHT_PROGBITS"
            or offset < 0
            or size < 24
            or offset + size > section["sh_size"]
        ):
            return None
        stream.seek(section["sh_offset"] + offset)
        raw = stream.read(size)
        if len(raw) != size:
            return None
        count = struct.unpack_from("<I", raw, 20)[0]
        if size != 24 + 16 * count:
            return None
        references = {}
        for rel in relas.iter_relocations():
            location = rel["r_offset"] - start
            if 0 <= location < size:
                if location not in (0, 8) and (location < 24 or (location - 24) % 16):
                    return None
                if location in references or rel["r_info_type"] != 1:
                    return None
                references[location] = (
                    symbols.get_symbol(rel["r_info_sym"]).name,
                    rel["r_addend"],
                )
        if references.get(0) != ("_ZTVN10__cxxabiv121__vmi_class_type_infoE", 16):
            return None
        edges = {}
        for index in range(count):
            slot = 24 + 16 * index
            base, addend = references.get(slot, ("", 0))
            if not base.startswith("_ZTI") or addend or base in edges:
                return None
            edges[base] = struct.unpack_from("<q", raw, slot + 8)[0]
        return edges
