"""castxml ``standard_layout``: each [class.prop]/3 rule on hand-built CastXML XML.

The integration test (``test_castxml_record_traits_layout_integration.py``)
checks real castxml output against g++'s ``__is_standard_layout``. This file
covers each rule and each unknown branch without a toolchain. The expected
value of every case is written from the C++ rule it names, not from the
implementation.
"""

from __future__ import annotations

import pytest
from defusedxml.ElementTree import fromstring

from abicheck.extract.headers.castxml.context import CastxmlParserContext
from abicheck.extract.headers.castxml.record_traits import standard_layout

_INT = '<FundamentalType id="_int" name="int" size="32" align="32"/>'


def _trait(body: str, record: str = "_S") -> bool | None:
    root = fromstring(f"<CastXML>{_INT}{body}</CastXML>")
    ctx = CastxmlParserContext(root, set(), set())
    ctx.build_id_map()
    return standard_layout(ctx, ctx.id_map[record])


def _struct(rid: str, members: str = "", bases: str = "", extra: str = "") -> str:
    return (
        f'<Struct id="{rid}" name="{rid}" members="{members}" {extra}>{bases}</Struct>'
    )


def _field(fid: str, ftype: str = "_int", access: str | None = "public") -> str:
    acc = f' access="{access}"' if access is not None else ""
    return f'<Field id="{fid}" name="{fid}" type="{ftype}"{acc}/>'


