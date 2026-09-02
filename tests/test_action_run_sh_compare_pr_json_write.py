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
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _workflow_exec import bash_executable

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_REAL_ABICHECK = shutil.which("abicheck")


def _compare_argv(
    tmp_path: Path,
    env_extra: dict[str, str],
    *,
    old: Path | None = None,
    new: Path | None = None,
) -> str:
    """Run compare mode and return the argv the CLI stub actually received.

    *old*/*new* default to a pair of single-file JSON snapshots; pass a
    directory to exercise the release-style-operand path instead (CLI
    cleanup phase two, PR E: `--write` now applies there too).
    """
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

    if old is None:
        old = tmp_path / "old.json"
        old.write_text("{}", encoding="utf-8")
    if new is None:
        new = tmp_path / "new.json"
        new.write_text("{}", encoding="utf-8")

    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env = {
        **base_env,
        "PATH": f"{fake_bin}{os.pathsep}{base_env.get('PATH', '')}",
        "INPUT_MODE": "compare",
        "INPUT_OLD_LIBRARY": str(old),
        "INPUT_NEW_LIBRARY": str(new),
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

    def test_extra_args_overriding_json_away_still_injects_a_write(
        self, tmp_path: Path
    ) -> None:
        # ADR-064's effective-format-override gap (Codex review, PR #998,
        # fresh evidence): `format: json` nominally means "the primary is
        # already JSON, no secondary needed" -- but `extra-args` overriding
        # to `--format text` (Click keeps only the last occurrence) really
        # does make the primary output text, so skipping the injection here
        # left such a run with no JSON report anywhere. The injection must
        # fire based on the *effective* format, not the nominal one.
        argv = _compare_argv(
            tmp_path,
            {"INPUT_FORMAT": "json", "INPUT_EXTRA_ARGS": "--format text"},
        )
        assert argv.count("--write") == 1, argv
        assert "abicheck-pr-json" in argv, argv

    def test_extra_args_overriding_to_json_still_injects_nothing(
        self, tmp_path: Path
    ) -> None:
        # The reverse direction: a nominal non-json primary whose own
        # extra-args overrides *to* json needs no secondary either -- the
        # primary already is one.
        argv = _compare_argv(
            tmp_path,
            {"INPUT_FORMAT": "markdown", "INPUT_EXTRA_ARGS": "--format json"},
        )
        assert "--write" not in argv, argv


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestCompareInjectsWriteForReleaseStyleOperandToo:
    """CLI cleanup phase two, PR E: the `--write` injection used to skip a
    directory/package (release-style) operand outright, since the CLI
    itself used to reject `--write` there. Now that the CLI supports it
    (see cli_compare_release.py's own `secondary_output_options`), this
    injection must reach a directory operand's argv exactly the same way
    it already does for a single-file one -- driven through the real
    `run.sh` against a fake `abicheck` on `$PATH` that records its own
    argv, so this proves what reaches the command line, not just what the
    script's text appears to intend.
    """

    def test_write_reaches_argv_for_a_directory_operand(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old_release"
        new_dir = tmp_path / "new_release"
        old_dir.mkdir()
        new_dir.mkdir()
        argv = _compare_argv(tmp_path, {}, old=old_dir, new=new_dir)
        assert argv.count("--write") == 1, argv
        assert "abicheck-pr-json" in argv, argv

    def test_a_user_write_still_suppresses_the_internal_one_for_a_directory(
        self, tmp_path: Path
    ) -> None:
        old_dir = tmp_path / "old_release"
        new_dir = tmp_path / "new_release"
        old_dir.mkdir()
        new_dir.mkdir()
        argv = _compare_argv(
            tmp_path,
            {"INPUT_EXTRA_ARGS": "--write json=mine.json"},
            old=old_dir,
            new=new_dir,
        )
        assert argv.count("--write") == 1, argv
        assert "mine.json" in argv
        assert "abicheck-pr-json" not in argv, argv


@pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or _REAL_ABICHECK is None
    or not RUN_SH.is_file(),
    reason="Linux-only (matches test_action_baseline.py's own real-abicheck "
    "end-to-end precedent) with a real abicheck binary and action/run.sh",
)
class TestRealAbicheckWritesPersistedAnnotationsForADirectoryOperand:
    """The genuinely end-to-end proof this contract needs (not a stub that
    could pass regardless of what the real CLI does): the real `abicheck`
    binary, run by the real `run.sh` against a real directory operand with
    a genuine breaking-change pair of snapshots, actually produces the
    `--write`-injected JSON file, and that file's `libraries[].annotations`
    is exactly what CLI cleanup phase two, PR E added.
    """

    def test_write_json_is_a_real_file_with_persisted_annotations(
        self, tmp_path: Path
    ) -> None:
        import json

        from abicheck.model import AbiSnapshot, Function, Visibility
        from abicheck.serialization import snapshot_to_json

        old_dir = tmp_path / "old_release"
        new_dir = tmp_path / "new_release"
        old_dir.mkdir()
        new_dir.mkdir()

        def _fn(name: str, mangled: str) -> Function:
            return Function(
                name=name,
                mangled=mangled,
                return_type="void",
                visibility=Visibility.PUBLIC,
            )

        old_snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("foo", "_Z3foov"), _fn("bar", "_Z3barv")],
            from_headers=True,
        )
        new_snap = AbiSnapshot(
            library="libfoo.so",
            version="2.0",
            functions=[_fn("foo", "_Z3foov")],
            from_headers=True,
        )
        (old_dir / "libfoo.json").write_text(
            snapshot_to_json(old_snap), encoding="utf-8"
        )
        (new_dir / "libfoo.json").write_text(
            snapshot_to_json(new_snap), encoding="utf-8"
        )

        # An explicit, own-chosen --write path via extra-args, rather than
        # relying on the internal $PR_JSON injection: run.sh's own EXIT trap
        # unconditionally removes $PR_JSON (it exists only to feed the PR
        # comment step, never meant to survive the run) -- an internally
        # injected path would already be gone by the time this test's own
        # subprocess.run() returns and can inspect it. Using extra-args also
        # suppresses that internal injection outright (already proven by
        # TestCompareInjectsWriteForReleaseStyleOperandToo above), so there
        # is exactly one --write in play here.
        write_path = tmp_path / "release_write.json"
        base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
        # Real abicheck already resolved via $PATH -- no fake bin prepended.
        env = {
            **base_env,
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": str(old_dir),
            "INPUT_NEW_LIBRARY": str(new_dir),
            "INPUT_FORMAT": "markdown",
            "INPUT_ADD_JOB_SUMMARY": "false",
            "INPUT_PR_COMMENT": "false",
            "INPUT_EXTRA_ARGS": f"--write json={write_path}",
            "GITHUB_OUTPUT": str(tmp_path / "gh_output"),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "gh_summary"),
        }
        result = subprocess.run(
            [bash_executable(), str(RUN_SH)],
            capture_output=True, text=True, env=env, cwd=tmp_path, check=False,
        )
        # run.sh's own fail-on-breaking wrapper maps a real ABI break to
        # exit 1 (its own step-failure convention), not abicheck's raw
        # exit 4 -- confirmed against the actual output below.
        assert result.returncode == 1, result.stdout + result.stderr
        assert "verdict: BREAKING" in result.stdout, result.stdout
        assert write_path.is_file(), result.stdout + result.stderr
        data = json.loads(write_path.read_text(encoding="utf-8"))
        [lib] = data["libraries"]
        assert lib["verdict"] == "BREAKING"
        assert lib["annotations"], "expected persisted annotations on the write file"
        assert any(e["level"] == "error" for e in lib["annotations"])


