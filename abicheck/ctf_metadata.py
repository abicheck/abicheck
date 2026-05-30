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

"""CTF (Compact C Type Format) parser for illumos/Solaris ABI analysis.

Pure-Python implementation using only the ``struct`` module — no external
dependencies beyond pyelftools (for ELF section access).

CTF is a compact debug format used by illumos, SmartOS, OmniOS, and DTrace.
It stores struct/union layouts, enum types, typedefs, and function signatures
in a space-efficient binary format.

Supports CTF v2 (legacy) and v3 (current).

Reference: illumos ``sys/ctf.h`` and ``libctf`` source.

Public API
----------
parse_ctf_metadata(elf_path)
    → CtfMetadata (implements TypeMetadataSource protocol)

has_ctf_section(elf_path)
    → bool  (quick check without full parse)
"""
from __future__ import annotations

import logging
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from .dwarf_metadata import DwarfMetadata, EnumInfo, FieldInfo, StructLayout
from .type_metadata import FuncProto, read_null_terminated_string

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CTF constants (from sys/ctf.h)
# ---------------------------------------------------------------------------

CTF_MAGIC = 0xCFF1
CTF_VERSION_2 = 2
CTF_VERSION_3 = 3

# CTF type kinds (encoded in ctt_info)
CTF_K_UNKNOWN = 0
CTF_K_INTEGER = 1
CTF_K_FLOAT = 2
CTF_K_POINTER = 3
CTF_K_ARRAY = 4
CTF_K_FUNCTION = 5
CTF_K_STRUCT = 6
CTF_K_UNION = 7
CTF_K_ENUM = 8
CTF_K_FORWARD = 9
CTF_K_TYPEDEF = 10
CTF_K_VOLATILE = 11
CTF_K_CONST = 12
CTF_K_RESTRICT = 13

# CTF integer encoding bits
CTF_INT_SIGNED = 0x01
CTF_INT_CHAR = 0x02
CTF_INT_BOOL = 0x04

# CTF header flags
CTF_F_COMPRESS = 0x01

# Size thresholds for large vs small type encoding
_CTF_V2_LSTRUCT_THRESH = 0x1FFF   # vlen threshold for v2 "large" members
_CTF_V3_LSTRUCT_THRESH = 0x1FFF

# Header sizes
_CTF_PREAMBLE_SIZE = 4  # magic(2) + version(1) + flags(1)
_CTF_V2_HEADER_SIZE = 36
_CTF_V3_HEADER_SIZE = 36


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CtfType:
    """Raw parsed CTF type entry."""
    type_id: int
    name_off: int
    info: int
    size_or_type: int  # depends on kind
    extra: bytes = b""

    @property
    def kind(self) -> int:
        return (self.info >> 24) & 0x1F  # v3; v2 uses >> 11 & 0x1F

    @property
    def vlen(self) -> int:
        return self.info & 0xFFFF  # v3; v2 uses & 0x3FF

    @property
    def isroot(self) -> bool:
        return bool((self.info >> 31) & 1)  # v3; v2 uses >> 10 & 1


@dataclass
class CtfMetadata:
    """CTF-derived ABI-relevant type information.

    Implements the same interface as DwarfMetadata so the checker's
    detectors work without modification (TypeMetadataSource protocol).
    """
    structs: dict[str, StructLayout] = field(default_factory=dict)
    enums: dict[str, EnumInfo] = field(default_factory=dict)
    func_protos: dict[str, FuncProto] = field(default_factory=dict)
    typedefs: dict[str, str] = field(default_factory=dict)
    has_ctf: bool = False
    type_count: int = 0

    # TypeMetadataSource protocol
    @property
    def has_data(self) -> bool:
        return self.has_ctf

    def get_struct_layout(self, name: str) -> StructLayout | None:
        return self.structs.get(name)

    def get_enum_info(self, name: str) -> EnumInfo | None:
        return self.enums.get(name)

    def get_function_proto(self, name: str) -> FuncProto | None:
        return self.func_protos.get(name)

    def get_typedef(self, name: str) -> str | None:
        return self.typedefs.get(name)

    def to_dwarf_metadata(self) -> DwarfMetadata:
        """Convert to DwarfMetadata for checker compatibility."""
        return DwarfMetadata(
            structs=dict(self.structs),
            enums=dict(self.enums),
            has_dwarf=self.has_ctf,
        )