CASES: list[tuple[str, str, bool | None]] = [
    # A plain aggregate of scalars is standard-layout.
    ("plain", _struct("_S", "_a _b") + _field("_a") + _field("_b"), True),
    # [class.prop]/3.3: mixed access control.
    (
        "mixed-access",
        _struct("_S", "_a _b") + _field("_a") + _field("_b", access="protected"),
        False,
    ),
    # An access attribute castxml did not emit is unknown, not public.
    (
        "access-missing",
        _struct("_S", "_a") + _field("_a", access=None),
        None,
    ),
    # [class.prop]/3.2: a virtual function.
    (
        "virtual-method",
        _struct("_S", "_a _m")
        + _field("_a")
        + '<Method id="_m" name="f" virtual="1"/>',
        False,
    ),
    # [class.prop]/3.2: a virtual base.
    (
        "virtual-base",
        _struct("_B")
        + _struct("_S", "", '<Base type="_B" access="public" virtual="1"/>'),
        False,
    ),
    # [class.prop]/3.1: a reference member, also through a typedef.
    (
        "reference-member",
        _struct("_S", "_r")
        + '<ReferenceType id="_ref" type="_int"/>'
        + '<Typedef id="_td" name="R" type="_ref"/>'
        + _field("_r", "_td"),
        False,
    ),
    # A member whose record is not standard-layout poisons the outer one.
    (
        "member-not-standard",
        _struct("_I", "_x _y")
        + _field("_x")
        + _field("_y", access="private")
        + _struct("_S", "_i")
        + '<CvQualifiedType id="_ci" type="_I" const="1"/>'
        + _field("_i", "_ci"),
        False,
    ),
    # A member type castxml does not resolve is unknown.
    ("member-unresolved", _struct("_S", "_a") + _field("_a", "_missing"), None),
    # An incomplete record has no trait.
    ("incomplete", _struct("_S", extra='incomplete="1"'), None),
    # [class.prop]/3.5: data members in a base and in the derived class.
    (
        "data-in-base-and-derived",
        _struct("_B", "_k")
        + _field("_k")
        + _struct("_S", "_d", '<Base type="_B" access="public" virtual="0"/>')
        + _field("_d"),
        False,
    ),
    # [class.prop]/3.5: data members in two bases.
    (
        "data-in-two-bases",
        _struct("_B1", "_k1")
        + _field("_k1")
        + _struct("_B2", "_k2")
        + _field("_k2")
        + _struct(
            "_S",
            "",
            '<Base type="_B1" access="public" virtual="0"/>'
            '<Base type="_B2" access="public" virtual="0"/>',
        ),
        False,
    ),
    # Data in exactly one base, none in the derived class, is fine.
    (
        "data-in-one-base",
        _struct("_B", "_k")
        + _field("_k")
        + _struct("_S", "", '<Base type="_B" access="public" virtual="0"/>'),
        True,
    ),
    # An empty base plus derived data is fine.
    (
        "empty-base",
        _struct("_E")
        + _struct("_S", "_d", '<Base type="_E" access="public" virtual="0"/>')
        + _field("_d"),
        True,
    ),
    # A base castxml does not resolve is unknown.
    (
        "base-unresolved",
        _struct("_S", "_d", '<Base type="_nope" access="public" virtual="0"/>')
        + _field("_d"),
        None,
    ),
    # [class.prop]/3.6: the first member has the type of an (indirect) base.
    (
        "first-member-is-indirect-base",
        _struct("_E")
        + _struct("_M", "", '<Base type="_E" access="public" virtual="0"/>')
        + _struct("_S", "_e", '<Base type="_M" access="public" virtual="0"/>')
        + _field("_e", "_E"),
        False,
    ),
    # The first member of a different empty type is fine.
    (
        "first-member-other-empty",
        _struct("_E")
        + _struct("_F")
        + _struct("_S", "_f", '<Base type="_E" access="public" virtual="0"/>')
        + _field("_f", "_F"),
        True,
    ),
    # [class.prop]/3.8 looks through arrays: an int array is not a base.
    (
        "first-member-int-array",
        _struct("_E")
        + '<ArrayType id="_arr" type="_int" min="0" max="1"/>'
        + _struct("_S", "_a", '<Base type="_E" access="public" virtual="0"/>')
        + _field("_a", "_arr"),
        True,
    ),
    # ... and an array of the base type is in M(D).
    (
        "first-member-array-of-base",
        _struct("_E")
        + '<ArrayType id="_arr" type="_E" min="0" max="1"/>'
        + _struct("_S", "_a", '<Base type="_E" access="public" virtual="0"/>')
        + _field("_a", "_arr"),
        False,
    ),
    # M(D) recurses through the first member's own first member.
    (
        "nested-first-member-is-base",
        _struct("_E")
        + _struct("_IN", "_e")
        + _field("_e", "_E")
        + _struct("_S", "_i", '<Base type="_E" access="public" virtual="0"/>')
        + _field("_i", "_IN"),
        False,
    ),
    # A base that is a *later* member of the first member is not in M(D).
    (
        "nested-later-member-is-base",
        _struct("_E")
        + _struct("_IN", "_x _e")
        + _field("_x")
        + _field("_e", "_E")
        + _struct("_S", "_i", '<Base type="_E" access="public" virtual="0"/>')
        + _field("_i", "_IN"),
        True,
    ),
    # A union contributes every member type to M(D).
    (
        "union-first-member-holds-base",
        _struct("_E")
        + '<Union id="_U" name="_U" members="_ui _ue"/>'
        + _field("_ui")
        + _field("_ue", "_E")
        + _struct("_S", "_u", '<Base type="_E" access="public" virtual="0"/>')
        + _field("_u", "_U"),
        False,
    ),
    # [class.prop]/3.7: two base subobjects of the same (empty) type.
    (
        "repeated-empty-base-subobject",
        _struct("_E")
        + _struct("_L", "", '<Base type="_E" access="public" virtual="0"/>')
        + _struct("_R", "", '<Base type="_E" access="public" virtual="0"/>')
        + _struct(
            "_S",
            "",
            '<Base type="_L" access="public" virtual="0"/>'
            '<Base type="_R" access="public" virtual="0"/>',
        ),
        False,
    ),
]


@pytest.mark.parametrize(
    ("body", "want"), [(b, w) for _, b, w in CASES], ids=[c[0] for c in CASES]
)
def test_standard_layout_follows_class_prop(body: str, want: bool | None) -> None:
    assert _trait(body) is want


