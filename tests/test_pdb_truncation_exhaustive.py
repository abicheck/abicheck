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

"""Any truncated supported TPI record fails the PDB (ADR-062 D15).

Exhaustive over a small domain: one real PDB carrying every supported
record kind and every supported ``LF_FIELDLIST`` sub-record kind; each
record is cut at every byte offset inside it and the PDB re-parsed through
the real parser. Oracle: a cut record is a parse failure (``PdbParseError``)
-- except a field-list cut that lands exactly on a sub-record boundary of a
*struct's* list, whose prefix is itself a well-formed shorter list (CodeView
gives a struct no member count to check it against; an enum's list is
checked against its count, so every enum-list cut fails). The test computes
those boundaries from its own construction, not from the parser.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from abicheck.pdb_metadata import PdbParseError, parse_pdb_debug_info
from abicheck.pdb_parser import (
    LF_ARGLIST,
    LF_ARRAY,
    LF_BCLASS,
    LF_BITFIELD,
    LF_CLASS,
    LF_ENUM,
    LF_FIELDLIST,
    LF_INDEX,
    LF_IVBCLASS,
    LF_METHOD,
    LF_MFUNCTION,
    LF_MODIFIER,
    LF_NESTTYPE,
    LF_ONEMETHOD,
    LF_POINTER,
    LF_PROCEDURE,
    LF_STMEMBER,
    LF_STRUCTURE,
    LF_UNION,
    LF_VBCLASS,
    LF_VFUNCTAB,
)
from tests.test_pdb_parser import (
    _build_minimal_pdb,
    _cv_cstring,
    _cv_numeric,
    _make_lf_array,
    _make_lf_bitfield,
    _make_lf_enum,
    _make_lf_enumerate,
    _make_lf_member,
    _make_lf_mfunction,
    _make_lf_modifier,
    _make_lf_pointer,
    _make_lf_procedure,
    _make_lf_structure,
    _make_lf_union,
)

_INT = 0x74

#: Every supported field-list sub-record, built independently of the parser.
_SUBRECORDS = {
    "LF_BCLASS": struct.pack("<HHI", LF_BCLASS, 3, 0x1010) + _cv_numeric(0),
    "LF_VBCLASS": struct.pack("<HHII", LF_VBCLASS, 3, 0x1010, 0x1011)
    + _cv_numeric(0)
    + _cv_numeric(1),
    "LF_IVBCLASS": struct.pack("<HHII", LF_IVBCLASS, 3, 0x1010, 0x1011)
    + _cv_numeric(0)
    + _cv_numeric(1),
    "LF_VFUNCTAB": struct.pack("<HHI", LF_VFUNCTAB, 0, 0x1012),
    "LF_MEMBER": _make_lf_member(3, _INT, 0x9000, "big_offset"),  # LF_USHORT leaf
    "LF_STMEMBER": struct.pack("<HHI", LF_STMEMBER, 3, _INT) + _cv_cstring("s"),
    "LF_METHOD": struct.pack("<HHI", LF_METHOD, 2, 0x1013) + _cv_cstring("ov"),
    "LF_ONEMETHOD": struct.pack("<HHI", LF_ONEMETHOD, 3, 0x1006) + _cv_cstring("m"),
    "LF_ONEMETHOD_virtual": struct.pack("<HHII", LF_ONEMETHOD, 3 | (4 << 2), 0x1006, 0)
    + _cv_cstring("vm"),
    "LF_NESTTYPE": struct.pack("<HHI", LF_NESTTYPE, 0, _INT) + _cv_cstring("n"),
    "LF_INDEX": struct.pack("<HHI", LF_INDEX, 0, 0x1001),
}


def _aligned(pieces: list[bytes]) -> tuple[bytes, set[int]]:
    """A field list from *pieces* with LF_PADn alignment, plus every cut
    offset that leaves a well-formed shorter list (a piece's end, through
    its padding)."""
    data, boundaries = b"", {0}
    for rec in pieces:
        data += rec
        pad = (4 - len(data) % 4) % 4
        boundaries.update(range(len(data), len(data) + pad + 1))
        data += bytes([0xF0 + pad] * pad)
    return data, boundaries


#: ti 0x1000.. in order. Each entry: (name, leaf, payload, boundary cuts).
_STRUCT_FL, _STRUCT_CUTS = _aligned(list(_SUBRECORDS.values()))
_CONT_FL, _CONT_CUTS = _aligned([_make_lf_member(3, _INT, 4, "tail")])
_ENUM_FL, _ENUM_BOUNDS = _aligned(
    [_make_lf_enumerate(3, 1, "A"), _make_lf_enumerate(3, 0x9000, "B")]
)
_RECORDS: list[tuple[str, int, bytes, set[int]]] = [
    ("LF_FIELDLIST/struct", LF_FIELDLIST, _STRUCT_FL, _STRUCT_CUTS),  # 0x1000
    ("LF_FIELDLIST/continuation", LF_FIELDLIST, _CONT_FL, _CONT_CUTS),  # 0x1001
    (
        "LF_STRUCTURE",
        LF_STRUCTURE,
        _make_lf_structure(12, 0, 0x1000, 0x9000, "S"),
        set(),
    ),
    # Only a cut inside the final padding loses nothing: the count catches
    # every earlier boundary.
    (
        "LF_FIELDLIST/enum",
        LF_FIELDLIST,
        _ENUM_FL,
        {k for k in _ENUM_BOUNDS if k >= len(_ENUM_FL.rstrip(b"\xf1\xf2\xf3"))},
    ),  # 0x1003
    ("LF_ENUM", LF_ENUM, _make_lf_enum(2, 0, _INT, 0x1003, "E"), set()),
    ("LF_ARGLIST", LF_ARGLIST, struct.pack("<III", 2, _INT, _INT), set()),  # 0x1005
    (
        "LF_MFUNCTION",
        LF_MFUNCTION,
        _make_lf_mfunction(_INT, 0x1002, 0x1008, 0, 2, 0x1005),
        set(),
    ),
    ("LF_PROCEDURE", LF_PROCEDURE, _make_lf_procedure(_INT, 0, 2, 0x1005), set()),
    ("LF_POINTER", LF_POINTER, _make_lf_pointer(0x1002), set()),
    ("LF_ARRAY", LF_ARRAY, _make_lf_array(_INT, 0x23, 16, "arr"), set()),
    ("LF_MODIFIER", LF_MODIFIER, _make_lf_modifier(_INT, is_const=True), set()),
    ("LF_BITFIELD", LF_BITFIELD, _make_lf_bitfield(_INT, 3, 1), set()),
    ("LF_UNION", LF_UNION, _make_lf_union(1, 0, 0x1001, 4, "U"), set()),
    ("LF_CLASS", LF_CLASS, _make_lf_structure(1, 0, 0x1001, 4, "C"), set()),
]


def _pdb(tmp_path: Path, cut: tuple[int, int] | None) -> Path:
    records = [
        (leaf, payload[: cut[1]] if cut is not None and i == cut[0] else payload)
        for i, (_n, leaf, payload, _b) in enumerate(_RECORDS)
    ]
    path = tmp_path / "t.pdb"
    path.write_bytes(_build_minimal_pdb(tpi_records=records))
    return path


def test_the_untruncated_pdb_parses_every_kind(tmp_path: Path) -> None:
    meta, _adv = parse_pdb_debug_info(_pdb(tmp_path, None))
    assert set(meta.structs) >= {"S", "U", "C"}
    assert meta.enums["E"].members == {"A": 1, "B": 0x9000}
    s_fields = {f.name for f in meta.structs["S"].fields}
    assert {"big_offset", "tail"} <= s_fields  # LF_INDEX continuation followed


def _cuts() -> list[tuple[str, int, int]]:
    return [
        (name, i, k)
        for i, (name, _leaf, payload, boundaries) in enumerate(_RECORDS)
        for k in range(len(payload))
        if k not in boundaries
    ]


@pytest.mark.parametrize(("name", "index", "cut"), _cuts(), ids=lambda v: str(v))
def test_every_truncated_supported_record_fails_the_pdb(
    tmp_path: Path, name: str, index: int, cut: int
) -> None:
    with pytest.raises(PdbParseError):
        parse_pdb_debug_info(_pdb(tmp_path, (index, cut)))


def test_the_domain_reaches_every_subrecord_kind() -> None:
    """Not vacuous: each sub-record contributes cuts that must fail."""
    cuts = {k for name, _i, k in _cuts() if name == "LF_FIELDLIST/struct"}
    data = b""
    for key, rec in _SUBRECORDS.items():
        start = len(data) + (4 - len(data) % 4) % 4 if data else 0
        assert any(start < k < start + len(rec) for k in cuts), key
        data = data + bytes((4 - len(data) % 4) % 4) + rec if data else rec
