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

from .extract.ctf_type_resolver import (
    CTF_F_COMPRESS as CTF_F_COMPRESS,
    CTF_INT_BOOL as CTF_INT_BOOL,
    CTF_INT_CHAR as CTF_INT_CHAR,
    CTF_INT_SIGNED as CTF_INT_SIGNED,
    CTF_K_ARRAY as CTF_K_ARRAY,
    CTF_K_CONST as CTF_K_CONST,
    CTF_K_ENUM as CTF_K_ENUM,
    CTF_K_FLOAT as CTF_K_FLOAT,
    CTF_K_FORWARD as CTF_K_FORWARD,
    CTF_K_FUNCTION as CTF_K_FUNCTION,
    CTF_K_INTEGER as CTF_K_INTEGER,
    CTF_K_POINTER as CTF_K_POINTER,
    CTF_K_RESTRICT as CTF_K_RESTRICT,
    CTF_K_STRUCT as CTF_K_STRUCT,
    CTF_K_TYPEDEF as CTF_K_TYPEDEF,
    CTF_K_UNION as CTF_K_UNION,
    CTF_K_UNKNOWN as CTF_K_UNKNOWN,
    CTF_K_VOLATILE as CTF_K_VOLATILE,
    CTF_MAGIC as CTF_MAGIC,
    CTF_VERSION_2 as CTF_VERSION_2,
    CTF_VERSION_3 as CTF_VERSION_3,
    CtfType as CtfType,
    _read_string as _read_string,
    _TypeResolver as _TypeResolver,
)
from .model.dwarf_facts import DwarfMetadata, EnumInfo, FieldInfo, StructLayout
from .type_metadata import FuncProto

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CTF constants (from sys/ctf.h)
#
# The kind/version/int-encoding/header-flag constants and the raw CtfType
# record, plus _read_string and _TypeResolver, live in
# extract/ctf_type_resolver.py -- its canonical ADR-061 owner package, since
# this is a read-a-debug-fact responsibility -- to keep this module under
# the architecture debt-no-growth ceiling. Explicitly re-exported above (the
# `X as X` spelling, same convention checker_policy.py uses for ChangeKind)
# since existing callers -- including this module's own tests -- import
# them from here.
# ---------------------------------------------------------------------------

# Cap on zlib-decompressed CTF payload size, to prevent a zip-bomb DoS.
# Module-level rather than a function-local literal so the guard's threshold is
# a named, patchable knob: the zip-bomb regression test can lower it and cross
# it with a few MiB instead of allocating and compressing a real 257 MiB buffer.
_MAX_DECOMPRESS = 256 * 1024 * 1024

# Size thresholds for large vs small type encoding
_CTF_V2_LSTRUCT_THRESH = 0x1FFF  # vlen threshold for v2 "large" members
_CTF_V3_LSTRUCT_THRESH = 0x1FFF

# P2 review, fresh evidence (Codex, mirrors the identical BTF fix): every
# kind _extra_data_size() has a real branch for (CTF_K_UNKNOWN never appears
# as a real record -- type_id 0 is the implicit sentinel this module
# synthesizes itself). A kind outside this set is one _extra_data_size()
# cannot size correctly at all -- its fallback `return 0` would misalign
# every subsequent record's offset in the type section, not just this one
# type's own facts.
_KNOWN_CTF_KINDS = frozenset(
    {
        CTF_K_INTEGER,
        CTF_K_FLOAT,
        CTF_K_POINTER,
        CTF_K_ARRAY,
        CTF_K_FUNCTION,
        CTF_K_STRUCT,
        CTF_K_UNION,
        CTF_K_ENUM,
        CTF_K_FORWARD,
        CTF_K_TYPEDEF,
        CTF_K_VOLATILE,
        CTF_K_CONST,
        CTF_K_RESTRICT,
    }
)

