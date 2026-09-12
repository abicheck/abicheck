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

"""``abicheck project history`` (ADR-066 S1) end-to-end CLI wiring.

Exercises the typed API (``abicheck.workflows.history``) through the real
Click command, matching the pattern in
``test_cli_project_validate_use_cases.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner, Result

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import save_snapshot


def _run(args: list[str]) -> Result:
    return CliRunner().invoke(main, ["project", "history", *args])


def _snapshot(tmp_path: Path, version: str, names: list[str]) -> str:
    functions = [Function(name=n, mangled=n, return_type="int") for n in names]
    snap = AbiSnapshot(library="libmath.so", version=version, functions=functions)
    out = tmp_path / f"{version}.json"
    save_snapshot(snap, out)
    return str(out)


class TestJsonOutput:
    def test_three_release_chain_reports_events_and_no_gap(
        self, tmp_path: Path
    ) -> None:
        p1 = _snapshot(tmp_path, "1.0.0", ["add", "subtract"])
        p2 = _snapshot(tmp_path, "1.1.0", ["add"])
        p3 = _snapshot(tmp_path, "1.2.0", ["add", "multiply"])

        res = _run([p1, p2, p3])
        assert res.exit_code == 0, res.output
        doc = json.loads(res.output)
        assert doc["schema"] == "abicheck.longitudinal-history/v1"
        assert doc["library"] == "libmath.so"
        assert len(doc["entries"]) == 3
        events_by_name = {e["display_name"]: e["event"] for e in doc["events"]}
        assert events_by_name["subtract"] == "removed"
        assert events_by_name["multiply"] == "introduced"
        assert doc["coverage"]["gaps"] == []

    def test_coverage_gap_is_serialized_in_json_output(self, tmp_path: Path) -> None:
        p1 = _snapshot(tmp_path, "1.0.0", ["add"])
        p2 = _snapshot(tmp_path, "1.5.0", ["add"])  # skips intermediate releases

        res = _run([p1, p2])
        assert res.exit_code == 0, res.output
        doc = json.loads(res.output)
        assert doc["coverage"]["gaps"] == [
            {
                "from_version": "1.0.0",
                "to_version": "1.5.0",
                "kind": "unknown_interval",
                "detail": doc["coverage"]["gaps"][0]["detail"],
            }
        ]
        assert "1.0.0" in doc["coverage"]["gaps"][0]["detail"]

    def test_explicit_version_labels(self, tmp_path: Path) -> None:
        p1 = _snapshot(tmp_path, "snap-a", ["add"])
        p2 = _snapshot(tmp_path, "snap-b", ["add", "multiply"])

        res = _run(
            [p1, p2, "--version", "1.0.0", "--version", "1.1.0", "-o", "json=-"]
        )
        assert res.exit_code == 0, res.output
        doc = json.loads(res.output)
        assert [e["version"] for e in doc["entries"]] == ["1.0.0", "1.1.0"]

    def test_output_flag_writes_file(self, tmp_path: Path) -> None:
        p1 = _snapshot(tmp_path, "1.0.0", ["add"])
        p2 = _snapshot(tmp_path, "2.0.0", ["add"])
        out_file = tmp_path / "history.json"

        res = _run([p1, p2, "-o", f"json={out_file}"])
        assert res.exit_code == 0, res.output
        assert res.output.strip().startswith("Report written to")
        doc = json.loads(out_file.read_text())
        assert doc["library"] == "libmath.so"


class TestTextOutput:
    def test_text_format_lists_entries_and_events(self, tmp_path: Path) -> None:
        p1 = _snapshot(tmp_path, "1.0.0", ["add", "subtract"])
        p2 = _snapshot(tmp_path, "2.0.0", ["add"])

        res = _run([p1, p2, "-o", "text=-"])
        assert res.exit_code == 0, res.output
        assert "longitudinal history: libmath.so" in res.output
        assert "1.0.0" in res.output
        assert "2.0.0" in res.output
        assert "removed function subtract" in res.output

    def test_text_format_shows_coverage_gap(self, tmp_path: Path) -> None:
        p1 = _snapshot(tmp_path, "1.0.0", ["add"])
        p2 = _snapshot(tmp_path, "1.5.0", ["add"])  # skips intermediate releases

        res = _run([p1, p2, "-o", "text=-"])
        assert res.exit_code == 0, res.output
        assert "coverage gaps" in res.output


class TestUsageErrors:
    def test_single_snapshot_is_accepted(self, tmp_path: Path) -> None:
        # nargs=-1 with required=True accepts one path; the workflow itself
        # only requires "at least one" (a lone snapshot's own entities are
        # all `first_observed`, nothing to compare against).
        p1 = _snapshot(tmp_path, "1.0.0", ["add"])
        res = _run([p1])
        assert res.exit_code == 0, res.output
        doc = json.loads(res.output)
        assert len(doc["entries"]) == 1
        assert all(e["event"] == "first_observed" for e in doc["events"])

    def test_no_snapshots_is_a_usage_error(self) -> None:
        res = _run([])
        assert res.exit_code != 0

    def test_nonexistent_snapshot_is_a_usage_error(self, tmp_path: Path) -> None:
        res = _run([str(tmp_path / "does-not-exist.json")])
        assert res.exit_code != 0

    def test_mismatched_version_count_is_a_usage_error(self, tmp_path: Path) -> None:
        p1 = _snapshot(tmp_path, "1.0.0", ["add"])
        p2 = _snapshot(tmp_path, "2.0.0", ["add"])
        res = _run([p1, p2, "--version", "only-one"])
        assert res.exit_code == 64
        assert "--version" in res.output

    def test_malformed_snapshot_file_is_a_usage_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("not valid json at all")
        good = _snapshot(tmp_path, "1.0.0", ["add"])
        res = _run([bad.as_posix(), good])
        assert res.exit_code == 64