class TestSarifDefaultOutputFileUsesEffectiveFormat:
    """Codex review, PR #998, fresh evidence: `format: sarif` with no
    explicit `output-file:` defaults `OUTPUT_FILE` to
    `abicheck-results.sarif` -- the exact path a workflow's own
    `upload-sarif: true` step looks for. That default used to key off the
    nominal `$FORMAT`, so `extra-args: --format json` (or any other
    override away from sarif) still produced the default sarif-shaped
    filename while writing genuinely non-SARIF content into it -- silently
    feeding mismatched content to the CodeQL upload step. Gating the
    default on the effective format instead means an override away from
    sarif gets no default `-o` at all (output goes to stdout), so an
    unrelated upload-sarif step fails loudly on a missing file rather than
    silently uploading the wrong content.
    """

    def test_sarif_overridden_away_gets_no_default_output_file(
        self, tmp_path: Path
    ) -> None:
        argv = _compare_argv(
            tmp_path,
            {"INPUT_FORMAT": "sarif", "INPUT_EXTRA_ARGS": "--format json"},
        )
        assert "abicheck-results.sarif" not in argv, argv
        assert "-o" not in argv.split(), argv

    def test_plain_sarif_still_gets_the_default_output_file(
        self, tmp_path: Path
    ) -> None:
        # Negative control: the ordinary, unoverridden case must keep
        # working exactly as before.
        argv = _compare_argv(tmp_path, {"INPUT_FORMAT": "sarif"})
        assert "abicheck-results.sarif" in argv, argv

    def test_extra_args_overriding_to_sarif_still_gets_the_default(
        self, tmp_path: Path
    ) -> None:
        # The reverse direction: a nominal non-sarif primary overridden *to*
        # sarif should still get the default sarif filename.
        argv = _compare_argv(
            tmp_path,
            {"INPUT_FORMAT": "markdown", "INPUT_EXTRA_ARGS": "--format sarif"},
        )
        assert "abicheck-results.sarif" in argv, argv


