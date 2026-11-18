"""``Function.return_by_value_fact`` (schema v70): the castxml producer and its codec.

The producer oracle is a hand-built castxml type graph, shaped the way castxml
writes each declared return type: a record is a ``Struct`` node, a pointer or
reference wraps it, and a cv-qualifier or typedef wraps it one level further.
"""

from __future__ import annotations

from typing import Any
from xml.etree.ElementTree import Element

import pytest

from abicheck.extract.headers.castxml.return_by_value import (
    castxml_return_by_value_fact,
)
from abicheck.storage.fact_return_by_value import decode_return_by_value


class _Ctx:
    """The one method the producer reads: ``resolve(id) -> Element | None``."""

    def __init__(self, nodes: dict[str, Element]) -> None:
        self._nodes = nodes

    def resolve(self, type_id: str) -> Element | None:
        return self._nodes.get(type_id)


def _graph() -> _Ctx:
    nodes = {
        "rec": Element("Struct", id="rec", name="S"),
        "int": Element("FundamentalType", id="int", name="int"),
        "enm": Element("Enumeration", id="enm", name="E"),
        "ptr_rec": Element("PointerType", id="ptr_rec", type="rec"),
        "ref_rec": Element("ReferenceType", id="ref_rec", type="rec"),
        "cv_rec": Element("CvQualifiedType", id="cv_rec", type="rec"),
        "td_rec": Element("Typedef", id="td_rec", type="rec"),
        "td_ptr": Element("Typedef", id="td_ptr", type="ptr_rec"),
        "cv_td_ptr": Element("CvQualifiedType", id="cv_td_ptr", type="td_ptr"),
    }
    return _Ctx(nodes)


@pytest.mark.parametrize(
    ("type_id", "expected"),
    [
        ("rec", True),  # S
        ("ptr_rec", False),  # S *
        ("ref_rec", False),  # S &
        ("cv_rec", True),  # const S
        ("td_rec", True),  # typedef S T
        ("td_ptr", False),  # typedef S * T
        ("cv_td_ptr", False),  # const T, T = S *
        ("int", False),  # a fundamental is never returned in a record slot
        ("enm", False),  # an enumeration is a scalar
    ],
)
def test_the_producer_reads_by_value_from_the_type_graph(
    type_id: str, expected: bool
) -> None:
    fact = castxml_return_by_value_fact(_graph(), type_id)
    assert fact is not None
    assert fact.value is expected


def test_an_unresolved_return_type_is_undetermined() -> None:
    assert castxml_return_by_value_fact(_graph(), "missing") is None


def test_a_wrapper_cycle_is_undetermined_not_guessed() -> None:
    ctx = _Ctx({"a": Element("Typedef", id="a", type="a")})
    assert castxml_return_by_value_fact(ctx, "a") is None


@pytest.mark.parametrize("value", [True, False])
def test_the_fact_round_trips_through_the_codec(value: bool) -> None:
    # The serialized form every fact key shares: status, value, diagnostics.
    wire: dict[str, Any] = {
        "return_by_value_fact": {
            "status": "present",
            "value": value,
            "diagnostics": [],
            "producer": None,
        }
    }
    decoded = decode_return_by_value(wire, 70)
    assert decoded is not None
    assert decoded.value is value


def test_an_absent_key_reads_as_unset_at_every_version() -> None:
    assert decode_return_by_value({}, 70) is None
    assert decode_return_by_value({}, 69) is None