def test_answer_is_cached_per_record() -> None:
    root = fromstring(f"<CastXML>{_INT}{_struct('_S', '_a')}{_field('_a')}</CastXML>")
    ctx = CastxmlParserContext(root, set(), set())
    ctx.build_id_map()
    el = ctx.id_map["_S"]
    assert standard_layout(ctx, el) is True
    assert ctx.standard_layout_cache["_S"] is True
    ctx.standard_layout_cache["_S"] = False  # a second call reads the cache
    assert standard_layout(ctx, el) is False


def _record(body: str, record: str = "_S"):
    from abicheck.extract.headers.castxml.records import build_record_type

    root = fromstring(f"<CastXML>{_INT}{body}</CastXML>")
    ctx = CastxmlParserContext(root, set(), set())
    ctx.build_id_map()
    return build_record_type(ctx, ctx.id_map[record])


@pytest.mark.parametrize("access", ["public", "protected", "private"])
def test_base_access_is_read_from_each_base(access: str) -> None:
    rec = _record(
        _struct("_B")
        + _struct("_S", "", f'<Base type="_B" access="{access}" virtual="0"/>')
    )
    assert rec.base_access_fact.value == {"_B": access}


def test_a_base_without_a_known_access_leaves_the_map_unknown() -> None:
    from abicheck.model.availability import FactStatus

    rec = _record(
        _struct("_B1")
        + _struct("_B2")
        + _struct(
            "_S",
            "",
            '<Base type="_B1" access="public" virtual="0"/>'
            '<Base type="_B2" virtual="0"/>',
        )
    )
    assert rec.base_access_fact.status is FactStatus.NOT_COLLECTED
    # The traits ride along: no virtual base, no data -> standard-layout.
    assert rec.is_standard_layout is True


EDGE_CASES: list[tuple[str, str, bool | None]] = [
    # A base reached through a typedef is the same base.
    (
        "base-through-typedef",
        _struct("_B", "_k")
        + _field("_k")
        + '<Typedef id="_tb" name="TB" type="_B"/>'
        + _struct("_S", "_d", '<Base type="_tb" access="public" virtual="0"/>')
        + _field("_d"),
        False,
    ),
    # A member of a type castxml does not model (here an unknown tag) is unknown.
    (
        "member-unknown-type-tag",
        _struct("_S", "_a")
        + '<Unimplemented id="_u" kind="Weird"/>'
        + _field("_a", "_u"),
        None,
    ),
    # An incomplete base leaves the data-member rule unknown.
    (
        "incomplete-base",
        _struct("_B", extra='incomplete="1"')
        + _struct("_S", "_d", '<Base type="_B" access="public" virtual="0"/>')
        + _field("_d"),
        None,
    ),
    # The first member's own type is incomplete: M(D) is unknown.
    (
        "first-member-incomplete",
        _struct("_E")
        + _struct("_I", extra='incomplete="1"')
        + _struct("_S", "_i", '<Base type="_E" access="public" virtual="0"/>')
        + '<PointerType id="_p" type="_I"/>'
        + _field("_i", "_I"),
        None,
    ),
    # The first member's type is unresolved: M(D) is unknown.
    (
        "first-member-unresolved",
        _struct("_E")
        + _struct("_S", "_i", '<Base type="_E" access="public" virtual="0"/>')
        + _field("_i", "_nope"),
        None,
    ),
    # The first member's first member is unresolved: M(D) is unknown.
    (
        "nested-first-member-unresolved",
        _struct("_E")
        + _struct("_IN", "_q")
        + _field("_q", "_nope")
        + _struct("_S", "_i", '<Base type="_E" access="public" virtual="0"/>')
        + _field("_i", "_IN"),
        None,
    ),
]


@pytest.mark.parametrize(
    ("body", "want"),
    [(b, w) for _, b, w in EDGE_CASES],
    ids=[c[0] for c in EDGE_CASES],
)
def test_standard_layout_unknown_and_wrapper_edges(
    body: str, want: bool | None
) -> None:
    assert _trait(body) is want
