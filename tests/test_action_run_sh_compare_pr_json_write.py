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

"""``action/run.sh``'s compare branch must not inject a losing ``--write``.

Compare mode adds an internal ``--write json=$PR_JSON`` so the sticky PR
comment can reuse this run's own analysis instead of re-invoking abicheck.
``extra-args`` is appended *after* it and Click honors the last occurrence,
so when the user's own ``extra-args`` already carries ``--write`` the
injected one loses and ``$PR_JSON`` stays empty -- at which point
``_maybe_post_pr_comment`` reruns the whole comparison purely to obtain
JSON, doubling a potentially expensive analysis to produce the very file the
injection existed to avoid rerunning for (Codex review).

The scan branch already guarded this with ``_extra_args_has_write_flag``;
compare did not. Driven through the real ``run.sh`` against a fake
``abicheck`` on ``$PATH`` that records its own argv, so this proves what
reaches the command line rather than what the script appears to intend.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from _workflow_exec import bash_executable

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"


def _compare_argv(tmp_path: Path, env_extra: dict[str, str]) -> str:
    """Run compare mode and return the argv the CLI stub actually received."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    captured = tmp_path / "captured_argv.txt"
    stub = fake_bin / "abicheck"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf \'%s\\n\' "$*" >> "{captured}"\n'
        'echo \'{"verdict":"COMPATIBLE"}\'\n'
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    old_json = tmp_path / "old.json"
    new_json = tmp_path / "new.json"
    old_json.write_text("{}", encoding="utf-8")
    new_json.write_text("{}", encoding="utf-8")

    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env = {
        **base_env,
        "PATH": f"{fake_bin}{os.pathsep}{base_env.get('PATH', '')}",
        "INPUT_MODE": "compare",
        "INPUT_OLD_LIBRARY": str(old_json),
        "INPUT_NEW_LIBRARY": str(new_json),
        "INPUT_FORMAT": "markdown",
        "INPUT_ADD_JOB_SUMMARY": "false",
        "INPUT_PR_COMMENT": "false",
        "GITHUB_OUTPUT": str(tmp_path / "gh_output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "gh_summary"),
        **env_extra,
    }
    result = subprocess.run(
        [bash_executable(), str(RUN_SH)],
        capture_output=True, text=True, env=env, cwd=tmp_path, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert captured.is_file(), "abicheck stub was never invoked"
    return captured.read_text(encoding="utf-8").strip()


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestCompareDoesNotInjectALosingWrite:
    @pytest.mark.parametrize("spelling", ["--write json=mine.json", "--write=json=mine.json"])
    def test_a_user_write_suppresses_the_internal_one(
        self, tmp_path: Path, spelling: str
    ) -> None:
        # Both documented spellings, since the guard matches on the raw
        # string and a separator-only difference would slip past a check
        # written for just one of them.
        argv = _compare_argv(tmp_path, {"INPUT_EXTRA_ARGS": spelling})
        assert argv.count("--write") == 1, argv
        assert "mine.json" in argv
        # The injected one names a mktemp path under RUNNER_TEMP/tmp; its
        # absence is the whole point.
        assert "abicheck-pr-json" not in argv, argv

    def test_without_a_user_write_the_internal_one_is_still_injected(
        self, tmp_path: Path
    ) -> None:
        # The negative control: the guard must not disable the injection
        # outright, or every non-JSON PR run pays for the rerun this exists
        # to avoid.
        argv = _compare_argv(tmp_path, {})
        assert argv.count("--write") == 1, argv
        assert "abicheck-pr-json" in argv, argv

    def test_a_json_primary_still_injects_nothing(self, tmp_path: Path) -> None:
        # Pre-existing behaviour, pinned here so the new guard cannot be
        # mistaken for what suppresses this case.
        argv = _compare_argv(tmp_path, {"INPUT_FORMAT": "json"})
        assert "--write" not in argv, argv


def test_both_branches_share_the_same_write_guard() -> None:
    """compare and scan must not drift apart on this.

    Only scan carried the guard, which is exactly how compare shipped
    without it; asserting both call sites reference the shared helper keeps
    a future edit to one from silently leaving the other behind.
    """
    text = RUN_SH.read_text(encoding="utf-8")
    guarded = [
        line for line in text.splitlines() if "_extra_args_has_write_flag" in line
    ]
    # One definition, one docstring cross-reference per branch, and two
    # actual call sites -- assert on the calls specifically.
    calls = [line for line in guarded if "! _extra_args_has_write_flag" in line]
    assert len(calls) == 2, guarded
