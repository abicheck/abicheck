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

"""ADR-064 stage 1b: `augment_report`/`check_report_exit_backfill` upgrade
an older report's `exit` block(s) before stamping the current schema
version, so a re-stamped report never claims a shape it doesn't satisfy.

Split out of `tests/test_check_report.py` (a debt-tracked, no-growth test
module -- see `architecture/debt.yaml`) rather than added there, so this
regression didn't need to fight that file's frozen line budget.

Two distinct gaps, both Codex review findings with fresh evidence:

- A pre-2.47/1.22 report's `exit`/`diff.exit` is *present* but missing the
  five new keys -- backfilled with their documented default (``0``).
- A pre-1.22 `NOT_COMPARABLE` scan report's `diff` has *no* `exit` key at
  all (`{"reason": ...}` was the whole shape before stage 1b) -- the
  decision `scan_engine.py` itself now persists for that outcome is
  synthesized instead of leaving the promised block absent.
"""

from __future__ import annotations

from abicheck.buildsource.check_report import augment_report

_ADR_064_EXIT_FIELDS = (
    "operational_error_contribution",
    "evidence_contract_error_contribution",
    "budget_overflow_contribution",
    "not_comparable_contribution",
    "removed_required_library_contribution",
    # ADR-065 S2 (schema 2.50): the same additive, always-0-when-absent
    # treatment as the five ADR-064 fields above.
    "incomplete_scope_contribution",
    "no_comparison_completed_contribution",
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


def test_a_pre_2_42_exit_block_also_backfills_crosscheck_promotion() -> None:
    """Codex review, fresh evidence, second round: report_schema_version
    2.41 introduced the `exit` block itself with only three fields
    (`compatibility_contribution`/`contract_coverage_contribution`/
    `analysis_assurance_contribution`) -- `crosscheck_promotion_
    contribution` was schema 2.42's own addition, one version *before*
    the five ADR-064 fields. A genuine 2.41 report is missing six keys
    total, not five; backfilling only the five ADR-064 ones left this
    sixth one silently absent from a document now claiming schema 2.47."""
    old_exit = {
        "code": 0,
        "reasons": ["clean"],
        "compatibility_contribution": 0,
        "contract_coverage_contribution": 0,
        "analysis_assurance_contribution": 0,
    }
    report = {"report_schema_version": "2.41", "verdict": "NO_CHANGE", "exit": old_exit}
    out = _augment(report)
    assert out["exit"]["crosscheck_promotion_contribution"] == 0
    for field in _ADR_064_EXIT_FIELDS:
        assert out["exit"][field] == 0
    assert "crosscheck_promotion_contribution" not in old_exit


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


def test_pre_1_22_not_comparable_scan_diff_gets_a_synthesized_exit_block() -> None:
    """A pre-1.22 NOT_COMPARABLE scan report's `diff` never carried an
    `exit` key at all -- augmenting it to 1.22 must still add one, matching
    exactly what `scan_engine.py` itself now persists for this outcome.
    """
    diff = {"reason": "scope drift"}
    report = {"scan_schema_version": "1.21", "exit_code": 6, "verdict": "NOT_COMPARABLE", "diff": diff}
    out = _augment(report)
    exit_block = out["diff"]["exit"]
    assert exit_block["code"] == 6
    assert exit_block["reasons"] == ["not_comparable"]
    assert exit_block["not_comparable_contribution"] == 6
    # The input report's nested diff dict is never mutated in place.
    assert report["diff"] is diff and "exit" not in diff


def test_advisory_preserves_operational_error_contribution_in_the_exit_block() -> None:
    """Codex review: `_neutralize_gate`'s advisory rewrite must not wipe
    `operational_error_contribution` -- `final_exit_code`'s own docstring
    says operational errors fail every gate mode, including `advisory`, so
    zeroing this axis in the persisted `exit` block would make it falsely
    claim a clean pass while the job's real exit code still fails.
    """
    report = {
        "report_schema_version": "2.47",
        "verdict": "ERROR",
        "exit": {
            "code": 4,
            "reasons": ["operational_error"],
            "compatibility_contribution": 0,
            "contract_coverage_contribution": 0,
            "analysis_assurance_contribution": 0,
            "crosscheck_promotion_contribution": 0,
            "operational_error_contribution": 4,
            "evidence_contract_error_contribution": 0,
            "budget_overflow_contribution": 0,
            "not_comparable_contribution": 0,
            "removed_required_library_contribution": 0,
            "incomplete_scope_contribution": 0,
            "no_comparison_completed_contribution": 0,
        },
    }
    out = augment_report(
        report,
        name="libfoo",
        profile_id="p",
        baseline_channel="c",
        requested_depth="headers",
        gate_mode="advisory",
    )
    exit_block = out["exit"]
    assert exit_block["code"] == 4
    assert exit_block["operational_error_contribution"] == 4
    assert exit_block["compatibility_contribution"] == 0
    assert "operational_error" in exit_block["reasons"]


def test_advisory_preserves_not_comparable_contribution_in_a_scan_diff_exit_block() -> (
    None
):
    """Codex review, fresh evidence, second round: `_classify_verdict`
    treats a `NOT_COMPARABLE` scan verdict identically to a genuine
    operational error (both fail every gate mode per `final_exit_code`),
    but the first advisory-preservation fix only carried over
    `operational_error_contribution` -- a `NOT_COMPARABLE` report's own
    signal lives in `not_comparable_contribution` instead, so it was still
    wiped to a claimed-clean `exit.code: 0`."""
    diff = {
        "verdict": "NOT_COMPARABLE",
        "exit": {
            "code": 6,
            "reasons": ["not_comparable"],
            "compatibility_contribution": 0,
            "contract_coverage_contribution": 0,
            "analysis_assurance_contribution": 0,
            "crosscheck_promotion_contribution": 0,
            "operational_error_contribution": 0,
            "evidence_contract_error_contribution": 0,
            "budget_overflow_contribution": 0,
            "not_comparable_contribution": 6,
            "removed_required_library_contribution": 0,
            "incomplete_scope_contribution": 0,
            "no_comparison_completed_contribution": 0,
        },
    }
    report = {
        "scan_schema_version": "1.22",
        "exit_code": 6,
        "verdict": "NOT_COMPARABLE",
        "diff": diff,
    }
    out = augment_report(
        report,
        name="libfoo",
        profile_id="p",
        baseline_channel="c",
        requested_depth="headers",
        gate_mode="advisory",
    )
    exit_block = out["diff"]["exit"]
    assert exit_block["code"] == 6
    assert exit_block["not_comparable_contribution"] == 6
    assert exit_block["compatibility_contribution"] == 0
    assert "not_comparable" in exit_block["reasons"]