# Header sizes
_CTF_PREAMBLE_SIZE = 4  # magic(2) + version(1) + flags(1)
_CTF_V2_HEADER_SIZE = 36
_CTF_V3_HEADER_SIZE = 36


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


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
    extraction_partial: bool = False  # any stage below raised+caught (P2 review)

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
        parsed_state = "partial" if self.extraction_partial else "parsed"
        state = parsed_state if self.has_ctf else "not_available"
        return DwarfMetadata(
            structs=dict(self.structs),
            enums=dict(self.enums),
            has_dwarf=self.has_ctf,
            evidence_source="ctf",
            evidence_state=state,
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


def _read_ctf_section(elf_path: Path) -> tuple[bytes, int] | None:
    """Read raw .ctf section data from an ELF file; return (data, pointer_size).

    P2 review, fresh evidence (Codex): the container's ELF class was
    previously discarded here, so every CTF-sourced pointer member read a
    hardcoded 64-bit size regardless of the binary's real word size --
    identical to the bug `btf_type_resolver._TypeResolver` was already
    fixed for; mirrors that module's own `_read_btf_section`.
    """
    from elftools.elf.elffile import ELFFile

    with open(elf_path, "rb") as f:
        elf = ELFFile(f)  # type: ignore[no-untyped-call]
        section = elf.get_section_by_name(".ctf")  # type: ignore[no-untyped-call]
        if section is None:
            section = elf.get_section_by_name(".SUNW_ctf")  # type: ignore[no-untyped-call]
        if section is None:
            return None
        pointer_size = 4 if elf.elfclass == 32 else 8
        return bytes(section.data()), pointer_size


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

    (
        parent_label,
        parent_name,
        label_off,
        object_off,
        func_off,
        type_off,
        str_off,
        str_len,
    ) = struct.unpack_from("<IIIIIIII", data, 4)

    return CtfHeader(
        magic=magic,
        version=version,
        flags=flags,
        parent_label=parent_label,
        parent_name=parent_name,
        label_off=label_off,
        object_off=object_off,
        func_off=func_off,
        type_off=type_off,
        str_off=str_off,
        str_len=str_len,
    )


def _decompress_if_needed(data: bytes, header: CtfHeader) -> bytes:
    """Decompress CTF data if CTF_F_COMPRESS flag is set."""
    if not (header.flags & CTF_F_COMPRESS):
        return data
    # Data after the preamble (4 bytes) is zlib-compressed; _MAX_DECOMPRESS
    # caps the output to prevent a zip-bomb DoS.
    try:
        decompressor = zlib.decompressobj()
        decompressed = decompressor.decompress(
            data[_CTF_PREAMBLE_SIZE:], _MAX_DECOMPRESS
        )
        if decompressor.unconsumed_tail:
            limit_mib = _MAX_DECOMPRESS // (1024 * 1024)
            raise ValueError(f"CTF decompressed data exceeds {limit_mib} MiB limit")
        if not decompressor.eof:
            # P2 review, fresh evidence (Codex): zlib.decompressobj().
            # decompress() can return a complete-looking payload without
            # raising even when the input was truncated -- cutting only the
            # trailing checksum/end marker (as little as 1 byte) still
            # yields every decompressed byte, since decompression itself
            # finished before that marker is even consumed. `eof` is the
            # one signal that actually distinguishes "the stream properly
            # terminated" from "we simply ran out of input mid-stream" (the
            # sibling `unconsumed_tail` check above catches the opposite
            # shape: more compressed data than we chose to consume).
            raise ValueError("CTF compressed stream is truncated (missing end marker)")
    except zlib.error as exc:
        raise ValueError(f"CTF decompression failed: {exc}") from exc
    # Reassemble: preamble + decompressed body
    return data[:_CTF_PREAMBLE_SIZE] + decompressed


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
    type_data: bytes,
    version: int,
    truncated: list[bool] | None = None,
) -> list[CtfType]:
    """Parse all CTF type entries from the type section.

    P2 review, fresh evidence (mirrors the identical BTF fix): every early
    exit from the loop below is a ``break`` on insufficient remaining bytes,
    none of which raise -- so a truncated final entry silently returns every
    type parsed before the cut instead of signaling incompleteness.
    *truncated*, when passed a one-element list, has ``True`` appended to it
    at each such ``break`` site (a header, size field, or extra-data
    truncation), and is left empty when the loop's own ``while`` condition
    ends it after fully consuming the section -- an out-parameter, not a
    ``pos < len(type_data)`` postcondition, since a truncated header can
    happen to consume exactly the remaining bytes (e.g. a well-formed
    12-byte v3 header immediately followed by missing extra data), which
    would leave ``pos == len(type_data)`` despite the entry being cut off.
    An opt-in out-parameter rather than a return-type change, so every
    existing caller that only wants the type list is unaffected.
    """
    types: list[CtfType] = [CtfType(type_id=0, name_off=0, info=0, size_or_type=0)]

    parse_info = _parse_info_v3 if version >= CTF_VERSION_3 else _parse_info_v2

    pos = 0
    type_id = 1

    while pos < len(type_data):
        # Each type starts with: name(4) + info(4)
        # Then either size(4) for large or type(2) for small (v2)
        # v3 always uses 4-byte size_or_type
        if version >= CTF_VERSION_3:
            if pos + 12 > len(type_data):
                if truncated is not None:
                    truncated.append(True)
                break
            name_off, info, size_or_type = struct.unpack_from("<III", type_data, pos)
            pos += 12
        else:
            # CTF v2: name(4) + info(2) + size_or_type(2 or 4)
            if pos + 6 > len(type_data):
                if truncated is not None:
                    truncated.append(True)
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
                    else:
                        # P2 review, fresh evidence (mirrors the header/
                        # extra-data truncation sites this function already
                        # guards): the 16-bit large-size marker itself
                        # decoded fine, but the mandatory 4-byte real size
                        # that a "large" struct/union always carries next is
                        # missing. Falling through here would keep the raw
                        # marker value as size_or_type (never a real size)
                        # and, for a small enough vlen (e.g. 0),
                        # _extra_data_size could read as fully satisfied --
                        # silently accepting a malformed/cut-off entry as a
                        # complete parse.
                        if truncated is not None:
                            truncated.append(True)
                        break
            elif pos + 2 <= len(type_data):
                size_or_type = struct.unpack_from("<H", type_data, pos)[0]
                pos += 2
            else:
                if truncated is not None:
                    truncated.append(True)
                break
            # Re-encode info for uniform handling (v3 layout)
            info = (kind << 24) | (int(isroot) << 31) | vlen
            # kind, vlen already decoded above — no re-parse needed

        if version >= CTF_VERSION_3:
            # For v3, kind/vlen not yet decoded — decode now
            kind, vlen, _isroot = _parse_info_v3(info)

        if kind not in _KNOWN_CTF_KINDS:
            # An unsupported kind's real extra-data size is unknowable to
            # this parser -- continuing would misread its own payload as the
            # start of the next type record, corrupting every type_id after
            # it. Same "stop and report truncated" shape as an
            # actually-truncated buffer: this type (and everything after it)
            # is dropped, not silently misparsed.
            log.warning("CTF type %d has unsupported kind %d", type_id, kind)
            if truncated is not None:
                truncated.append(True)
            break

        # Read kind-specific extra data
        extra_size = _extra_data_size(kind, vlen, version, size_or_type)
        if pos + extra_size > len(type_data):
            log.warning("CTF type %d (kind=%d) truncated", type_id, kind)
            if truncated is not None:
                truncated.append(True)
            break

        extra = type_data[pos : pos + extra_size]
        pos += extra_size

        types.append(
            CtfType(
                type_id=type_id,
                name_off=name_off,
                info=info,
                size_or_type=size_or_type,
                extra=extra,
            )
        )
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
# High-level extraction
# ---------------------------------------------------------------------------


