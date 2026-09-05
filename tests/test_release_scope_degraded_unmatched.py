# Copyright (c) 2026 abicheck contributors
# SPDX-License-Identifier: Apache-2.0
"""ADR-065 D8 x D2: a degraded member one stored package marks and the
other side does not carry at all (Codex review, twenty-seventh round).

The fan-out used to keep only the *matched* degraded markers, so an OLD-only
degraded member reached the record builder as an ordinary unmatched member
(`NOT_SUPPLIED`) and a proven-complete NEW inventory then promoted it to a
removal (exit 8 under `--fail-on-removed-library`, `bundle_library_removed`
findings) even though its OLD acquisition had failed. The invariant, stated
over every placement of a marker: a degraded member is `FAILED` on the scope
record whether matched or not, and is never in the proven removed/added set.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st
from test_release_scope_bundle import _lib
from test_release_scope_completeness import _invoke_json, _write_stored_package

from abicheck.model.scope_acquisition import AcquisitionState
from abicheck.workflows.release_scope import (
    StoredDegradedMembers,
    stored_degraded_members,
)


def _packages(tmp_path: Path, *, degraded_side: str) -> tuple[Path, Path]:
    """Both sides stored (both inventories proven). ``libfoo.so`` is shared;
    ``libonly.so`` exists only on *degraded_side*, marked degraded there."""
    shared = {"libfoo.so": _lib("libfoo.so", exports=("foo",))}
    only = {"libonly.so": _lib("libonly.so", exports=("only",))}
    old, new = tmp_path / "old_pkg", tmp_path / "new_pkg"
    marker = {"libonly.so": "dump failed: boom"}
    if degraded_side == "old":
        _write_stored_package(old, {**shared, **only}, degraded=marker)
        _write_stored_package(new, shared)
    else:
        _write_stored_package(old, shared)
        _write_stored_package(new, {**shared, **only}, degraded=marker)
    return old, new


class TestUnmatchedDegradedMemberIsFailedNotProven:
    @pytest.mark.parametrize("degraded_side", ["old", "new"])
    @pytest.mark.parametrize("policy", ["warn", "block"])
    def test_fan_out_records_it_failed(
        self, tmp_path: Path, degraded_side: str, policy: str
    ) -> None:
        old, new = _packages(tmp_path, degraded_side=degraded_side)
        code, doc = _invoke_json(
            "compare",
            str(old),
            str(new),
            "-j",
            "1",
            "--fail-on-removed-library",
            "--on-incomplete-scope",
            policy,
        )
        scope = doc["comparison_scope"]
        assert isinstance(scope, dict)
        by_name = {m["member"]: m for m in scope["members"]}
        assert by_name["libonly.so"]["state"] == "failed"
        assert "degraded" in by_name["libonly.so"]["reason"]
        assert by_name["libfoo.so"]["state"] == "available"
        assert scope["proven_removed"] == [] and scope["proven_added"] == []
        assert scope["counts"]["failed"] == 1
        assert "bundle_library_removed" not in json.dumps(doc)
        # The member is still listed as unmatched -- recorded, not hidden.
        side = "unmatched_old" if degraded_side == "old" else "unmatched_new"
        # (Names are the materialized filenames, keyed as `<key>-<file>`.)
        assert [n for n in doc[side] if "libonly.so" in n] == list(doc[side]) != []
        assert doc["run_outcome"]["scope"] == "incomplete"
        assert code == (1 if policy == "block" else 0), doc["run_outcome"]

    @pytest.mark.parametrize("degraded_side", ["old", "new"])
    def test_reader_places_the_marker_by_membership(
        self, tmp_path: Path, degraded_side: str
    ) -> None:
        old, new = _packages(tmp_path, degraded_side=degraded_side)
        old_map = {"libfoo.so": old / "libfoo.so"}
        new_map = {"libfoo.so": new / "libfoo.so"}
        (old_map if degraded_side == "old" else new_map)["libonly.so"] = Path("x")
        found = stored_degraded_members(
            old, new, old_map, new_map, old_variant=None, new_variant=None
        )
        assert found.matched == {}
        placed = found.old_unmatched if degraded_side == "old" else found.new_unmatched
        other = found.new_unmatched if degraded_side == "old" else found.old_unmatched
        assert set(placed) == {"libonly.so"} and other == {}
        assert degraded_side.upper() in placed["libonly.so"]


_KEYS = st.sets(st.sampled_from(["a.so", "b.so", "c.so", "d.so"]))


class TestRecordBuilderNeverPromotesAFailedMember:
    """Property: whatever the marker placement, every key fed as
    ``old_failed``/``new_failed`` is ``FAILED`` and absent from the proven
    sets, under proven inventories on both sides -- the oracle is the
    state itself, not the builder's own removal predicate."""

    @settings(max_examples=60, deadline=None)
    @given(old=_KEYS, new=_KEYS, marked=_KEYS)
    def test_failed_members_stay_out_of_the_proven_sets(
        self, old: set[str], new: set[str], marked: set[str]
    ) -> None:
        from abicheck.workflows.release_scope import (
            build_release_scope_record,
            release_inventory_evidence,
        )

        old_map = {k: Path("old") / k for k in old}
        new_map = {k: Path("new") / k for k in new}
        matched = sorted(old & new)
        bucket = StoredDegradedMembers()
        for k in marked:
            if k in old and k in new:
                bucket.matched[k] = "degraded"
            elif k in old:
                bucket.old_unmatched[k] = "degraded"
            elif k in new:
                bucket.new_unmatched[k] = "degraded"
        results = [
            {"library": k, "verdict": "failed", "reason": "degraded"}
            if k in bucket.matched
            else {"library": k, "verdict": "NO_CHANGE"}
            for k in matched
        ]
        record = build_release_scope_record(
            old_map,
            new_map,
            matched,
            results,
            release_inventory_evidence(old_stored=True, new_stored=True),
            old_failed=bucket.old_unmatched,
            new_failed=bucket.new_unmatched,
        )
        states = {m.member: m.state for m in record.members}
        for k in marked & (old | new):
            assert states[k] is AcquisitionState.FAILED
        removed = {m.member for m in record.proven_removed_members}
        added = {m.member for m in record.proven_added_members}
        assert not (removed & marked) and not (added & marked)
        assert removed == (old - new) - marked
        assert added == (new - old) - marked
