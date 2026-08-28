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

Two distinct engine-level layers, closed by two distinct classes below
(Codex review, PR #905 -- an earlier draft of this file conflated them):

- `_dedup_cross_kind` only ever calls `cross_tier_transition` for a change
  whose kind is one of `_DWARF_TO_AST_EQUIV`'s keys/values (the five
  struct/type size-alignment-field kinds it bridges AST vs. DWARF findings
  for) -- every other kind, `PYTHON_STABLE_ABI_VIOLATION` included, never
  reaches this function's own `cross_tier_transition` calls at all,
  regardless of what its value looks like. `TestListValuedFindingSurvives
  TheRealDedupCrossKind` proves the mechanism itself is safe when it *is*
  reached, using one of those five in-scope kinds (no real producer emits a
  list-valued transition for them today -- this states the primitive's
  contract for the slot regardless of which kind currently exercises it,
  the same "don't test only the input you already thought of" reasoning
  `tests/unit/compare/test_dedup_key.py`'s own module docstring states).
- `TestRealCollisionReachesCompareOutput` proves something narrower but
  still real: a genuine, non-synthetic producer
  (`diff_python._diff_stable_abi_violations`/`_diff_abi3_floor_raised`)
  emitting two distinct list-valued findings survives the *whole*
  `checker.compare()` post-processing pipeline uncollapsed -- not
  specifically `_dedup_cross_kind`'s own transition set, since
  `PYTHON_STABLE_ABI_VIOLATION` never reaches that one, but every other
  post-processing step `compare()` runs (`_dedup_exact`'s (kind,
  description) key, `_deduplicate_cross_detector`'s identity-based key,
  and anything downstream), which a test that only calls
  `_deduplicate_ast_dwarf`/`cross_tier_transition` directly cannot rule out
  either mishandling or re-merging the same list value.
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


def _change(
    symbol: str,
    old: object,
    new: object,
    description: str = "d",
    kind: ChangeKind = ChangeKind.FUNC_PARAMS_CHANGED,
) -> Change:
    c = Change(
        kind=kind,
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


class TestListValuedFindingSurvivesTheRealDedupCrossKind:
    """`_dedup_cross_kind` itself, driven with an in-scope kind.

    Unlike the module-level tests above (which use the default
    ``FUNC_PARAMS_CHANGED`` -- a kind `_dedup_cross_kind` never indexes at
    all, so those tests exercise `_deduplicate_ast_dwarf`'s outer dedup
    passes and `cross_tier_transition` directly, not this specific
    function's own transition-set logic), these use
    ``TYPE_SIZE_CHANGED``/``STRUCT_SIZE_CHANGED`` -- a real key/value pair
    from ``_DWARF_TO_AST_EQUIV`` -- so `cross_tier_transition` is reached
    via `_dedup_cross_kind`'s own index-build (line ~1395) and match/drop
    decision (line ~1424), the exact call sites Codex review (PR #905)
    named as unreached by the compare()-level test below.

    A malformed (non-string) value for one of these kinds is deliberately
    never treated as *agreeing* across tiers, even when the raw content is
    byte-identical -- `STRUCT_SIZE_CHANGED` denotes bytes and
    `TYPE_SIZE_CHANGED` denotes bits, and the well-formed path's byte*8
    conversion is exactly what a malformed value skips, so there is no safe
    way to compare the two. A second Codex review round on the crash fix
    below caught an earlier revision of this class doing precisely that --
    matching two malformed transitions purely because their raw lists
    happened to be equal.
    """

    def test_identical_malformed_transitions_never_match_across_tiers(
        self,
    ) -> None:
        """A DWARF (bytes) and an AST (bits) finding sharing the exact same
        RAW list content must NOT be treated as agreeing, even though
        `_matches` would call them equal by content alone.

        `STRUCT_SIZE_CHANGED`'s well-formed comparison multiplies a byte
        count by 8 before comparing against `TYPE_SIZE_CHANGED`'s bit
        count -- a malformed (non-string) value skips that conversion
        entirely, so the same raw ``["64"]`` denotes two different
        quantities depending on which tier it came from. Matching them
        anyway (Codex review, PR #905: an earlier revision of this fix did
        exactly that, falling back to a bare `hashable_value` pair with no
        tier tag) would silently drop the DWARF finding as "redundant" when
        the two tiers were never actually shown to agree.
        """
        result = _deduplicate_ast_dwarf(
            [
                _change(
                    "Widget",
                    ["same-raw-content"],
                    ["same-raw-content"],
                    description="ast",
                    kind=ChangeKind.TYPE_SIZE_CHANGED,
                ),
                _change(
                    "Widget",
                    ["same-raw-content"],
                    ["same-raw-content"],
                    description="dwarf",
                    kind=ChangeKind.STRUCT_SIZE_CHANGED,
                ),
            ]
        )

        assert {c.kind for c in result} == {
            ChangeKind.TYPE_SIZE_CHANGED,
            ChangeKind.STRUCT_SIZE_CHANGED,
        }

    def test_disagreeing_list_valued_transitions_are_not_over_merged(
        self,
    ) -> None:
        """A DWARF finding whose list-valued transition DISAGREES with the
        AST-tier finding for the same symbol must survive independently --
        collapsing it here would be the over-merge this whole class exists
        to prevent."""
        result = _deduplicate_ast_dwarf(
            [
                _change(
                    "Widget",
                    ["old-repr"],
                    ["new-repr"],
                    description="ast",
                    kind=ChangeKind.TYPE_SIZE_CHANGED,
                ),
                _change(
                    "Widget",
                    ["different-old"],
                    ["different-new"],
                    description="dwarf",
                    kind=ChangeKind.STRUCT_SIZE_CHANGED,
                ),
            ]
        )

        assert {c.kind for c in result} == {
            ChangeKind.TYPE_SIZE_CHANGED,
            ChangeKind.STRUCT_SIZE_CHANGED,
        }

    def test_a_malformed_dwarf_finding_still_survives_without_crashing(
        self,
    ) -> None:
        """The crash-safety half, isolated from the unit-tagging half above:
        a single malformed byte-value finding must not crash and must not
        vanish, even with no AST-tier counterpart present at all."""
        result = _deduplicate_ast_dwarf(
            [
                _change(
                    "Widget",
                    ["old-repr"],
                    ["new-repr"],
                    kind=ChangeKind.STRUCT_SIZE_CHANGED,
                ),
            ]
        )

        assert [c.kind for c in result] == [ChangeKind.STRUCT_SIZE_CHANGED]


class TestRealCollisionReachesCompareOutput:
    """A real, publicly-documented producer of list-valued findings
    (``diff_python._diff_stable_abi_violations``/``_diff_abi3_floor_raised``,
    both PEP-Limited-API "abi3" detectors) run through the actual
    ``checker.compare()`` entry point, not a hand-built `Change` fed
    directly into `_deduplicate_ast_dwarf`.

    Deliberately does NOT claim to reach `_dedup_cross_kind`'s own
    `cross_tier_transition` calls -- `PYTHON_STABLE_ABI_VIOLATION` is not
    one of `_DWARF_TO_AST_EQUIV`'s keys/values, so it never does (see
    `TestListValuedFindingSurvivesTheRealDedupCrossKind` above for that).
    What this proves instead: the *rest* of `compare()`'s post-processing
    pipeline -- `_dedup_exact`, `_deduplicate_cross_detector`, and anything
    else a future change adds -- doesn't crash on or silently re-merge a
    real, non-synthetic producer's list-valued findings either."""

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
