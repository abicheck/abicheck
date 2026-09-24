# SPDX-License-Identifier: Apache-2.0
"""Both ``_quoted_spans`` copies agree with the original character loop.

The no-quote early return must not change any answer; the oracle is the
loop as it was before that return existed.
"""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from abicheck.name_classification import _quoted_spans as nc_spans
from abicheck.storage.closure_identity import _quoted_spans as ci_spans


def _reference(name: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = None
    i = 0
    while i < len(name):
        ch = name[i]
        if ch == "\\" and start is not None:
            i += 2
            continue
        if ch == '"':
            if start is None:
                start = i
            else:
                spans.append((start, i + 1))
                start = None
        i += 1
    return spans


@pytest.mark.parametrize("impl", [nc_spans, ci_spans])
@given(st.text(alphabet='ab"\\( )<>:', max_size=30))
def test_matches_reference(impl, name: str) -> None:
    assert impl(name) == _reference(name)
