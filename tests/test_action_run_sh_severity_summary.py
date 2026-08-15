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
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_START_MARKER = "case $VERDICT in"
_END_MARKER = "    esac"
#: The case block calls shared helpers (``_json_report_src`` and friends), so
#: extracting it alone runs a snippet whose lookups all silently answer empty
#: -- every branch then takes its "no report available" fallback and the tests
#: pass on a script that never asked the question. Their definitions are
#: extracted with it, from the first one to the first mode dispatch below them.
_HELPERS_START = "_json_report_src() {"
_HELPERS_END = 'if [[ "$MODE" == "deps-compare" ]]; then'
#: `_report_query` (one of the helpers above) reads `$_PY_BIN`, which is now
#: resolved once near the top of run.sh -- well before `_HELPERS_START` --
#: rather than immediately above `_report_query`'s own definition (moved
#: there so the baseline-set fallback, which runs even earlier, can use the
#: same resolved interpreter too). Extracted separately via regex, not
#: hand-copied, so a future edit to this line is still exercised here
#: rather than silently drifting from the real file: without it, $_PY_BIN
#: is empty in the assembled test script, `_report_query` answers nothing
#: for every lookup, and every case branch below silently takes its
#: "no report available" fallback -- the identical vacuous-pass failure
#: mode `_HELPERS_START`'s own docstring already warns about.
_PY_BIN_LINE = re.compile(r"^_PY_BIN=.*$", re.MULTILINE)
#: `_report_query` also references `$_PY_SAFE_DIR` (Codex review, fresh
#: evidence: it now runs isolated the same way as every other Python
#: invocation in run.sh) -- extracted for the identical reason as
#: `_PY_BIN_LINE` above: without it, `$_PY_SAFE_DIR` is empty in the
#: assembled test script, the `cd "$_PY_SAFE_DIR"` inside `_report_query`
#: silently no-ops (an empty `cd` argument stays in the current directory),
#: and every test here would keep passing regardless of whether the real
#: isolation is exercised at all.
_PY_SAFE_DIR_START = 'if ! _PY_SAFE_DIR="$(mktemp -d)"; then'
_PY_SAFE_DIR_END = "\ntrap 'rm -rf \"$_PY_SAFE_DIR\"' EXIT\n"


def _py_safe_dir_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_PY_SAFE_DIR_START)
    end = text.index(_PY_SAFE_DIR_END, start) + len(_PY_SAFE_DIR_END)
    return text[start:end]


def _verdict_case_region() -> str:
    """The Job Summary's ``case $VERDICT in ... esac`` block and its helpers."""
    text = RUN_SH.read_text(encoding="utf-8")
    py_bin_match = _PY_BIN_LINE.search(text)
    assert py_bin_match, "_PY_BIN resolution line not found in run.sh"
    helpers_start = text.index(_HELPERS_START)
    helpers = text[helpers_start : text.index(_HELPERS_END, helpers_start)]
    # A renamed helper would otherwise silently restore the vacuous state
    # this extraction exists to avoid: every lookup answers empty, every
    # branch takes its fallback, and the tests still pass.
    assert "_json_report_src()" in helpers and "_severity_gate_exit()" in helpers
    start = text.index(_START_MARKER)
    end = text.index(_END_MARKER, start) + len(_END_MARKER)
    return (
        py_bin_match.group(0)
        + "\n"
        + _py_safe_dir_source()
        + helpers
        + "\n"
        + text[start:end]
    )


#: `_report_query`'s own report-path anchoring block: `local report_path=`
#: through its closing `esac` -- extracted separately from the rest of the
#: function (which shells out to Python) so its anchoring *decision* can be
#: tested directly, without needing a real file for the Python heredoc to
#: read (Codex review, fresh evidence: a Windows UNC path like
#: ``\\server\share\report.json`` matched neither the POSIX nor
#: drive-letter branch of the original pattern and was wrongly rewritten as
#: ``$PWD/\\server\share\report.json``).
_REPORT_PATH_ANCHOR_START = 'local report_path="$1"'
_REPORT_PATH_ANCHOR_END = "\n  esac\n"


def _report_path_anchor_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_REPORT_PATH_ANCHOR_START)
    end = text.index(_REPORT_PATH_ANCHOR_END, start) + len(_REPORT_PATH_ANCHOR_END)
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


