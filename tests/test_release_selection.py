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

"""ADR-065 S1: :class:`~abicheck.model.release_selection.ReleaseSelection`,
:func:`~abicheck.workflows.release_plan.build_release_plan`, and
:func:`~abicheck.workflows.release_plan.build_declared_selection_record`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.model.release_selection import ReleaseSelection
from abicheck.model.scope_acquisition import AcquisitionState
from abicheck.workflows.release_plan import (
    build_declared_selection_record,
    build_release_plan,
)
from abicheck.workflows.release_scope import release_inventory_evidence


class TestReleaseSelection:
    def test_from_lists_required_wins_over_optional(self) -> None:
        sel = ReleaseSelection.from_lists(
            required=["libfoo.so"], optional=["libfoo.so", "libbar.so"]
        )
        assert sel.members == {"libfoo.so": True, "libbar.so": False}
        assert sel.required_members == frozenset({"libfoo.so"})
        assert sel.optional_members == frozenset({"libbar.so"})

    def test_empty_selection_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one declared member"):
            ReleaseSelection(members={})

    def test_from_lists_with_nothing_given_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            ReleaseSelection.from_lists()

    def test_round_trip_dict(self) -> None:
        sel = ReleaseSelection.from_lists(required=["a"], optional=["b"])
        again = ReleaseSelection.from_dict(sel.to_dict())
        assert again == sel

    def test_from_dict_rejects_non_bool_required(self) -> None:
        with pytest.raises(ValueError, match="must be a boolean"):
            ReleaseSelection.from_dict({"members": {"libfoo.so": "yes"}})

    def test_from_dict_rejects_empty_members(self) -> None:
        with pytest.raises(ValueError, match="non-empty mapping"):
            ReleaseSelection.from_dict({"members": {}})

    def test_contains(self) -> None:
        sel = ReleaseSelection.from_lists(required=["libfoo.so"])
        assert "libfoo.so" in sel
        assert "libbar.so" not in sel


class TestBuildReleasePlan:
    def test_no_selection_mirrors_all_expected(self) -> None:
        old_map = {
            "libfoo.so": Path("/old/libfoo.so"),
            "libbar.so": Path("/old/libbar.so"),
        }
        new_map = {"libfoo.so": Path("/new/libfoo.so")}
        plan = build_release_plan(old_map, new_map)
        assert plan.selection_kind == "discovered"
        by_member = {e.member: e for e in plan.entries}
        assert by_member["libfoo.so"].would_compare
        assert not by_member["libbar.so"].would_compare
        assert by_member["libbar.so"].required  # default selection requires everything
        assert plan.missing_required == (by_member["libbar.so"],)
        assert plan.out_of_scope == ()

    def test_declared_selection_marks_undeclared_out_of_scope(self) -> None:
        old_map = {
            "libfoo.so": Path("/old/libfoo.so"),
            "libbaz.so": Path("/old/libbaz.so"),
        }
        new_map = {"libfoo.so": Path("/new/libfoo.so")}
        sel = ReleaseSelection.from_lists(
            required=["libfoo.so"], optional=["libbar.so"]
        )
        plan = build_release_plan(old_map, new_map, selection=sel)
        by_member = {e.member: e for e in plan.entries}
        assert by_member["libfoo.so"].would_compare
        assert not by_member["libbaz.so"].declared
        assert by_member["libbaz.so"] in plan.out_of_scope
        # declared but produced nowhere
        assert not by_member["libbar.so"].would_compare
        assert not by_member["libbar.so"].required
        assert plan.missing_required == ()

    def test_missing_required_declared_member_is_reported(self) -> None:
        sel = ReleaseSelection.from_lists(required=["libmissing.so"])
        plan = build_release_plan({}, {}, selection=sel)
        assert len(plan.missing_required) == 1
        assert plan.missing_required[0].member == "libmissing.so"

    def test_plan_is_order_independent(self) -> None:
        old_map = {"b.so": Path("/b.so"), "a.so": Path("/a.so")}
        new_map = {"a.so": Path("/a2.so"), "b.so": Path("/b2.so")}
        plan1 = build_release_plan(old_map, new_map)
        plan2 = build_release_plan(dict(reversed(old_map.items())), new_map)
        assert [e.member for e in plan1.entries] == [e.member for e in plan2.entries]


class TestBuildDeclaredSelectionRecord:
    def _evidence(self):
        return release_inventory_evidence(old_stored=False, new_stored=False)

    def test_optional_missing_member_does_not_make_scope_incomplete(self) -> None:
        sel = ReleaseSelection.from_lists(
            required=["libfoo.so"], optional=["libbar.so"]
        )
        old_map = {"libfoo.so": Path("/old/libfoo.so")}
        new_map = {"libfoo.so": Path("/new/libfoo.so")}
        record = build_declared_selection_record(
            old_map,
            new_map,
            ["libfoo.so"],
            [{"library": "libfoo.so", "verdict": "NO_CHANGE"}],
            self._evidence(),
            sel,
            old_failed=None,
            new_failed=None,
        )
        assert record.selection == "declared"
        by_member = {m.member: m for m in record.members}
        assert by_member["libbar.so"].state is AcquisitionState.EXPECTED_NOT_PRODUCED
        assert not by_member["libbar.so"].required
        assert not record.is_incomplete

    def test_required_missing_member_makes_scope_incomplete(self) -> None:
        sel = ReleaseSelection.from_lists(required=["libfoo.so", "libbaz.so"])
        old_map = {"libfoo.so": Path("/old/libfoo.so")}
        new_map = {"libfoo.so": Path("/new/libfoo.so")}
        record = build_declared_selection_record(
            old_map,
            new_map,
            ["libfoo.so"],
            [{"library": "libfoo.so", "verdict": "NO_CHANGE"}],
            self._evidence(),
            sel,
            old_failed=None,
            new_failed=None,
        )
        by_member = {m.member: m for m in record.members}
        assert by_member["libbaz.so"].state is AcquisitionState.EXPECTED_NOT_PRODUCED
        assert by_member["libbaz.so"].required
        assert record.is_incomplete

    def test_undeclared_discovered_member_is_out_of_scope(self) -> None:
        sel = ReleaseSelection.from_lists(required=["libfoo.so"])
        old_map = {
            "libfoo.so": Path("/old/libfoo.so"),
            "libextra.so": Path("/old/libextra.so"),
        }
        new_map = {"libfoo.so": Path("/new/libfoo.so")}
        record = build_declared_selection_record(
            old_map,
            new_map,
            ["libfoo.so"],
            [{"library": "libfoo.so", "verdict": "NO_CHANGE"}],
            self._evidence(),
            sel,
            old_failed=None,
            new_failed=None,
        )
        by_member = {m.member: m for m in record.members}
        assert by_member["libextra.so"].state is AcquisitionState.OUT_OF_SCOPE
        # out-of-scope members never make the scope incomplete
        assert not record.is_incomplete

    def test_declared_but_matched_member_reads_available(self) -> None:
        sel = ReleaseSelection.from_lists(required=["libfoo.so"])
        old_map = {"libfoo.so": Path("/old/libfoo.so")}
        new_map = {"libfoo.so": Path("/new/libfoo.so")}
        record = build_declared_selection_record(
            old_map,
            new_map,
            ["libfoo.so"],
            [{"library": "libfoo.so", "verdict": "BREAKING"}],
            self._evidence(),
            sel,
            old_failed=None,
            new_failed=None,
        )
        (member,) = record.members
        assert member.state is AcquisitionState.AVAILABLE

    def test_optional_but_failed_member_still_makes_scope_incomplete(self) -> None:
        """Codex security review, #1094: an optional (``--select``, not
        ``--select-required``) member whose acquisition was *attempted* and
        failed (e.g. a NEW-side stored package marking it degraded, or a
        crashed extraction) must stay incomplete -- ``required: false``
        excuses absence, never a failure the run actually observed. Otherwise
        an attacker-controlled NEW artifact could mark a declared-optional
        member ``failed`` and have the scope read complete/clean anyway."""
        sel = ReleaseSelection.from_lists(optional=["libbar.so"])
        old_map = {"libbar.so": Path("/old/libbar.so")}
        new_map = {"libbar.so": Path("/new/libbar.so")}
        record = build_declared_selection_record(
            old_map,
            new_map,
            ["libbar.so"],
            [{"library": "libbar.so", "verdict": "ERROR", "error": "crashed"}],
            self._evidence(),
            sel,
            old_failed=None,
            new_failed=None,
        )
        (member,) = record.members
        assert member.state is AcquisitionState.FAILED
        assert not member.required
        assert member in record.unchecked_members
        assert record.is_incomplete
