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

"""Behavioral test for ``action/run.sh``'s step-summary "Full report" block.

Verified defect: the block unconditionally wrapped ``$ABICHECK_OUTPUT`` in a
```` ``` ```` code fence, so a markdown-format report rendered as a literal
code block in the GitHub Actions job summary instead of formatted Markdown
(headings/tables/bold text). Extracts the real snippet from ``run.sh`` (the
same "parse the real file, don't hand-copy it" discipline as
``test_action_run_sh_helpers.py``) so a future edit to the real logic is
exercised here too, not a stale copy.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_START_MARKER = "# If output was captured"
_END_MARKER = 'echo "</details>"\n    fi'


def _summary_fence_region() -> str:
    """The "Full report" step-summary snippet, extracted verbatim from run.sh.

    Includes the trailing ``fi`` that closes the outer
    ``if [[ -n "$ABICHECK_OUTPUT" ]]; then`` — without it the extracted
    snippet is unbalanced and fails to parse as a standalone script.
    """
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


def _run(fmt: str, output: str, effective_fmt: str | None = None) -> str:
    """Run the extracted snippet with FORMAT/ABICHECK_OUTPUT set, return stdout.

    Values are passed via the subprocess environment (not embedded in the
    script text) so no shell-quoting of arbitrary content is needed.
    *effective_fmt*, when given, sets ``$_EFFECTIVE_FORMAT`` too (an
    ``extra-args --format`` override — see ``_effective_format``'s own
    docstring); omitted, the isolated snippet behaves exactly as it did
    before that value existed.
    """
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n",
    ) as f:
        f.write(_summary_fence_region())
        script_path = f.name
    env = dict(os.environ, FORMAT=fmt, ABICHECK_OUTPUT=output)
    if effective_fmt is not None:
        env["_EFFECTIVE_FORMAT"] = effective_fmt
    else:
        env.pop("_EFFECTIVE_FORMAT", None)
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


class TestStepSummaryFullReportFencing:
    def test_markdown_format_not_fenced(self) -> None:
        out = _run("markdown", "# Report\n\nSome **bold** text and a table.")
        assert "```" not in out
        assert "# Report" in out
        assert "Some **bold** text" in out

    def test_markdown_default_not_fenced(self) -> None:
        """FORMAT unset (empty string) falls back to the markdown default."""
        out = _run("", "# Report\n\nDefault format.")
        assert "```" not in out

    def test_json_format_still_fenced(self) -> None:
        out = _run("json", '{"verdict": "COMPATIBLE"}')
        assert "```" in out
        assert '{"verdict": "COMPATIBLE"}' in out

    def test_sarif_format_still_fenced(self) -> None:
        out = _run("sarif", '{"$schema": "sarif"}')
        assert "```" in out

    def test_text_format_still_fenced(self) -> None:
        out = _run("text", "Verdict: COMPATIBLE")
        assert "```" in out


class TestStepSummaryFullReportFencingUsesEffectiveFormat:
    """Codex review, PR #998, fresh evidence: a `format: json` step whose own
    `extra-args` overrides to `--format markdown` (or the reverse) rendered
    the real output under the *wrong* fencing rule when this block still
    checked the nominal `$FORMAT` -- the identical effective-format-override
    class this PR already fixed for report *detection*, now closed here too
    for report *rendering*.
    """

    def test_json_overridden_to_markdown_is_not_fenced(self) -> None:
        out = _run(
            "json",
            "# Report\n\nSome **bold** text.",
            effective_fmt="markdown",
        )
        assert "```" not in out
        assert "# Report" in out

    def test_markdown_overridden_to_json_is_fenced(self) -> None:
        out = _run(
            "markdown",
            '{"verdict": "COMPATIBLE"}',
            effective_fmt="json",
        )
        assert "```" in out
        assert '{"verdict": "COMPATIBLE"}' in out

    def test_falls_back_to_nominal_format_when_effective_format_unset(
        self,
    ) -> None:
        # Isolated callers that never assign `$_EFFECTIVE_FORMAT` (matching
        # every test above this class) must keep behaving exactly as before
        # this fix.
        out = _run("markdown", "# Report\n\nDefault behaviour.")
        assert "```" not in out
