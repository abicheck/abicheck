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

"""G42 "Explicit check identifiers" end-to-end coverage for
``actions/check-target/run.sh``'s ``INPUT_EXPLICIT_ID`` -> ``--explicit-id``
threading -- split out of ``test_action_check_target.py`` (that file
carries a ``no_growth`` debt-baseline entry, per this repo's own
``file-size`` gate convention: grow via a new sibling test file, not by
extending the file at its baseline).

Real bug this closes (Codex review on PR #1008): ``run_plan.py`` generates
a ``check_id`` with a ``~<explicit_id>`` tail the moment a project declares
``checks[].id``, but nothing forwarded that value from the run-plan-derived
matrix cell through to the actual report envelope ``check-target`` writes
-- ``report_envelope.py`` rebuilt every ``check_id`` from ``name``/
``profile_id``/``baseline_channel``/``requested_depth`` alone. The run
plan's own expected identity and the report ``aggregate`` actually sees
would disagree, breaking the "two checks, same target/profile/channel/
depth, distinct id:" scenario this whole G42 phase exists to support. This
file exercises the real ``run.sh`` -> ``report_envelope.py`` path end to
end (not just the pure Python functions) so the fix is pinned at the layer
the bug was actually in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_action_check_target import (
    _BASE_IDENTITY,
    PROFILE,
    RUN_SH,
    _run_finalize,
    _write_compare_report,
)

pytestmark = pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/check-target/run.sh not found"
)


class TestExplicitIdThreadsIntoTheRealReportEnvelope:
    def test_augment_mode_check_id_carries_the_explicit_id_tail(
        self, tmp_path: Path
    ) -> None:
        report_path = tmp_path / "analysis.json"
        _write_compare_report(report_path, verdict="BREAKING", exit_code=4)
        result, outputs = _run_finalize(
            {
                **_BASE_IDENTITY,
                "INPUT_EXPLICIT_ID": "l4-plugin",
                "RESOLVE_RAN": "true",
                "RESOLVE_OUTCOME": "resolved",
                "ANALYSIS_RAN": "true",
                "ANALYSIS_REPORT_PATH": str(report_path),
            },
            tmp_path,
        )
        assert result.returncode == 4, result.stderr
        assert outputs["check-id"].endswith("~l4-plugin")
        report = json.loads((tmp_path / outputs["report-path"]).read_text())
        assert report["check_id"] == outputs["check-id"]
        assert report["target_id"] == outputs["check-id"]

    def test_operational_error_mode_check_id_also_carries_the_tail(
        self, tmp_path: Path
    ) -> None:
        """The precheck/operational-error path is a separate report_envelope.py
        invocation from the augment path -- both must thread explicit_id, not
        just the common case."""
        result, outputs = _run_finalize(
            {
                **_BASE_IDENTITY,
                "INPUT_EXPLICIT_ID": "l4-plugin",
                "RESOLVE_RAN": "true",
                "RESOLVE_OUTCOME": "ambiguous",
                "RESOLVE_MESSAGE": "baseline-set resolution failed.",
            },
            tmp_path,
        )
        assert outputs["check-id"].endswith("~l4-plugin")
        assert result.returncode != 0

    def test_no_explicit_id_input_is_the_unqualified_pre_g42_shape(
        self, tmp_path: Path
    ) -> None:
        """Omitting INPUT_EXPLICIT_ID (every existing invocation, and every
        checks[] entry with no id:) produces the byte-identical pre-G42
        check_id -- the backward-compatibility guarantee, pinned at the
        run.sh layer too, not just the pure Python functions."""
        report_path = tmp_path / "analysis.json"
        _write_compare_report(report_path, verdict="BREAKING", exit_code=4)
        result, outputs = _run_finalize(
            {
                **_BASE_IDENTITY,
                "RESOLVE_RAN": "true",
                "RESOLVE_OUTCOME": "resolved",
                "ANALYSIS_RAN": "true",
                "ANALYSIS_REPORT_PATH": str(report_path),
            },
            tmp_path,
        )
        assert result.returncode == 4, result.stderr
        assert outputs["check-id"] == f"libpvxs@{PROFILE}#accepted-main@headers"
        assert "~" not in outputs["check-id"]

    def test_two_checks_differing_only_in_explicit_id_produce_distinct_report_filenames(
        self, tmp_path: Path
    ) -> None:
        """Real bug (Codex review): the per-check report filename's identity
        digest folded name/profile/baseline_channel/requested_depth only --
        two checks[] entries sharing that base tuple but declaring distinct
        id: would collide on the SAME report filename within one job,
        silently overwriting one report with the other before 'aggregate'
        ever ran. Assert the two filenames are now distinct."""
        report_path = tmp_path / "analysis.json"
        _write_compare_report(report_path, verdict="BREAKING", exit_code=4)
        _, outputs_a = _run_finalize(
            {
                **_BASE_IDENTITY,
                "INPUT_EXPLICIT_ID": "l4-replay",
                "RESOLVE_RAN": "true",
                "RESOLVE_OUTCOME": "resolved",
                "ANALYSIS_RAN": "true",
                "ANALYSIS_REPORT_PATH": str(report_path),
            },
            tmp_path,
        )
        _, outputs_b = _run_finalize(
            {
                **_BASE_IDENTITY,
                "INPUT_EXPLICIT_ID": "l4-plugin",
                "RESOLVE_RAN": "true",
                "RESOLVE_OUTCOME": "resolved",
                "ANALYSIS_RAN": "true",
                "ANALYSIS_REPORT_PATH": str(report_path),
            },
            tmp_path,
        )
        assert outputs_a["report-path"] != outputs_b["report-path"]
        assert outputs_a["check-id"] != outputs_b["check-id"]
