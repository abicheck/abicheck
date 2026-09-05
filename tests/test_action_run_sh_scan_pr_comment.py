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
import re
import subprocess
from pathlib import Path

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_START_MARKER = "_maybe_post_pr_comment() {"
_END_MARKER = (
    'echo "abicheck: scan --artifact-set has no single-artifact JSON shape; '
    'skipping PR comment."\n    return 0\n  fi\n'
)
#: The dry-run/``pr-comment-on: never``/ERROR/BUDGET_OVERFLOW guards that
#: immediately follow the ``scan --artifact-set`` block, up to (not
#: including) the pull_request-event check.
_VERDICT_GUARDS_END_MARKER = '[[ "$VERDICT" == "BUDGET_OVERFLOW" ]] && return 0\n'
#: Dependency order matters: `_extra_args_has_dry_run_flag` calls
#: `_extra_args_options`, which calls `_extra_args_is_value_option`.
_DRY_RUN_FLAG_HELPER_MARKERS = (
    "_extra_args_is_value_option() {",
    "_extra_args_options() {",
    "_extra_args_has_dry_run_flag() {",
)


def _extra_args_has_dry_run_flag_source() -> str:
    """`_maybe_post_pr_comment`'s own dry-run guard (Codex review, P2, fresh
    evidence) now calls this helper too -- extracted verbatim, the same
    discipline as the fragments below, and prepended (along with the two
    functions it now calls in turn -- a later Codex review round made this
    a shared, option/value-aware tokenizer instead of a standalone token
    scan) so the isolated function body doesn't hit "command not found"
    for a real caller it depends on."""
    text = RUN_SH.read_text(encoding="utf-8")
    parts = []
    for marker in _DRY_RUN_FLAG_HELPER_MARKERS:
        start = text.index(marker)
        end = text.index("\n}\n", start) + len("\n}\n")
        parts.append(text[start:end])
    return "\n".join(parts)


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
    # The "GATE_PASSED" echo (CodeRabbit review) is emitted only from
    # *inside* the function, right before its own `return 0` -- unlike the
    # caller's trailing "REACHED" echo (outside the function, always
    # printed regardless of what the gate did), this can only appear if
    # every guard in the fragment above actually let execution fall
    # through, so a test asserting it can't pass vacuously.
    return body + '  echo "GATE_PASSED"\n  return 0\n}\n'


def _mode_and_verdict_gate_fragment() -> str:
    """As :func:`_mode_gate_fragment`, but extended to also include the
    dry-run/``pr-comment-on: never``/ERROR/BUDGET_OVERFLOW early-return
    guards -- verbatim, up to (not including) the pull_request-event check.
    """
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_START_MARKER)
    end = text.index(_VERDICT_GUARDS_END_MARKER, start) + len(
        _VERDICT_GUARDS_END_MARKER
    )
    body = text[start:end]
    return (
        _extra_args_has_dry_run_flag_source()
        + "\n"
        + body
        + '  echo "PAST_VERDICT_GUARDS"\n  return 0\n}\n'
    )


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
    mode: str, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    script = _mode_gate_fragment() + '\n_maybe_post_pr_comment\necho "REACHED"\n'
    env = dict(os.environ)
    env["MODE"] = mode
    # Pinned unconditionally (CodeRabbit review), not inherited from the
    # caller's environment: an already-exported INPUT_PR_COMMENT=false or
    # SCAN_ARTIFACT_SET in the test runner's own environment would silently
    # route these tests through the skip path, and the trailing "REACHED"
    # echo (outside the function) would still make them look like they
    # passed. extra_env can still override either, same as before.
    env["INPUT_PR_COMMENT"] = "true"
    env["SCAN_ARTIFACT_SET"] = ""
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
    assert "GATE_PASSED" in result.stdout


def test_scan_mode_passes_the_gate():
    result = _run("scan")
    assert result.returncode == 0, result.stderr
    assert "GATE_PASSED" in result.stdout


def test_dump_mode_is_a_no_op():
    # `_maybe_post_pr_comment` returns before the fragment's own trailing
    # `return 0`/"GATE_PASSED" echo — the case statement's `*) return 0 ;;`
    # fires first, so the caller-visible "REACHED" line still prints (it's
    # outside the function), but nothing inside the gate ran.
    result = _run("dump")
    assert result.returncode == 0, result.stderr
    assert "REACHED" in result.stdout
    assert "GATE_PASSED" not in result.stdout
    assert "skipping PR comment" not in result.stdout


def test_scan_artifact_set_is_skipped_with_a_diagnostic():
    result = _run("scan", {"SCAN_ARTIFACT_SET": "/some/dir"})
    assert result.returncode == 0, result.stderr
    assert "no single-artifact JSON shape" in result.stdout
    assert "REACHED" in result.stdout
    assert "GATE_PASSED" not in result.stdout


def test_scan_without_artifact_set_is_not_skipped():
    result = _run("scan", {"SCAN_ARTIFACT_SET": ""})
    assert result.returncode == 0, result.stderr
    assert "no single-artifact JSON shape" not in result.stdout
    assert "GATE_PASSED" in result.stdout