# ---------------------------------------------------------------------------
# CTF section reader
# ---------------------------------------------------------------------------

def has_ctf_section(elf_path: Path) -> bool:
    """Quick check: does the ELF file have a .ctf section?"""
    try:
        from elftools.elf.elffile import ELFFile
        with open(elf_path, "rb") as f:
            elf = ELFFile(f)  # type: ignore[no-untyped-call]
            # CTF can be in .ctf or .SUNW_ctf sections
            return (
                elf.get_section_by_name(".ctf") is not None  # type: ignore[no-untyped-call]
                or elf.get_section_by_name(".SUNW_ctf") is not None  # type: ignore[no-untyped-call]
            )
    except Exception:  # noqa: BLE001
        return False


def _read_ctf_section(elf_path: Path) -> bytes | None:
    """Read raw .ctf section data from an ELF file."""
    from elftools.elf.elffile import ELFFile
    with open(elf_path, "rb") as f:
        elf = ELFFile(f)  # type: ignore[no-untyped-call]
        section = elf.get_section_by_name(".ctf")  # type: ignore[no-untyped-call]
        if section is None:
            section = elf.get_section_by_name(".SUNW_ctf")  # type: ignore[no-untyped-call]
        if section is None:
            return None
        return bytes(section.data())


# ---------------------------------------------------------------------------
# CTF header parsing
# ---------------------------------------------------------------------------

@dataclass
class CtfHeader:
    """Parsed CTF header."""
    magic: int
    version: int
    flags: int
    parent_label: int
    parent_name: int
    label_off: int
    object_off: int
    func_off: int
    type_off: int
    str_off: int
    str_len: int


def _parse_header(data: bytes) -> CtfHeader:
    """Parse CTF preamble + header."""
    if len(data) < _CTF_PREAMBLE_SIZE:
        raise ValueError(f"CTF data too small ({len(data)} bytes)")

    magic, version, flags = struct.unpack_from("<HBB", data, 0)
    if magic != CTF_MAGIC:
        raise ValueError(f"Bad CTF magic: 0x{magic:04X} (expected 0x{CTF_MAGIC:04X})")
    if version not in (CTF_VERSION_2, CTF_VERSION_3):
        raise ValueError(f"Unsupported CTF version {version}")

    if len(data) < _CTF_V3_HEADER_SIZE:
        raise ValueError(f"CTF header truncated ({len(data)} bytes)")

    (parent_label, parent_name,
     label_off, object_off, func_off, type_off,
     str_off, str_len) = struct.unpack_from("<IIIIIIII", data, 4)

    return CtfHeader(
        magic=magic, version=version, flags=flags,
        parent_label=parent_label, parent_name=parent_name,
        label_off=label_off, object_off=object_off,
        func_off=func_off, type_off=type_off,
        str_off=str_off, str_len=str_len,
    )


def _decompress_if_needed(data: bytes, header: CtfHeader) -> bytes:
    """Decompress CTF data if CTF_F_COMPRESS flag is set."""
    if not (header.flags & CTF_F_COMPRESS):
        return data
    # Data after the preamble (4 bytes) is zlib-compressed.
    # Limit decompressed size to 256 MiB to prevent zip-bomb DoS.
    _MAX_DECOMPRESS = 256 * 1024 * 1024
    try:
        decompressor = zlib.decompressobj()
        decompressed = decompressor.decompress(data[_CTF_PREAMBLE_SIZE:], _MAX_DECOMPRESS)
        if decompressor.unconsumed_tail:
            raise ValueError("CTF decompressed data exceeds 256 MiB limit")
    except zlib.error as exc:
        raise ValueError(f"CTF decompression failed: {exc}") from exc
    # Reassemble: preamble + decompressed body
    return data[:_CTF_PREAMBLE_SIZE] + decompressed


# ---------------------------------------------------------------------------
# CTF string table
# ---------------------------------------------------------------------------

def _read_string(str_data: bytes, offset: int) -> str:
    """Read a null-terminated string from the CTF string table."""
    return read_null_terminated_string(str_data, offset)


# ---------------------------------------------------------------------------
# CTF type parsing
# ---------------------------------------------------------------------------

def _parse_info_v2(info: int) -> tuple[int, int, bool]:
    """Parse v2 ctt_info: kind(5 bits), isroot(1 bit), vlen(10 bits)."""
    kind = (info >> 11) & 0x1F
    isroot = bool((info >> 10) & 1)
    vlen = info & 0x3FF
    return kind, vlen, isroot


