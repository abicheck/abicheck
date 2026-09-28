# SPDX-License-Identifier: Apache-2.0
"""Property tests for three hot-path rewrites that must not change results.

Each rewrite replaced a per-call recomputation with an index or cache; the
oracle in every case is a direct restatement of the pre-rewrite logic, not
the new code path, so a divergence in the new structure shows up here.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from types import SimpleNamespace

from hypothesis import given, settings, strategies as st

from abicheck import pattern_verdicts
from abicheck.checker_policy import ChangeKind
from abicheck.idioms import AntiPattern
from abicheck.model.graph_facts import SharedGraphValues
from abicheck.policy import selectors

# --- anti-pattern annotation index -----------------------------------------

_segments = st.sampled_from(["a", "b", "ns", "X", "f", "g", ""])
_symbols = st.lists(_segments, min_size=1, max_size=3).map("::".join)


def _naive_annotation(symbol: str, aps: list[AntiPattern]):
    short = symbol.rsplit("::", 1)[-1]
    exact = [ap for ap in aps if ap.symbol == symbol]
    short_aps = [ap for ap in aps if ap.symbol.rsplit("::", 1)[-1] == short]
    matched = exact if exact else (short_aps if len(short_aps) == 1 else [])
    if not matched:
        return None
    return "anti-pattern-raise", [e for ap in matched for e in ap.evidence]


@settings(max_examples=300, deadline=None)
@given(
    ap_symbols=st.lists(_symbols, max_size=8),
    queries=st.lists(_symbols, min_size=1, max_size=8),
)
def test_antipattern_index_matches_linear_scan(ap_symbols, queries):
    aps = [
        AntiPattern(s, ChangeKind.FUNC_REMOVED, "d", [f"e{i}"])
        for i, s in enumerate(ap_symbols)
    ]
    index = pattern_verdicts._AntiPatternIndex(aps)
    for q in queries:
        change = SimpleNamespace(symbol=q)
        assert pattern_verdicts._antipattern_annotation(change, index) == (
            _naive_annotation(q, aps)
        )


def test_antipattern_oracle_is_not_vacuous():
    aps = [AntiPattern("ns::f", ChangeKind.FUNC_REMOVED, "d", ["e"])]
    assert _naive_annotation("other::f", aps) == ("anti-pattern-raise", ["e"])
    assert _naive_annotation("ns::g", aps) is None


# --- cached current date ----------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(
    steps=st.lists(
        st.floats(min_value=-2 * 86400, max_value=2 * 86400), min_size=1, max_size=12
    )
)
def test_current_date_tracks_a_moving_clock(steps):
    """Across any clock walk -- midnight crossings, backwards steps -- the
    cached date equals the local date at that instant."""
    clock = [datetime(2026, 3, 29, 1, 30).timestamp()]  # near a DST switch

    class _FakeDate(date):
        @classmethod
        def today(cls):
            return datetime.fromtimestamp(clock[0]).date()

    real_time, real_date = selectors.time, selectors.date
    selectors.time = SimpleNamespace(time=lambda: clock[0])
    selectors.date = _FakeDate
    selectors._today_cache = (date.min, 0.0, float("-inf"))
    try:
        for step in steps:
            clock[0] += step
            got = selectors._current_date()
            assert got == datetime.fromtimestamp(clock[0]).date()
    finally:
        selectors.time, selectors.date = real_time, real_date
        selectors._today_cache = (date.min, 0.0, float("-inf"))


def test_current_date_matches_date_today():
    assert selectors._current_date() == date.today()


# --- shared attrs flyweight --------------------------------------------------

_scalar = st.one_of(
    st.text(max_size=3),
    st.integers(-2, 2),
    st.booleans(),
    st.none(),
    st.sampled_from([0.0, -0.0, 1.5, math.inf, math.nan]),
)
_attrs = st.dictionaries(st.sampled_from(["k", "v", "w"]), _scalar, max_size=3)


def _repr(d):
    return [(k, type(v), v.hex() if type(v) is float else v) for k, v in d.items()]


@settings(max_examples=300, deadline=None)
@given(
    dicts=st.lists(
        st.one_of(_attrs, _attrs.map(lambda d: {**d, "l": [1]})), max_size=10
    )
)
def test_shared_attrs_preserve_content_and_share_only_equal(dicts):
    sv = SharedGraphValues()
    out = [sv._attrs_dict(dict(d)) for d in dicts]
    for original, shared in zip(dicts, out, strict=True):
        assert _repr(shared) == _repr(original)
    for i, a in enumerate(out):
        for j, b in enumerate(out):
            if a is b and i != j:
                assert _repr(dicts[i]) == _repr(dicts[j])
            if _repr(dicts[i]) == _repr(dicts[j]) and "l" not in dicts[i]:
                assert a is b