def _extract_structs(
    types: list[CtfType],
    resolver: _TypeResolver,
    str_data: bytes,
    version: int,
    *,
    invalid_strings: list[bool] | None = None,
) -> dict[str, StructLayout]:
    """Extract struct/union layouts from CTF types.

    P2 review: *invalid_strings* records (append-only) every ``name_off``/
    ``m_name_off`` that ``_read_string`` reports out of ``str_data``'s
    bounds -- a corrupt/malformed CTF blob, not a legitimate anonymous
    (empty) name. Left ``None`` for a caller that doesn't track it.
    """
    structs: dict[str, StructLayout] = {}

    for t in types:
        if t.kind not in (CTF_K_STRUCT, CTF_K_UNION):
            continue

        name, name_valid = _read_string(str_data, t.name_off)
        if invalid_strings is not None and not name_valid:
            invalid_strings.append(True)
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

            m_name, m_name_valid = _read_string(str_data, m_name_off)
            if invalid_strings is not None and not m_name_valid:
                invalid_strings.append(True)
            byte_offset = m_offset // 8
            bit_offset = m_offset % 8

            fields.append(
                FieldInfo(
                    name=m_name,
                    type_name=resolver.name(m_type),
                    byte_offset=byte_offset,
                    byte_size=resolver.size(m_type),
                    bit_offset=bit_offset if bit_offset else 0,
                    bit_size=0,  # CTF doesn't encode bitfield size directly
                )
            )

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
    types: list[CtfType],
    str_data: bytes,
    *,
    invalid_strings: list[bool] | None = None,
) -> dict[str, EnumInfo]:
    """Extract enum types from CTF."""
    enums: dict[str, EnumInfo] = {}

    for t in types:
        if t.kind != CTF_K_ENUM:
            continue

        name, name_valid = _read_string(str_data, t.name_off)
        if invalid_strings is not None and not name_valid:
            invalid_strings.append(True)
        if not name:
            continue

        members: dict[str, int] = {}
        for i in range(t.vlen):
            off = i * 8
            if off + 8 > len(t.extra):
                break
            e_name_off, e_val = struct.unpack_from("<Ii", t.extra, off)
            e_name, e_name_valid = _read_string(str_data, e_name_off)
            if invalid_strings is not None and not e_name_valid:
                invalid_strings.append(True)
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
    types: list[CtfType],
    resolver: _TypeResolver,
    str_data: bytes,
    *,
    invalid_strings: list[bool] | None = None,
) -> dict[str, str]:
    """Extract typedef mappings."""
    typedefs: dict[str, str] = {}
    for t in types:
        if t.kind != CTF_K_TYPEDEF:
            continue
        name, name_valid = _read_string(str_data, t.name_off)
        if invalid_strings is not None and not name_valid:
            invalid_strings.append(True)
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
        log.warning(
            "parse_ctf_metadata: failed to read .ctf from %s: %s", elf_path, exc
        )
        return empty

    if raw is None:
        log.debug("parse_ctf_metadata: no .ctf section in %s", elf_path)
        return empty

    ctf_data, pointer_size = raw
    return parse_ctf_from_bytes(ctf_data, pointer_size=pointer_size)


