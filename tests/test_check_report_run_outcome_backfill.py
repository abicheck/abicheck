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

"""ADR-063 Phase 7 follow-up (Codex review, fresh evidence): `augment_report`
must synthesize `run_outcome` for an older report that never carried one,
before `_stamp_schema_version` claims the current schema for it --
`check_report_exit_backfill.backfill_exit_block_fields` already closes this
gap for the `exit` block; `check_report_run_outcome.backfill_run_outcome`
is its `run_outcome` sibling.

Split out as its own module (rather than added to `tests/test_check_report.py`
or `tests/test_run_outcome.py`, both debt-tracked/no-growth) mirroring
`tests/test_check_report_exit_backfill.py`'s own precedent for the identical
reason.
"""

from __future__ import annotations

from abicheck.buildsource.check_report import augment_report


def _augment(report: dict[str, object]) -> dict[str, object]:
    return augment_report(
        report,
        name="libfoo",
        profile_id="p",
        baseline_channel="c",
        requested_depth="headers",
        gate_mode="local",
    )


def test_old_compare_report_gets_backfilled_run_outcome() -> None:
    report = {
        "verdict": "BREAKING",
        "severity": {
            "exit_code": 4,
            "blocking": True,
            "blocking_categories": ["abi_breaking"],
        },
        "library": "libfoo.so",
    }
    out = _augment(report)
    run_outcome = out["run_outcome"]
    assert run_outcome["compatibility"] == "BREAKING"
    assert run_outcome["gate"] == "abi_breaking"
    assert run_outcome["operational"] == "none"
    # The input report is never mutated (augment_report's own stated contract).
    assert "run_outcome" not in report


def test_old_compare_report_without_severity_derives_gate_from_legacy_verdict() -> None:
    report = {"verdict": "API_BREAK", "library": "libfoo.so"}
    out = _augment(report)
    run_outcome = out["run_outcome"]
    assert run_outcome["compatibility"] == "API_BREAK"
    # No severity.exit_code available -> falls back to the legacy verdict->exit mapping.
    assert run_outcome["gate"] == "potential_breaking"


def test_old_scan_report_gets_backfilled_run_outcome() -> None:
    report = {"scan_schema_version": "1.21", "verdict": "BREAKING", "exit_code": 4}
    out = _augment(dict(report))
    run_outcome = out["run_outcome"]
    assert run_outcome["compatibility"] == "BREAKING"
    assert run_outcome["gate"] == "abi_breaking"


def test_old_release_report_gets_backfilled_run_outcome() -> None:
    report = {
        "libraries": [{"name": "a", "verdict": "BREAKING"}],
        "old_dir": "/old",
        "new_dir": "/new",
        "verdict": "BREAKING",
        "exit": {},
    }
    out = _augment(dict(report))
    run_outcome = out["run_outcome"]
    assert run_outcome["compatibility"] == "BREAKING"


def test_report_already_carrying_run_outcome_is_left_untouched() -> None:
    sentinel = {
        "schema_version": "1.0",
        "compatibility": "BREAKING",
        "gate": "abi_breaking",
        "assurance": None,
        "operational": "none",
        "lifecycle": "existing",
    }
    report = {"verdict": "BREAKING", "run_outcome": dict(sentinel)}
    out = _augment(dict(report))
    assert out["run_outcome"] == sentinel
