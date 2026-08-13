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

"""Behavioral tests for ``action/run.sh``'s ``_maybe_post_pr_comment`` MODE
gate — extracted verbatim (same discipline as ``test_action_run_sh_pr_json.py``
and ``test_action_run_sh_helpers.py``) rather than hand-duplicated, so a
future edit to the real gate can't silently drift from what's tested here.

Only the MODE dispatch (``compare``/``scan`` proceed, everything else is a
no-op) and the ``scan --artifact-set`` skip are exercised — the rest of
``_maybe_post_pr_comment`` (JSON acquisition, PR-number resolution, posting)
is already covered by ``test_action_run_sh_pr_json.py`` and doesn't depend on
MODE.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_START_MARKER = "_maybe_post_pr_comment() {"
_END_MARKER = (
    'echo "abicheck: scan --artifact-set has no single-artifact JSON shape; '
    'skipping PR comment."\n    return 0\n  fi\n'
)


def _mode_gate_fragment() -> str:
    """The MODE dispatch + ``scan --artifact-set`` guard at the top of
    ``_maybe_post_pr_comment``, extracted verbatim from run.sh — up to (not
    including) the pull_request-event check, since these tests never reach
    that far.
    """
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_START_MARKER)
    end = text.index(_END_MARKER, start) + len(_END_MARKER)
    body = text[start:end]
    # Close the function (the real one continues past the artifact-set
    # guard; this fragment stops right after its closing `fi`, a complete,
    # balanced sub-body) so it parses as a callable function on its own.
    return body + "  return 0\n}\n"


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


def _run(mode: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    script = _mode_gate_fragment() + '\n_maybe_post_pr_comment\necho "REACHED"\n'
    env = dict(os.environ)
    env["MODE"] = mode
    env["INPUT_PR_COMMENT"] = env.get("INPUT_PR_COMMENT", "true")
    for k, v in (extra_env or {}).items():
        env[k] = v
    return subprocess.run(
        [_bash_executable(), "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_compare_mode_passes_the_gate():
    result = _run("compare")
    assert result.returncode == 0, result.stderr
    assert "REACHED" in result.stdout


def test_scan_mode_passes_the_gate():
    result = _run("scan")
    assert result.returncode == 0, result.stderr
    assert "REACHED" in result.stdout


def test_dump_mode_is_a_no_op():
    # `_maybe_post_pr_comment` returns before the fragment's own trailing
    # `return 0`/echo — the case statement's `*) return 0 ;;` fires first,
    # so the caller-visible "REACHED" line still prints (it's outside the
    # function), but nothing inside the gate ran.
    result = _run("dump")
    assert result.returncode == 0, result.stderr
    assert "REACHED" in result.stdout
    assert "skipping PR comment" not in result.stdout


def test_scan_artifact_set_is_skipped_with_a_diagnostic():
    result = _run("scan", {"SCAN_ARTIFACT_SET": "/some/dir"})
    assert result.returncode == 0, result.stderr
    assert "no single-artifact JSON shape" in result.stdout
    assert "REACHED" in result.stdout


def test_scan_without_artifact_set_is_not_skipped():
    result = _run("scan", {"SCAN_ARTIFACT_SET": ""})
    assert result.returncode == 0, result.stderr
    assert "no single-artifact JSON shape" not in result.stdout


def test_pr_comment_false_disables_every_mode():
    result = _run("scan", {"INPUT_PR_COMMENT": "false"})
    assert result.returncode == 0, result.stderr
    assert "no single-artifact JSON shape" not in result.stdout