def parse_ctf_from_bytes(data: bytes, pointer_size: int = 8) -> CtfMetadata:
    """Parse CTF from raw bytes (useful for testing without ELF wrapper).

    Args:
        data: Raw CTF section bytes.
        pointer_size: Pointer size in bytes (4 for 32-bit, 8 for 64-bit).
            Defaults to 64-bit, matching ``parse_btf_from_bytes``'s own
            default -- a caller with no ELF container to read the class
            from (e.g. a bare raw-blob input) genuinely cannot know it.

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
    type_end = (
        hdr_size + header.str_off
    )  # type section ends where string section begins
    str_start = hdr_size + header.str_off
    str_end = str_start + header.str_len

    if type_end > len(data) or str_end > len(data):
        log.warning("parse_ctf_from_bytes: section bounds exceed data size")
        return empty
    if type_start > type_end:
        # P2 review, fresh evidence (Codex): a header with type_off > str_off
        # (the type section reversed against the string section it's
        # supposed to precede) previously passed the length check above
        # unnoticed -- data[type_start:type_end] is a plain Python slice, so
        # start > end silently yields b"" rather than raising, discarding
        # every type record with no truncation signal at all. Reject the
        # malformed ordering outright, the same way a too-small header
        # already is.
        log.warning(
            "parse_ctf_from_bytes: type section start %d exceeds its own end %d "
            "(type_off > str_off)",
            type_start,
            type_end,
        )
        return empty

    type_data = data[type_start:type_end]
    str_data = data[str_start:str_end]

    type_truncated: list[bool] = []
    try:
        types = _parse_types(type_data, header.version, type_truncated)
    except (struct.error, ValueError) as exc:
        log.warning("parse_ctf_from_bytes: type parsing failed: %s", exc)
        return empty

    # P2 review, fresh evidence: an out-of-bounds string offset (a
    # corrupt/malformed CTF blob) doesn't raise either -- read_null_
    # terminated_string() silently falls back to "", indistinguishable from
    # a legitimate anonymous name, so this shared accumulator is what lets
    # every extractor below report it into extraction_partial. Also handed
    # to the resolver itself (P2 review, round 2): a type reached only
    # through name()/size() resolution reads its own name_off via the
    # resolver's private _str_at(), which no direct extractor's own
    # accumulator observes.
    invalid_strings: list[bool] = []
    # P2 review, fresh evidence (Codex): CTF sibling of the identical BTF
    # fix -- offset 0 in the CTF string section is reserved for the empty
    # string, the sentinel every anonymous (name_off=0) reference relies
    # on. read_null_terminated_string() only flags an out-of-bounds offset
    # or a missing terminator, so a string section that never actually
    # stored that sentinel byte reads whatever bytes sit at offset 0 as a
    # plausible, valid-looking name instead of empty -- fabricating or
    # renaming a struct/enum/typedef with no completeness signal. Flagged
    # once for the whole string section, mirroring btf_metadata.py's own
    # equivalent check.
    if not str_data or str_data[0:1] != b"\x00":
        invalid_strings.append(True)
    resolver = _TypeResolver(
        types,
        str_data,
        header.version,
        pointer_size=pointer_size,
        invalid_strings=invalid_strings,
    )

    meta = CtfMetadata(has_ctf=True, type_count=len(types) - 1)
    if type_truncated:
        # P2 review, fresh evidence: a truncated final type entry doesn't
        # raise -- it logs and returns every type parsed before the cut --
        # so the receipt must not silently claim "parsed" for a channel
        # whose type table was read incomplete.
        meta.extraction_partial = True

    try:
        meta.structs = _extract_structs(
            types, resolver, str_data, header.version, invalid_strings=invalid_strings
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_ctf_from_bytes: struct extraction failed: %s", exc)
        meta.extraction_partial = True

    try:
        meta.enums = _extract_enums(types, str_data, invalid_strings=invalid_strings)
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_ctf_from_bytes: enum extraction failed: %s", exc)
        meta.extraction_partial = True

    try:
        meta.typedefs = _extract_typedefs(
            types, resolver, str_data, invalid_strings=invalid_strings
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_ctf_from_bytes: typedef extraction failed: %s", exc)
        meta.extraction_partial = True

    if invalid_strings:
        meta.extraction_partial = True

    return meta
