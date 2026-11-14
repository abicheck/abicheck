"""Per-target data size of the shapes whose answer depends on the target.

Oracle: the placement ``struct X : R { char z; }`` gets from each target's
compilers (``tests/_data_size_matrix.py`` measures it on real toolchains),
stated here per target from the ABI rules:

* an empty class -- POD or not, under C++03 POD (generic Itanium) or C++11
  POD (Apple arm64) -- is placed as an empty base, so ``z`` lands at 0;
* a bit-field ends the data at its last bit under Itanium allocation (Linux,
  Apple), but MinGW/Cygwin allocate bit-fields Microsoft's way (GCC's
  default ``-mms-bitfields``): ``struct { Bits(); int a : 3; }`` puts ``z``
  at 4, not 1. That allocation is not modeled, so the answer is unknown;
* an MSVC target has no Itanium data size at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.extract.headers.castxml.data_size import data_size_bits
from abicheck.extract.headers.castxml.tail_padding_rule import ms_bitfield_layout
from tests.test_castxml_data_size import _ctx, _field, _implicit, _rec, _struct

LINUX = "x86_64-pc-linux-gnu"
APPLE_ARM64 = "arm64-apple-darwin23.0.0"
APPLE_X86 = "x86_64-apple-darwin23.0.0"
MINGW = "x86_64-w64-mingw32"
MINGW32 = "i686-w64-mingw32"
CYGWIN = "x86_64-pc-cygwin"
MSVC = "x86_64-pc-windows-msvc"

_ALL = (LINUX, APPLE_ARM64, APPLE_X86, MINGW, MINGW32, CYGWIN)
_CTOR = (
    '<Constructor id="_k" name="B" context="_b" access="public" file="f1" line="3"/>'
)


def _empty_np(tmp_path: Path, triple: str):
    """``struct Empty {}; struct EmptyNP : Empty {};``"""
    empty = _struct("_e", "Empty", 8, _implicit("_e"))
    derived = _struct(
        "_d",
        "EmptyNP",
        8,
        '<Base type="_e" access="public" virtual="0" offset="0"/>' + _implicit("_d"),
    )
    return _ctx(empty + derived, tmp_path, triple=triple)


@pytest.mark.parametrize("triple", _ALL)
def test_empty_classes_hold_no_data_on_every_itanium_target(
    tmp_path: Path, triple: str
) -> None:
    ctx = _empty_np(tmp_path, triple)
    assert data_size_bits(ctx, _rec(ctx, "Empty")) == 0
    assert data_size_bits(ctx, _rec(ctx, "EmptyNP")) == 0


def test_msvc_target_has_no_data_size(tmp_path: Path) -> None:
    ctx = _empty_np(tmp_path, MSVC)
    assert data_size_bits(ctx, _rec(ctx, "EmptyNP")) is None


def _bits(tmp_path: Path, triple: str, members: str, size: int):
    return _ctx(_struct("_b", "B", size, _CTOR + members), tmp_path, triple=triple)


#: (fields, sizeof bits, Itanium data size bits): ``B(); int a : 3;``,
#: ``B(); char c; int a : 11;`` and ``B(); char c; int : 12;``.
_BITFIELD_SHAPES = [
    (_field("_ba", "_b", "_int", 0, ' bits="3"'), 32, 8),
    (
        _field("_bc", "_b", "_char", 0) + _field("_ba", "_b", "_int", 8, ' bits="11"'),
        32,
        24,
    ),
    (
        _field("_bc", "_b", "_char", 0) + _field("_bu", "_b", "_int", 8, ' bits="12"'),
        32,
        24,
    ),
]


@pytest.mark.parametrize("triple", _ALL)
@pytest.mark.parametrize(("fields", "size", "itanium"), _BITFIELD_SHAPES)
def test_bitfield_data_size_follows_the_targets_allocation(
    tmp_path: Path, triple: str, fields: str, size: int, itanium: int
) -> None:
    ctx = _bits(tmp_path, triple, fields, size)
    ms = triple in (MINGW, MINGW32, CYGWIN)
    assert data_size_bits(ctx, _rec(ctx, "B")) == (None if ms else itanium)


@pytest.mark.parametrize(
    ("triple", "ms"),
    [
        (LINUX, False),
        (APPLE_ARM64, False),
        (APPLE_X86, False),
        ("aarch64-unknown-linux-android", False),
        (MINGW, True),
        (MINGW32, True),
        ("aarch64-w64-windows-gnu", True),
        (CYGWIN, True),
        (MSVC, False),
        (None, False),
    ],
)
def test_ms_bitfield_layout_targets(triple: str | None, ms: bool) -> None:
    assert ms_bitfield_layout(triple) is ms


def test_record_without_bitfields_is_unaffected_on_mingw(tmp_path: Path) -> None:
    fields = _field("_ba", "_b", "_int", 0) + _field("_bc", "_b", "_char", 32)
    ctx = _bits(tmp_path, MINGW, fields, 64)
    assert data_size_bits(ctx, _rec(ctx, "B")) == 40
