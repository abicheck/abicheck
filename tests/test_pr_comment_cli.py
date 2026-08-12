# Copyright 2026 Nikolay Petrov
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

"""Tests for the ``pr-comment`` CLI command — split out of test_pr_comment.py
(that file's own line-count cap; CLAUDE.md's "Files that are large" section).

`pr-comment` is Action/library-only (ADR-043 D1): not a registered `abicheck`
subcommand, invoked as `python -m abicheck.cli_pr_comment` (see
`action/run.sh`). Tests below drive the standalone Click command object
directly rather than through `abicheck.cli.main`.
"""

from __future__ import annotations

import json


def _compare_report(changes: list[dict] | None = None) -> dict:
    return {
        "verdict": "BREAKING",
        "library": "libfoo.so",
        "old_version": "v1.2.0",
        "new_version": "v1.3.0",
        "policy": "strict_abi",
        "changes": changes
        if changes is not None
        else [
            {
                "kind": "func_removed",
                "symbol": "foo_init",
                "description": "removed",
                "severity": "breaking",
                "source_location": "foo.h:20",
            },
        ],
    }


def _run_cli(args):
    from click.testing import CliRunner

    from abicheck.cli_pr_comment import pr_comment_cmd

    return CliRunner().invoke(pr_comment_cmd, args)


def test_cli_pr_comment_writes_body(tmp_path):
    from abicheck.pr_comment import MARKER

    report = tmp_path / "report.json"
    report.write_text(json.dumps(_compare_report()), encoding="utf-8")
    out = tmp_path / "comment.md"
    result = _run_cli([str(report), "--sha", "abc1234", "-o", str(out)])
    assert result.exit_code == 0
    body = out.read_text(encoding="utf-8")
    assert MARKER in body
    assert "ABI BREAKING" in body


def test_cli_pr_comment_skip_writes_empty_file(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_compare_report([])), encoding="utf-8")
    out = tmp_path / "comment.md"
    result = _run_cli([str(report), "--on", "changes", "-o", str(out)])
    assert result.exit_code == 0
    # nothing to post → empty file so the action's `-s` check skips
    assert out.read_text(encoding="utf-8") == ""


def test_cli_pr_comment_invalid_json_errors(tmp_path):
    report = tmp_path / "bad.json"
    report.write_text("{not json", encoding="utf-8")
    result = _run_cli([str(report)])
    assert result.exit_code != 0
    assert "Cannot read JSON report" in result.output


def test_cli_pr_comment_non_object_errors(tmp_path):
    report = tmp_path / "arr.json"
    report.write_text("[1, 2, 3]", encoding="utf-8")
    result = _run_cli([str(report)])
    assert result.exit_code != 0
    assert "must be an object" in result.output


def test_cli_pr_comment_no_gate_breaking_mirrors_fail_on_breaking(tmp_path):
    # action/run.sh passes --no-gate-breaking when INPUT_FAIL_ON_BREAKING is
    # false — the resulting comment must render the merely-advisory
    # headline for a breaking-severity evidence finding, not the blocking one.
    report_data = _compare_report(
        [
            {
                "kind": "layer_coverage_asymmetric",
                "symbol": "evidence:coverage",
                "description": "Base was analyzed with evidence the target lacks.",
                "severity": "breaking",
            },
        ]
    )
    report = tmp_path / "report.json"
    report.write_text(json.dumps(report_data), encoding="utf-8")
    out = tmp_path / "comment.md"
    result = _run_cli(
        [str(report), "--no-gate-breaking", "--on", "always", "-o", str(out)]
    )
    assert result.exit_code == 0
    body = out.read_text(encoding="utf-8")
    assert "Analysis coverage reduced" in body
    assert "Source analysis incomplete" not in body
