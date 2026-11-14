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


def _record(rid: str, name: str, size: int, bases: str, fields: str = "") -> str:
    """A record with CastXML's implicit default and copy constructors (the
    copy constructor is what proves it trivially copyable) and *bases*."""
    specials = (
        _implicit(rid) + f'<Constructor id="{rid}_cc" name="{name}" context="{rid}" '
        'access="public" file="f1" line="1" inline="1" artificial="1">'
        f'<Argument type="{rid}_cr"/></Constructor>'
        f'<ReferenceType id="{rid}_cr" type="{rid}_cq" size="64" align="64"/>'
        f'<CvQualifiedType id="{rid}_cq" type="{rid}" const="1"/>'
    )
    ids = [f"{rid}_dc", f"{rid}_cc"] + [p.split('"')[1] for p in fields.split("<")[1:]]
    return (
        f'<Struct id="{rid}" name="{name}" context="_1" file="f1" line="1" '
        f'members="{" ".join(ids)}" size="{size}" align="32">{bases}</Struct>'
        + specials
        + fields
    )


def _base(rid: str, offset_bytes: int) -> str:
    return f'<Base type="{rid}" access="public" virtual="0" offset="{offset_bytes}"/>'


def _nv(tmp_path: Path, triple: str, nvx_size: int, empty_at: int):
    """``struct Empty {}; struct NvB : Empty { int i; char c; };
    struct NvX : NvB, Empty {};`` -- NvX's own Empty base cannot share
    offset 0 with NvB's, so it goes past NvB's data (byte *empty_at*)."""
    empty = _record("_e", "Empty", 8, "")
    nvb = _record(
        "_nb",
        "NvB",
        64,
        _base("_e", 0),
        _field("_nbi", "_nb", "_int", 0) + _field("_nbc", "_nb", "_char", 32),
    )
    nvx = _record("_nx", "NvX", nvx_size, _base("_nb", 0) + _base("_e", empty_at))
    return _ctx(empty + nvb + nvx, tmp_path, triple=triple)


@pytest.mark.parametrize(
    ("triple", "nvx_size", "empty_at", "nvb_dsize", "nvx_dsize"),
    [
        # C++03 POD (generic Itanium): a class with a base is not POD, so
        # NvB's tail padding is reused (5 bytes of data); NvX's Empty goes
        # at byte 5 and the next member after NvX at 6 (its nvsize).
        (LINUX, 64, 5, 40, 48),
        (APPLE_X86, 64, 5, 40, 48),
        # C++11 POD (Apple arm64): NvB is trivial and standard-layout, so it
        # keeps sizeof (8) and NvX's Empty goes at byte 8. NvX holds two
        # Empty subobjects -- not standard-layout since CWG 1813, but
        # standard-layout as C++11 defined it, the trait Clang lays out by
        # (isCXX11StandardLayout): POD, so it keeps sizeof (12).
        (APPLE_ARM64, 96, 8, 64, 96),
    ],
)
def test_duplicate_empty_base_data_size_per_target(
    tmp_path: Path,
    triple: str,
    nvx_size: int,
    empty_at: int,
    nvb_dsize: int,
    nvx_dsize: int,
) -> None:
    ctx = _nv(tmp_path, triple, nvx_size, empty_at)
    assert data_size_bits(ctx, _rec(ctx, "NvB")) == nvb_dsize
    assert data_size_bits(ctx, _rec(ctx, "NvX")) == nvx_dsize


def test_duplicate_base_subobject_is_cxx11_but_not_current_standard_layout(
    tmp_path: Path,
) -> None:
    from abicheck.extract.headers.castxml.record_traits import standard_layout

    ctx = _nv(tmp_path, LINUX, 64, 5)
    nvx = _rec(ctx, "NvX")
    assert standard_layout(ctx, nvx) is False
    assert standard_layout(ctx, nvx, cxx11=True) is True
