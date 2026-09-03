# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Contract tests for the XML projection ADR-061 Phase 2 routes JUnit through.

These test the primitive directly rather than only through ``to_junit_xml``,
per AGENTS.md's "Primitive-level property tests" guidance: the encoding is a
general element-tree <-> JSON round trip, so its contract ("lossless",
"``None`` text is not an empty string", "attribute order survives") is stated
here as invariants instead of being implied by one caller's golden output.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from hypothesis import given, strategies as st

from abicheck.report.document import ReportDocument
from abicheck.report.render_xml import (
    element_from_mapping,
    element_to_mapping,
    render_xml_document,
)


def _round_trip(element: ET.Element) -> ET.Element:
    document = ReportDocument.from_mapping(element_to_mapping(element))
    rendered = render_xml_document(document, indent=False)
    return ET.fromstring(rendered)


def test_encoding_is_json_compatible_so_a_document_can_hold_it() -> None:
    root = ET.Element("testsuites", {"tests": "2"})
    ET.SubElement(root, "testsuite", {"name": "lib.so"}).text = "detail"

    # from_mapping raises TypeError on any non-JSON value, so this is the
    # real assertion that the encoding never leaks an ET object.
    document = ReportDocument.from_mapping(element_to_mapping(root))

    assert document.to_mapping()["tag"] == "testsuites"


def test_round_trip_preserves_structure_text_and_attribute_order() -> None:
    root = ET.Element("testsuites", {"b": "2", "a": "1"})
    child = ET.SubElement(root, "testsuite", {"name": "lib.so"})
    ET.SubElement(child, "failure", {"type": "abi"}).text = "boom"

    restored = _round_trip(root)

    assert list(restored.attrib.items()) == [("b", "2"), ("a", "1")]
    assert restored[0][0].tag == "failure"
    assert restored[0][0].text == "boom"


def test_absent_text_stays_absent_rather_than_becoming_empty() -> None:
    element = ET.Element("testcase")
    assert element.text is None

    encoded = element_to_mapping(element)

    assert "text" not in encoded and "tail" not in encoded
    assert _round_trip(element).text is None


def test_tail_round_trips_and_stays_absent_when_unset() -> None:
    """``tail`` is half the losslessness claim, so it is checked like ``text``.

    ``ET.indent`` expresses *all* of its formatting through ``tail``, so a
    projection that dropped it would silently render every JUnit suite on one
    line — the encoding has to carry it even though nothing in this package
    sets one before rendering.
    """
    root = ET.Element("testsuites")
    child = ET.SubElement(root, "testsuite")
    child.tail = "\n  "

    assert "tail" not in element_to_mapping(root)
    assert element_to_mapping(root)["children"][0]["tail"] == "\n  "

    restored = element_from_mapping(
        ReportDocument.from_mapping(element_to_mapping(root)).to_mapping()
    )

    assert restored.tail is None
    assert restored[0].tail == "\n  "


def test_projection_cannot_mutate_the_source_tree() -> None:
    root = ET.Element("testsuites")
    ET.SubElement(root, "testsuite")

    render_xml_document(ReportDocument.from_mapping(element_to_mapping(root)))

    # ET.indent() mutates in place; rendering must not reach the caller's tree.
    assert root.text is None and root[0].tail is None


def test_declaration_and_indentation_belong_to_the_projection() -> None:
    root = ET.Element("testsuites")
    ET.SubElement(root, "testsuite")
    document = ReportDocument.from_mapping(element_to_mapping(root))

    indented = render_xml_document(document)
    flat = render_xml_document(document, indent=False)

    assert indented.startswith("<?xml version='1.0' encoding='UTF-8'?>")
    assert "\n  <testsuite" in indented
    assert "\n  <testsuite" not in flat


_TAGS = st.sampled_from(
    ["testsuites", "testsuite", "testcase", "failure", "properties"]
)
_TEXT = st.one_of(
    st.none(), st.text(alphabet=st.characters(min_codepoint=32), max_size=8)
)


def _trees(depth: int = 0) -> st.SearchStrategy[ET.Element]:
    attribs = st.dictionaries(
        st.sampled_from(["a", "b", "name"]), st.text(max_size=4), max_size=3
    )
    leaves = st.builds(
        lambda tag, attrib, text, tail: _make(tag, attrib, text, tail, []),
        _TAGS,
        attribs,
        _TEXT,
        _TEXT,
    )
    if depth >= 2:
        return leaves
    return st.builds(
        _make, _TAGS, attribs, _TEXT, _TEXT, st.lists(_trees(depth + 1), max_size=3)
    )


def _make(
    tag: str,
    attrib: dict[str, str],
    text: str | None,
    tail: str | None,
    children: list[ET.Element],
) -> ET.Element:
    element = ET.Element(tag, dict(attrib))
    if text is not None:
        element.text = text
    if tail is not None:
        element.tail = tail
    element.extend(children)
    return element


@given(_trees())
def test_encoding_is_lossless_for_any_tree(element: ET.Element) -> None:
    """The encoding itself round-trips exactly.

    Checked against :func:`element_from_mapping` rather than by re-parsing
    rendered XML on purpose: XML serialization is itself lossy for inputs
    this encoding handles fine (an empty ``text`` comes back as ``None``, a
    control character is not representable at all), so re-parsing would test
    ElementTree's limits instead of this module's contract.
    """
    document = ReportDocument.from_mapping(element_to_mapping(element))
    restored = element_from_mapping(document.to_mapping())

    assert element_to_mapping(restored) == element_to_mapping(element)


@pytest.mark.parametrize(
    "bad",
    [
        {"tag": "a", "attrib": []},
        {"tag": "a", "children": {}},
        {"tag": "a", "children": [1]},
    ],
)
def test_malformed_nodes_are_rejected(bad: dict[str, object]) -> None:
    """A wrong-shaped node fails loudly rather than rendering a silent no-op.

    Empty containers of the wrong type are included deliberately: a truthiness
    guard (``node.get("attrib") or {}``) accepts them, which would let a
    genuine encoding bug render as valid-looking XML.
    """
    with pytest.raises(TypeError):
        render_xml_document(ReportDocument.from_mapping(bad))