def _compare_github_output(
    tmp_path: Path, env_extra: dict[str, str]
) -> tuple[str, str]:
    """Run compare mode with a stub that actually honors ``-o PATH`` (writing
    real content there, unlike ``_compare_argv``'s stdout-only stub, since
    this test cares whether ``$OUTPUT_FILE`` ends up a real, non-empty file)
    and return ``($GITHUB_OUTPUT file contents, run.sh's own stdout+stderr)``
    -- the latter so a caller can confirm a workflow-command annotation
    (``::warning::``) actually reached the log rather than being silently
    swallowed into the environment file.
    """
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    stub = fake_bin / "abicheck"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "_out=''\n"
        'while [[ $# -gt 0 ]]; do\n'
        '  case "$1" in\n'
        "    -o) _out=\"$2\"; shift 2 ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        'if [[ -n "$_out" ]]; then\n'
        '  printf \'not-actually-sarif\\n\' > "$_out"\n'
        "else\n"
        "  echo '{\"verdict\":\"COMPATIBLE\"}'\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    old_json = tmp_path / "old.json"
    new_json = tmp_path / "new.json"
    old_json.write_text("{}", encoding="utf-8")
    new_json.write_text("{}", encoding="utf-8")
    github_output = tmp_path / "gh_output"
    github_output.write_text("")

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
        "GITHUB_OUTPUT": str(github_output),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "gh_summary"),
        **env_extra,
    }
    result = subprocess.run(
        [bash_executable(), str(RUN_SH)],
        capture_output=True, text=True, env=env, cwd=tmp_path, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return github_output.read_text(encoding="utf-8"), result.stdout + result.stderr


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestSarifUploadReportPathWithheldOnEffectiveFormatMismatch:
    """Codex review, PR #998, fresh evidence (second round): the earlier
    SARIF fix only covered the no-explicit-`output-file` default path.
    `format: sarif` + `upload-sarif: true` + an *explicit* `output-file:`
    + `extra-args: --format json` still wrote real (non-SARIF) content to
    that explicit path, and `report-path` -- the exact value
    `action.yml`'s upload-sarif step's own `if:` condition gates on
    (`steps.run-abicheck.outputs.report-path != ''`) -- was still published
    unconditionally whenever the file existed, regardless of whether its
    content actually matched what was requested. Withholding `report-path`
    specifically for this dangerous combination closes both the default and
    explicit-path cases at once, without needing a new Action output or an
    action.yml change: the upload step already refuses to run without one.
    """

    def test_explicit_output_file_withholds_report_path_on_mismatch(
        self, tmp_path: Path
    ) -> None:
        explicit = tmp_path / "myreport.sarif"
        out, log = _compare_github_output(
            tmp_path,
            {
                "INPUT_FORMAT": "sarif",
                "INPUT_UPLOAD_SARIF": "true",
                "INPUT_OUTPUT_FILE": str(explicit),
                "INPUT_EXTRA_ARGS": "--format json",
            },
        )
        assert explicit.is_file()  # the mismatched content really was written
        assert "report-path=\n" in out or out.rstrip().endswith("report-path="), out
        # The warning must reach the log, not the environment file (Codex
        # review, PR #998, fresh evidence: an earlier revision echoed it
        # *inside* the redirected `{ ... } >> "$GITHUB_OUTPUT"` block).
        assert "::warning" in log, log
        assert "::warning" not in out, out

    def test_default_output_file_withholds_report_path_on_mismatch(
        self, tmp_path: Path
    ) -> None:
        # The no-explicit-output-file default path, now checked via
        # report-path directly rather than only via -o's absence from argv.
        out, log = _compare_github_output(
            tmp_path,
            {
                "INPUT_FORMAT": "sarif",
                "INPUT_UPLOAD_SARIF": "true",
                "INPUT_EXTRA_ARGS": "--format json",
            },
        )
        assert "report-path=\n" in out or out.rstrip().endswith("report-path="), out
        assert "::warning" in log, log
        assert "::warning" not in out, out

    def test_matching_sarif_still_publishes_report_path(self, tmp_path: Path) -> None:
        # Negative control: the ordinary, unoverridden sarif+upload
        # combination must keep publishing report-path exactly as before.
        out, log = _compare_github_output(
            tmp_path,
            {"INPUT_FORMAT": "sarif", "INPUT_UPLOAD_SARIF": "true"},
        )
        assert "report-path=abicheck-results.sarif" in out, out
        assert "::warning" not in log, log

    def test_mismatch_without_upload_sarif_still_publishes_report_path(
        self, tmp_path: Path
    ) -> None:
        # The suppression is scoped to the upload-sarif combination
        # specifically -- report-path for any other purpose is unaffected.
        explicit = tmp_path / "myreport.sarif"
        out, log = _compare_github_output(
            tmp_path,
            {
                "INPUT_FORMAT": "sarif",
                "INPUT_OUTPUT_FILE": str(explicit),
                "INPUT_EXTRA_ARGS": "--format json",
            },
        )
        assert f"report-path={explicit}" in out, out
        assert "::warning" not in log, log


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
