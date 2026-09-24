# SPDX-License-Identifier: Apache-2.0
"""``current_section_payload`` is the ``SectionDTO`` round trip, minus the copies.

Oracle: ``SectionDTO.from_dict(raw).to_dict()["payload"]`` -- the path the
loader took before -- on generated JSON payloads, plus the header/payload
rejections both paths must share.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.storage.dto import (
    GRAPH_SECTION_KIND,
    SECTION_SCHEMA_VERSIONS,
    SectionDTO,
    current_section_payload,
)

_json = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=5),
    lambda kids: (
        st.lists(kids, max_size=4)
        | st.dictionaries(st.text(max_size=4), kids, max_size=4)
    ),
    max_leaves=25,
)


@settings(max_examples=300, deadline=None)
@given(st.dictionaries(st.text(max_size=4), _json, max_size=5))
def test_payload_equals_dto_round_trip(payload: dict) -> None:
    raw = {
        "section_kind": GRAPH_SECTION_KIND,
        "section_schema_version": SECTION_SCHEMA_VERSIONS[GRAPH_SECTION_KIND],
        "payload": payload,
    }
    kind, fast = current_section_payload(raw)  # type: ignore[misc]
    expected = SectionDTO.from_dict(raw).to_dict()["payload"]
    assert kind == GRAPH_SECTION_KIND
    assert fast == expected
    assert list(fast) == list(expected)  # same (sorted) key order
    assert fast is not payload


def test_stale_version_defers_to_the_migration_path() -> None:
    raw = {
        "section_kind": GRAPH_SECTION_KIND,
        "section_schema_version": 999,
        "payload": {},
    }
    assert current_section_payload(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        [],
        {"section_kind": GRAPH_SECTION_KIND, "section_schema_version": 1},
        {"section_kind": "", "section_schema_version": 1, "payload": {}},
        {
            "section_kind": GRAPH_SECTION_KIND,
            "section_schema_version": True,
            "payload": {},
        },
        {
            "section_kind": GRAPH_SECTION_KIND,
            "section_schema_version": 1,
            "payload": [],
        },
        {
            "section_kind": GRAPH_SECTION_KIND,
            "section_schema_version": 1,
            "payload": {1: "x"},
        },
        {
            "section_kind": GRAPH_SECTION_KIND,
            "section_schema_version": 1,
            "payload": {"x": float("inf")},
        },
    ],
)
def test_rejects_what_the_dto_rejects(raw: object) -> None:
    with pytest.raises((ValueError, TypeError)) as dto_err:
        SectionDTO.from_dict(raw)  # type: ignore[arg-type]
    with pytest.raises(dto_err.type):
        current_section_payload(raw)  # type: ignore[arg-type]


@settings(max_examples=200, deadline=None)
@given(st.dictionaries(st.text(max_size=4), _json, max_size=5))
def test_graph_document_from_owned_equals_round_trip(graph: dict) -> None:
    from abicheck.storage.canonical import canonical_form, canonical_input_trusted
    from abicheck.storage.graph_section_codec import GraphSection

    payload = {"surface_graph": canonical_form(graph)}
    expected = GraphSection.from_document(payload).to_document()
    assert GraphSection.document_from_owned(payload) == expected  # untrusted: full path
    with canonical_input_trusted():
        assert GraphSection.document_from_owned(payload) == expected


@pytest.mark.parametrize(
    "payload", [[], {}, {"surface_graph": []}, {"surface_graph": {}, "x": 1}]
)
def test_graph_document_from_owned_rejects_like_from_document(payload: object) -> None:
    from abicheck.storage.canonical import canonical_input_trusted
    from abicheck.storage.graph_section_codec import GraphSection

    with pytest.raises(ValueError):
        GraphSection.from_document(payload)  # type: ignore[arg-type]
    with canonical_input_trusted(), pytest.raises(ValueError):
        GraphSection.document_from_owned(payload)  # type: ignore[arg-type]


@settings(max_examples=200, deadline=None)
@given(st.dictionaries(st.text(max_size=4), _json, max_size=5))
def test_section_dto_dict_equals_dto_to_dict(payload: dict) -> None:
    from abicheck.storage.dto import section_dto_dict

    expected = SectionDTO(
        section_kind=GRAPH_SECTION_KIND,
        section_schema_version=SECTION_SCHEMA_VERSIONS[GRAPH_SECTION_KIND],
        payload=payload,
    ).to_dict()
    got = section_dto_dict(GRAPH_SECTION_KIND, payload)
    assert got == expected
    assert list(got["payload"]) == list(expected["payload"])


@settings(max_examples=200, deadline=None)
@given(st.dictionaries(st.text(max_size=4), _json, max_size=5))
def test_graph_validated_document_canonicalizes_to_round_trip(graph: dict) -> None:
    from abicheck.storage.canonical import canonical_form
    from abicheck.storage.graph_section_codec import GraphSection

    payload = {"surface_graph": graph}
    assert canonical_form(GraphSection.validated_document(payload)) == (
        GraphSection.from_document(payload).to_document()
    )


def test_sectioned_document_round_trips_a_real_snapshot() -> None:
    """End to end through both fast paths: write then read is lossless."""
    from abicheck.model import AbiSnapshot, Function, Visibility
    from abicheck.serialization import (
        SCHEMA_VERSION,
        snapshot_from_dict,
        snapshot_to_dict,
    )
    from abicheck.storage.sectioned_document import (
        from_sectioned_document,
        to_sectioned_document,
    )

    snap = AbiSnapshot(
        library="lib.so",
        version="1",
        functions=[
            Function(
                name="f",
                mangled="_Z1fv",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ],
    )
    legacy = snapshot_to_dict(snap)
    sectioned = to_sectioned_document(legacy, max_known_schema_version=SCHEMA_VERSION)
    assert (
        snapshot_to_dict(snapshot_from_dict(from_sectioned_document(sectioned)))
        == legacy
    )
