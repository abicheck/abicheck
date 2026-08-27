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

`TestRealCollisionReachesCompareOutput` (Phase 5 of
``docs/contribute/plans/bug-class-regression-testing.md`` -- the
``matching.dedup_key_soundness`` bug class's own first known-gap entry)
closes one more layer than the rest of this file: every other test here
calls `_deduplicate_ast_dwarf`/`cross_tier_transition` directly, so none of
them proves a real, list-valued collision actually survives the FULL
`checker.compare()` pipeline `DeduplicateAstDwarf`'s post-processing step
runs inside -- a different post-processing step re-merging the two changes
downstream, or the finding never reaching this pass at all, would be
invisible to a test that calls the primitive in isolation.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.diff_filtering import _deduplicate_ast_dwarf
from abicheck.diff_helpers import cross_tier_transition
from abicheck.model import AbiSnapshot
from abicheck.model.python_facts import PythonExtMetadata


def _change(symbol: str, old: object, new: object, description: str = "d") -> Change:
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


class TestRealCollisionReachesCompareOutput:
    """A real, publicly-documented producer of list-valued findings
    (``diff_python._diff_stable_abi_violations``/``_diff_abi3_floor_raised``,
    both PEP-Limited-API "abi3" detectors) run through the actual
    ``checker.compare()`` entry point, not a hand-built `Change` fed
    directly into `_deduplicate_ast_dwarf`."""

    def test_two_distinct_list_valued_findings_both_survive_compare(self) -> None:
        # old: an abi3 build with no CPython imports yet (empty baseline).
        old = AbiSnapshot(
            library="libfoo.so",
            version="1",
            python_ext=PythonExtMetadata(
                module_name="foo",
                soabi_tag="abi3",
                limited_api=True,
                declared_abi3=(3, 9),
                cpython_imports=[],
            ),
        )
        # new: gains one genuinely private import (-> a "gained non-stable
        # imports" finding, new_value=["_PyDict_GetItemStringWithError"]) AND
        # one real stable symbol added after the declared 3.9 floor (-> an
        # "above floor" finding, new_value=["PyBuffer_FillInfo (added
        # 3.11)"]) -- two DISTINCT findings, same ChangeKind, each carrying
        # a different single-element list as its new_value.
        new = AbiSnapshot(
            library="libfoo.so",
            version="2",
            python_ext=PythonExtMetadata(
                module_name="foo",
                soabi_tag="abi3",
                limited_api=True,
                declared_abi3=(3, 9),
                cpython_imports=[
                    "_PyDict_GetItemStringWithError",
                    "PyBuffer_FillInfo",
                ],
            ),
        )

        result = compare(old, new)
        violations = [
            c
            for c in result.changes
            if c.kind == ChangeKind.PYTHON_STABLE_ABI_VIOLATION
        ]
        new_values = {tuple(c.new_value) for c in violations}  # type: ignore[misc]

        # Sanity: confirm the fixture actually produces two DIFFERENT list
        # values, not two copies of the same one -- otherwise a merge into
        # one survivor would be indistinguishable from correct behavior.
        assert new_values == {
            ("_PyDict_GetItemStringWithError",),
            ("PyBuffer_FillInfo (added 3.11)",),
        }
        assert len(violations) == 2
