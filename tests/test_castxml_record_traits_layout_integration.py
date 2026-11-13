"""CastXML's standard-layout and trivially-copyable traits agree with g++.

Oracle: g++'s own ``__is_standard_layout(T)`` and ``__is_trivially_copyable(T)``
builtins, evaluated on the same header. ``record_traits`` may answer ``None``
(unproven), but a ``True``/``False`` must match the compiler, and the shapes
listed below must be decided (not left unknown).

Several declarations put their special members on the same line as the rest
of the record, as the edit corpus does; that is the shape that once made a
defaulted default constructor hide a user-provided copy constructor.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from defusedxml.ElementTree import parse

from abicheck.extract.headers.castxml.context import CastxmlParserContext
from abicheck.extract.headers.castxml.record_traits import (
    standard_layout,
    trivially_copyable,
)

pytestmark = pytest.mark.integration

# name -> (declaration, expected standard_layout, expected trivially_copyable)
# ``None`` means that answer is not asserted (castxml may leave it unknown),
# but a non-None answer must still match the compiler.
_DECLS: dict[str, tuple[str, bool | None, bool | None]] = {
    "LayoutPlain": ("struct LayoutPlain { int a; int b; };", True, True),
    "LayoutMixed": ("struct LayoutMixed { int a; protected: int b; };", False, True),
    "LayoutVirtual": ("struct LayoutVirtual { virtual void f(); int x; };", False, False),
    "LayoutVBase": ("struct LayoutVBase : virtual LayoutPlain { int y; };", False, False),
    "LayoutRefMember": ("struct LayoutRefMember { int &r; };", False, None),
    "LayoutBothData": ("struct LayoutBase1 { int a; };\nstruct LayoutBothData : LayoutBase1 { int b; };", False, None),
    "LayoutEmptyBase": ("struct LayoutEmpty {};\nstruct LayoutEmptyBase : LayoutEmpty { int x; };", True, True),
    "LayoutFirstIsBase": ("struct LayoutB {};\nstruct LayoutFirstIsBase : LayoutB { LayoutB b; int x; };", False, None),
    "LayoutOuter": ("struct LayoutInner { virtual void f(); };\nstruct LayoutOuter { LayoutInner i; };", False, False),
    "LayoutUnion": ("union LayoutUnion { int a; float b; };", True, True),
    "LayoutUserCopy": ("struct LayoutUserCopy { int a; LayoutUserCopy() = default; LayoutUserCopy(const LayoutUserCopy &o); };", True, False),
    "LayoutDefaultedCopy": ("struct LayoutDefaultedCopy { int a; LayoutDefaultedCopy() = default; LayoutDefaultedCopy(const LayoutDefaultedCopy &) = default; };", True, True),
    "LayoutUserDtor": ("struct LayoutUserDtor { int x; ~LayoutUserDtor() {} };", True, False),
    "LayoutDefaultedDtor": ("struct LayoutDefaultedDtor { int x; ~LayoutDefaultedDtor() = default; };", True, True),
    "LayoutOutOfLineDtor": ("struct LayoutOutOfLineDtor { int x; ~LayoutOutOfLineDtor(); };", True, False),
    "LayoutRepeatedBase": ("struct LayoutRE {};\nstruct LayoutRL : LayoutRE {};\nstruct LayoutRR : LayoutRE {};\nstruct LayoutRepeatedBase : LayoutRL, LayoutRR {};", False, None),
    "LayoutNestedFirst": ("struct LayoutNE {};\nstruct LayoutNI { LayoutNE e; };\nstruct LayoutNestedFirst : LayoutNE { LayoutNI first; };", False, None),
    "LayoutNestedLater": ("struct LayoutLE {};\nstruct LayoutLI { int x; LayoutLE e; };\nstruct LayoutNestedLater : LayoutLE { LayoutLI first; };", True, None),
    "LayoutArrayOfBase": ("struct LayoutAE {};\nstruct LayoutArrayOfBase : LayoutAE { LayoutAE a[2]; };", False, None),
    "LayoutUnionFirst": ("struct LayoutUE {};\nunion LayoutUU { int i; LayoutUE e; };\nstruct LayoutUnionFirst : LayoutUE { LayoutUU u; };", False, None),
    "LayoutCommentDefault": ("struct LayoutCommentDefault { int a; LayoutCommentDefault() = default; LayoutCommentDefault(const LayoutCommentDefault &) /* = default */; };", True, False),
}  # fmt: skip


#: Shapes where g++'s ``__is_standard_layout`` checks only the immediate first
#: member, not C++17 [class.prop]/3's recursive first-member set M(D): g++
#: answers ``True`` and still places the first member at offset 1 to keep the
#: two base subobjects apart. clang++ follows the standard, so it is the oracle
#: for these; with only g++ installed their compiler comparison is skipped
#: (the expected value from the standard is still asserted).
_GCC_SHALLOW_M_SET = frozenset(
    {"LayoutNestedFirst", "LayoutArrayOfBase", "LayoutUnionFirst"}
)
_ORACLE = shutil.which("clang++") or "g++"


def _compiler_traits(header: Path, name: str) -> tuple[bool, bool]:
    probe = header.parent / f"probe_{name}.cpp"
    probe.write_text(
        f'#include "{header.name}"\nstatic_assert(__is_standard_layout({name}), "");\n'
    )
    sl = (
        subprocess.run(
            [_ORACLE, "-std=c++17", "-fsyntax-only", str(probe)], capture_output=True
        ).returncode
        == 0
    )
    probe.write_text(
        f'#include "{header.name}"\nstatic_assert(__is_trivially_copyable({name}), "");\n'
    )
    tc = (
        subprocess.run(
            [_ORACLE, "-std=c++17", "-fsyntax-only", str(probe)], capture_output=True
        ).returncode
        == 0
    )
    return sl, tc


def test_layout_traits_agree_with_compiler(tmp_path: Path) -> None:
    for tool in ("g++", "castxml"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")
    header = tmp_path / "layout.hpp"
    body = "\n".join(decl for decl, _, _ in _DECLS.values())
    header.write_text(f"#pragma once\n{body}\n")
    xml = tmp_path / "layout.xml"
    subprocess.run(
        ["castxml", "--castxml-output=1", "--castxml-cc-gnu", "g++", "-std=c++17",
         "-o", str(xml), str(header)],
        check=True,
    )  # fmt: skip
    ctx = CastxmlParserContext(parse(xml).getroot(), set(), set())
    ctx.build_id_map()
    records = {el.get("name"): el for el in ctx.record_els if el.get("name") in _DECLS}
    assert set(records) == set(_DECLS), sorted(set(_DECLS) - set(records))
    for name, (_, want_sl, want_tc) in _DECLS.items():
        got_sl = standard_layout(ctx, records[name])
        got_tc = trivially_copyable(ctx, records[name])
        cc_sl, cc_tc = _compiler_traits(header, name)
        gcc_deviates = name in _GCC_SHALLOW_M_SET and _ORACLE == "g++"
        if got_sl is not None and not gcc_deviates:
            assert got_sl is cc_sl, (name, "standard_layout", got_sl)
        if got_tc is not None:
            assert got_tc is cc_tc, (name, "trivially_copyable", got_tc)
        if want_sl is not None:
            assert got_sl is want_sl, (name, "standard_layout", got_sl)
        if want_tc is not None:
            assert got_tc is want_tc, (name, "trivially_copyable", got_tc)