def _parse_info_v3(info: int) -> tuple[int, int, bool]:
    """Parse v3 ctt_info: kind(5 bits) + isroot(1 bit) in upper, vlen(16 bits) in lower."""
    kind = (info >> 24) & 0x1F
    isroot = bool((info >> 31) & 1)
    vlen = info & 0xFFFF
    return kind, vlen, isroot


def _parse_types(
    type_data: bytes, version: int,
) -> list[CtfType]:
    """Parse all CTF type entries from the type section."""
    types: list[CtfType] = [
        CtfType(type_id=0, name_off=0, info=0, size_or_type=0)
    ]

    parse_info = _parse_info_v3 if version >= CTF_VERSION_3 else _parse_info_v2

    pos = 0
    type_id = 1

    while pos < len(type_data):
        # Each type starts with: name(4) + info(4)
        # Then either size(4) for large or type(2) for small (v2)
        # v3 always uses 4-byte size_or_type
        if version >= CTF_VERSION_3:
            if pos + 12 > len(type_data):
                break
            name_off, info, size_or_type = struct.unpack_from("<III", type_data, pos)
            pos += 12
        else:
            # CTF v2: name(4) + info(2) + size_or_type(2 or 4)
            if pos + 6 > len(type_data):
                break
            name_off = struct.unpack_from("<I", type_data, pos)[0]
            info = struct.unpack_from("<H", type_data, pos + 4)[0]
            pos += 6
            kind, vlen, isroot = parse_info(info)
            # In v2, if size >= CTF_LSTRUCT_THRESH, next 4 bytes are actual size
            if kind in (CTF_K_STRUCT, CTF_K_UNION) and pos + 2 <= len(type_data):
                size_or_type = struct.unpack_from("<H", type_data, pos)[0]
                pos += 2
                if size_or_type >= _CTF_V2_LSTRUCT_THRESH:
                    if pos + 4 <= len(type_data):
                        size_or_type = struct.unpack_from("<I", type_data, pos)[0]
                        pos += 4
            elif pos + 2 <= len(type_data):
                size_or_type = struct.unpack_from("<H", type_data, pos)[0]
                pos += 2
            else:
                break
            # Re-encode info for uniform handling (v3 layout)
            info = (kind << 24) | (int(isroot) << 31) | vlen
            # kind, vlen already decoded above — no re-parse needed

        if version >= CTF_VERSION_3:
            # For v3, kind/vlen not yet decoded — decode now
            kind, vlen, _isroot = _parse_info_v3(info)

        # Read kind-specific extra data
        extra_size = _extra_data_size(kind, vlen, version, size_or_type)
        if pos + extra_size > len(type_data):
            log.warning("CTF type %d (kind=%d) truncated", type_id, kind)
            break

        extra = type_data[pos:pos + extra_size]
        pos += extra_size

        types.append(CtfType(
            type_id=type_id,
            name_off=name_off,
            info=info,
            size_or_type=size_or_type,
            extra=extra,
        ))
        type_id += 1

    return types


def _extra_data_size(kind: int, vlen: int, version: int, size_or_type: int) -> int:
    """Calculate the size of kind-specific extra data."""
    if kind == CTF_K_INTEGER:
        return 4  # encoding word
    if kind == CTF_K_FLOAT:
        return 4  # encoding word
    if kind == CTF_K_ARRAY:
        if version >= CTF_VERSION_3:
            return 12  # contents(4) + index(4) + nelems(4)
        return 6  # contents(2) + index(2) + nelems(2)  (v2 uses short)
    if kind in (CTF_K_STRUCT, CTF_K_UNION):
        if version >= CTF_VERSION_3:
            # v3: always 4+4 per member (name_off + ctm_offset) for small,
            # 4+4+4 (name_off + offset_hi + offset_lo) for large
            if size_or_type >= 0x2000:  # large struct
                return vlen * 12
            return vlen * 8
        else:
            # v2: small = name(2) + offset(2), large = name(2) + pad(2) + offset_hi(2) + offset_lo(2)
            if size_or_type >= _CTF_V2_LSTRUCT_THRESH:
                return vlen * 8
            return vlen * 4
    if kind == CTF_K_ENUM:
        return vlen * 8  # name(4) + value(4) per enumerator
    if kind == CTF_K_FUNCTION:
        # vlen argument type IDs
        size = vlen * 4 if version >= CTF_VERSION_3 else vlen * 2
        # Pad to 4-byte alignment
        return (size + 3) & ~3
    # POINTER, FORWARD, TYPEDEF, VOLATILE, CONST, RESTRICT: no extra
    return 0


