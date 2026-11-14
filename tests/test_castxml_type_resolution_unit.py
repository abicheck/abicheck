# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Boundary cases of ``extract.headers.castxml.type_resolution``, called on
the owner directly rather than through a ``_CastxmlParser`` delegate."""

from __future__ import annotations

from xml.etree.ElementTree import Element

import pytest

from abicheck.dumper import _CastxmlParser
from abicheck.extract.headers.castxml.type_resolution import (
    cv_qualifies_pointer_value,
    type_name,
)


@pytest.mark.parametrize("type_id", ["", "does-not-exist"])
def test_cv_qualifies_pointer_value_is_false_without_a_resolvable_type(
    type_id: str,
) -> None:
    root = Element("GCC_XML")
    root.append(Element("FundamentalType", id="t1", name="int"))
    parser = _CastxmlParser(root, set(), set())
    assert cv_qualifies_pointer_value(parser._ctx, type_id) is False


# A closure type (lambda) is a record castxml emits with no ``name``
# attribute; an anonymous ``struct { ... }`` member has ``name=""``. The
# oracle is castxml's real XML shape (``<Class id=.. context="<function>"/>``
# for ``inline auto f() { return [](int){...}; }``): an unnamed type of that
# context, never the "no evidence" ``?`` sentinel.
@pytest.mark.parametrize(
    ("tag", "owner", "expected"),
    [
        ("Class", ("Function", "kmake"), "kmake::(unnamed class)"),
        ("Struct", ("Method", "run"), "run::(unnamed struct)"),
        ("Union", ("Function", "pick"), "pick::(unnamed union)"),
        ("Class", ("Namespace", "::"), "(unnamed class)"),
        ("Class", None, "(unnamed class)"),
    ],
)
def test_nameless_record_is_spelled_as_an_unnamed_type(
    tag: str, owner: tuple[str, str] | None, expected: str
) -> None:
    root = Element("GCC_XML")
    attrs = {"id": "r1"}
    if owner is not None:
        root.append(Element(owner[0], id="o1", name=owner[1]))
        attrs["context"] = "o1"
    root.append(Element(tag, **attrs))
    parser = _CastxmlParser(root, set(), set())
    parser._ctx.build_id_map()
    assert type_name(parser._ctx, "r1") == expected


def test_named_and_anonymous_member_records_keep_their_spelling() -> None:
    root = Element("GCC_XML")
    root.append(Element("Struct", id="r1", name="Foo"))
    root.append(Element("Struct", id="r2", name=""))
    parser = _CastxmlParser(root, set(), set())
    parser._ctx.build_id_map()
    assert (type_name(parser._ctx, "r1"), type_name(parser._ctx, "r2")) == ("Foo", "")
