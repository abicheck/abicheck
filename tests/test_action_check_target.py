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

"""Behavioral tests for ``actions/check-target``'s shell layer (G30 P1.3,
ADR-047 §4/§7).

``action.yml``'s own step orchestration (composing ``resolve-baseline`` +
``collect-facts`` + the root Action as nested ``uses:`` steps) needs a real
GitHub Actions runner to exercise end-to-end -- these tests instead drive
``validate-inputs.sh`` and ``run.sh`` directly, simulating the env vars
``action.yml`` would inject from those steps' own outputs (``RESOLVE_*``,
``ANALYSIS_*``, ``COLLECT_*``). The pure report-envelope logic those two
scripts delegate to is unit-tested in isolation in
``tests/test_check_report.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ACTION_DIR = Path(__file__).resolve().parents[1] / "actions" / "check-target"
RUN_SH = ACTION_DIR / "run.sh"
VALIDATE_SH = ACTION_DIR / "validate-inputs.sh"

PROFILE = "linux-x86_64-gcc13-release"


def _bash_executable() -> str:
    if os.name != "nt":
        return "bash"
    for candidate in (
        os.environ.get("GIT_BASH_PATH"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return "bash"


def _run(
    script: Path, env_extra: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess[str]:
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env = {**base_env, "ACTION_PATH": str(ACTION_DIR), **env_extra}
    return subprocess.run(
        [_bash_executable(), str(script)],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )


def _run_finalize(
    env_extra: dict[str, str], cwd: Path
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    github_output = cwd / "github_output"
    github_output.write_text("")
    result = _run(RUN_SH, {"GITHUB_OUTPUT": str(github_output), **env_extra}, cwd)
    outputs: dict[str, str] = {}
    for line in github_output.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            outputs[k] = v
    return result, outputs


_BASE_IDENTITY = {
    "INPUT_NAME": "libpvxs",
    "INPUT_PROFILE": PROFILE,
    "INPUT_BASELINE_CHANNEL": "accepted-main",
    "INPUT_REQUESTED_DEPTH": "headers",
    "INPUT_GATE_MODE": "local",
    "INPUT_PROJECT": "epics-base/pvxs",
    "INPUT_HEAD_SHA": "deadbeef",
    "INPUT_BASE_REF": "main",
    "INPUT_ACTION_VERSION": "abicheck/abicheck@v1",
}


def _write_compare_report(
    path: Path,
    *,
    verdict: str = "BREAKING",
    exit_code: int = 4,
    old_depth: str = "headers",
    new_depth: str = "headers",
) -> None:
    path.write_text(
        json.dumps(
            {
                "report_schema_version": "2.12",
                "library": "libpvxs",
                "verdict": verdict,
                "old_evidence_depth": old_depth,
                "new_evidence_depth": new_depth,
                "severity": {
                    "config": {},
                    "categories": {},
                    "exit_code": exit_code,
                    "blocking": exit_code != 0,
                    "blocking_categories": ["abi_breaking"] if exit_code else [],
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(),
    reason="actions/check-target/validate-inputs.sh not found",
)
class TestValidateInputs:
    def test_valid_library_target_passes(self, tmp_path: Path) -> None:
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_BASELINE_PATH": "./baseline",
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stderr

    def test_baseline_channel_none_does_not_require_baseline_path(
        self, tmp_path: Path
    ) -> None:
        result = _run(
            VALIDATE_SH,
            {**_BASE_IDENTITY, "INPUT_BASELINE_CHANNEL": "none"},
            tmp_path,
        )
        assert result.returncode == 0, result.stderr

    def test_missing_baseline_path_fails_for_real_channel(self, tmp_path: Path) -> None:
        result = _run(VALIDATE_SH, {**_BASE_IDENTITY}, tmp_path)
        assert result.returncode == 64

    def test_unknown_kind_fails(self, tmp_path: Path) -> None:
        result = _run(
            VALIDATE_SH,
            {**_BASE_IDENTITY, "INPUT_KIND": "bogus", "INPUT_BASELINE_PATH": "./b"},
            tmp_path,
        )
        assert result.returncode == 64

    def test_unknown_target_kind_fails(self, tmp_path: Path) -> None:
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_TARGET_KIND": "bogus",
                "INPUT_BASELINE_PATH": "./b",
            },
            tmp_path,
        )
        assert result.returncode == 64

    def test_unknown_gate_mode_fails(self, tmp_path: Path) -> None:
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_GATE_MODE": "bogus",
                "INPUT_BASELINE_PATH": "./b",
            },
            tmp_path,
        )
        assert result.returncode == 64

    def test_unknown_requested_depth_fails(self, tmp_path: Path) -> None:
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_REQUESTED_DEPTH": "bogus",
                "INPUT_BASELINE_PATH": "./b",
            },
            tmp_path,
        )
        assert result.returncode == 64

    def test_bundle_kind_requires_bundle_members(self, tmp_path: Path) -> None:
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_KIND": "bundle",
                "INPUT_BASELINE_PATH": "./b",
                "INPUT_BUNDLE_MEMBERS": "[]",
            },
            tmp_path,
        )
        assert result.returncode == 64

    def test_bundle_kind_rejects_build_depth(self, tmp_path: Path) -> None:
        # kind: bundle compares directories, which the CLI's per-library
        # release fan-out never collects build/source evidence for -- must
        # fail loud rather than silently running at a shallower depth
        # (Codex review).
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_KIND": "bundle",
                "INPUT_REQUESTED_DEPTH": "build",
                "INPUT_BASELINE_PATH": "./b",
                "INPUT_BUNDLE_MEMBERS": '["libpvxs", "libpvxsIoc"]',
            },
            tmp_path,
        )
        assert result.returncode == 64

    def test_bundle_kind_rejects_source_depth(self, tmp_path: Path) -> None:
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_KIND": "bundle",
                "INPUT_REQUESTED_DEPTH": "source",
                "INPUT_BASELINE_PATH": "./b",
                "INPUT_BUNDLE_MEMBERS": '["libpvxs", "libpvxsIoc"]',
            },
            tmp_path,
        )
        assert result.returncode == 64

    def test_bundle_kind_allows_headers_depth(self, tmp_path: Path) -> None:
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_KIND": "bundle",
                "INPUT_REQUESTED_DEPTH": "headers",
                "INPUT_BASELINE_PATH": "./b",
                "INPUT_BUNDLE_MEMBERS": '["libpvxs", "libpvxsIoc"]',
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stderr

    def test_bundle_kind_rejects_non_library_target_kind(self, tmp_path: Path) -> None:
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_KIND": "bundle",
                "INPUT_TARGET_KIND": "app-consumer",
                "INPUT_BASELINE_PATH": "./b",
                "INPUT_BUNDLE_MEMBERS": '["libpvxs", "libpvxsIoc"]',
            },
            tmp_path,
        )
        assert result.returncode == 64

    def test_app_consumer_requires_consumer_binary(self, tmp_path: Path) -> None:
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_TARGET_KIND": "app-consumer",
                "INPUT_BASELINE_PATH": "./b",
            },
            tmp_path,
        )
        assert result.returncode == 64

    def test_plugin_contract_requires_contract_file(self, tmp_path: Path) -> None:
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_TARGET_KIND": "plugin-contract",
                "INPUT_BASELINE_PATH": "./b",
            },
            tmp_path,
        )
        assert result.returncode == 64

    def test_app_consumer_rejects_baseline_channel_none(self, tmp_path: Path) -> None:
        # baseline-channel: none routes to `scan`, which has no --used-by
        # equivalent -- an app-consumer check would silently run unscoped
        # (Codex review).
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_BASELINE_CHANNEL": "none",
                "INPUT_TARGET_KIND": "app-consumer",
                "INPUT_CONSUMER_BINARY": "./consumer.so",
            },
            tmp_path,
        )
        assert result.returncode == 64

    def test_plugin_contract_rejects_baseline_channel_none(
        self, tmp_path: Path
    ) -> None:
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_BASELINE_CHANNEL": "none",
                "INPUT_TARGET_KIND": "plugin-contract",
                "INPUT_CONTRACT_FILE": "./contract.syms",
            },
            tmp_path,
        )
        assert result.returncode == 64

    def test_library_target_allows_baseline_channel_none(self, tmp_path: Path) -> None:
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_BASELINE_CHANNEL": "none",
            },
            tmp_path,
        )
        assert result.returncode == 0


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/check-target/run.sh not found"
)
class TestFinalizeAugmentMode:
    """The common path: baseline resolved (or channel: none), analysis ran."""

    def test_local_gate_mode_reflects_real_exit_code(self, tmp_path: Path) -> None:
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
        assert outputs["outcome"] == "resolved"
        assert outputs["verdict"] == "BREAKING"
        assert outputs["compatibility-verdict"] == "BREAKING"
        report = json.loads((tmp_path / outputs["report-path"]).read_text())
        assert report["check_id"] == f"libpvxs@{PROFILE}#accepted-main@headers"
        assert report["severity"]["exit_code"] == 4

    def test_deferred_gate_mode_never_fails_job_but_keeps_real_severity(
        self, tmp_path: Path
    ) -> None:
        report_path = tmp_path / "analysis.json"
        _write_compare_report(report_path, verdict="BREAKING", exit_code=4)
        result, outputs = _run_finalize(
            {
                **_BASE_IDENTITY,
                "INPUT_GATE_MODE": "deferred",
                "RESOLVE_RAN": "true",
                "RESOLVE_OUTCOME": "resolved",
                "ANALYSIS_RAN": "true",
                "ANALYSIS_REPORT_PATH": str(report_path),
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads((tmp_path / outputs["report-path"]).read_text())
        assert report["severity"]["exit_code"] == 4  # aggregate needs the real value
        assert report["policy_gate_decision"] == "fail"

    def test_advisory_gate_mode_neutralizes_severity(self, tmp_path: Path) -> None:
        report_path = tmp_path / "analysis.json"
        _write_compare_report(report_path, verdict="BREAKING", exit_code=4)
        result, outputs = _run_finalize(
            {
                **_BASE_IDENTITY,
                "INPUT_GATE_MODE": "advisory",
                "RESOLVE_RAN": "true",
                "RESOLVE_OUTCOME": "resolved",
                "ANALYSIS_RAN": "true",
                "ANALYSIS_REPORT_PATH": str(report_path),
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads((tmp_path / outputs["report-path"]).read_text())
        assert report["severity"]["exit_code"] == 0
        assert report["severity"]["blocking"] is False
        assert report["compatibility_verdict"] == "BREAKING"
        assert report["policy_gate_decision"] == "fail"

    def test_baseline_channel_none_skips_resolve_and_still_augments(
        self, tmp_path: Path
    ) -> None:
        report_path = tmp_path / "analysis.json"
        _write_compare_report(report_path, verdict="COMPATIBLE", exit_code=0)
        result, outputs = _run_finalize(
            {
                **_BASE_IDENTITY,
                "INPUT_BASELINE_CHANNEL": "none",
                "RESOLVE_RAN": "false",
                "ANALYSIS_RAN": "true",
                "ANALYSIS_REPORT_PATH": str(report_path),
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert outputs["outcome"] == "skipped"

    def test_two_invocations_in_the_same_job_do_not_overwrite_each_others_report(
        self, tmp_path: Path
    ) -> None:
        """A fixed "check-target-report.json" filename would collide across
        two check-target calls in the same job (e.g. the same target against
        two baseline channels) -- an earlier step's own report-path output
        would end up pointing at the LATER check's envelope by the time
        anything reads it (Codex review). The filename must be scoped to
        each check's own identity."""
        report_path = tmp_path / "analysis.json"
        _write_compare_report(report_path, verdict="BREAKING", exit_code=4)
        first_result, first_outputs = _run_finalize(
            {
                **_BASE_IDENTITY,
                "INPUT_BASELINE_CHANNEL": "accepted-main",
                "RESOLVE_RAN": "true",
                "RESOLVE_OUTCOME": "resolved",
                "ANALYSIS_RAN": "true",
                "ANALYSIS_REPORT_PATH": str(report_path),
            },
            tmp_path,
        )
        assert first_result.returncode == 4, first_result.stderr
        second_report_path = tmp_path / "analysis2.json"
        _write_compare_report(second_report_path, verdict="COMPATIBLE", exit_code=0)
        second_result, second_outputs = _run_finalize(
            {
                **_BASE_IDENTITY,
                "INPUT_BASELINE_CHANNEL": "release-contract",
                "RESOLVE_RAN": "true",
                "RESOLVE_OUTCOME": "resolved",
                "ANALYSIS_RAN": "true",
                "ANALYSIS_REPORT_PATH": str(second_report_path),
            },
            tmp_path,
        )
        assert second_result.returncode == 0, second_result.stderr
        assert first_outputs["report-path"] != second_outputs["report-path"]
        # The first report is still on disk, unmodified by the second call.
        first_report = json.loads((tmp_path / first_outputs["report-path"]).read_text())
        assert first_report["severity"]["exit_code"] == 4
        second_report = json.loads(
            (tmp_path / second_outputs["report-path"]).read_text()
        )
        assert second_report["severity"]["exit_code"] == 0


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/check-target/run.sh not found"
)
class TestFinalizeOperationalError:
    @pytest.mark.parametrize("gate_mode", ["local", "deferred", "advisory"])
    def test_resolve_failure_always_fails_regardless_of_gate_mode(
        self, gate_mode: str, tmp_path: Path
    ) -> None:
        result, outputs = _run_finalize(
            {
                **_BASE_IDENTITY,
                "INPUT_GATE_MODE": gate_mode,
                "RESOLVE_RAN": "true",
                "RESOLVE_OUTCOME": "wrong_profile",
                "RESOLVE_MESSAGE": "baseline built for a different profile.",
                "ANALYSIS_RAN": "false",
            },
            tmp_path,
        )
        assert result.returncode == 1, result.stderr
        assert outputs["outcome"] == "wrong_profile"
        assert outputs["verdict"] == "ERROR"
        report = json.loads((tmp_path / outputs["report-path"]).read_text())
        assert report["operational_errors"] == [
            {
                "kind": "wrong_profile",
                "message": "baseline built for a different profile.",
            }
        ]
        assert "severity" not in report

    def test_analysis_never_producing_a_report_is_an_operational_error(
        self, tmp_path: Path
    ) -> None:
        result, outputs = _run_finalize(
            {
                **_BASE_IDENTITY,
                "RESOLVE_RAN": "true",
                "RESOLVE_OUTCOME": "resolved",
                "ANALYSIS_RAN": "false",
            },
            tmp_path,
        )
        assert result.returncode == 1, result.stderr
        report = json.loads((tmp_path / outputs["report-path"]).read_text())
        assert report["operational_errors"][0]["kind"] == "ambiguous"

    def test_collect_verify_failure_is_a_distinct_operational_error(
        self, tmp_path: Path
    ) -> None:
        """action.yml gates the analysis step on collect_verify not having
        failed -- a broken/empty wrapper or clang-plugin pack must never be
        silently handed to compare as --build-info (review)."""
        result, outputs = _run_finalize(
            {
                **_BASE_IDENTITY,
                "RESOLVE_RAN": "true",
                "RESOLVE_OUTCOME": "resolved",
                "ANALYSIS_RAN": "false",
                "COLLECT_VERIFY_OUTCOME": "failure",
            },
            tmp_path,
        )
        assert result.returncode == 1, result.stderr
        assert outputs["verdict"] == "ERROR"
        report = json.loads((tmp_path / outputs["report-path"]).read_text())
        assert "verify failed" in report["operational_errors"][0]["message"]

    def test_collect_replay_failure_is_a_distinct_operational_error(
        self, tmp_path: Path
    ) -> None:
        result, outputs = _run_finalize(
            {
                **_BASE_IDENTITY,
                "RESOLVE_RAN": "true",
                "RESOLVE_OUTCOME": "resolved",
                "ANALYSIS_RAN": "false",
                "COLLECT_REPLAY_OUTCOME": "failure",
            },
            tmp_path,
        )
        assert result.returncode == 1, result.stderr
        assert outputs["verdict"] == "ERROR"
        report = json.loads((tmp_path / outputs["report-path"]).read_text())
        assert "replay" in report["operational_errors"][0]["message"]


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/check-target/run.sh not found"
)
class TestFinalizeBootstrap:
    def test_bootstrap_pass_never_fails_the_job(self, tmp_path: Path) -> None:
        result, outputs = _run_finalize(
            {
                **_BASE_IDENTITY,
                "INPUT_BASELINE_REQUIRED": "false",
                "RESOLVE_RAN": "true",
                "RESOLVE_OUTCOME": "not_found",
                "RESOLVE_BOOTSTRAP": "true",
                "RESOLVE_MESSAGE": "no baseline set exists yet.",
                "ANALYSIS_RAN": "false",
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert outputs["outcome"] == "not_found"
        assert outputs["verdict"] == "NO_BASELINE"
        report = json.loads((tmp_path / outputs["report-path"]).read_text())
        assert report["baseline_bootstrap"] is True
        assert report["operational_errors"] == []


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/check-target/run.sh not found"
)
class TestFinalizeEvidenceDegradation:
    """ADR-047 §7's effective_depth reads the real achieved depth straight
    from the analysis report's own old_evidence_depth/new_evidence_depth --
    correct regardless of *how* that depth was achieved (a composed
    collect-facts producer, or a direct --build-info/--sources input with no
    producer at all -- the case an earlier producer-based heuristic here got
    wrong, Codex review)."""

    def test_source_depth_degrades_when_report_only_reached_headers(
        self, tmp_path: Path
    ) -> None:
        report_path = tmp_path / "analysis.json"
        _write_compare_report(
            report_path,
            verdict="COMPATIBLE",
            exit_code=0,
            old_depth="headers",
            new_depth="headers",
        )
        result, outputs = _run_finalize(
            {
                **_BASE_IDENTITY,
                "INPUT_REQUESTED_DEPTH": "source",
                "RESOLVE_RAN": "true",
                "RESOLVE_OUTCOME": "resolved",
                "ANALYSIS_RAN": "true",
                "ANALYSIS_REPORT_PATH": str(report_path),
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads((tmp_path / outputs["report-path"]).read_text())
        assert report["requested_depth"] == "source"
        assert report["effective_depth"] == "headers"
        assert report["check_evidence_coverage"]["state"] == "degraded"

    def test_source_depth_stays_when_report_reached_source(
        self, tmp_path: Path
    ) -> None:
        report_path = tmp_path / "analysis.json"
        _write_compare_report(
            report_path,
            verdict="COMPATIBLE",
            exit_code=0,
            old_depth="source",
            new_depth="source",
        )
        result, outputs = _run_finalize(
            {
                **_BASE_IDENTITY,
                "INPUT_REQUESTED_DEPTH": "source",
                "RESOLVE_RAN": "true",
                "RESOLVE_OUTCOME": "resolved",
                "ANALYSIS_RAN": "true",
                "ANALYSIS_REPORT_PATH": str(report_path),
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads((tmp_path / outputs["report-path"]).read_text())
        assert report["effective_depth"] == "source"
        assert report["check_evidence_coverage"]["state"] == "complete"

    def test_source_depth_via_direct_build_info_with_no_producer_is_not_degraded(
        self, tmp_path: Path
    ) -> None:
        """The exact Codex-flagged regression: evidence-producer is unset
        (a producer-less check using --build-info/--sources directly), but
        the analysis genuinely reached source depth on both sides."""
        report_path = tmp_path / "analysis.json"
        _write_compare_report(
            report_path,
            verdict="COMPATIBLE",
            exit_code=0,
            old_depth="source",
            new_depth="source",
        )
        result, outputs = _run_finalize(
            {
                **_BASE_IDENTITY,
                "INPUT_REQUESTED_DEPTH": "source",
                "RESOLVE_RAN": "true",
                "RESOLVE_OUTCOME": "resolved",
                "ANALYSIS_RAN": "true",
                "ANALYSIS_REPORT_PATH": str(report_path),
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads((tmp_path / outputs["report-path"]).read_text())
        assert report["effective_depth"] == "source"
        assert report["check_evidence_coverage"]["state"] == "complete"


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/check-target/run.sh not found"
)
class TestFinalizeScanGuardSentinel:
    """A baseline-channel: none scan run that hits a guard (--budget
    exceeded, service_scan.py's BUDGET_OVERFLOW) is not a compatibility
    finding -- the scan never completed its comparison. gate-mode: deferred/
    advisory must not turn that into a quiet pass the way they do for a real
    BREAKING/API_BREAK compatibility verdict (Codex review)."""

    @pytest.mark.parametrize("gate_mode", ["local", "deferred", "advisory"])
    def test_budget_overflow_always_fails_regardless_of_gate_mode(
        self, gate_mode: str, tmp_path: Path
    ) -> None:
        report_path = tmp_path / "analysis.json"
        report_path.write_text(
            json.dumps(
                {
                    "scan_schema_version": "1.1",
                    "verdict": "BUDGET_OVERFLOW",
                    "exit_code": 5,
                    "level": {"depth": "headers"},
                }
            ),
            encoding="utf-8",
        )
        result, outputs = _run_finalize(
            {
                **_BASE_IDENTITY,
                "INPUT_BASELINE_CHANNEL": "none",
                "INPUT_GATE_MODE": gate_mode,
                "RESOLVE_RAN": "false",
                "ANALYSIS_RAN": "true",
                "ANALYSIS_REPORT_PATH": str(report_path),
            },
            tmp_path,
        )
        assert result.returncode == 1, result.stderr
        report = json.loads((tmp_path / outputs["report-path"]).read_text())
        assert report["operational_errors"] == [
            {
                "kind": "scan_guard_triggered",
                "message": "the analysis reported a non-compatibility verdict: 'BUDGET_OVERFLOW'",
            }
        ]
