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

"""Header-tier ``ENUM_UNDERLYING_SIZE_CHANGED``.

Bug class ``evidence.header_observable_fact_compared_only_from_debug_info``:
the enum underlying size was compared only by the DWARF layout pass, so a
headers-plus-binary compare without debug info read a widened by-value enum
as NO_CHANGE although the header model carried both spellings.

The oracle is :mod:`ctypes` (the host C ABI's own sizes), never the table
under test, over every pair of target-independent integer spellings.
"""

from __future__ import annotations

import ctypes
import itertools

import pytest

from abicheck.checker import compare
from abicheck.compare.enum_underlying import underlying_byte_size
from abicheck.model.declarations import Function, Param
from abicheck.model.entities import EnumMember, EnumType
from abicheck.model.snapshot import AbiSnapshot

#: spelling -> the host C ABI's size for it.
_ORACLE: dict[str, int] = {
    "unsigned char": ctypes.sizeof(ctypes.c_ubyte),
    "signed char": ctypes.sizeof(ctypes.c_byte),
    "uint8_t": ctypes.sizeof(ctypes.c_uint8),
    "short": ctypes.sizeof(ctypes.c_short),
    "unsigned short": ctypes.sizeof(ctypes.c_ushort),
    "int16_t": ctypes.sizeof(ctypes.c_int16),
    "int": ctypes.sizeof(ctypes.c_int),
    "unsigned int": ctypes.sizeof(ctypes.c_uint),
    "std::uint32_t": ctypes.sizeof(ctypes.c_uint32),
    "long long": ctypes.sizeof(ctypes.c_longlong),
    "unsigned long long": ctypes.sizeof(ctypes.c_ulonglong),
    "int64_t": ctypes.sizeof(ctypes.c_int64),
}

#: Spellings whose size depends on the data model, or that are not integer
#: types at all: never compared, so they can never manufacture a break.
_UNKNOWN = ("long", "unsigned long", "wchar_t", "?", "", "MyTypedef")


def _snap(version: str, underlying: str) -> AbiSnapshot:
    enum = EnumType(name="E", members=[EnumMember("A", 0)], underlying_type=underlying)
    func = Function(
        name="f", mangled="f", return_type="void", params=[Param(name="e", type="E")]
    )
    return AbiSnapshot(
        library="libe.so",
        version=version,
        from_headers=True,
        functions=[func],
        enums=[enum],
    )


def _underlying_kinds(old: str, new: str) -> list[str]:
    result = compare(_snap("1", old), _snap("2", new))
    return [
        c.kind.value
        for c in result.changes
        if c.kind.value == "enum_underlying_size_changed"
    ]


@pytest.mark.parametrize("spelling", sorted(_ORACLE))
def test_size_table_matches_the_host_c_abi(spelling: str) -> None:
    assert underlying_byte_size(spelling) == _ORACLE[spelling]


@pytest.mark.parametrize(
    ("old", "new"), list(itertools.permutations(sorted(_ORACLE), 2))
)
def test_reported_exactly_when_the_size_differs(old: str, new: str) -> None:
    expected = 1 if _ORACLE[old] != _ORACLE[new] else 0
    assert len(_underlying_kinds(old, new)) == expected


@pytest.mark.parametrize(
    ("old", "new"), [(u, k) for u in _UNKNOWN for k in ("unsigned char", "int")]
)
def test_an_unknown_spelling_never_reports(old: str, new: str) -> None:
    assert _underlying_kinds(old, new) == []
    assert _underlying_kinds(new, old) == []


def test_qualifiers_do_not_change_the_size() -> None:
    assert underlying_byte_size("const unsigned  char") == 1
    assert _underlying_kinds("unsigned char", "const unsigned char") == []


def test_finding_carries_the_dwarf_pass_values() -> None:
    """Byte sizes, the DWARF pass's spelling, so the two collapse into one."""
    result = compare(_snap("1", "unsigned char"), _snap("2", "unsigned int"))
    (change,) = [
        c for c in result.changes if c.kind.value == "enum_underlying_size_changed"
    ]
    assert (change.symbol, change.old_value, change.new_value) == ("E", "1", "4")
    assert result.verdict.value == "BREAKING"


def _platform_snap(
    version: str, underlying: str, platform: str, pointer: int
) -> AbiSnapshot:
    from abicheck.model.elf_facts import ElfMetadata

    snap = _snap(version, underlying)
    snap.platform = platform
    if platform == "elf":
        snap.elf = ElfMetadata(pointer_size=pointer)
    return snap


@pytest.mark.parametrize(
    ("platform", "pointer", "long_size"),
    [("elf", 8, 8), ("elf", 4, 4), ("pe", 8, 4)],
)
@pytest.mark.parametrize("other", ["int", "long long"])
def test_long_is_sized_by_the_targets_data_model(
    platform: str, pointer: int, long_size: int, other: str
) -> None:
    """LP64, ILP32 and LLP64: ``long`` vs ``int``/``long long`` differs
    exactly when the data model says the widths differ."""
    other_size = {"int": 4, "long long": 8}[other]
    result = compare(
        _platform_snap("1", other, platform, pointer),
        _platform_snap("2", "long int", platform, pointer),
    )
    found = [
        c for c in result.changes if c.kind.value == "enum_underlying_size_changed"
    ]
    assert len(found) == (1 if long_size != other_size else 0)


def test_long_without_a_known_data_model_never_reports() -> None:
    assert _underlying_kinds("int", "long") == []
