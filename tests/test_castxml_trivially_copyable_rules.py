"""castxml ``trivially_copyable``: special-member reading on hand-built XML.

CastXML marks no special member as ``= default``, so the trait reads the
declaration's own text from the header. Each case writes a real header line
and states the expected trait from C++ [class.prop]/1 (a user-provided copy
or move constructor, assignment or destructor makes a class not trivially
copyable; one defaulted on its first declaration does not), never from the
implementation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from defusedxml.ElementTree import fromstring

from abicheck.extract.headers.castxml.context import CastxmlParserContext
from abicheck.extract.headers.castxml.record_traits import trivially_copyable

_TYPES = (
    '<FundamentalType id="_int" name="int" size="32" align="32"/>'
    '<CvQualifiedType id="_cs" type="_S" const="1"/>'
    '<ReferenceType id="_ref" type="_cs"/>'
    '<RValueReferenceType id="_rref" type="_S"/>'
)
_IMPLICIT_COPY = (
    '<Constructor id="_ic" name="S" artificial="1">'
    '<Argument type="_ref"/></Constructor>'
)


def _trait(tmp_path: Path, header: str, members: str, ids: str) -> bool | None:
    path = tmp_path / "s.h"
    path.write_text(header, encoding="utf-8")
    xml = (
        f'<CastXML><File id="f1" name="{path}"/>{_TYPES}'
        f'<Struct id="_S" name="S" members="_a {ids}"/>'
        '<Field id="_a" name="a" type="_int" access="public"/>'
        f"{members}</CastXML>"
    )
    ctx = CastxmlParserContext(fromstring(xml), set(), set())
    ctx.build_id_map()
    return trivially_copyable(ctx, ctx.id_map["_S"])


def _copy(line: int, arg: str = "_ref") -> str:
    return (
        f'<Constructor id="_c" name="S" file="f1" line="{line}">'
        f'<Argument type="{arg}"/></Constructor>'
    )


CASES: list[tuple[str, str, str, str, bool | None]] = [
    # Only implicit members: trivially copyable.
    ("implicit-only", "struct S { int a; };", _IMPLICIT_COPY, "_ic", True),
    # A copy constructor defaulted on its first declaration is trivial.
    (
        "copy-defaulted",
        "struct S { int a; S(const S&) = default; };",
        _copy(1),
        "_c",
        True,
    ),
    # A user-provided copy constructor is not.
    ("copy-user", "struct S { int a; S(const S&); };", _copy(1), "_c", False),
    # The defaulted default constructor on the same line must not hide the
    # user-provided copy constructor.
    (
        "copy-beside-defaulted-default",
        "struct S { int a; S() = default; S(const S& o); };",
        _copy(1),
        "_c",
        False,
    ),
    # `= default` inside a comment is not a declaration token.
    (
        "default-in-block-comment",
        "struct S { int a; S(const S&) /* = default */; };",
        _copy(1),
        "_c",
        False,
    ),
    (
        "default-in-line-comment",
        "struct S { int a; S(const S&); // = default\n};",
        _copy(1),
        "_c",
        False,
    ),
    # A user-provided move constructor.
    ("move-user", "struct S { int a; S(S&&); };", _copy(1, "_rref"), "_c", False),
    # A defaulted copy assignment, with the implicit copy constructor present.
    (
        "assign-defaulted",
        "struct S { int a; S& operator=(const S&) = default; };",
        _IMPLICIT_COPY + '<OperatorMethod id="_o" name="=" file="f1" line="1">'
        '<Argument type="_ref"/></OperatorMethod>',
        "_ic _o",
        True,
    ),
    # A user-provided destructor.
    (
        "dtor-user",
        "struct S { int a; ~S(); };",
        _IMPLICIT_COPY + '<Destructor id="_d" name="S" file="f1" line="1"/>',
        "_ic _d",
        False,
    ),
    # A defaulted destructor.
    (
        "dtor-defaulted",
        "struct S { int a; ~S() = default; };",
        _IMPLICIT_COPY + '<Destructor id="_d" name="S" file="f1" line="1"/>',
        "_ic _d",
        True,
    ),
    # A virtual method disproves the trait whatever the special members say.
    (
        "virtual-method",
        "struct S { int a; virtual int f(); };",
        _IMPLICIT_COPY + '<Method id="_m" name="f" virtual="1"/>',
        "_ic _m",
        False,
    ),
    # A non-copy constructor (int) is not a special member.
    (
        "converting-ctor",
        "struct S { int a; S(int); };",
        _IMPLICIT_COPY + '<Constructor id="_k" name="S" file="f1" line="1">'
        '<Argument type="_int"/></Constructor>',
        "_ic _k",
        True,
    ),
    # The declaration line is past the end of the header: unknown.
    ("line-out-of-range", "struct S { int a; };", _copy(99), "_c", None),
    # A declaration whose end is not within the scanned lines: unknown.
    (
        "unterminated",
        "struct S { int a; S(const S&)\n\n\n\n\n= default; };",
        _copy(1),
        "_c",
        None,
    ),
    # No copy constructor emitted at all (CastXML omits a deleted one).
    ("no-copy-ctor", "struct S { int a; };", "", "", None),
]


@pytest.mark.parametrize(
    ("header", "members", "ids", "want"),
    [c[1:] for c in CASES],
    ids=[c[0] for c in CASES],
)
def test_trivially_copyable_reads_the_special_members(
    tmp_path: Path, header: str, members: str, ids: str, want: bool | None
) -> None:
    assert _trait(tmp_path, header, members, ids) is want


def test_an_unreadable_header_is_unknown(tmp_path: Path) -> None:
    xml = (
        f'<CastXML><File id="f1" name="{tmp_path / "missing.h"}"/>{_TYPES}'
        '<Struct id="_S" name="S" members="_c"/>'
        f"{_copy(1)}</CastXML>"
    )
    ctx = CastxmlParserContext(fromstring(xml), set(), set())
    ctx.build_id_map()
    assert trivially_copyable(ctx, ctx.id_map["_S"]) is None


def _plain(members: str, ids: str, extra_types: str = "") -> bool | None:
    xml = (
        f"<CastXML>{_TYPES}{extra_types}"
        f'<Struct id="_S" name="S" members="_a {ids}"/>'
        f"{members}</CastXML>"
    )
    ctx = CastxmlParserContext(fromstring(xml), set(), set())
    ctx.build_id_map()
    return trivially_copyable(ctx, ctx.id_map["_S"])


@pytest.mark.parametrize(
    ("members", "ids", "extra", "want"),
    [
        # A member through a typedef keeps its scalar trait.
        (
            '<Field id="_a" name="a" type="_td" access="public"/>' + _IMPLICIT_COPY,
            "_ic",
            '<Typedef id="_td" name="T" type="_int"/>',
            True,
        ),
        # A member whose type castxml does not resolve is unknown.
        (
            '<Field id="_a" name="a" type="_nope" access="public"/>' + _IMPLICIT_COPY,
            "_ic",
            "",
            None,
        ),
        # A reference member is not a scalar the trait can prove.
        (
            '<Field id="_a" name="a" type="_ref" access="public"/>' + _IMPLICIT_COPY,
            "_ic",
            "",
            None,
        ),
        # A user-declared special member without a source location is unknown.
        (
            '<Field id="_a" name="a" type="_int" access="public"/>'
            '<Constructor id="_c" name="S"><Argument type="_ref"/></Constructor>',
            "_c",
            "",
            None,
        ),
        # A non-assignment operator is not a special member.
        (
            '<Field id="_a" name="a" type="_int" access="public"/>'
            + _IMPLICIT_COPY
            + '<OperatorMethod id="_o" name="+"><Argument type="_ref"/></OperatorMethod>',
            "_ic _o",
            "",
            True,
        ),
        # An unrelated record elsewhere in the document changes nothing.
        (
            '<Field id="_a" name="a" type="_int" access="public"/>' + _IMPLICIT_COPY,
            "_ic",
            '<Struct id="_VB" name="VB" members=""/>',
            True,
        ),
    ],
    ids=[
        "typedef-member",
        "unresolved-member",
        "reference-member",
        "no-location",
        "operator-plus",
        "plain-with-unrelated",
    ],
)
def test_trivially_copyable_member_edges(
    members: str, ids: str, extra: str, want
) -> None:
    assert _plain(members, ids, extra) is want


def test_virtual_base_and_incomplete_record() -> None:
    xml = (
        f"<CastXML>{_TYPES}"
        '<Struct id="_VB" name="VB" members=""/>'
        '<Struct id="_S" name="S" members="_ic"><Base type="_VB" access="public" virtual="1"/></Struct>'
        f"{_IMPLICIT_COPY}"
        '<Struct id="_X" name="X" incomplete="1"/></CastXML>'
    )
    ctx = CastxmlParserContext(fromstring(xml), set(), set())
    ctx.build_id_map()
    assert trivially_copyable(ctx, ctx.id_map["_S"]) is False
    assert trivially_copyable(ctx, ctx.id_map["_X"]) is None
    # A second query reads the cached answer.
    assert trivially_copyable(ctx, ctx.id_map["_S"]) is False