# ---------------------------------------------------------------------------
# Type resolution
# ---------------------------------------------------------------------------

class _TypeResolver:
    """Resolves CTF type references to names and sizes."""

    def __init__(self, types: list[CtfType], str_data: bytes, version: int) -> None:
        self._types = types
        self._str = str_data
        self._version = version
        self._name_cache: dict[int, str] = {}
        self._size_cache: dict[int, int] = {}
        self._resolving_name: set[int] = set()
        self._resolving_size: set[int] = set()

    def name(self, type_id: int) -> str:
        if type_id in self._name_cache:
            return self._name_cache[type_id]
        if type_id in self._resolving_name:
            return "..."
        self._resolving_name.add(type_id)
        try:
            result = self._resolve_name(type_id)
            self._name_cache[type_id] = result
            return result
        finally:
            self._resolving_name.discard(type_id)

    def size(self, type_id: int) -> int:
        if type_id in self._size_cache:
            return self._size_cache[type_id]
        if type_id in self._resolving_size:
            return 0
        self._resolving_size.add(type_id)
        try:
            result = self._resolve_size(type_id)
            self._size_cache[type_id] = result
            return result
        finally:
            self._resolving_size.discard(type_id)

    def _get(self, type_id: int) -> CtfType | None:
        if 0 <= type_id < len(self._types):
            return self._types[type_id]
        return None

    def _str_at(self, offset: int) -> str:
        return _read_string(self._str, offset)

    def _resolve_name_array(self, t: CtfType) -> str:
        """Return the name string for a CTF array type."""
        if self._version >= CTF_VERSION_3 and len(t.extra) >= 12:
            elem_type = struct.unpack_from("<I", t.extra, 0)[0]
            nelems = struct.unpack_from("<I", t.extra, 8)[0]
            return f"{self.name(elem_type)}[{nelems}]"
        if self._version < CTF_VERSION_3 and len(t.extra) >= 6:
            elem_type = struct.unpack_from("<H", t.extra, 0)[0]
            nelems = struct.unpack_from("<H", t.extra, 4)[0]
            return f"{self.name(elem_type)}[{nelems}]"
        return "[]"

    def _resolve_name_tagged(self, kind: int, tname: str) -> str | None:
        """Handle tagged aggregate/forward kinds; return None if not applicable."""
        if kind in (CTF_K_STRUCT, CTF_K_UNION):
            tag = "union" if kind == CTF_K_UNION else "struct"
            return tname if tname else f"<anon {tag}>"
        if kind == CTF_K_ENUM:
            return tname if tname else "<anon enum>"
        if kind == CTF_K_FORWARD:
            return tname if tname else "<fwd>"
        return None

    # Module-level qualifier map shared across all resolver instances.
    _CV_QUALIFIERS: dict[int, str] = {
        CTF_K_VOLATILE: "volatile",
        CTF_K_CONST: "const",
        CTF_K_RESTRICT: "restrict",
    }

    def _resolve_name_simple(self, kind: int, tname: str, size_or_type: int) -> str | None:
        """Handle simple/named kinds; return None if kind is not handled here."""
        if kind == CTF_K_INTEGER:
            return tname if tname else "int"
        if kind == CTF_K_FLOAT:
            return tname if tname else "float"
        if kind == CTF_K_POINTER:
            return f"{self.name(size_or_type)} *"
        tagged = self._resolve_name_tagged(kind, tname)
        if tagged is not None:
            return tagged
        if kind == CTF_K_TYPEDEF:
            return tname if tname else self.name(size_or_type)
        if kind in self._CV_QUALIFIERS:
            return f"{self._CV_QUALIFIERS[kind]} {self.name(size_or_type)}"
        return None

    def _resolve_name(self, type_id: int) -> str:
        if type_id == 0:
            return "void"
        t = self._get(type_id)
        if t is None:
            return f"<ctf:{type_id}>"

        kind = t.kind
        tname = self._str_at(t.name_off)

        simple = self._resolve_name_simple(kind, tname, t.size_or_type)
        if simple is not None:
            return simple
        if kind == CTF_K_ARRAY:
            return self._resolve_name_array(t)
        if kind == CTF_K_FUNCTION:
            return f"{self.name(t.size_or_type)}(...)"

        return f"<ctf_kind_{kind}:{type_id}>"

    def _resolve_size(self, type_id: int) -> int:
        if type_id == 0:
            return 0
        t = self._get(type_id)
        if t is None:
            return 0

        kind = t.kind

        if kind in (CTF_K_STRUCT, CTF_K_UNION, CTF_K_ENUM):
            return t.size_or_type

        if kind == CTF_K_INTEGER:
            if len(t.extra) >= 4:
                enc: int = struct.unpack_from("<I", t.extra, 0)[0]
                nr_bits = enc & 0xFFFF
                return (nr_bits + 7) // 8
            return 0

        if kind == CTF_K_FLOAT:
            if len(t.extra) >= 4:
                enc_f: int = struct.unpack_from("<I", t.extra, 0)[0]
                nr_bits = enc_f & 0xFFFF
                return (nr_bits + 7) // 8
            return 0

        if kind == CTF_K_POINTER:
            return 8  # assume 64-bit

        if kind == CTF_K_ARRAY:
            if self._version >= CTF_VERSION_3 and len(t.extra) >= 12:
                elem_type: int
                nelems: int
                elem_type, _, nelems = struct.unpack_from("<III", t.extra, 0)
                return self.size(elem_type) * nelems
            if self._version < CTF_VERSION_3 and len(t.extra) >= 6:
                elem_type_v2: int
                nelems_v2: int
                elem_type_v2, _, nelems_v2 = struct.unpack_from("<HHH", t.extra, 0)
                return self.size(elem_type_v2) * nelems_v2
            return 0

        if kind in (CTF_K_TYPEDEF, CTF_K_VOLATILE, CTF_K_CONST, CTF_K_RESTRICT):
            return self.size(t.size_or_type)

        return 0


