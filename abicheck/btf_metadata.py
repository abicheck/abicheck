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

"""BTF (BPF Type Format) parser for Linux kernel ABI analysis.

Pure-Python implementation using only the ``struct`` module — no external
dependencies beyond pyelftools (for ELF section access).

BTF is a compact, pre-deduplicated type format used by Linux kernel 5.x+
and eBPF programs.  It is often the **only** debug format available in
production kernel builds (DWARF stripped, BTF kept).

Reference: ``include/uapi/linux/btf.h`` in the Linux kernel source.

Public API
----------
parse_btf_metadata(elf_path)
    → BtfMetadata (implements TypeMetadataSource protocol)

has_btf_section(elf_path)
    → bool  (quick check without full parse)
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .btf_type_resolver import (
    BTF_INT_BOOL as BTF_INT_BOOL,
    BTF_INT_CHAR as BTF_INT_CHAR,
    BTF_INT_SIGNED as BTF_INT_SIGNED,
    BTF_KIND_ARRAY as BTF_KIND_ARRAY,
    BTF_KIND_CONST as BTF_KIND_CONST,
    BTF_KIND_DATASEC as BTF_KIND_DATASEC,
    BTF_KIND_DECL_TAG as BTF_KIND_DECL_TAG,
    BTF_KIND_ENUM as BTF_KIND_ENUM,
    BTF_KIND_ENUM64 as BTF_KIND_ENUM64,
    BTF_KIND_FLOAT as BTF_KIND_FLOAT,
    BTF_KIND_FUNC as BTF_KIND_FUNC,
    BTF_KIND_FUNC_PROTO as BTF_KIND_FUNC_PROTO,
    BTF_KIND_FWD as BTF_KIND_FWD,
    BTF_KIND_INT as BTF_KIND_INT,
    BTF_KIND_PTR as BTF_KIND_PTR,
    BTF_KIND_RESTRICT as BTF_KIND_RESTRICT,
    BTF_KIND_STRUCT as BTF_KIND_STRUCT,
    BTF_KIND_TYPE_TAG as BTF_KIND_TYPE_TAG,
    BTF_KIND_TYPEDEF as BTF_KIND_TYPEDEF,
    BTF_KIND_UNION as BTF_KIND_UNION,
    BTF_KIND_VAR as BTF_KIND_VAR,
    BTF_KIND_VOID as BTF_KIND_VOID,
    BTF_KIND_VOLATILE as BTF_KIND_VOLATILE,
    BTF_MAGIC as BTF_MAGIC,
    BTF_VERSION as BTF_VERSION,
    BtfType as BtfType,
    _read_string as _read_string,
    _TypeResolver as _TypeResolver,
)
from .model.dwarf_facts import DwarfMetadata, EnumInfo, FieldInfo, StructLayout
from .type_metadata import FuncProto

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# BTF constants (from include/uapi/linux/btf.h)
#
# The kind/int-encoding constants and the raw BtfType record, plus
# _read_string and _TypeResolver, live in btf_type_resolver.py to keep this
# module under the architecture debt-no-growth ceiling (ADR-061) -- mirrors
# ctf_metadata.py's own identical split. Explicitly re-exported above (the
# `X as X` spelling, same convention checker_policy.py uses for ChangeKind)
# since existing callers -- including this module's own tests -- import
# them from here.
# ---------------------------------------------------------------------------

# Header size
_BTF_HEADER_SIZE = 24  # magic(2) + version(1) + flags(1) + hdr_len(4) + type_off/len(4+4) + str_off/len(4+4)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class BtfMetadata:
    """BTF-derived ABI-relevant type information.

    Implements the same interface as DwarfMetadata so the checker's
    detectors work without modification (TypeMetadataSource protocol).
    """

    structs: dict[str, StructLayout] = field(default_factory=dict)
    enums: dict[str, EnumInfo] = field(default_factory=dict)
    func_protos: dict[str, FuncProto] = field(default_factory=dict)
    typedefs: dict[str, str] = field(default_factory=dict)
    has_btf: bool = False
    type_count: int = 0
    extraction_partial: bool = False  # any stage below raised+caught (P2 review)

    # TypeMetadataSource protocol
    @property
    def has_data(self) -> bool:
        return self.has_btf

    def get_struct_layout(self, name: str) -> StructLayout | None:
        return self.structs.get(name)

    def get_enum_info(self, name: str) -> EnumInfo | None:
        return self.enums.get(name)

    def get_function_proto(self, name: str) -> FuncProto | None:
        return self.func_protos.get(name)

    def get_typedef(self, name: str) -> str | None:
        return self.typedefs.get(name)

    def to_dwarf_metadata(self) -> DwarfMetadata:
        """Convert to DwarfMetadata for checker compatibility.

        Note: Only structs and enums are transferred; func_protos and
        typedefs are not included in DwarfMetadata. Callers needing full
        BTF data should use BtfMetadata directly.
        """
        parsed_state = "partial" if self.extraction_partial else "parsed"
        state = parsed_state if self.has_btf else "not_available"
        return DwarfMetadata(
            structs=dict(self.structs),
            enums=dict(self.enums),
            has_dwarf=self.has_btf,
            evidence_source="btf",
            evidence_state=state,
        )


# ---------------------------------------------------------------------------
# BTF section reader
# ---------------------------------------------------------------------------


def has_btf_section(elf_path: Path) -> bool:
    """Quick check: does the ELF file have a .BTF section?"""
    try:
        from elftools.elf.elffile import ELFFile

        with open(elf_path, "rb") as f:
            elf = ELFFile(f)  # type: ignore[no-untyped-call]
            return elf.get_section_by_name(".BTF") is not None  # type: ignore[no-untyped-call]
    except Exception:  # noqa: BLE001
        return False


def _read_btf_section(elf_path: Path) -> tuple[bytes, int] | None:
    """Read raw .BTF section data from an ELF file; return (data, pointer_size)."""
    from elftools.elf.elffile import ELFFile

    with open(elf_path, "rb") as f:
        elf = ELFFile(f)  # type: ignore[no-untyped-call]
        section = elf.get_section_by_name(".BTF")  # type: ignore[no-untyped-call]
        if section is None:
            return None
        pointer_size = 4 if elf.elfclass == 32 else 8
        return bytes(section.data()), pointer_size


# ---------------------------------------------------------------------------
# BTF header + type/string parsing
# ---------------------------------------------------------------------------


@dataclass
class BtfHeader:
    """Parsed BTF header."""

    magic: int
    version: int
    flags: int
    hdr_len: int
    type_off: int
    type_len: int
    str_off: int
    str_len: int


def _parse_header(data: bytes) -> BtfHeader:
    """Parse BTF header from raw bytes."""
    if len(data) < _BTF_HEADER_SIZE:
        raise ValueError(
            f"BTF data too small ({len(data)} bytes, need {_BTF_HEADER_SIZE})"
        )

    magic, version, flags, hdr_len = struct.unpack_from("<HBBI", data, 0)

    if magic != BTF_MAGIC:
        raise ValueError(f"Bad BTF magic: 0x{magic:04X} (expected 0x{BTF_MAGIC:04X})")
    if version != BTF_VERSION:
        log.warning(
            "BTF version %d (expected %d), parsing may fail", version, BTF_VERSION
        )

    type_off, type_len, str_off, str_len = struct.unpack_from("<IIII", data, 8)

    return BtfHeader(
        magic=magic,
        version=version,
        flags=flags,
        hdr_len=hdr_len,
        type_off=type_off,
        type_len=type_len,
        str_off=str_off,
        str_len=str_len,
    )


def _parse_types(
    type_data: bytes, truncated: list[bool] | None = None
) -> list[BtfType]:
    """Parse all BTF type entries from the type section.

    Returns a list indexed by type_id (0-based; type_id 0 is void/sentinel).

    P2 review, fresh evidence: every early exit below is a truncation that
    does not raise -- it logs and stops, returning every type parsed
    *before* the cut rather than losing all of them (a later duplicate-name
    definition might still complete the canonical record elsewhere). That
    graceful-degradation shape means the caller has no way to tell "fully
    parsed" apart from "silently truncated" from the return value alone.
    *truncated*, when passed a one-element list, has ``True`` appended to it
    whenever the loop stopped with unconsumed bytes still remaining --
    either the inner ``pos + extra_size > len(type_data)`` truncation, or
    the outer ``while`` loop ending because fewer than 12 bytes remain for
    even the next entry's fixed header. An opt-in out-parameter rather than
    a return-type change, so every existing caller that only wants the type
    list is unaffected.
    """
    # Type ID 0 is always void (implicit, not in the data)
    types: list[BtfType] = [
        BtfType(type_id=0, name_off=0, info=0, size_or_type=0, extra=b"")
    ]

    pos = 0
    type_id = 1
    while pos + 12 <= len(type_data):
        name_off, info, size_or_type = struct.unpack_from("<III", type_data, pos)
        pos += 12
        kind = (info >> 24) & 0x1F
        vlen = info & 0xFFFF

        # Determine extra data size based on kind
        extra_size = _extra_data_size(kind, vlen)
        if pos + extra_size > len(type_data):
            log.warning(
                "BTF type %d (kind=%d) truncated at offset %d", type_id, kind, pos
            )
            if truncated is not None:
                truncated.append(True)
            return types

        extra = type_data[pos : pos + extra_size]
        pos += extra_size

        types.append(
            BtfType(
                type_id=type_id,
                name_off=name_off,
                info=info,
                size_or_type=size_or_type,
                extra=extra,
            )
        )
        type_id += 1

    if truncated is not None and pos < len(type_data):
        # Loop ended because fewer than 12 bytes remain for even the next
        # entry's fixed header -- also a truncation, just one the inner
        # `pos + extra_size > len(type_data)` check above never reaches.
        truncated.append(True)

    return types


def _extra_data_size(kind: int, vlen: int) -> int:
    """Calculate the size of kind-specific extra data following a btf_type."""
    if kind in (BTF_KIND_INT, BTF_KIND_FLOAT):
        return 4  # encoding info
    if kind == BTF_KIND_ARRAY:
        return 12  # btf_array: type(4) + index_type(4) + nelems(4)
    if kind in (BTF_KIND_STRUCT, BTF_KIND_UNION):
        return vlen * 12  # btf_member: name_off(4) + type(4) + offset(4)
    if kind == BTF_KIND_ENUM:
        return vlen * 8  # btf_enum: name_off(4) + val(4)
    if kind == BTF_KIND_ENUM64:
        return vlen * 12  # btf_enum64: name_off(4) + val_lo32(4) + val_hi32(4)
    if kind == BTF_KIND_FUNC_PROTO:
        return vlen * 8  # btf_param: name_off(4) + type(4)
    if kind == BTF_KIND_VAR:
        return 4  # linkage
    if kind == BTF_KIND_DATASEC:
        return vlen * 12  # btf_var_secinfo: type(4) + offset(4) + size(4)
    if kind == BTF_KIND_DECL_TAG:
        return 4  # component_idx
    # PTR, FWD, TYPEDEF, VOLATILE, CONST, RESTRICT, FUNC, TYPE_TAG: no extra
    return 0


# ---------------------------------------------------------------------------
# High-level extraction
# ---------------------------------------------------------------------------


def _extract_structs(
    types: list[BtfType],
    resolver: _TypeResolver,
    str_data: bytes,
) -> dict[str, StructLayout]:
    """Extract struct/union layouts from BTF types."""
    structs: dict[str, StructLayout] = {}

    for t in types:
        if t.kind not in (BTF_KIND_STRUCT, BTF_KIND_UNION):
            continue

        name = _read_string(str_data, t.name_off)
        if not name:
            continue  # skip anonymous

        fields: list[FieldInfo] = []
        vlen = t.vlen
        for i in range(vlen):
            off = i * 12
            if off + 12 > len(t.extra):
                break
            m_name_off, m_type, m_offset = struct.unpack_from("<III", t.extra, off)
            m_name = _read_string(str_data, m_name_off)

            # kflag determines offset encoding:
            # kflag=0: m_offset is byte_offset * 8 (bit offset from struct start)
            # kflag=1: bits 0-23 = bit offset, bits 24-31 = bitfield size
            if t.kflag:
                bit_size = (m_offset >> 24) & 0xFF
                bit_offset_total = m_offset & 0xFFFFFF
            else:
                bit_size = 0
                bit_offset_total = m_offset

            byte_offset = bit_offset_total // 8
            bit_offset = bit_offset_total % 8 if bit_size else 0

            fields.append(
                FieldInfo(
                    name=m_name,
                    type_name=resolver.name(m_type),
                    byte_offset=byte_offset,
                    byte_size=resolver.size(m_type),
                    bit_offset=bit_offset,
                    bit_size=bit_size,
                )
            )

        layout = StructLayout(
            name=name,
            byte_size=t.size_or_type,
            alignment=0,  # BTF doesn't store alignment
            fields=fields,
            is_union=(t.kind == BTF_KIND_UNION),
        )

        if name not in structs:
            structs[name] = layout

    return structs


def _parse_enum32_members(t: BtfType, str_data: bytes) -> dict[str, int]:
    """Parse 32-bit BTF enum enumerators (8 bytes each)."""
    members: dict[str, int] = {}
    # kflag=1 → signed enumerators, kflag=0 → unsigned
    fmt = "<Ii" if t.kflag else "<II"
    for i in range(t.vlen):
        off = i * 8
        if off + 8 > len(t.extra):
            break
        e_name_off, e_val = struct.unpack_from(fmt, t.extra, off)
        e_name = _read_string(str_data, e_name_off)
        if e_name:
            members[e_name] = e_val
    return members


def _parse_enum64_members(t: BtfType, str_data: bytes) -> dict[str, int]:
    """Parse 64-bit BTF enum enumerators (12 bytes each)."""
    members: dict[str, int] = {}
    for i in range(t.vlen):
        off = i * 12
        if off + 12 > len(t.extra):
            break
        e_name_off, e_val_lo, e_val_hi = struct.unpack_from("<III", t.extra, off)
        e_name = _read_string(str_data, e_name_off)
        e_val = e_val_lo | (e_val_hi << 32)
        # kflag=1 → signed: sign-extend 64-bit value
        if t.kflag and e_val >= (1 << 63):
            e_val -= 1 << 64
        if e_name:
            members[e_name] = e_val
    return members


def _extract_enums(
    types: list[BtfType],
    str_data: bytes,
) -> dict[str, EnumInfo]:
    """Extract enum types from BTF."""
    enums: dict[str, EnumInfo] = {}

    for t in types:
        if t.kind == BTF_KIND_ENUM:
            members = _parse_enum32_members(t, str_data)
        elif t.kind == BTF_KIND_ENUM64:
            members = _parse_enum64_members(t, str_data)
        else:
            continue

        name = _read_string(str_data, t.name_off)
        if name and name not in enums:
            enums[name] = EnumInfo(
                name=name,
                underlying_byte_size=t.size_or_type,
                members=members,
            )

    return enums


def _extract_func_protos(
    types: list[BtfType],
    resolver: _TypeResolver,
    str_data: bytes,
) -> dict[str, FuncProto]:
    """Extract function prototypes from BTF FUNC + FUNC_PROTO pairs."""
    # Build proto_id → FuncProto mapping first
    proto_map: dict[int, BtfType] = {}
    for t in types:
        if t.kind == BTF_KIND_FUNC_PROTO:
            proto_map[t.type_id] = t

    funcs: dict[str, FuncProto] = {}
    for t in types:
        if t.kind != BTF_KIND_FUNC:
            continue
        name = _read_string(str_data, t.name_off)
        if not name:
            continue

        proto = proto_map.get(t.size_or_type)
        if proto is None:
            continue

        ret_type = resolver.name(proto.size_or_type)
        params: list[tuple[str, str]] = []
        for i in range(proto.vlen):
            off = i * 8
            if off + 8 > len(proto.extra):
                break
            p_name_off, p_type = struct.unpack_from("<II", proto.extra, off)
            p_name = _read_string(str_data, p_name_off)
            p_type_name = resolver.name(p_type)
            params.append((p_name, p_type_name))

        if name not in funcs:
            funcs[name] = FuncProto(
                name=name,
                return_type=ret_type,
                params=params,
                # BTF_KIND_FUNC reuses vlen for linkage (0 static, 1 global,
                # 2 extern); the consumer decides whether a 0 is trustworthy.
                linkage=t.vlen,
            )

    return funcs


def _extract_typedefs(
    types: list[BtfType],
    resolver: _TypeResolver,
    str_data: bytes,
) -> dict[str, str]:
    """Extract typedef mappings."""
    typedefs: dict[str, str] = {}
    for t in types:
        if t.kind != BTF_KIND_TYPEDEF:
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


def parse_btf_metadata(elf_path: Path) -> BtfMetadata:
    """Parse BTF section from an ELF file and return BtfMetadata.

    Returns ``BtfMetadata()`` on any error.  Never raises.
    """
    empty = BtfMetadata()

    try:
        raw = _read_btf_section(elf_path)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "parse_btf_metadata: failed to read .BTF from %s: %s", elf_path, exc
        )
        return empty

    if raw is None:
        log.debug("parse_btf_metadata: no .BTF section in %s", elf_path)
        return empty

    btf_data, pointer_size = raw
    return parse_btf_from_bytes(btf_data, pointer_size=pointer_size)


def parse_btf_from_bytes(data: bytes, pointer_size: int = 8) -> BtfMetadata:
    """Parse BTF from raw bytes (useful for testing without ELF wrapper).

    Args:
        data: Raw BTF section bytes.
        pointer_size: Pointer size in bytes (4 for 32-bit, 8 for 64-bit).
            Defaults to 8 (typical for kernel BTF).

    Returns ``BtfMetadata()`` on any error.  Never raises.
    """
    empty = BtfMetadata()

    try:
        header = _parse_header(data)
    except (ValueError, struct.error) as exc:
        log.warning("parse_btf_from_bytes: bad header: %s", exc)
        return empty

    hdr_len = header.hdr_len
    type_start = hdr_len + header.type_off
    type_end = type_start + header.type_len
    str_start = hdr_len + header.str_off
    str_end = str_start + header.str_len

    if type_end > len(data) or str_end > len(data):
        log.warning("parse_btf_from_bytes: section bounds exceed data size")
        return empty

    type_data = data[type_start:type_end]
    str_data = data[str_start:str_end]

    type_truncated: list[bool] = []
    try:
        types = _parse_types(type_data, type_truncated)
    except (struct.error, ValueError) as exc:
        log.warning("parse_btf_from_bytes: type parsing failed: %s", exc)
        return empty

    resolver = _TypeResolver(types, str_data, pointer_size=pointer_size)

    meta = BtfMetadata(has_btf=True, type_count=len(types) - 1)
    if type_truncated:
        # P2 review, fresh evidence: a truncated final type entry doesn't
        # raise -- it logs and returns every type parsed before the cut --
        # so the receipt must not silently claim "parsed" for a channel
        # whose type table was read incomplete.
        meta.extraction_partial = True

    try:
        meta.structs = _extract_structs(types, resolver, str_data)
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_btf_from_bytes: struct extraction failed: %s", exc)
        meta.extraction_partial = True

    try:
        meta.enums = _extract_enums(types, str_data)
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_btf_from_bytes: enum extraction failed: %s", exc)
        meta.extraction_partial = True

    try:
        meta.func_protos = _extract_func_protos(types, resolver, str_data)
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_btf_from_bytes: func_proto extraction failed: %s", exc)
        meta.extraction_partial = True

    try:
        meta.typedefs = _extract_typedefs(types, resolver, str_data)
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_btf_from_bytes: typedef extraction failed: %s", exc)
        meta.extraction_partial = True

    return meta