def test_pr_comment_false_disables_every_mode():
    result = _run("scan", {"INPUT_PR_COMMENT": "false"})
    assert result.returncode == 0, result.stderr
    assert "no single-artifact JSON shape" not in result.stdout
    assert "GATE_PASSED" not in result.stdout


def _run_verdict_guards(verdict: str) -> subprocess.CompletedProcess:
    script = (
        _mode_and_verdict_gate_fragment() + '\n_maybe_post_pr_comment\necho "REACHED"\n'
    )
    env = dict(os.environ)
    env["MODE"] = "scan"
    env["INPUT_PR_COMMENT"] = "true"
    env["INPUT_DRY_RUN"] = "false"
    env["INPUT_PR_COMMENT_ON"] = "changes"
    env["VERDICT"] = verdict
    return subprocess.run(
        [_bash_executable(), "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_budget_overflow_verdict_skips_the_comment():
    # Codex review: scan's own `_BudgetOverflow` handler
    # (`abicheck/cli_scan.py`) exits 5 before `_emit_scan_report` ever runs,
    # so there is no `--write`/primary JSON to reuse -- letting
    # this VERDICT through re-runs the identical budget-limited scan a
    # second time only to hit the same overflow again.
    result = _run_verdict_guards("BUDGET_OVERFLOW")
    assert result.returncode == 0, result.stderr
    assert "PAST_VERDICT_GUARDS" not in result.stdout
    assert "REACHED" in result.stdout


def test_error_verdict_still_skips_the_comment():
    result = _run_verdict_guards("ERROR")
    assert result.returncode == 0, result.stderr
    assert "PAST_VERDICT_GUARDS" not in result.stdout


def test_compatible_verdict_passes_the_verdict_guards():
    result = _run_verdict_guards("COMPATIBLE")
    assert result.returncode == 0, result.stderr
    assert "PAST_VERDICT_GUARDS" in result.stdout


def test_an_effective_dry_run_via_extra_args_skips_the_comment():
    # Codex review, P2, fresh evidence: `INPUT_DRY_RUN` alone used to be
    # checked here, so a caller passing `--dry-run` through `extra-args`
    # (with the dedicated input left false) fell through into this
    # function's own JSON-acquisition path -- launching a doomed second
    # invocation (retaining `--dry-run` while appending `--format json
    # -o ...`) instead of the clean, silent skip a real dry run gets.
    script = (
        _mode_and_verdict_gate_fragment() + '\n_maybe_post_pr_comment\necho "REACHED"\n'
    )
    env = dict(os.environ)
    env["MODE"] = "scan"
    env["INPUT_PR_COMMENT"] = "true"
    env["INPUT_DRY_RUN"] = "false"
    env["INPUT_PR_COMMENT_ON"] = "changes"
    env["VERDICT"] = "COMPATIBLE"
    env["INPUT_EXTRA_ARGS"] = "--dry-run"
    result = subprocess.run(
        [_bash_executable(), "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "PAST_VERDICT_GUARDS" not in result.stdout
    assert "REACHED" in result.stdout


def test_pr_comment_renderer_uses_resolved_py_bin_not_bare_python3():
    # Codex review: on a Windows Git Bash runner, actions/setup-python
    # exposes python/python.exe but not always python3 -- a hard-coded
    # `python3 -m abicheck.cli_pr_comment` would silently fail (swallowed
    # by the trailing `|| true`), leaving PR_BODY empty and the comment
    # skipped or an existing sticky one deleted. Every other Python
    # invocation in this script already goes through the resolved
    # $_PY_BIN; the renderer must too.
    #
    # A later Codex review (fresh evidence, PR #774) found the original
    # `-m abicheck.cli_pr_comment` shape inserts this process's CWD into
    # sys.path[0], letting a malicious PR's own checked-out
    # abicheck/cli_pr_comment.py shadow the real, pip-installed module --
    # converted to an equivalent `-c '...runpy.run_module(...)...'`
    # invocation running from $_PY_SAFE_DIR (the same checkout-isolation
    # mechanism every other abicheck-importing inline script in this file
    # now uses) to close that. runpy.run_module's own module-name string
    # argument is asserted here instead of the old literal `-m` spelling.
    text = RUN_SH.read_text(encoding="utf-8")
    # Codex review (fresh evidence): assert the isolation wrapper and the
    # runpy call as one connected block, not as two independent substring
    # matches -- either alone could pass against a script that moved the
    # renderer back out of $_PY_SAFE_DIR while some *other* invocation still
    # contains a matching wrapper/runpy line elsewhere in the file.
    assert re.search(
        r'cd "\$_PY_SAFE_DIR" && PYTHONPATH= "\$_PY_BIN" -c \'\n'
        r"import runpy\n+"
        r'runpy\.run_module\("abicheck\.cli_pr_comment", run_name="__main__"\)',
        text,
    ), "the PR-comment renderer is no longer invoked from inside the isolation wrapper"
    assert "python3 -m abicheck.cli_pr_comment" not in text
