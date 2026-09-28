# SPDX-License-Identifier: Apache-2.0
"""Property tests for three hot-path rewrites that must not change results.

Each rewrite replaced a per-call recomputation with an index or cache; the
oracle in every case is a direct restatement of the pre-rewrite logic, not
the new code path, so a divergence in the new structure shows up here.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from hypothesis import example, given, settings, strategies as st

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
    selectors.time = SimpleNamespace(
        time=lambda: clock[0],
        timezone=real_time.timezone,
        altzone=real_time.altzone,
        tzname=real_time.tzname,
    )
    selectors.date = _FakeDate
    selectors._today_cache = (date.min, 0.0, float("-inf"), None)
    try:
        for step in steps:
            clock[0] += step
            got = selectors._current_date()
            assert got == datetime.fromtimestamp(clock[0]).date()
    finally:
        selectors.time, selectors.date = real_time, real_date
        selectors._today_cache = (date.min, 0.0, float("-inf"), None)


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


def test_current_date_rederives_after_a_timezone_change():
    """A stale zone in the cache is never trusted, even inside its window."""
    import time as _time

    selectors._current_date()
    today, start, end, _zone = selectors._today_cache
    selectors._today_cache = (today - timedelta(days=1), start, end, ("stale",))
    try:
        assert selectors._current_date() == date.today()
        assert selectors._today_cache[3] == (
            _time.timezone,
            _time.altzone,
            _time.tzname,
        )
    finally:
        selectors._today_cache = (date.min, 0.0, float("-inf"), None)


# --- sorted-slice trie builder ------------------------------------------------

from typing import Any  # noqa: E402

from abicheck.compare import spelling_pattern  # noqa: E402
from abicheck.storage import closure_identity  # noqa: E402

_END = object()


def _dict_trie_body(node: dict[Any, Any], depth: int) -> str:
    """The dict-trie builder the sorted-slice one replaced, verbatim in shape."""
    if depth > spelling_pattern._MAX_TRIE_DEPTH:
        raise spelling_pattern._TrieTooDeep
    literal: list[str] = []
    while _END not in node and len(node) == 1:
        (edge,) = node
        literal.append(re.escape(edge))
        node = node[edge]
    prefix = "".join(literal)
    alternatives = [
        re.escape(edge) + _dict_trie_body(node[edge], depth + 1)
        for edge in sorted(k for k in node if k is not _END)
    ]
    if not alternatives:
        return prefix
    group = (
        alternatives[0]
        if len(alternatives) == 1
        else "(?:" + "|".join(alternatives) + ")"
    )
    if _END in node:
        return prefix + "(?:" + group + ")?"
    return prefix + group


def _dict_trie_pattern_text(spellings) -> str | None:
    root: dict[Any, Any] = {}
    for spelling in spellings:
        node = root
        for ch in spelling:
            node = node.setdefault(ch, {})
        node[_END] = True
    try:
        return spelling_pattern._bounded(_dict_trie_body(root, 0))
    except spelling_pattern._TrieTooDeep:
        return None


_spelling_alphabet = st.sampled_from(list("ab:_<>*&, .()[]$^\\1"))
_vocab = st.sets(st.text(_spelling_alphabet, max_size=6), min_size=1, max_size=25)


@settings(max_examples=500, deadline=None)
@given(vocab=_vocab)
def test_sorted_trie_builds_the_dict_trie_pattern_text(vocab):
    """Byte-identical pattern text, so matching (and ``re``'s compile
    cache) is unchanged -- including the empty spelling, prefix chains,
    regex metacharacters and prefix-related spellings."""
    expected = _dict_trie_pattern_text(vocab)
    built = spelling_pattern._build_spelling_pattern(vocab)
    assert expected is not None and built is not None
    assert built.pattern == expected


def test_sorted_trie_too_deep_still_falls_back_to_flat():
    deep = {"<" * i + "x" for i in range(spelling_pattern._MAX_TRIE_DEPTH + 5)}
    deep |= {"<" * i + "y" for i in range(spelling_pattern._MAX_TRIE_DEPTH + 5)}
    built = spelling_pattern._build_spelling_pattern(deep)
    flat = spelling_pattern._build_flat_spelling_pattern(deep)
    assert _dict_trie_pattern_text(deep) is None
    assert built is not None and flat is not None and built.pattern == flat.pattern


# --- anonymous-marker scan ---------------------------------------------------


def _old_scan(name: str, prefix_match):
    depth = 0
    i = prefix_match.end()
    last = None
    while i < len(name):
        ch = name[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                if closure_identity._ANON_TYPE_TRAILING_LINE_COL_RE.search(
                    name[prefix_match.end() : i]
                ):
                    last = i
            else:
                depth -= 1
        i += 1
    if last is not None:
        return closure_identity._anon_type_match_from_close_paren(
            name, prefix_match, last
        )
    for i in range(prefix_match.end(), len(name)):
        if name[i] == ")" and closure_identity._ANON_TYPE_TRAILING_LINE_COL_RE.search(
            name[prefix_match.end() : i]
        ):
            return closure_identity._anon_type_match_from_close_paren(
                name, prefix_match, i
            )
    return None


def _old_matches(name: str):
    if "(" not in name:
        return []
    spans = closure_identity._quoted_spans(name)
    out, consumed = [], -1
    for prefix in closure_identity._ANON_TYPE_MARKER_PREFIX_RE.finditer(name):
        if prefix.start() < consumed:
            continue
        if any(a <= prefix.start() < b for a, b in spans):
            continue
        m = _old_scan(name, prefix)
        if m is not None:
            out.append(m)
            consumed = m.end
    return out


_marker_parts = st.sampled_from(
    [
        "(lambda:",
        "(unnamed struct:",
        "(anonymous union:",
        "a.h",
        "b(c.hpp",
        "x)y.h",
        ":1:2",
        ":10:3",
        ":1:2)",
        "a.h:3:4)",
        " ",
        ")",
        "(",
        "<",
        ">",
        '"',
        "\\",
        "::",
        "foo",
    ]
)


@settings(max_examples=3000, deadline=None)
@given(parts=st.lists(_marker_parts, max_size=14))
@example(parts=["(lambda:", "foo:1:2)", "bar.hpp:10:2)"])
@example(parts=["(lambda:", "b(c.hpp", ":1:2)", "(lambda:", "a.h:3:4)"])
@example(parts=["(unnamed struct:", "x)y.h", ":10:3", ")"])
def test_anon_marker_scan_matches_the_character_loop(parts):
    name = "".join(parts)
    expected = _old_matches(name)
    assert list(closure_identity._anon_type_ordinal_matches(name)) == expected
    # And the memoized answer is the same on a repeat.
    assert list(closure_identity._anon_type_ordinal_matches(name)) == expected
