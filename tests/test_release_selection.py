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

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.release_selection import ReleaseSelection
from abicheck.model.scope_acquisition import AcquisitionState, MemberAcquisition
from abicheck.serialization import snapshot_to_json, write_snapshot
from abicheck.workflows.release_plan import (
    build_declared_selection_record,
    build_release_plan,
    build_release_plan_from_directories,
)
from abicheck.workflows.release_scope import release_inventory_evidence


def _snap(library: str = "libfoo.so") -> AbiSnapshot:
    return AbiSnapshot(
        library=library,
        version="1.0",
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ],
        from_headers=True,
    )


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


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

    def test_construction_rejects_empty_key_even_via_from_lists(self) -> None:
        """Codex review, #1094: from_dict() rejected an empty key but
        from_lists() (the CLI's own --select "" path) did not -- validation
        must live in __post_init__ so every constructor enforces it."""
        with pytest.raises(ValueError, match="non-empty string"):
            ReleaseSelection.from_lists(optional=[""])

    def test_construction_rejects_non_bool_required_directly(self) -> None:
        with pytest.raises(ValueError, match="must be a boolean"):
            ReleaseSelection(members={"libfoo.so": 0})  # type: ignore[dict-item]


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


class TestBuildReleasePlanFromDirectories:
    """A previous version of this preview discovered a directory's members
    via ``package.discover_shared_libraries``, which only recognizes real
    ELF shared objects -- silently showing an empty/wrong plan for a
    directory of non-ELF supported inputs (e.g. ``.json`` snapshots) that
    the real live-directory fan-out (``cli_helpers_compare.
    _collect_release_inputs``) accepts just fine. These tests pin the fix:
    the preview must discover *exactly* what the real run would."""

    def test_json_snapshot_pair_is_discovered_and_would_compare(
        self, tmp_path: Path
    ) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        write_snapshot(snap, old_dir / "libfoo.json")
        write_snapshot(snap, new_dir / "libfoo.json")
        plan = build_release_plan_from_directories(old_dir, new_dir)
        assert len(plan.would_compare_members) == 1
        (entry,) = plan.entries
        assert entry.member == "libfoo.json"
        assert entry.would_compare

    def test_json_snapshot_with_declared_selection(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        write_snapshot(snap, old_dir / "libfoo.json")
        write_snapshot(snap, new_dir / "libfoo.json")
        sel = ReleaseSelection.from_lists(required=["libfoo.json"])
        plan = build_release_plan_from_directories(old_dir, new_dir, selection=sel)
        assert plan.would_compare_members
        assert plan.missing_required == ()

    def test_empty_directories_produce_no_candidates(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        plan = build_release_plan_from_directories(old_dir, new_dir)
        assert plan.entries == ()


class TestMemberAcquisitionFromDictRequired:
    def test_rejects_non_bool_required(self) -> None:
        """Codex review, #1094: a present but non-boolean 'required' (e.g.
        the JSON integer 0) must not be silently coerced by bool(...) --
        that would let a malformed document quietly excuse a member from
        D6's completeness axis."""
        with pytest.raises(ValueError, match="must be a boolean"):
            MemberAcquisition.from_dict(
                {
                    "member": "libfoo.so",
                    "state": "not_supplied",
                    "old_present": True,
                    "new_present": False,
                    "required": 0,
                }
            )

    def test_absent_required_defaults_true(self) -> None:
        member = MemberAcquisition.from_dict(
            {
                "member": "libfoo.so",
                "state": "not_supplied",
                "old_present": True,
                "new_present": False,
            }
        )
        assert member.required is True


class TestReleaseSelectionCli:
    """ADR-065 S1's --select/--select-required end to end, through the real
    `compare` directory dispatch (fast, JSON-snapshot fixtures -- no gcc)."""

    def test_select_filters_out_undeclared_member(self, tmp_path: Path) -> None:
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        # libbar loses its only public function -- always BREAKING.
        _write_snap(old_dir / "libbar.json", _snap("libbar.so"))
        _write_snap(
            new_dir / "libbar.json",
            AbiSnapshot(library="libbar.so", version="1.0", from_headers=True),
        )
        # Without --select, libbar's real break would fail the run.
        code, _ = _invoke("compare", str(old_dir), str(new_dir))
        assert code == 4
        # --select libfoo.json alone excludes libbar.json entirely.
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--select",
            "libfoo.json",
            "-o",
            "json=-",
        )
        assert code == 0
        data = json.loads(out)
        assert [lib["library"] for lib in data["libraries"]] == ["libfoo.json"]
        scope = data["comparison_scope"]
        assert scope["selection"] == "declared"
        by_member = {m["member"]: m for m in scope["members"]}
        assert by_member["libbar.json"]["state"] == "out_of_scope"

    def test_select_required_missing_member_blocks(self, tmp_path: Path) -> None:
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("scope:\n  on_incomplete: block\n")
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--select-required",
            "libmissing.json",
            "--config",
            str(cfg),
            "-o",
            "json=-",
        )
        assert code == 1
        data = json.loads(out)
        by_member = {m["member"]: m for m in data["comparison_scope"]["members"]}
        assert by_member["libmissing.json"]["state"] == "expected_not_produced"
        assert by_member["libmissing.json"]["required"] is True

    def test_select_warns_and_is_ignored_for_single_file_input(
        self, tmp_path: Path
    ) -> None:
        old_f, new_f = tmp_path / "libfoo.json", tmp_path / "libfoo2.json"
        _write_snap(old_f, _snap())
        _write_snap(new_f, _snap())
        code, out = _invoke(
            "compare", str(old_f), str(new_f), "--select", "libfoo.json"
        )
        assert code == 0
        assert "--select" in out and "only apply to directory/package" in out

    def test_dry_run_shows_comparison_plan(self, tmp_path: Path) -> None:
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        _write_snap(old_dir / "libbar.json", _snap("libbar.so"))
        code, out = _invoke("compare", str(old_dir), str(new_dir), "--dry-run")
        assert code == 0
        assert "Comparison plan" in out
        assert "libfoo.json" in out
        assert "libbar.json" in out
