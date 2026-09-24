"""Invariants of the per-detector-pass memo and the memoized helpers."""

from __future__ import annotations

import random

from abicheck.compare.detection_memo import detection_memo_scope, memoized
from abicheck.diff_helpers import depth_aware_bare_name


class _Subject:
    pass


def test_outside_scope_every_call_computes():
    calls = []
    s = _Subject()
    for _ in range(3):
        memoized("n", s, None, lambda: calls.append(1) or len(calls))
    assert len(calls) == 3


def test_inside_scope_same_key_computes_once_distinct_keys_separately():
    calls = []

    def compute(tag):
        calls.append(tag)
        return tag

    a, b = _Subject(), _Subject()
    with detection_memo_scope():
        for _ in range(4):
            assert memoized("n", a, 1, lambda: compute("a1")) == "a1"
            assert memoized("n", a, 2, lambda: compute("a2")) == "a2"
            assert memoized("n", b, 1, lambda: compute("b1")) == "b1"
            assert memoized("m", a, 1, lambda: compute("ma1")) == "ma1"
    assert sorted(calls) == ["a1", "a2", "b1", "ma1"]


def test_scope_is_discarded_when_pass_ends():
    calls = []
    s = _Subject()
    for _ in range(2):
        with detection_memo_scope():
            memoized("n", s, None, lambda: calls.append(1))
    assert len(calls) == 2


def test_nested_scope_shares_outer_memo():
    calls = []
    s = _Subject()
    with detection_memo_scope():
        memoized("n", s, None, lambda: calls.append(1) or 1)
        with detection_memo_scope():
            memoized("n", s, None, lambda: calls.append(1) or 1)
        memoized("n", s, None, lambda: calls.append(1) or 1)
    assert len(calls) == 1


def _reference_bare_name(q: str) -> str:
    # Independent oracle: split on '::' only at bracket depth 0.
    depth, last = 0, 0
    i = 0
    while i < len(q):
        ch = q[i]
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth = max(depth - 1, 0)
        elif ch == ":" and depth == 0 and q[i + 1 : i + 2] == ":":
            last = i + 2
            i += 1
        i += 1
    return q[last:]


def test_cached_bare_name_matches_uncached_and_oracle_on_generated_names():
    rng = random.Random(1234)
    atoms = ["a", "ns", "T", "std", "Wrapper<dep::Tag>", "f(int)", "X[3]"]
    names = [
        "::".join(rng.choice(atoms) for _ in range(rng.randint(1, 5)))
        for _ in range(500)
    ]
    uncached = depth_aware_bare_name.__wrapped__
    for n in names + names:  # second round is served from the cache
        assert depth_aware_bare_name(n) == uncached(n) == _reference_bare_name(n)


def test_stdlib_reachability_scan_runs_once_per_snapshot_per_pass(monkeypatch):
    from abicheck import type_reachability as tr
    from abicheck.model import AbiSnapshot

    calls = []
    real = tr._directly_referenced_stdlib_types_uncached

    def spy(snapshot, **kw):
        calls.append((id(snapshot), kw["exclude_export_only_roots"]))
        return real(snapshot, **kw)

    monkeypatch.setattr(tr, "_directly_referenced_stdlib_types_uncached", spy)
    old, new = (
        AbiSnapshot(library="l", version="1"),
        AbiSnapshot(library="l", version="2"),
    )
    with detection_memo_scope():
        for _ in range(5):
            tr.directly_referenced_stdlib_types(old)
            tr.directly_referenced_stdlib_types(new)
            tr.directly_referenced_stdlib_types(old, exclude_export_only_roots=True)
    assert len(calls) == 3
