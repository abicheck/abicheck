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

"""Strict CodeView readers for TPI records and ``LF_FIELDLIST`` sub-records.

Every reader here raises ``ValueError`` when the bytes end before the
record does -- a numeric leaf cut short, a name with no terminating NUL, a
sub-record header past the end, a trailing byte that is neither padding
nor a whole sub-leaf. ``pdb_parser.TypeDatabase.parse_all`` records that as
a parse failure and ``pdb_metadata.parse_pdb_debug_info`` fails the PDB
(ADR-062 D15), so a truncated record can never stand in as a struct with
fewer fields or an enum with fewer members.

The one tolerated stop is a sub-leaf this parser does not know: its length
is unknowable, so the walk ends there; that is an unsupported kind, not a
truncation.
"""

from __future__ import annotations

import logging
import struct
from typing import NamedTuple

log = logging.getLogger(__name__)

__all__ = [
    "FieldEntry",
    "read_cstring",
    "read_numeric_leaf",
    "walk_fieldlist",
]

# cvinfo.h sub-leaf kinds a field list may hold.
_LF_BCLASS = 0x1400
_LF_VBCLASS = 0x1401
_LF_IVBCLASS = 0x1402
_LF_VFUNCTAB = 0x1409
_LF_ENUMERATE = 0x1502
_LF_MEMBER = 0x150D
_LF_STMEMBER = 0x150E
_LF_METHOD = 0x150F
_LF_NESTTYPE = 0x1510
_LF_ONEMETHOD = 0x1511
_LF_INDEX = 0x1602

# Numeric leaf tag -> struct format of the value that follows it.
_NUMERIC_FORMATS = {
    0x8000: "<b",  # LF_CHAR
    0x8001: "<h",  # LF_SHORT
    0x8002: "<H",  # LF_USHORT
    0x8003: "<i",  # LF_LONG
    0x8004: "<I",  # LF_ULONG
    0x8009: "<q",  # LF_QUADWORD
    0x800A: "<Q",  # LF_UQUADWORD
}


def _need(d: bytes, pos: int, size: int, what: str) -> None:
    if pos + size > len(d):
        raise ValueError(
            f"truncated {what} at {pos} ({len(d)} bytes, need {pos + size})"
        )


def read_numeric_leaf(d: bytes, pos: int) -> tuple[int, int]:
    """``(value, new_pos)`` for the CodeView numeric leaf at *pos*."""
    _need(d, pos, 2, "numeric leaf")
    (val,) = struct.unpack_from("<H", d, pos)
    if val < 0x8000:
        return val, pos + 2
    fmt = _NUMERIC_FORMATS.get(val)
    if fmt is None:
        raise ValueError(f"unsupported numeric leaf 0x{val:04x} at {pos}")
    _need(d, pos + 2, struct.calcsize(fmt), "numeric leaf value")
    (v,) = struct.unpack_from(fmt, d, pos + 2)
    return v, pos + 2 + struct.calcsize(fmt)


def read_cstring(d: bytes, pos: int) -> tuple[str, int]:
    """``(name, new_pos)`` for the NUL-terminated name at *pos*."""
    end = d.find(b"\x00", pos)
    if end < 0 or pos > len(d):
        raise ValueError(f"unterminated name at {pos}")
    return d[pos:end].decode("utf-8", errors="replace"), end + 1


class FieldEntry(NamedTuple):
    """One field-list sub-record the type database keeps.

    *kind* is ``member`` (attr, type_ti, offset, name), ``enumerate``
    (value, name), ``onemethod`` (attr, type_ti, name) or ``index``
    (continuation type index).
    """

    kind: str
    attr: int = 0
    type_ti: int = 0
    value: int = 0
    name: str = ""


def _sub_record(sub_leaf: int, d: bytes, pos: int) -> tuple[FieldEntry | None, int]:
    if sub_leaf == _LF_MEMBER:
        _need(d, pos, 6, "LF_MEMBER")
        attr, ti = struct.unpack_from("<HI", d, pos)
        offset, pos = read_numeric_leaf(d, pos + 6)
        name, pos = read_cstring(d, pos)
        return FieldEntry("member", attr, ti, offset, name), pos
    if sub_leaf == _LF_ENUMERATE:
        _need(d, pos, 2, "LF_ENUMERATE")
        value, pos = read_numeric_leaf(d, pos + 2)
        name, pos = read_cstring(d, pos)
        return FieldEntry("enumerate", value=value, name=name), pos
    if sub_leaf == _LF_INDEX:
        _need(d, pos, 6, "LF_INDEX")
        (cont_ti,) = struct.unpack_from("<I", d, pos + 2)
        return FieldEntry("index", type_ti=cont_ti), pos + 6
    if sub_leaf == _LF_ONEMETHOD:
        _need(d, pos, 6, "LF_ONEMETHOD")
        attr, ti = struct.unpack_from("<HI", d, pos)
        pos += 6
        if (attr >> 2) & 0x07 in (4, 6):  # intro/pure intro virtual: vbaseoff
            _need(d, pos, 4, "LF_ONEMETHOD vbaseoff")
            pos += 4
        name, pos = read_cstring(d, pos)
        return FieldEntry("onemethod", attr, ti, name=name), pos
    if sub_leaf in (_LF_STMEMBER, _LF_NESTTYPE, _LF_METHOD):
        _need(d, pos, 6, f"sub-leaf 0x{sub_leaf:04x}")
        _name, pos = read_cstring(d, pos + 6)
        return None, pos
    if sub_leaf == _LF_VFUNCTAB:
        _need(d, pos, 6, "LF_VFUNCTAB")
        return None, pos + 6
    if sub_leaf == _LF_BCLASS:
        _need(d, pos, 6, "LF_BCLASS")
        _off, pos = read_numeric_leaf(d, pos + 6)
        return None, pos
    # LF_VBCLASS / LF_IVBCLASS
    _need(d, pos, 10, f"sub-leaf 0x{sub_leaf:04x}")
    _vbpoff, pos = read_numeric_leaf(d, pos + 10)
    _vbtableoff, pos = read_numeric_leaf(d, pos)
    return None, pos


_KNOWN = frozenset(
    {
        _LF_MEMBER,
        _LF_ENUMERATE,
        _LF_INDEX,
        _LF_ONEMETHOD,
        _LF_STMEMBER,
        _LF_NESTTYPE,
        _LF_METHOD,
        _LF_VFUNCTAB,
        _LF_BCLASS,
        _LF_VBCLASS,
        _LF_IVBCLASS,
    }
)


def walk_fieldlist(d: bytes) -> list[FieldEntry]:
    """The entries of one ``LF_FIELDLIST`` payload, strictly.

    Raises ``ValueError`` when a supported sub-record is cut short or a
    trailing byte is neither ``LF_PADn`` padding nor a whole sub-leaf.
    """
    entries: list[FieldEntry] = []
    pos = 0
    while pos < len(d):
        if d[pos] >= 0xF0:  # LF_PADn: lower nibble = pad length
            pos += (d[pos] & 0x0F) or 1
            continue
        _need(d, pos, 2, "field-list sub-leaf")
        (sub_leaf,) = struct.unpack_from("<H", d, pos)
        if sub_leaf not in _KNOWN:
            log.debug("Unknown fieldlist sub-leaf 0x%04x at pos %d", sub_leaf, pos)
            break
        entry, end = _sub_record(sub_leaf, d, pos + 2)
        if entry is not None:
            entries.append(entry)
        pos = (end + 3) & ~3  # 4-byte alignment within a field list
    return entries
