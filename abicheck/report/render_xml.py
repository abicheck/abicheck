# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Pure XML projection for canonical report documents.

ADR-061 Phase 2 requires every output format to be rendered from one
immutable :class:`~abicheck.report.document.ReportDocument` rather than from
mutable workflow state.  JSON-shaped formats (the native JSON report, SARIF)
reach that boundary directly, but XML-shaped ones — today JUnit — carry an
element tree, which a ``ReportDocument`` cannot hold: it stores JSON values
only, deliberately, so a renderer cannot be handed a live object graph it
could mutate.

:func:`element_to_mapping` is the lossless JSON encoding of an element tree
(``tag``/``attrib``/``text``/``tail``/``children``) that closes that gap, and
:func:`render_xml_document` is its inverse plus serialization.  The split is
not cosmetic: indentation and the XML declaration are *formatting*, so they
belong to the projection, while the tree's structure and values are report
facts, so they belong to the document.  A renderer therefore receives a
frozen description of the report and can only format it — it cannot reach
back into ``DiffResult`` or re-run policy, exactly as D9 requires.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET

from .document import ReportDocument


def element_to_mapping(element: ET.Element) -> dict[str, object]:
    """Return a lossless, JSON-shaped encoding of *element* and its subtree.

    ``text``/``tail`` are omitted when ``None`` so a document round-trips to
    an identical tree rather than to one carrying empty strings where the
    original carried nothing.
    """
    node: dict[str, object] = {
        "tag": element.tag,
        "attrib": dict(element.attrib),
    }
    if element.text is not None:
        node["text"] = element.text
    if element.tail is not None:
        node["tail"] = element.tail
    node["children"] = [element_to_mapping(child) for child in element]
    return node


def element_from_mapping(node: object) -> ET.Element:
    """Rebuild the element tree :func:`element_to_mapping` encoded.

    The inverse is public because it is what makes the encoding checkable as
    a round trip rather than only through one renderer's golden output.
    """
    if not isinstance(node, dict):
        raise TypeError("an XML report node must be an object")
    attrib = node.get("attrib", {})
    if not isinstance(attrib, dict):
        raise TypeError("an XML report node's attrib must be an object")
    element = ET.Element(str(node["tag"]), {str(k): str(v) for k, v in attrib.items()})
    text = node.get("text")
    if text is not None:
        element.text = str(text)
    tail = node.get("tail")
    if tail is not None:
        element.tail = str(tail)
    children = node.get("children", [])
    if not isinstance(children, list):
        raise TypeError("an XML report node's children must be an array")
    element.extend(element_from_mapping(child) for child in children)
    return element


def render_xml_document(
    document: ReportDocument, *, indent: bool = True, encoding: str = "UTF-8"
) -> str:
    """Serialize *document* as XML without deriving or changing report facts."""

    root = element_from_mapping(document.to_mapping())
    if indent:
        ET.indent(root)
    buf = io.BytesIO()
    ET.ElementTree(root).write(buf, encoding=encoding, xml_declaration=True)
    return buf.getvalue().decode(encoding)


def render_element_as_xml(root: ET.Element, *, indent: bool = True) -> str:
    """Freeze a completed element tree as a report document and render it.

    The XML counterpart of
    :func:`~abicheck.report.render_json.render_mapping_as_json`, and the entry
    point a builder that assembles an element tree should call: it makes the
    freeze one step rather than three, so a caller cannot skip it by reaching
    straight for ``ElementTree.write``.
    """
    return render_xml_document(
        ReportDocument.from_mapping(element_to_mapping(root)), indent=indent
    )
