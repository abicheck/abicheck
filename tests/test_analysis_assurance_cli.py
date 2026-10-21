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

"""Analysis-assurance axis through the CLI: the ``--config``-driven
completeness gate's exit codes, the JSON report's assurance block,
and the scoped-path exit floor applied before rendering."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.policy.analysis_assurance import (
    ANALYSIS_ASSURANCE_SCHEMA_VERSION,
    ASSURANCE_STATUS_VALUES,
)
from abicheck.serialization import snapshot_to_json
from tests._analysis_assurance_support import (
    assurance_config_args,
    breaking_pair,
    compare_cli,
    elf_only_pair,
    header_pair,
    write_snapshot_pair,
)


class TestAnalysisAssuranceCliIntegration:
    """End-to-end: the CLI always reports the block; the flag is what
    changes the exit code, and only when asked."""

    def test_json_report_always_carries_analysis_assurance(
        self, tmp_path: Path
    ) -> None:
        res = compare_cli(tmp_path, header_pair(), "-o", "json=-")
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output[res.output.index("{") :])
        aa = payload["analysis_assurance"]
        assert aa["schema_version"] == ANALYSIS_ASSURANCE_SCHEMA_VERSION
        assert aa["status"] in ASSURANCE_STATUS_VALUES

    def test_complete_run_is_unaffected_by_the_flag(self, tmp_path: Path) -> None:
        res = compare_cli(tmp_path, header_pair(), "--require-complete-analysis")
        assert res.exit_code == 0, res.output

    def test_flag_omitted_never_changes_the_exit_code(self, tmp_path: Path) -> None:
        """A run whose status is not 'complete' (the minimal ELF-only pair)
        must exit 0 exactly as it always did when the flag isn't passed --
        purely additive."""
        res = compare_cli(tmp_path, elf_only_pair())
        assert res.exit_code == 0, res.output
        payload_res = compare_cli(tmp_path, elf_only_pair(), "-o", "json=-")
        payload = json.loads(payload_res.output[payload_res.output.index("{") :])
        # Sanity: this fixture really is a non-"complete" case, so the next
        # assertion (flag raises it to 1) is actually testing something.
        assert payload["analysis_assurance"]["status"] != "complete"

    def test_json_report_persists_the_exit_contribution_for_aggregate(
        self, tmp_path: Path
    ) -> None:
        """`abicheck aggregate` reads `analysis_assurance_exit_contribution`
        directly rather than recomputing it (Codex review, PR #780) -- a
        real `compare` invocation must actually write it, end to end."""
        res = compare_cli(
            tmp_path,
            elf_only_pair(),
            "--require-complete-analysis",
            "-o",
            "json=-",
        )
        assert res.exception is None or isinstance(res.exception, SystemExit), (
            res.output
        )
        assert res.exit_code == 1, res.output
        # The floor diagnostic is echoed to stderr (see
        # test_the_floor_diagnostic_is_echoed_to_stderr's sibling in
        # test_scan_analysis_assurance.py) -- parse stdout alone so it
        # doesn't corrupt the JSON payload.
        payload = json.loads(res.stdout[res.stdout.index("{") :])
        assert payload["analysis_assurance_exit_contribution"] == 1

    def test_flag_raises_a_clean_exit_to_one_on_incomplete_status(
        self, tmp_path: Path
    ) -> None:
        res = compare_cli(tmp_path, elf_only_pair(), "--require-complete-analysis")
        assert res.exception is None or isinstance(res.exception, SystemExit), (
            res.output
        )
        assert res.exit_code == 1, res.output

    def test_flag_never_lowers_a_real_abi_break_end_to_end(
        self, tmp_path: Path
    ) -> None:
        res = compare_cli(tmp_path, breaking_pair(), "--require-complete-analysis")
        assert res.exit_code == 4, res.output

    def test_config_accepted_for_directory_release_compares(
        self, tmp_path: Path
    ) -> None:
        """It used to be a usage error here, and only here.

        ``assurance.require_complete`` was rejected outright for a
        directory/package operand ("the per-library fan-out has no single
        analysis_assurance result to gate on"), which made the setting's
        meaning depend on the input shape. The fan-out now folds each
        member's own 0/1 assurance floor with ``max()`` -- the identical
        contribution a single-pair ``compare`` of that member computes --
        so the setting is accepted. Behaviour equality between the two
        shapes is pinned in
        ``tests/test_one_comparison_product_parity.py``; this only pins
        that the rejection is gone.
        """
        old, new = header_pair()
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        (old_dir / "libfoo.json").write_text(snapshot_to_json(old), encoding="utf-8")
        (new_dir / "libfoo.json").write_text(snapshot_to_json(new), encoding="utf-8")
        res = CliRunner().invoke(
            main,
            ["compare", str(old_dir), str(new_dir), *assurance_config_args(tmp_path)],
        )
        assert res.exception is None or isinstance(res.exception, SystemExit), (
            res.output
        )
        assert res.exit_code == 0, res.output


# ``scan --against --require-complete-analysis``'s own CLI-integration
# tests moved to tests/test_scan_analysis_assurance.py (this file is at the
# AGENTS.md 2000-line hard cap; a sibling module is the established split
# pattern, not a second copy of the fixtures below).


class TestScopedExitFloorAppliedBeforeRendering:
    """Historical (P2): the scoped-exit path used to render reports from the
    *pre-floor* ``result.scoped_exit_code``, folding the coverage/analysis-
    assurance floors into it only right before ``sys.exit``.

    Workstream D-S1 supersedes that: those floors score the *global* exit
    code, not a per-consumer scope, so ``scoped_exit_code`` now stays raw
    and purely informational -- it and the real (also floored) process exit
    code are no longer expected to agree.
    """

    @staticmethod
    def _pair(tmp_path: Path) -> tuple[Path, Path]:
        old, new = elf_only_pair()
        return write_snapshot_pair(tmp_path, old, new)

    def test_sarif_gate_exit_code_is_the_consumer_scope_alone(
        self, tmp_path: Path
    ) -> None:
        """Consumer scope (0) and process exit (1, assurance floor) now
        disagree -- see class docstring. ``res.stdout``, not ``.output``:
        Click 8.2+ mixes the assurance diagnostic into ``.output``."""
        old_p, new_p = self._pair(tmp_path)
        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--required-symbol",
                "_Z5pub_av",
                "-o",
                "sarif=-",
                *assurance_config_args(tmp_path),
            ],
        )
        # This is the crux of the finding: the flag must be able to fail the
        # process even though the *scoped* verdict itself is compatible (the
        # ELF-only fixture's incompleteness is what --require-complete-analysis
        # is catching, not a real ABI break).
        assert res.exception is None or isinstance(res.exception, SystemExit), (
            res.output
        )
        assert res.exit_code == 1, res.output
        out = res.stdout
        doc = json.loads(out[out.index("{") :])
        props = doc["runs"][0]["properties"]
        scoped_gate = props["scopedGate"]
        # Raw consumer scope; exitCode follows the full-library gate (0).
        assert scoped_gate["gateExitCode"] == 0
        assert scoped_gate["scopedExitCode"] == 0
        assert doc["runs"][0]["invocations"][0]["exitCode"] == 0

    def test_junit_gate_exit_code_is_the_consumer_scope_alone(
        self, tmp_path: Path
    ) -> None:
        """JUnit sibling of the SARIF test above (workstream D-S1)."""
        old_p, new_p = self._pair(tmp_path)
        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--required-symbol",
                "_Z5pub_av",
                "-o",
                "junit=-",
                *assurance_config_args(tmp_path),
            ],
        )
        assert res.exception is None or isinstance(res.exception, SystemExit), (
            res.output
        )
        assert res.exit_code == 1, res.output
        assert 'name="abicheck.gate_exit_code" value="0"' in res.stdout
        assert 'name="abicheck.scoped_exit_code" value="0"' in res.stdout

    def test_assurance_floor_diagnostic_is_emitted_on_the_scoped_path(
        self, tmp_path: Path
    ) -> None:
        """Before the fix, this path skipped ``assurance_floor_diagnostic()``
        entirely -- the stderr explanation of *why* the exit code was raised
        never appeared for a scoped run."""
        old_p, new_p = self._pair(tmp_path)
        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--required-symbol",
                "_Z5pub_av",
                *assurance_config_args(tmp_path),
            ],
        )
        assert res.exception is None or isinstance(res.exception, SystemExit), (
            res.output
        )
        assert res.exit_code == 1, res.output
        assert "Analysis assurance incomplete" in res.output

    def test_flag_omitted_scoped_exit_code_still_zero(self, tmp_path: Path) -> None:
        """Sanity: without the flag, the scoped gate stays purely additive --
        same fixture, same scoping, no floor applied."""
        old_p, new_p = self._pair(tmp_path)
        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--required-symbol",
                "_Z5pub_av",
                "-o",
                "sarif=-",
            ],
        )
        assert res.exit_code == 0, res.output
        out = res.output
        doc = json.loads(out[out.index("{") :])
        scoped_gate = doc["runs"][0]["properties"]["scopedGate"]
        assert scoped_gate["gateExitCode"] == 0
        assert doc["runs"][0]["invocations"][0]["exitCode"] == 0