# ---------------------------------------------------------------------------
# High-level extraction
# ---------------------------------------------------------------------------

def _extract_structs(
    types: list[CtfType], resolver: _TypeResolver,
    str_data: bytes, version: int,
) -> dict[str, StructLayout]:
    """Extract struct/union layouts from CTF types."""
    structs: dict[str, StructLayout] = {}

    for t in types:
        if t.kind not in (CTF_K_STRUCT, CTF_K_UNION):
            continue

        name = _read_string(str_data, t.name_off)
        if not name:
            continue

        fields: list[FieldInfo] = []
        byte_size = t.size_or_type
        is_large = byte_size >= 0x2000

        for i in range(t.vlen):
            if version >= CTF_VERSION_3:
                if is_large:
                    off = i * 12
                    if off + 12 > len(t.extra):
                        break
                    m_name_off = struct.unpack_from("<I", t.extra, off)[0]
                    m_off_hi = struct.unpack_from("<I", t.extra, off + 4)[0]
                    m_off_lo = struct.unpack_from("<I", t.extra, off + 8)[0]
                    m_type = m_off_hi >> 16  # upper 16 bits = type
                    m_offset = ((m_off_hi & 0xFFFF) << 32) | m_off_lo
                else:
                    off = i * 8
                    if off + 8 > len(t.extra):
                        break
                    m_name_off, m_off_val = struct.unpack_from("<II", t.extra, off)
                    m_type = m_off_val >> 16  # upper 16 bits = type
                    m_offset = m_off_val & 0xFFFF  # lower 16 bits = bit offset
            else:
                # CTF v2
                if is_large:
                    off = i * 8
                    if off + 8 > len(t.extra):
                        break
                    m_name_off = struct.unpack_from("<H", t.extra, off)[0]
                    m_type = struct.unpack_from("<H", t.extra, off + 2)[0]
                    m_off_hi = struct.unpack_from("<H", t.extra, off + 4)[0]
                    m_off_lo = struct.unpack_from("<H", t.extra, off + 6)[0]
                    m_offset = (m_off_hi << 16) | m_off_lo
                else:
                    off = i * 4
                    if off + 4 > len(t.extra):
                        break
                    m_name_off, m_off_val = struct.unpack_from("<HH", t.extra, off)
                    m_type = (m_off_val >> 10) & 0x3F  # v2 packs type in offset
                    m_offset = m_off_val & 0x3FF

            m_name = _read_string(str_data, m_name_off)
            byte_offset = m_offset // 8
            bit_offset = m_offset % 8

            fields.append(FieldInfo(
                name=m_name,
                type_name=resolver.name(m_type),
                byte_offset=byte_offset,
                byte_size=resolver.size(m_type),
                bit_offset=bit_offset if bit_offset else 0,
                bit_size=0,  # CTF doesn't encode bitfield size directly
            ))

        layout = StructLayout(
            name=name,
            byte_size=byte_size,
            alignment=0,
            fields=fields,
            is_union=(t.kind == CTF_K_UNION),
        )

        if name not in structs:
            structs[name] = layout

    return structs


