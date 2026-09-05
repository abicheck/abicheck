# Copyright 2025 abicheck contributors
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
# SPDX-License-Identifier: Apache-2.0
"""The release PR comment's unmatched-libraries note names each member's
own acquisition state instead of asserting one reason ("the lacking
side's inventory is unproven") for all of them -- an OLD member whose
`--dso-only` classification failed under a proven-complete NEW package is
`failed`, not "NEW unproven" (Codex review, twenty-sixth round)."""

from __future__ import annotations

import pytest

from abicheck.pr_comment import build_model, render_comment


def _release_report(members: list[dict], *, unmatched_old: list[str]) -> dict:
    return {
        "mode": "release",
        "verdict": "NO_CHANGE",
        "old_dir": "/old",
        "new_dir": "/new",
        "libraries": [
            {
                "library": "libok.so",
                "verdict": "NO_CHANGE",
                "breaking": 0,
                "source_breaks": 0,
                "compatible_additions": 0,
            }
        ],
        "unmatched_old": unmatched_old,
        "unmatched_new": [],
        "comparison_scope": {
            "completeness": "incomplete",
            "policy": "warn",
            "incomplete_scope_exit_contribution": 0,
            "no_comparison_completed": False,
            "no_comparison_completed_exit_contribution": 0,
            "old_inventory": {"completeness": "unproven", "provenance": "t"},
            "new_inventory": {"completeness": "proven", "provenance": "t"},
            "members": members,
            "unchecked": [m["name"] for m in members if m["state"] != "available"],
            "out_of_scope": [],
            "proven_removed": [],
            "proven_added": [],
        },
    }


def _member(name: str, state: str) -> dict:
    return {
        "member": name,
        "name": name,
        "state": state,
        "old_present": True,
        "new_present": state == "available",
        "reason": "why",
    }


@pytest.mark.parametrize(
    ("state", "label"),
    [
        ("failed", "(failed)"),
        ("not_supplied", "(not supplied)"),
        ("unsupported", "(unsupported)"),
    ],
)
def test_each_unmatched_member_carries_its_own_state(state: str, label: str) -> None:
    report = _release_report(
        [_member("libok.so", "available"), _member("libgone.so", state)],
        unmatched_old=["libgone.so"],
    )
    model = build_model(report)
    assert model.unmatched_states == {"libok.so": "available", "libgone.so": state}
    body = render_comment(model, sha="x")
    note = next(ln for ln in body.splitlines() if "Unmatched libraries" in ln)
    assert f"`libgone.so` {label}" in note
    assert "unproven" not in note
    assert "ADR-065 D2" in note


def test_a_pre_s2_report_renders_the_neutral_note_without_states() -> None:
    report = _release_report([], unmatched_old=["libgone.so"])
    del report["comparison_scope"]
    model = build_model(report)
    assert model.unmatched_states == {}
    # Without a scope block the raw list is what the report called removed.
    assert model.removed_libraries == ["libgone.so"]
    body = render_comment(model, sha="x")
    assert "Unmatched libraries" not in body
    assert "`libgone.so`" in body
