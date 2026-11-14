# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""A multi-TU manifest merge carries the ``using std::X;`` re-export table.

Bug class: a merged snapshot dropped every TU's re-export table, so the
manifest path recorded it as not modelled. The oracle is a hand-written
expectation per TU combination, never the merge helper itself.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.tu_fragment import TuFragment
from abicheck.tu_merge import merge_fragments

ABS = {"lib::abs": "std::abs"}
STRING = {"lib::string": "std::string"}


def _tu(name: str, table: dict[str, str] | None) -> TuFragment:
    return TuFragment(tu_name=name, std_reexports=table)


@pytest.mark.parametrize(
    ("tables", "expected"),
    [
        ([ABS], ABS),
        ([ABS, STRING], {**ABS, **STRING}),
        ([ABS, ABS], ABS),
        ([{}, {}], {}),
        ([ABS, {}], ABS),
        # an unmodelled TU may hold a re-export the others lack
        ([ABS, None], None),
        ([None, None], None),
        # one name bound to different std targets: not established
        ([ABS, {"lib::abs": "std::__1::abs"}], None),
    ],
)
def test_the_merged_table_is_the_union_or_uncertain(tables, expected) -> None:
    for order in itertools.permutations(range(len(tables))):
        frags = [_tu(f"tu{i}", tables[i]) for i in order]
        assert merge_fragments(frags).std_reexports == expected


def test_no_fragments_is_not_modelled() -> None:
    assert merge_fragments([]).std_reexports is None