def _run(env_overrides: dict[str, str], *, cwd: Path | None = None) -> str:
    """Run the extracted VERDICT-case snippet, return its stdout."""
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".sh",
        delete=False,
        encoding="utf-8",
        newline="\n",
    ) as f:
        f.write(_verdict_case_region())
        script_path = f.name
    env = dict(os.environ)
    env.update(env_overrides)
    try:
        result = subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=cwd,
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

    def test_reads_a_relative_output_file(self, tmp_path) -> None:
        """Codex review, fresh evidence: unlike every other test here,
        OUTPUT_FILE can genuinely be relative (a bare user-supplied
        INPUT_OUTPUT_FILE value, or a relative default) -- relative to the
        workflow's own working directory. _report_query now runs isolated
        via a `cd "$_PY_SAFE_DIR"`, which would resolve a relative path
        against the wrong directory unless anchored to $PWD first."""
        (tmp_path / "report.json").write_text(
            json.dumps({"severity": {"blocking_categories": ["addition"]}}),
            encoding="utf-8",
        )
        out = _run(
            {
                "VERDICT": "SEVERITY_ERROR",
                "FORMAT": "json",
                "OUTPUT_FILE": "report.json",
                "ABICHECK_EXIT": "1",
            },
            cwd=tmp_path,
        )
        assert "`addition` configured as `error`" in out

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
        report.write_text(
            json.dumps({"severity": {"blocking_categories": []}}), encoding="utf-8"
        )
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
        out = _run(
            {"VERDICT": "COMPATIBLE", "FORMAT": "markdown", "ABICHECK_EXIT": "0"}
        )
        assert "No binary ABI break detected" in out


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestReportPathAnchoring:
    """`_report_query`'s path-anchoring `case` block (Codex review, fresh
    evidence): a path that is already absolute in *some* real-world sense
    must survive unchanged, not get a spurious `$PWD/` prefix prepended."""

    def _anchor(self, report_path: str, cwd: Path) -> str:
        script = (
            'f() {\n  local report_path="$1"\n'
            + _report_path_anchor_source()[len(_REPORT_PATH_ANCHOR_START) :]
            + '  printf "%s" "$report_path"\n}\nf "$1"\n'
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
        ) as f:
            f.write(script)
            script_path = f.name
        try:
            result = subprocess.run(
                [_bash_executable(), script_path, report_path],
                capture_output=True,
                text=True,
                cwd=cwd,
            )
        finally:
            os.unlink(script_path)
        assert result.returncode == 0, result.stderr
        return result.stdout

    def test_posix_absolute_path_is_unchanged(self, tmp_path: Path) -> None:
        assert self._anchor("/abs/report.json", tmp_path) == "/abs/report.json"

    def test_windows_drive_letter_path_is_unchanged(self, tmp_path: Path) -> None:
        assert self._anchor("C:\\report.json", tmp_path) == "C:\\report.json"

    def test_unc_path_is_unchanged(self, tmp_path: Path) -> None:
        """The original bug: `\\\\server\\share\\report.json` matched neither
        the POSIX nor drive-letter branch and was wrongly rewritten with a
        `$PWD/` prefix -- silently pointing at a nonexistent path and making
        `_report_query` read as "no report available"."""
        unc = "\\\\server\\share\\report.json"
        assert self._anchor(unc, tmp_path) == unc

    def test_windows_root_relative_path_is_unchanged(self, tmp_path: Path) -> None:
        assert self._anchor("\\report.json", tmp_path) == "\\report.json"

    def test_windows_drive_relative_path_is_unchanged(self, tmp_path: Path) -> None:
        """`C:report.json` (no separator after the drive letter) is a
        distinct, real Windows path form -- relative to drive C's own
        current directory, not drive C's root. This bash script has no way
        to correctly resolve it against $PWD either way, so a `$PWD/`
        prefix would be unconditionally wrong, not just "some other
        wrong" -- left alone instead (Codex review, fresh evidence)."""
        assert self._anchor("C:report.json", tmp_path) == "C:report.json"

    def test_genuinely_relative_path_is_anchored_to_pwd(self, tmp_path: Path) -> None:
        assert self._anchor("report.json", tmp_path) == f"{tmp_path}/report.json"
