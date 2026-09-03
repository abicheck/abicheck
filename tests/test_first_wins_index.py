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

"""Contract of the first-wins keyed index, stated as invariants.

``AbiSnapshot.index`` used to carry three hand-written copies of this loop.
Consolidating them into one primitive means the primitive itself now needs the
treatment AGENTS.md prescribes for a reusable merge/dedupe/grouping helper: a
standalone property-test class stating its contract, decoupled from the one
caller's domain, rather than only example tests written to confirm the
behaviour its author already had in mind.
"""

from __future__ import annotations

from collections import Counter

from hypothesis import given, strategies as st

from abicheck.model.first_wins_index import build_first_wins_index, describe_dropped

# (key, payload) pairs — the payload distinguishes two items sharing a key, so
# "which one won" is observable rather than collapsing into equality.
_ITEMS = st.lists(
    st.tuples(st.sampled_from(["a", "b", "c", "d"]), st.integers(0, 5)),
    max_size=40,
)


class TestBuildFirstWinsIndexProperties:
    """Invariants that hold for any input, not only the snapshot's own."""

    @given(_ITEMS)
    def test_every_key_present_exactly_once(self, items: list[tuple[str, int]]) -> None:
        result = build_first_wins_index(items, lambda item: item[0])
        assert set(result.mapping) == {key for key, _ in items}

    @given(_ITEMS)
    def test_the_first_item_to_claim_a_key_keeps_it(
        self, items: list[tuple[str, int]]
    ) -> None:
        result = build_first_wins_index(items, lambda item: item[0])
        for key, value in result.mapping.items():
            first = next(item for item in items if item[0] == key)
            assert value == first

    @given(_ITEMS)
    def test_dropped_counts_the_additional_claimants_only(
        self, items: list[tuple[str, int]]
    ) -> None:
        result = build_first_wins_index(items, lambda item: item[0])
        counts = Counter(key for key, _ in items)
        expected = {key: n - 1 for key, n in counts.items() if n > 1}
        assert result.dropped == expected

    @given(_ITEMS)
    def test_a_key_claimed_once_is_absent_from_dropped(
        self, items: list[tuple[str, int]]
    ) -> None:
        # ``if result.dropped`` is the caller's "were there duplicates" test, so
        # a zero entry would make an unambiguous input report duplicates.
        result = build_first_wins_index(items, lambda item: item[0])
        assert all(count > 0 for count in result.dropped.values())

    @given(_ITEMS)
    def test_mapping_is_in_first_appearance_order(
        self, items: list[tuple[str, int]]
    ) -> None:
        result = build_first_wins_index(items, lambda item: item[0])
        seen: list[str] = []
        for key, _ in items:
            if key not in seen:
                seen.append(key)
        assert list(result.mapping) == seen

    @given(_ITEMS)
    def test_nothing_is_both_kept_and_reported_as_total_loss(
        self, items: list[tuple[str, int]]
    ) -> None:
        # Every item is accounted for exactly once: kept, or counted as dropped.
        result = build_first_wins_index(items, lambda item: item[0])
        assert len(result.mapping) + sum(result.dropped.values()) == len(items)

    @given(_ITEMS)
    def test_indexing_an_already_indexed_mapping_drops_nothing(
        self, items: list[tuple[str, int]]
    ) -> None:
        first = build_first_wins_index(items, lambda item: item[0])
        second = build_first_wins_index(first.mapping.values(), lambda item: item[0])
        assert second.mapping == first.mapping
        assert second.dropped == {}

    def test_empty_input_produces_empty_result(self) -> None:
        result = build_first_wins_index([], lambda item: item)
        assert result.mapping == {}
        assert result.dropped == {}

    def test_a_key_function_can_collapse_distinct_items(self) -> None:
        # The key is whatever the caller says it is; identity of the item plays
        # no part in whether two items collide.
        result = build_first_wins_index(["alpha", "avocado", "beta"], lambda s: s[0])
        assert result.mapping == {"a": "alpha", "b": "beta"}
        assert result.dropped == {"a": 1}


class TestDescribeDropped:
    """The ledger renders as a count of *total* declarations, not of losses."""

    def test_reports_the_total_count_per_key(self) -> None:
        assert describe_dropped({"_ZN3fooEv": 1}) == "_ZN3fooEv (×2)"

    def test_joins_several_keys_in_ledger_order(self) -> None:
        assert describe_dropped({"a": 1, "b": 2}) == "a (×2), b (×3)"

    def test_an_empty_ledger_renders_empty(self) -> None:
        assert describe_dropped({}) == ""

    @given(_ITEMS)
    def test_every_dropped_key_is_named_in_the_message(
        self, items: list[tuple[str, int]]
    ) -> None:
        result = build_first_wins_index(items, lambda item: item[0])
        message = describe_dropped(result.dropped)
        assert all(key in message for key in result.dropped)
