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

"""``action/run.sh``'s scan branch's ``--write json=$PR_JSON`` injection must
key off the *effective* ``--format`` (after any ``extra-args`` override),
not the nominal ``format:`` input -- the compare-mode sibling of this defect
is covered in ``test_action_run_sh_compare_pr_json_write.py`` (ADR-064's
"effective-format-override" gap, Codex review, PR #998, fresh evidence).

Driven through the real ``run.sh`` against a fake ``abicheck`` on ``$PATH``
that records its own argv, same discipline as the compare-mode sibling test
module, so this proves what reaches the command line rather than what the
script's text appears to intend.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from _workflow_exec import bash_executable

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"


def _scan_argv(tmp_path: Path, env_extra: dict[str, str]) -> str:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    captured = tmp_path / "captured_argv.txt"
    stub = fake_bin / "abicheck"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf \'%s\\n\' "$*" >> "{captured}"\n'
        'echo \'{"scan_schema_version":"1.4","verdict":"COMPATIBLE","exit_code":0}\'\n'
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    binary = tmp_path / "lib.so"
    binary.write_bytes(b"\x7fELF" + b"\x00" * 92)

    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env = {
        **base_env,
        "PATH": f"{fake_bin}{os.pathsep}{base_env.get('PATH', '')}",
        "INPUT_MODE": "scan",
        "INPUT_NEW_LIBRARY": str(binary),
        "INPUT_FORMAT": "text",
        "INPUT_ADD_JOB_SUMMARY": "false",
        "INPUT_PR_COMMENT": "true",
        # Pinned, not inherited: these tests need INPUT_PR_COMMENT=true for
        # the injection decision itself to fire, which (unlike the
        # compare-mode sibling tests, which set INPUT_PR_COMMENT=false)
        # leaves _maybe_post_pr_comment's *own* end-of-script gate live --
        # it checks $GITHUB_EVENT_NAME next. An inherited "pull_request"
        # (true on this repo's own real PR-triggered CI runs, absent in an
        # ad hoc local shell) makes it proceed into its reuse-or-rerun
        # logic, and this stub never honors `--write json=PATH`, so it
        # reruns abicheck a second time -- doubling the captured argv and
        # silently corrupting every test in this module that counts
        # `--write` occurrences (reproduced against a real macOS CI run,
        # Codex/CI investigation). Pinning a non-PR event value here makes
        # the outcome depend only on this test's own inputs, not on
        # whichever event happened to trigger the runner.
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_OUTPUT": str(tmp_path / "gh_output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "gh_summary"),
        **env_extra,
    }
    result = subprocess.run(
        [bash_executable(), str(RUN_SH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert captured.is_file(), "abicheck stub was never invoked"
    return captured.read_text(encoding="utf-8").strip()


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanInjectsWriteByEffectiveFormat:
    def test_without_a_user_write_the_internal_one_is_still_injected(
        self, tmp_path: Path
    ) -> None:
        # The negative control (pre-existing behaviour): a plain text scan
        # with pr-comment on still gets the internal --write sidecar.
        argv = _scan_argv(tmp_path, {})
        assert argv.count("--write") == 1, argv
        assert "abicheck-pr-json" in argv, argv

    def test_a_json_primary_still_injects_nothing(self, tmp_path: Path) -> None:
        argv = _scan_argv(tmp_path, {"INPUT_FORMAT": "json"})
        assert "--write" not in argv, argv

    def test_extra_args_overriding_json_away_still_injects_a_write(
        self, tmp_path: Path
    ) -> None:
        # ADR-064's effective-format-override gap: `format: json` nominally
        # means "the primary is already JSON" -- but extra-args overriding
        # to `--format text` (Click keeps only the last occurrence) really
        # does make the primary output text, so the sidecar must still be
        # injected based on the *effective* format.
        argv = _scan_argv(
            tmp_path,
            {"INPUT_FORMAT": "json", "INPUT_EXTRA_ARGS": "--format text"},
        )
        assert argv.count("--write") == 1, argv
        assert "abicheck-pr-json" in argv, argv

    def test_extra_args_overriding_to_json_still_injects_nothing(
        self, tmp_path: Path
    ) -> None:
        argv = _scan_argv(
            tmp_path,
            {"INPUT_FORMAT": "text", "INPUT_EXTRA_ARGS": "--format json"},
        )
        assert "--write" not in argv, argv

    def test_a_user_write_suppresses_the_internal_one(self, tmp_path: Path) -> None:
        # Pre-existing behaviour, pinned alongside the new cases above so a
        # future edit to this same injection can't silently regress it.
        argv = _scan_argv(tmp_path, {"INPUT_EXTRA_ARGS": "--write json=mine.json"})
        assert argv.count("--write") == 1, argv
        assert "mine.json" in argv
        assert "abicheck-pr-json" not in argv, argv

    def test_an_effective_dry_run_via_extra_args_suppresses_the_injection(
        self, tmp_path: Path
    ) -> None:
        # Codex review, P2, fresh evidence: `INPUT_DRY_RUN` is a dedicated
        # input, so an effective dry run reached only through `extra-args
        # --dry-run` still took this command-assembly's non-dry-run branch
        # and injected `--write json=$PR_JSON` alongside it -- a combination
        # the CLI itself rejects, turning a clean dry-run preview into a
        # usage error. Sharpened by ADR-063 Track T8's own fix making this
        # injection unconditional on `pr-comment`, which widened exactly
        # this gap's exposure.
        argv = _scan_argv(tmp_path, {"INPUT_EXTRA_ARGS": "--dry-run"})
        assert "--write" not in argv, argv
        assert "--dry-run" in argv, argv

    def test_an_effective_dry_run_also_suppresses_output_file_forwarding(
        self, tmp_path: Path
    ) -> None:
        # A second Codex review round (fresh evidence beyond the sidecar
        # fix above) found the identical gap one line earlier: the
        # `output-file` input's own `-o "$OUTPUT_FILE"` forwarding sat
        # above the `--write` guard in the same non-dry-run branch, so it
        # was never suppressed by the earlier fix either -- `-o` is just
        # as mutually exclusive with a real `--dry-run` as `--write` is.
        argv = _scan_argv(
            tmp_path,
            {
                "INPUT_EXTRA_ARGS": "--dry-run",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
            },
        )
        assert "-o" not in argv.split(), argv
        assert "--dry-run" in argv, argv
