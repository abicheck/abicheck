"""castxml reader: multi-dimensional array bound order and ``va_list`` params.

Pure-Python counterpart of ``test_castxml_written_type_integration.py``: the
XML is hand-built in castxml's shape (an ``int m[2][3]`` is an ArrayType of 2
whose element is an ArrayType of 3 ints; ``max`` is the last valid index), so
the oracle is the C declarator rule, not a castxml run.
"""

from __future__ import annotations

import itertools
import xml.etree.ElementTree as ET

import pytest

from abicheck.extract.headers.castxml.context import CastxmlParserContext
from abicheck.extract.headers.castxml.type_resolution import (
    param_is_va_list,
    type_name,
)


def _ctx(*elements: ET.Element) -> CastxmlParserContext:
    ctx = CastxmlParserContext(ET.Element("CastXML"), set(), set())
    ctx.id_map.update({e.get("id"): e for e in elements})
    return ctx


def _el(tag: str, id_: str, **attrs: str) -> ET.Element:
    return ET.Element(tag, {"id": id_, **attrs})


def _nested_array(dims: tuple[int, ...], elem_id: str) -> list[ET.Element]:
    """castxml nesting for ``T x[d0][d1]...``: outermost ArrayType first."""
    out: list[ET.Element] = []
    for i, d in enumerate(dims):
        nxt = f"a{i + 1}" if i + 1 < len(dims) else elem_id
        out.append(_el("ArrayType", f"a{i}", type=nxt, max=str(d - 1)))
    return out


@pytest.mark.parametrize(
    "dims",
    [d for n in (1, 2, 3, 4) for d in itertools.product((1, 2, 3, 7), repeat=n)],
)
def test_bounds_are_spelled_outermost_first(dims: tuple[int, ...]) -> None:
    ctx = _ctx(_el("FundamentalType", "_int", name="int"), *_nested_array(dims, "_int"))
    assert type_name(ctx, "a0") == "int" + "".join(f"[{d}]" for d in dims)


def test_typedef_element_keeps_its_name() -> None:
    # typedef int row[3]; row m[2];  ->  row[2], not int[2][3]
    ctx = _ctx(
        _el("FundamentalType", "_int", name="int"),
        _el("ArrayType", "_row_arr", type="_int", max="2"),
        _el("Typedef", "_row", name="row", type="_row_arr"),
        _el("ArrayType", "a0", type="_row", max="1"),
    )
    assert type_name(ctx, "a0") == "row[2]"


@pytest.mark.parametrize(("max_", "suffix"), [("", "[]"), ("N", "[N]"), ("-1", "[0]")])
def test_unusual_bounds(max_: str, suffix: str) -> None:
    ctx = _ctx(
        _el("FundamentalType", "_int", name="int"),
        _el("ArrayType", "a0", type="_int", max=max_),
    )
    assert type_name(ctx, "a0") == "int" + suffix


def _va_chain() -> list[ET.Element]:
    return [
        _el("Typedef", "_bva", name="__builtin_va_list", type="_tag"),
        _el("Struct", "_tag", name="__va_list_tag"),
        _el("Typedef", "_gva", name="__gnuc_va_list", type="_bva"),
        _el("Typedef", "_va", name="va_list", type="_gva"),
        _el("ElaboratedType", "_el_va", type="_va"),
        _el("PointerType", "_ptr", type="_tag"),
        _el("FundamentalType", "_int", name="int"),
        _el("PointerType", "_void_p", type="_void"),
        _el("FundamentalType", "_void", name="void"),
        _el("CvQualifiedType", "_cva", type="_va", const="1"),
    ]


@pytest.mark.parametrize(
    ("attrs", "expected"),
    [
        ({"type": "_ptr", "original_type": "_va"}, True),  # x86-64 decayed array
        ({"type": "_va"}, True),
        ({"type": "_gva"}, True),
        ({"type": "_bva"}, True),
        ({"type": "_el_va"}, True),
        ({"type": "_ptr"}, False),  # a plain __va_list_tag *
        ({"type": "_int"}, False),
        ({"type": "_void_p"}, False),
        ({"type": "_cva"}, False),  # qualified alias is not followed
        ({"type": "_missing"}, False),
        ({}, False),
    ],
)
def test_param_is_va_list(attrs: dict[str, str], expected: bool) -> None:
    ctx = _ctx(*_va_chain())
    assert param_is_va_list(ctx, ET.Element("Argument", attrs)) is expected
