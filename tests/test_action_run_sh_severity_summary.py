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

"""Behavioral tests for ``action/run.sh``'s SEVERITY_ERROR Job Summary line.

Verified defect (companion to the ``pr_comment.py`` "ABI BREAKING" fix): the
Job Summary previously wrote a bare "Severity-level issue detected" for exit
code 1, which does not tell the reader *which* severity-config category
gated the check — in particular it looks identical whether a real risk was
promoted or a COMPATIBLE addition/quality finding was blocked by policy
(``severity-addition: error`` et al.). This extracts the real ``case
$VERDICT in ... esac`` block from ``run.sh`` (the same "parse the real file,
don't hand-copy it" discipline as ``test_action_run_sh_summary.py``) so a
future edit to the real logic is exercised here too.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_START_MARKER = "case $VERDICT in"
_END_MARKER = "    esac"


def _verdict_case_region() -> str:
    """The Job Summary's ``case $VERDICT in ... esac`` block, verbatim."""
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_START_MARKER)
    end = text.index(_END_MARKER, start) + len(_END_MARKER)
    return text[start:end]


def _bash_executable() -> str:
    """Resolve a real bash, bypassing Windows' WSL-launcher stub.

    See ``test_action_run_sh_helpers._bash_executable`` for the full
    rationale (GitHub windows-latest runners resolve a bare "bash" to a
    non-functional WSL stub ahead of Git for Windows' real bash).
    """
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


def _run(env_overrides: dict[str, str]) -> str:
    """Run the extracted VERDICT-case snippet, return its stdout."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n",
    ) as f:
        f.write(_verdict_case_region())
        script_path = f.name
    env = dict(os.environ)
    env.update(env_overrides)
    try:
        result = subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True, text=True, encoding="utf-8", env=env,
        )
    finally:
        os.unlink(script_path)
    if result.returncode != 0:
        raise AssertionError(
            f"harness script failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )
    return result.stdout


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not on PATH")
class TestSeverityErrorSummaryLine:
    def test_names_blocking_category_from_json_report(self, tmp_path) -> None:
        report = tmp_path / "report.json"
        report.write_text(
            json.dumps({"severity": {"blocking_categories": ["addition"]}}),
            encoding="utf-8",
        )
        out = _run(
            {
                "VERDICT": "SEVERITY_ERROR",
                "FORMAT": "json",
                "OUTPUT_FILE": str(report),
                "ABICHECK_EXIT": "1",
            }
        )
        assert "`addition` configured as `error`" in out
        # Must not read as an ABI/API break when it's only a policy gate.
        assert "ABI BREAKING" not in out
        assert "policy gate, not necessarily an ABI/API break" in out

    def test_joins_multiple_blocking_categories(self, tmp_path) -> None:
        report = tmp_path / "report.json"
        report.write_text(
            json.dumps(
                {"severity": {"blocking_categories": ["addition", "quality_issues"]}}
            ),
            encoding="utf-8",
        )
        out = _run(
            {
                "VERDICT": "SEVERITY_ERROR",
                "FORMAT": "json",
                "OUTPUT_FILE": str(report),
                "ABICHECK_EXIT": "1",
            }
        )
        assert "addition, quality_issues" in out

    def test_falls_back_to_generic_message_without_json(self, tmp_path) -> None:
        # FORMAT is markdown (the action default) -- no JSON report to read
        # `blocking_categories` from (PR_JSON explicitly unset too, so this
        # doesn't depend on there being no ambient PR_JSON in the test
        # runner's own environment), so the generic message is kept.
        out = _run(
            {
                "VERDICT": "SEVERITY_ERROR",
                "FORMAT": "markdown",
                "OUTPUT_FILE": "",
                "PR_JSON": "",
                "ABICHECK_EXIT": "1",
            }
        )
        assert "Severity-level issue detected" in out
        assert "configured as `error`" not in out

    def test_names_blocking_category_from_pr_json_when_format_is_markdown(
        self, tmp_path
    ) -> None:
        # Codex review, PR #595: the common case is FORMAT=markdown (the
        # action default) with PR comments on, where the compare-mode
        # command setup already asks the same abicheck invocation to write
        # an always-unfiltered secondary JSON report to $PR_JSON (via
        # --secondary-format/--secondary-output) before this Job Summary
        # code runs -- that must be read too, not just a FORMAT=json
        # primary output, or the common default-config case never gets the
        # category-aware message.
        report = tmp_path / "pr.json"
        report.write_text(
            json.dumps({"severity": {"blocking_categories": ["addition"]}}),
            encoding="utf-8",
        )
        out = _run(
            {
                "VERDICT": "SEVERITY_ERROR",
                "FORMAT": "markdown",
                "OUTPUT_FILE": "",
                "PR_JSON": str(report),
                "ABICHECK_EXIT": "1",
            }
        )
        assert "`addition` configured as `error`" in out
        assert "Severity-level issue detected" not in out

    def test_prefers_primary_json_output_over_pr_json(self, tmp_path) -> None:
        # When FORMAT=json, OUTPUT_FILE is the primary (unfiltered) report
        # and should win over a stale/mismatched $PR_JSON if both are set.
        primary = tmp_path / "primary.json"
        primary.write_text(
            json.dumps({"severity": {"blocking_categories": ["quality_issues"]}}),
            encoding="utf-8",
        )
        pr_json = tmp_path / "pr.json"
        pr_json.write_text(
            json.dumps({"severity": {"blocking_categories": ["addition"]}}),
            encoding="utf-8",
        )
        out = _run(
            {
                "VERDICT": "SEVERITY_ERROR",
                "FORMAT": "json",
                "OUTPUT_FILE": str(primary),
                "PR_JSON": str(pr_json),
                "ABICHECK_EXIT": "1",
            }
        )
        assert "`quality_issues` configured as `error`" in out
        assert "addition" not in out

    def test_falls_back_when_blocking_categories_empty(self, tmp_path) -> None:
        report = tmp_path / "report.json"
        report.write_text(json.dumps({"severity": {"blocking_categories": []}}), encoding="utf-8")
        out = _run(
            {
                "VERDICT": "SEVERITY_ERROR",
                "FORMAT": "json",
                "OUTPUT_FILE": str(report),
                "ABICHECK_EXIT": "1",
            }
        )
        assert "Severity-level issue detected" in out

    def test_other_verdicts_unaffected(self) -> None:
        out = _run({"VERDICT": "COMPATIBLE", "FORMAT": "markdown", "ABICHECK_EXIT": "0"})
        assert "No binary ABI break detected" in out
