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

"""ADR-064 stage 1b: `augment_report` backfills an older report's `exit`
block with the five new keys before stamping the current schema version.

Split out of `tests/test_check_report.py` (a debt-tracked, no-growth test
module -- see `architecture/debt.yaml`) rather than added there, so this
regression didn't need to fight that file's frozen line budget.

Codex review, fresh evidence: without the backfill, a report already on
schema 2.41-2.46 (pre-ADR-064, missing all five keys on its `exit` block)
would be re-stamped as schema 2.47 while its `exit` object still lacks the
now-`required` keys -- claiming a shape it doesn't satisfy against the
published JSON Schema.
"""

from __future__ import annotations

from abicheck.buildsource.check_report import augment_report

_ADR_064_EXIT_FIELDS = (
    "operational_error_contribution",
    "evidence_contract_error_contribution",
    "budget_overflow_contribution",
    "not_comparable_contribution",
    "removed_required_library_contribution",
)


def _augment(report: dict[str, object]) -> dict[str, object]:
    return augment_report(
        report,
        name="libfoo",
        profile_id="p",
        baseline_channel="c",
        requested_depth="headers",
        gate_mode="local",
    )


def test_older_top_level_exit_block_is_backfilled() -> None:
    old_exit = {"code": 0, "reasons": ["clean"], "compatibility_contribution": 0}
    report = {"report_schema_version": "2.46", "verdict": "NO_CHANGE", "exit": old_exit}
    out = _augment(report)
    for field in _ADR_064_EXIT_FIELDS:
        assert out["exit"][field] == 0
    # The input report is never mutated (this module's own stated contract).
    assert report["exit"] is old_exit
    assert "not_comparable_contribution" not in old_exit


def test_older_nested_scan_exit_block_is_backfilled() -> None:
    old_exit = {"code": 2, "reasons": ["compatibility_gate"], "compatibility_contribution": 2}
    diff = {"verdict": "API_BREAK", "exit": old_exit}
    report = {"scan_schema_version": "1.21", "exit_code": 2, "verdict": "API_BREAK", "diff": diff}
    out = _augment(report)
    for field in _ADR_064_EXIT_FIELDS:
        assert out["diff"]["exit"][field] == 0
    assert out["diff"]["exit"]["code"] == 2
    # Neither the report nor its nested diff/exit dicts are mutated in place.
    assert report["diff"] is diff
    assert diff["exit"] is old_exit
    assert "not_comparable_contribution" not in old_exit


def test_a_report_already_on_the_current_schema_is_left_alone() -> None:
    full_exit = {
        "code": 0,
        "reasons": ["clean"],
        "compatibility_contribution": 0,
        "contract_coverage_contribution": 0,
        "analysis_assurance_contribution": 0,
        "crosscheck_promotion_contribution": 0,
        **dict.fromkeys(_ADR_064_EXIT_FIELDS, 0),
    }
    report = {"report_schema_version": "2.47", "verdict": "NO_CHANGE", "exit": dict(full_exit)}
    out = _augment(report)
    assert out["exit"] == full_exit


def test_a_report_with_no_exit_block_at_all_is_unaffected() -> None:
    report = {"report_schema_version": "2.40", "verdict": "NO_CHANGE"}
    out = _augment(report)
    assert "exit" not in out
