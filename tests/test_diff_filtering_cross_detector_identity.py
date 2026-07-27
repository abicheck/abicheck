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

"""ADR-049 Phase 2: tests for the identity-based ``_deduplicate_cross_detector``.

Covers the wiring landed in ``diff_filtering.py`` -- the dedup key is now
``finding_identity.resolve_change_identity(c).primary_id`` instead of a
hand-rolled ``(change_category, symbol)`` tuple. These tests pin the exact
collapsing behavior the old tuple key already had (verified against the
pre-wiring implementation): same category + same symbol collapses; same
category + different symbol does not; different category + same symbol does
not; first occurrence wins; and the pre-existing symbol-version-node/alias
special case (independent of this dedup key) is unaffected.
"""

from __future__ import annotations

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import SYMBOL_VERSION_ALIAS_NOT_RETAINED_MARKER, Change
from abicheck.diff_filtering import _deduplicate_cross_detector


def _change(kind: ChangeKind, symbol: str, description: str = "") -> Change:
    return Change(kind=kind, symbol=symbol, description=description)


class TestSameCategorySameSymbolCollapses:
    def test_rich_and_l0_func_removal_collapse(self) -> None:
        changes = [
            _change(ChangeKind.FUNC_REMOVED, "_Z3foov", "rich"),
            _change(ChangeKind.FUNC_REMOVED_ELF_ONLY, "_Z3foov", "l0"),
        ]
        result = _deduplicate_cross_detector(changes)
        assert len(result) == 1
        assert result[0].description == "rich"  # first occurrence wins

    def test_duplicate_func_added_collapses(self) -> None:
        changes = [
            _change(ChangeKind.FUNC_ADDED, "_Z3barv", "first"),
            _change(ChangeKind.FUNC_ADDED, "_Z3barv", "second"),
        ]
        result = _deduplicate_cross_detector(changes)
        assert len(result) == 1
        assert result[0].description == "first"

    def test_duplicate_var_removed_collapses(self) -> None:
        changes = [
            _change(ChangeKind.VAR_REMOVED, "g_x"),
            _change(ChangeKind.VAR_REMOVED, "g_x"),
        ]
        assert len(_deduplicate_cross_detector(changes)) == 1

    def test_duplicate_var_added_collapses(self) -> None:
        changes = [
            _change(ChangeKind.VAR_ADDED, "g_y"),
            _change(ChangeKind.VAR_ADDED, "g_y"),
        ]
        assert len(_deduplicate_cross_detector(changes)) == 1

    def test_version_node_and_defined_removal_collapse(self) -> None:
        changes = [
            _change(ChangeKind.SYMBOL_VERSION_NODE_REMOVED, "GLIBC_2.17", "node"),
            _change(ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED, "GLIBC_2.17", "defined"),
        ]
        result = _deduplicate_cross_detector(changes)
        assert len(result) == 1
        assert result[0].description == "node"


class TestDistinctFindingsAreConserved:
    def test_same_category_different_symbol_both_kept(self) -> None:
        changes = [
            _change(ChangeKind.FUNC_REMOVED, "_Z3foov"),
            _change(ChangeKind.FUNC_REMOVED, "_Z3barv"),
        ]
        assert len(_deduplicate_cross_detector(changes)) == 2

    def test_different_category_same_symbol_both_kept(self) -> None:
        # FUNC_REMOVED ("func_removal") and FUNC_ADDED ("func_addition") never
        # collapse even if they happened to share a raw symbol string.
        changes = [
            _change(ChangeKind.FUNC_REMOVED, "_Z3foov"),
            _change(ChangeKind.FUNC_ADDED, "_Z3foov"),
        ]
        assert len(_deduplicate_cross_detector(changes)) == 2

    def test_non_dedup_eligible_kind_never_collapses(self) -> None:
        changes = [
            _change(ChangeKind.FUNC_RETURN_CHANGED, "_Z3foov"),
            _change(ChangeKind.FUNC_RETURN_CHANGED, "_Z3foov"),
        ]
        # Not one of the cross-detector-equivalent kinds this dedup targets --
        # both survive (a duplicate here would be a different detector's own
        # responsibility to avoid, not this stage's).
        assert len(_deduplicate_cross_detector(changes)) == 2

    def test_empty_list_returns_empty(self) -> None:
        assert _deduplicate_cross_detector([]) == []


class TestSymbolVersionAliasSpecialCaseUnaffected:
    """The alias/node-move collapsing above is independent of the identity
    dedup key -- it runs on its own (symbol, old_value, new_value) match
    before the identity-keyed pass even sees the change."""

    def test_alias_change_collapses_into_node_move_when_not_retained(self) -> None:
        changes = [
            Change(
                kind=ChangeKind.SYMBOL_MOVED_VERSION_NODE,
                symbol="foo",
                description="node moved",
                old_value="GLIBC_2.17",
                new_value="GLIBC_2.28",
            ),
            Change(
                kind=ChangeKind.SYMBOL_VERSION_ALIAS_CHANGED,
                symbol="foo",
                description=f"alias changed {SYMBOL_VERSION_ALIAS_NOT_RETAINED_MARKER}",
                old_value="GLIBC_2.17",
                new_value="GLIBC_2.28",
            ),
        ]
        result = _deduplicate_cross_detector(changes)
        assert len(result) == 1
        assert result[0].kind is ChangeKind.SYMBOL_MOVED_VERSION_NODE

    def test_alias_change_survives_when_old_alias_retained(self) -> None:
        changes = [
            Change(
                kind=ChangeKind.SYMBOL_MOVED_VERSION_NODE,
                symbol="foo",
                description="node moved",
                old_value="GLIBC_2.17",
                new_value="GLIBC_2.28",
            ),
            Change(
                kind=ChangeKind.SYMBOL_VERSION_ALIAS_CHANGED,
                symbol="foo",
                description="alias changed, old retained",
                old_value="GLIBC_2.17",
                new_value="GLIBC_2.28",
            ),
        ]
        result = _deduplicate_cross_detector(changes)
        assert len(result) == 2
