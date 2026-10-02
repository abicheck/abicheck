# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Laws of the one evidence-merge rule (``abicheck.model.evidence_merge``).

The oracle below is written from the rule's *statement* (a truth table over
"what did each reading say"), not from the implementation: it classifies
every reading into one letter and decides the merged letter from the set of
letters, with no fold and no status priority loop.
"""

from __future__ import annotations

import itertools
from functools import reduce

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.model.availability import FactStatus
from abicheck.model.evidence_merge import (
    is_completed_read,
    merge_collection,
    merge_presence,
    merged_capture_fact,
    presence_in,
)
from abicheck.model.fact import Fact

S = FactStatus
_UNKNOWN_RANK = {S.FAILED: 0, S.UNSUPPORTED: 1, S.PARTIAL: 2, S.NOT_COLLECTED: 3}


def _reading(
    status: FactStatus, value: bool | None, diag: str = "", producer: str | None = None
) -> Fact[bool]:
    diags = (diag,) if diag else ()
    if status in (S.PRESENT, S.PARTIAL):
        return Fact._make(status, value, diags, producer)
    return Fact._make(status, None, diags, producer)


# Every distinct bool reading shape (status x value), duplicates dropped.
_BOOL_SHAPES = sorted(
    {
        (st_, v if st_ in (S.PRESENT, S.PARTIAL) else None)
        for st_ in S
        for v in (True, False, None)
    },
    key=lambda p: (p[0].value, str(p[1])),
)


def _letter(fact: Fact[bool]) -> str:
    """Y = says yes; N = completed read saying no; A = not applicable; ? = unknown."""
    if fact.status in (S.PRESENT, S.PARTIAL) and fact.value is True:
        return "Y"
    if fact.status is S.PRESENT and fact.value is False:
        return "N"
    if fact.status is S.NOT_APPLICABLE:
        return "A"
    return "?"


def _oracle_presence(readings: list[Fact[bool]]) -> tuple[str, FactStatus | None]:
    letters = {_letter(f) for f in readings}
    if "Y" in letters:
        return "Y", S.PRESENT
    if letters == {"A"}:
        return "A", S.NOT_APPLICABLE
    if letters - {"A"} == {"N"}:
        return "N", S.PRESENT
    unknown = [
        f.status for f in readings if _letter(f) == "?" and f.status in _UNKNOWN_RANK
    ]
    expected = (
        min(unknown, key=_UNKNOWN_RANK.__getitem__) if unknown else S.NOT_COLLECTED
    )
    return "?", expected


def _observed(fact: Fact[bool]) -> tuple[str, FactStatus]:
    return _letter(fact), fact.status


def _key(fact: Fact[object]) -> tuple[object, ...]:
    value = fact.value
    if isinstance(value, frozenset):
        value = tuple(sorted(value))
    return (fact.status, value, fact.diagnostics, fact.producer)


class TestPresenceExhaustive:
    @pytest.mark.parametrize("n", [1, 2, 3])
    def test_matches_oracle_for_every_shape_combination(self, n: int) -> None:
        mismatches = []
        for combo in itertools.product(_BOOL_SHAPES, repeat=n):
            readings = [_reading(s, v) for s, v in combo]
            got = _observed(merge_presence(*readings))
            if got != _oracle_presence(readings):
                mismatches.append((combo, got))
        assert not mismatches, mismatches[:5]

    def test_oracle_is_not_vacuous(self) -> None:
        outcomes = {
            _oracle_presence([_reading(*a), _reading(*b)])
            for a, b in itertools.product(_BOOL_SHAPES, repeat=2)
        }
        assert {o[0] for o in outcomes} == {"Y", "N", "A", "?"}
        assert len({o[1] for o in outcomes}) >= 5

    def test_commutative_and_associative_and_idempotent(self) -> None:
        shapes = [
            _reading(s, v, d, p)
            for (s, v) in _BOOL_SHAPES
            for d in ("", "x")
            for p in (None, "p")
        ]
        for a, b in itertools.product(shapes, repeat=2):
            assert _key(merge_presence(a, b)) == _key(merge_presence(b, a))
            assert _key(merge_presence(a, a)) == _key(merge_presence(a))
            assert _key(merge_presence(merge_presence(a))) == _key(merge_presence(a))
        bare = [_reading(s, v) for (s, v) in _BOOL_SHAPES]
        for a, b, c in itertools.product(bare, repeat=3):
            left = merge_presence(merge_presence(a, b), c)
            right = merge_presence(a, merge_presence(b, c))
            flat = merge_presence(a, b, c)
            assert _key(left) == _key(right) == _key(flat), (a, b, c)

    def test_absent_never_manufactured_from_unknown(self) -> None:
        bare = [_reading(s, v) for (s, v) in _BOOL_SHAPES]
        for combo in itertools.product(bare, repeat=3):
            merged = merge_presence(*combo)
            if merged.status is S.PRESENT and merged.value is False:
                assert all(
                    is_completed_read(f) or f.status is S.NOT_APPLICABLE for f in combo
                ), combo

    def test_unknown_plus_absent_is_unknown(self) -> None:
        for unknown in (S.NOT_COLLECTED, S.UNSUPPORTED, S.FAILED):
            merged = merge_presence(Fact.present(False), _reading(unknown, None))
            assert merged.status is unknown and merged.value is None

    def test_empty_merge_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            merge_presence()


_ITEMS = st.sampled_from("abcd")
_COLLECTION_STATUS = st.sampled_from(list(S))


@st.composite
def _collection_reading(draw: st.DrawFn) -> Fact[frozenset[str]]:
    status = draw(_COLLECTION_STATUS)
    diag = draw(st.sampled_from(["", "d1", "d2"]))
    producer = draw(st.sampled_from([None, "castxml", "clang"]))
    diags = (diag,) if diag else ()
    if status in (S.PRESENT, S.PARTIAL):
        return Fact._make(status, frozenset(draw(st.sets(_ITEMS))), diags, producer)
    return Fact._make(status, None, diags, producer)


def _oracle_membership(readings: list[Fact[frozenset[str]]], item: str) -> str:
    """Y if any usable reading holds item; N only if every applicable reading completed."""
    if any(
        f.status in (S.PRESENT, S.PARTIAL) and item in (f.value or ()) for f in readings
    ):
        return "Y"
    applicable = [f for f in readings if f.status is not S.NOT_APPLICABLE]
    if not applicable:
        return "A"
    if all(f.status is S.PRESENT for f in applicable):
        return "N"
    return "?"


class TestCollectionProperties:
    @settings(max_examples=400, deadline=None)
    @given(st.lists(_collection_reading(), min_size=1, max_size=4), _ITEMS)
    def test_membership_matches_oracle(
        self, readings: list[Fact[frozenset[str]]], item: str
    ) -> None:
        answer = presence_in(merge_collection(*readings), item)
        expected = _oracle_membership(readings, item)
        if expected == "Y":
            assert answer.status is S.PRESENT and answer.value is True
        elif expected == "N":
            assert answer.status is S.PRESENT and answer.value is False
        elif expected == "A":
            assert answer.status is S.NOT_APPLICABLE
        else:
            assert answer.value is None and answer.status is not S.PRESENT

    @settings(max_examples=300, deadline=None)
    @given(_collection_reading(), _collection_reading(), _collection_reading())
    def test_laws(
        self, a: Fact[frozenset[str]], b: Fact[frozenset[str]], c: Fact[frozenset[str]]
    ) -> None:
        assert _key(merge_collection(a, b)) == _key(merge_collection(b, a))
        assert _key(merge_collection(a, a)) == _key(merge_collection(a))
        assert _key(merge_collection(merge_collection(a, b), c)) == _key(
            merge_collection(a, merge_collection(b, c))
        )
        assert _key(reduce(lambda x, y: merge_collection(x, y), [a, b, c])) == _key(
            merge_collection(a, b, c)
        )

    def test_unread_side_never_yields_absence(self) -> None:
        read = Fact.present(frozenset({"a"}))
        for unknown in (S.NOT_COLLECTED, S.UNSUPPORTED, S.FAILED):
            merged = merge_collection(read, Fact._make(unknown, None, (), None))
            assert merged.status is S.PARTIAL
            assert presence_in(merged, "a").value is True
            assert presence_in(merged, "b").value is None
        both_read = merge_collection(read, Fact.present(frozenset()))
        assert both_read.status is S.PRESENT
        assert presence_in(both_read, "b").value is False


class TestMergedCaptureFact:
    """``merged_capture_fact``: the cross-TU ``contract_attributes`` merge."""

    @pytest.mark.parametrize(
        ("captures", "status"),
        [
            ((None, None), S.NOT_COLLECTED),
            ((["a"], None), S.PARTIAL),
            ((None, []), S.PARTIAL),
            ((["a"], []), S.PRESENT),
            (([], []), S.PRESENT),
        ],
    )
    def test_status_follows_merge_collection(
        self, captures: tuple[list[str] | None, ...], status: FactStatus
    ) -> None:
        merged = None if all(c is None for c in captures) else ["a"]
        fact = merged_capture_fact("some", merged, *captures)
        assert fact.status is status
        assert fact.value == (merged if status is not S.NOT_COLLECTED else None)

    def test_tu_merge_marks_one_sided_capture_partial(self) -> None:
        from abicheck.model import Function
        from abicheck.tu_merge import _merge_functions

        captured = Function(
            name="f", mangled="f", return_type="int", contract_attributes=["nodiscard"]
        )
        uncaptured = Function(
            name="f", mangled="f", return_type="int", contract_attributes=None
        )
        merged = _merge_functions(
            captured, uncaptured, header_segs=[], dir_segs=[], have_public_set=False
        )
        assert merged is not None and merged.contract_attributes_fact is not None
        assert merged.contract_attributes_fact.status is S.PARTIAL
        both = _merge_functions(
            captured, captured, header_segs=[], dir_segs=[], have_public_set=False
        )
        assert both is not None and both.contract_attributes_fact is not None
        assert both.contract_attributes_fact.status is S.PRESENT