def _extract_enums(
    types: list[CtfType], str_data: bytes,
) -> dict[str, EnumInfo]:
    """Extract enum types from CTF."""
    enums: dict[str, EnumInfo] = {}

    for t in types:
        if t.kind != CTF_K_ENUM:
            continue

        name = _read_string(str_data, t.name_off)
        if not name:
            continue

        members: dict[str, int] = {}
        for i in range(t.vlen):
            off = i * 8
            if off + 8 > len(t.extra):
                break
            e_name_off, e_val = struct.unpack_from("<Ii", t.extra, off)
            e_name = _read_string(str_data, e_name_off)
            if e_name:
                members[e_name] = e_val

        if name not in enums:
            enums[name] = EnumInfo(
                name=name,
                underlying_byte_size=t.size_or_type,
                members=members,
            )

    return enums


def _extract_typedefs(
    types: list[CtfType], resolver: _TypeResolver, str_data: bytes,
) -> dict[str, str]:
    """Extract typedef mappings."""
    typedefs: dict[str, str] = {}
    for t in types:
        if t.kind != CTF_K_TYPEDEF:
            continue
        name = _read_string(str_data, t.name_off)
        if not name:
            continue
        target = resolver.name(t.size_or_type)
        if name not in typedefs:
            typedefs[name] = target
    return typedefs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_ctf_metadata(elf_path: Path) -> CtfMetadata:
    """Parse CTF section from an ELF file and return CtfMetadata.

    Returns ``CtfMetadata()`` on any error.  Never raises.
    """
    empty = CtfMetadata()

    try:
        raw = _read_ctf_section(elf_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_ctf_metadata: failed to read .ctf from %s: %s", elf_path, exc)
        return empty

    if raw is None:
        log.debug("parse_ctf_metadata: no .ctf section in %s", elf_path)
        return empty

    return parse_ctf_from_bytes(raw)


def parse_ctf_from_bytes(data: bytes) -> CtfMetadata:
    """Parse CTF from raw bytes (useful for testing without ELF wrapper).

    Returns ``CtfMetadata()`` on any error.  Never raises.
    """
    empty = CtfMetadata()

    try:
        header = _parse_header(data)
    except (ValueError, struct.error) as exc:
        log.warning("parse_ctf_from_bytes: bad header: %s", exc)
        return empty

    # Decompress if needed
    try:
        data = _decompress_if_needed(data, header)
        # Re-parse header after decompression (offsets may have changed)
        if header.flags & CTF_F_COMPRESS:
            header = _parse_header(data)
    except (ValueError, zlib.error) as exc:
        log.warning("parse_ctf_from_bytes: decompression failed: %s", exc)
        return empty

    hdr_size = _CTF_V3_HEADER_SIZE
    type_start = hdr_size + header.type_off
    type_end = hdr_size + header.str_off  # type section ends where string section begins
    str_start = hdr_size + header.str_off
    str_end = str_start + header.str_len

    if type_end > len(data) or str_end > len(data):
        log.warning("parse_ctf_from_bytes: section bounds exceed data size")
        return empty

    type_data = data[type_start:type_end]
    str_data = data[str_start:str_end]

    try:
        types = _parse_types(type_data, header.version)
    except (struct.error, ValueError) as exc:
        log.warning("parse_ctf_from_bytes: type parsing failed: %s", exc)
        return empty

    resolver = _TypeResolver(types, str_data, header.version)

    meta = CtfMetadata(has_ctf=True, type_count=len(types) - 1)

    try:
        meta.structs = _extract_structs(types, resolver, str_data, header.version)
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_ctf_from_bytes: struct extraction failed: %s", exc)

    try:
        meta.enums = _extract_enums(types, str_data)
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_ctf_from_bytes: enum extraction failed: %s", exc)

    try:
        meta.typedefs = _extract_typedefs(types, resolver, str_data)
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_ctf_from_bytes: typedef extraction failed: %s", exc)

    return meta
