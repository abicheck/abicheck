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
)


@pytest.mark.parametrize("type_id", ["", "does-not-exist"])
def test_cv_qualifies_pointer_value_is_false_without_a_resolvable_type(
    type_id: str,
) -> None:
    root = Element("GCC_XML")
    root.append(Element("FundamentalType", id="t1", name="int"))
    parser = _CastxmlParser(root, set(), set())
    assert cv_qualifies_pointer_value(parser._ctx, type_id) is False
