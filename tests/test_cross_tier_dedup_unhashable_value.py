# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors
"""The cross-tier dedup must key any finding a detector can produce.

`diff_filtering._dedup_cross_kind` puts `cross_tier_transition(change)` into
a `set`. `Change.old_value`/`new_value` are annotated `str | None`, but the
annotation is not enforced: `diff_python.py` stores lists there at seven
sites, and `reporter.py` serializes those as JSON arrays, so the list is the
published contract rather than a producer bug to correct. A finding shaped
that way used to raise `TypeError: unhashable type: 'list'` and abort the
whole comparison.

The primitive's own contract is covered in
`tests/unit/compare/test_dedup_key.py`; these tests pin the caller-level
consequences the primitive alone cannot state.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.diff_filtering import _deduplicate_ast_dwarf
from abicheck.diff_helpers import cross_tier_transition


def _change(
    symbol: str, old: object, new: object, description: str = "d"
) -> Change:
    c = Change(
        kind=ChangeKind.FUNC_PARAMS_CHANGED,
        symbol=symbol,
        description=description,
    )
    # Deliberately bypassing the annotation: the point is that the
    # annotation is not what actually reaches this code at runtime.
    c.old_value = old  # type: ignore[assignment]
    c.new_value = new  # type: ignore[assignment]
    return c


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(["a", "b"], id="list"),
        pytest.param([], id="empty-list"),
        pytest.param({"a": 1}, id="dict"),
    ],
)
def test_dedup_survives_a_non_scalar_value_slot(value: object) -> None:
    """The reported crash: dedup aborted the entire comparison."""
    result = _deduplicate_ast_dwarf([_change("sym", value, None)])

    assert [c.symbol for c in result] == ["sym"]


def test_the_transition_key_is_hashable_for_a_list_value() -> None:
    hash(cross_tier_transition(_change("sym", ["a"], ["b"])))


def test_findings_differing_only_in_a_list_value_stay_distinct() -> None:
    """Keying must not over-merge: two real findings, two survivors."""
    # Distinct descriptions, so the earlier exact-dedup pass (which keys on
    # kind+description) cannot be what merges them -- the value slot is.
    result = _deduplicate_ast_dwarf(
        [
            _change("sym", ["a"], None, description="a"),
            _change("sym", ["b"], None, description="b"),
        ]
    )

    assert len(result) == 2


def test_a_scalar_value_slot_still_keys_as_itself() -> None:
    assert cross_tier_transition(_change("sym", "old", "new")) == ("old", "new")
