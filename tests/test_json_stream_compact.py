# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""`iter_json_compact` is byte-identical to one-shot compact ``json.dumps``."""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.storage.json_stream import iter_json_compact

_scalars = (
    st.none() | st.booleans() | st.integers() | st.text() | st.floats(allow_nan=False)
)
_keys = st.text() | st.integers() | st.booleans() | st.none()
_documents = st.recursive(
    _scalars,
    lambda inner: (
        st.lists(inner, max_size=4) | st.dictionaries(_keys, inner, max_size=4)
    ),
    max_leaves=40,
)


@settings(max_examples=400, deadline=None)
@given(_documents, st.integers(min_value=0, max_value=4))
def test_matches_one_shot_compact_dumps(doc: object, depth: int) -> None:
    expected = json.dumps(doc, separators=(",", ":"))
    assert "".join(iter_json_compact(doc, depth=depth)) == expected


@pytest.mark.parametrize("doc", [{}, [], {"a": {}}, {"a": []}, [[], {}], {"é": "ü\n"}])
def test_empty_containers_and_escapes(doc: object) -> None:
    assert "".join(iter_json_compact(doc)) == json.dumps(doc, separators=(",", ":"))
