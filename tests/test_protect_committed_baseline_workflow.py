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

"""Tests for ``.github/workflows/protect-committed-baseline.yml`` (P1,
baseline-storage architecture review): the trusted gate against an ordinary
PR silently "approving itself" by updating a committed baseline it is also
compared against.

Structural tests assert the workflow's shape (``workflow_call`` only,
read-only permissions, a SHA-pinned checkout) the way
``tests/test_publish_baseline_workflows.py`` does for its siblings.
Behavioral tests extract the check step's ``run:`` script verbatim and
execute it against a real disposable git repository -- the same
"parse the real file, don't hand-copy it" discipline
``test_action_run_sh_dry_run_baseline.py`` uses for ``action/run.sh``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "protect-committed-baseline.yml"
)


def _load() -> dict[str, Any]:
    with WORKFLOW_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _check_job(data: dict[str, Any]) -> dict[str, Any]:
    return data["jobs"]["check"]


def _check_step(data: dict[str, Any]) -> dict[str, Any]:
    for step in _check_job(data)["steps"]:
        if (
            step.get("name")
            == "Check whether this PR modifies a protected baseline path"
        ):
            return step
    raise AssertionError("check step not found")


class TestStructure:
    def test_parses_as_valid_workflow_yaml(self) -> None:
        data = _load()
        assert data["jobs"]["check"]["steps"]

    def test_is_workflow_call_only(self) -> None:
        # Never pull_request/pull_request_target directly -- the calling
        # repository's own pull_request-triggered workflow decides when
        # this runs, same convention as publish-baseline.yml/
        # update-main-baseline.yml (ADR-047 §12).
        data = _load()
        assert set(data[True]) == {"workflow_call"}

    def test_permissions_are_read_only(self) -> None:
        data = _load()
        job = _check_job(data)
        perms = job["permissions"]
        assert perms == {"contents": "read", "pull-requests": "read"}

    def test_checkout_is_sha_pinned_with_fetch_depth_0(self) -> None:
        data = _load()
        job = _check_job(data)
        checkout_step = next(s for s in job["steps"] if "uses" in s)
        assert "@" in checkout_step["uses"]
        ref = checkout_step["uses"].split("@", 1)[1]
        assert len(ref) == 40, "expected a full commit SHA, not a floating tag"
        assert checkout_step["with"]["fetch-depth"] == 0
        assert checkout_step["with"]["persist-credentials"] is False

    def test_required_input_has_no_default(self) -> None:
        data = _load()
        protected_paths = data[True]["workflow_call"]["inputs"]["protected-paths"]
        assert protected_paths["required"] is True
        assert "default" not in protected_paths

    def test_bypass_label_defaults_to_disabled(self) -> None:
        data = _load()
        bypass = data[True]["workflow_call"]["inputs"]["bypass-label"]
        assert bypass["default"] == ""

    def test_residual_ruleset_gap_is_documented(self) -> None:
        # Pins the presence of the "known, acknowledged residual gap"
        # comment (Codex review, second round): the .github/workflows/**
        # guard closes "reconfigure protected-paths while still calling
        # this workflow", but nothing inside a workflow_call reusable
        # workflow can observe or prevent the CALLING workflow choosing
        # not to invoke it at all -- that needs a Ruleset the calling
        # repository controls. A silent removal of this documentation
        # would leave a real, structural limitation unexplained.
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        assert "Require workflows to pass" in text
        assert "Rulesets" in text


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


def _check_script() -> str:
    step = _check_step(_load())
    return step["run"]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)


def _commit_all(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return _git(repo, "rev-parse", "HEAD")


@pytest.mark.skipif(
    not WORKFLOW_PATH.is_file(), reason="protect-committed-baseline.yml not found"
)
class TestCheckScriptBehavior:
    def _run(
        self,
        repo: Path,
        *,
        base_sha: str,
        head_sha: str,
        protected_paths: str,
        bypass_label: str = "",
        pr_labels: list[str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "PROTECTED_PATHS": protected_paths,
            "BYPASS_LABEL": bypass_label,
            "BASE_SHA": base_sha,
            "HEAD_SHA": head_sha,
            "PR_LABELS_JSON": json.dumps(pr_labels or []),
        }
        # A temp-file invocation, not `bash -c "<script>"` -- the extracted
        # check script has grown large enough (workflow-file guard,
        # residual-gap documentation, the corrected glob translator) that
        # Windows' Git-Bash `-c` argument passing truncates it mid-parse,
        # surfacing as a bare "unterminated f-string"/"here-document
        # delimited by end-of-file" syntax error purely from where the
        # string got cut, not a real syntax error in the script itself
        # (confirmed: identical content passed via a file runs cleanly;
        # reproduced by a real windows-latest CI failure on this exact
        # test file). Mirrors test_action_run_sh_baseline_set_fallback.py's
        # and test_action_run_sh_dry_run_baseline.py's identical
        # `_run_bash_script` helper.
        fd, path = tempfile.mkstemp(suffix=".sh")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(_check_script())
            return subprocess.run(
                [_bash_executable(), path],
                capture_output=True,
                text=True,
                env=env,
                cwd=repo,
                check=False,
            )
        finally:
            os.unlink(path)

    def test_fails_when_pr_touches_a_protected_baseline_file(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "abi").mkdir()
        (repo / "abi" / "libfoo.abicheck.json").write_text("old", encoding="utf-8")
        (repo / "src.c").write_text("old", encoding="utf-8")
        base_sha = _commit_all(repo, "base")

        (repo / "abi" / "libfoo.abicheck.json").write_text("new", encoding="utf-8")
        (repo / "src.c").write_text("new", encoding="utf-8")
        head_sha = _commit_all(repo, "changes both code and baseline")

        result = self._run(
            repo, base_sha=base_sha, head_sha=head_sha, protected_paths="abi/**"
        )
        assert result.returncode == 1
        assert "abi/libfoo.abicheck.json" in result.stdout
        assert "silently approve itself" in result.stdout

    def test_passes_when_pr_does_not_touch_protected_paths(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "abi").mkdir()
        (repo / "abi" / "libfoo.abicheck.json").write_text("old", encoding="utf-8")
        (repo / "src.c").write_text("old", encoding="utf-8")
        base_sha = _commit_all(repo, "base")

        (repo / "src.c").write_text("new", encoding="utf-8")
        head_sha = _commit_all(repo, "code only")

        result = self._run(
            repo, base_sha=base_sha, head_sha=head_sha, protected_paths="abi/**"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "No protected baseline files" in result.stdout

    def test_bypass_label_allows_a_reviewed_refresh(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "abi").mkdir()
        (repo / "abi" / "libfoo.abicheck.json").write_text("old", encoding="utf-8")
        base_sha = _commit_all(repo, "base")

        (repo / "abi" / "libfoo.abicheck.json").write_text(
            "refreshed", encoding="utf-8"
        )
        head_sha = _commit_all(repo, "baseline refresh")

        # Without the label: fails.
        result = self._run(
            repo,
            base_sha=base_sha,
            head_sha=head_sha,
            protected_paths="abi/**",
            bypass_label="baseline-refresh",
            pr_labels=[],
        )
        assert result.returncode == 1

        # With the label: passes, and says so.
        result = self._run(
            repo,
            base_sha=base_sha,
            head_sha=head_sha,
            protected_paths="abi/**",
            bypass_label="baseline-refresh",
            pr_labels=["baseline-refresh", "unrelated-label"],
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::notice::" in result.stdout

    def test_workflow_file_change_is_protected_even_outside_protected_paths(
        self, tmp_path: Path
    ) -> None:
        # Regression (Codex review, P1): this reusable workflow's own
        # protected-paths/bypass-label configuration is supplied by the
        # CALLING workflow file, and for an ordinary pull_request trigger
        # that file is read from the PR's own head commit -- so a PR could
        # otherwise edit the calling workflow (e.g. narrowing
        # protected-paths to a glob that no longer matches) AND the
        # committed baseline in the same change, defeating this check
        # entirely. A change under .github/workflows/ is now ALWAYS
        # protected, independent of what protected-paths names.
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "abi").mkdir()
        (repo / "abi" / "libfoo.abicheck.json").write_text("old", encoding="utf-8")
        (repo / ".github" / "workflows").mkdir(parents=True)
        (repo / ".github" / "workflows" / "check-pr.yml").write_text(
            "old", encoding="utf-8"
        )
        base_sha = _commit_all(repo, "base")

        # Only the workflow file changes -- protected-paths ("abi/**")
        # doesn't even mention .github/workflows/, so this must be caught
        # by the always-on workflow-file guard alone.
        (repo / ".github" / "workflows" / "check-pr.yml").write_text(
            "new", encoding="utf-8"
        )
        head_sha = _commit_all(repo, "reconfigure the calling workflow")

        result = self._run(
            repo, base_sha=base_sha, head_sha=head_sha, protected_paths="abi/**"
        )
        assert result.returncode == 1
        assert ".github/workflows/check-pr.yml" in result.stdout
        assert "own protected-paths/bypass-label" in result.stdout

    def test_workflow_file_and_baseline_change_together_is_protected(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "abi").mkdir()
        (repo / "abi" / "libfoo.abicheck.json").write_text("old", encoding="utf-8")
        (repo / ".github" / "workflows").mkdir(parents=True)
        (repo / ".github" / "workflows" / "check-pr.yml").write_text(
            "old", encoding="utf-8"
        )
        base_sha = _commit_all(repo, "base")

        (repo / "abi" / "libfoo.abicheck.json").write_text("new", encoding="utf-8")
        (repo / ".github" / "workflows" / "check-pr.yml").write_text(
            "new", encoding="utf-8"
        )
        head_sha = _commit_all(repo, "reconfigure the workflow and the baseline")

        result = self._run(
            repo, base_sha=base_sha, head_sha=head_sha, protected_paths="abi/**"
        )
        assert result.returncode == 1
        assert "abi/libfoo.abicheck.json" in result.stdout
        assert ".github/workflows/check-pr.yml" in result.stdout
        assert "doubly suspicious" in result.stdout

    def test_bypass_label_also_covers_a_workflow_file_change(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / ".github" / "workflows").mkdir(parents=True)
        (repo / ".github" / "workflows" / "check-pr.yml").write_text(
            "old", encoding="utf-8"
        )
        base_sha = _commit_all(repo, "base")

        (repo / ".github" / "workflows" / "check-pr.yml").write_text(
            "new", encoding="utf-8"
        )
        head_sha = _commit_all(repo, "workflow change")

        result = self._run(
            repo,
            base_sha=base_sha,
            head_sha=head_sha,
            protected_paths="abi/**",
            bypass_label="baseline-refresh",
            pr_labels=["baseline-refresh"],
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::notice::" in result.stdout

    def test_a_lone_star_does_not_cross_a_path_separator(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "baselines" / "linux-x86_64").mkdir(parents=True)
        (repo / "baselines" / "linux-x86_64" / "libfoo.abicheck.json").write_text(
            "old", encoding="utf-8"
        )
        base_sha = _commit_all(repo, "base")

        # Protected pattern only covers one directory level deep --
        # baselines/<profile>/<file>.abicheck.json -- a file nested one
        # level deeper must NOT match a lone "*".
        (repo / "baselines" / "linux-x86_64" / "extra").mkdir()
        (
            repo / "baselines" / "linux-x86_64" / "extra" / "libfoo.abicheck.json"
        ).write_text("new", encoding="utf-8")
        head_sha = _commit_all(repo, "add a nested file")

        result = self._run(
            repo,
            base_sha=base_sha,
            head_sha=head_sha,
            protected_paths="baselines/*/*.abicheck.json",
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_double_star_crosses_path_separators(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "abi" / "sub" / "dir").mkdir(parents=True)
        (repo / "abi" / "sub" / "dir" / "libfoo.abicheck.json").write_text(
            "old", encoding="utf-8"
        )
        base_sha = _commit_all(repo, "base")
        (repo / "abi" / "sub" / "dir" / "libfoo.abicheck.json").write_text(
            "new", encoding="utf-8"
        )
        head_sha = _commit_all(repo, "nested change")

        result = self._run(
            repo, base_sha=base_sha, head_sha=head_sha, protected_paths="abi/**"
        )
        assert result.returncode == 1

    def test_double_star_slash_matches_only_complete_segments(
        self, tmp_path: Path
    ) -> None:
        # Regression (Codex review, P2): "**/" was translated to ".*"
        # followed by an OPTIONAL "/", which also matches a PARTIAL
        # segment -- for pattern "baselines/**/manifest.json", the ".*"
        # could consume "not" out of "notmanifest.json" and the optional
        # "/" then matched zero times, so this pattern wrongly matched
        # "baselines/notmanifest.json" too. "**/" must only ever match
        # zero or more COMPLETE path segments.
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "baselines").mkdir()
        (repo / "baselines" / "notmanifest.json").write_text("old", encoding="utf-8")
        base_sha = _commit_all(repo, "base")

        (repo / "baselines" / "notmanifest.json").write_text("new", encoding="utf-8")
        head_sha = _commit_all(repo, "unrelated file changed")

        result = self._run(
            repo,
            base_sha=base_sha,
            head_sha=head_sha,
            protected_paths="baselines/**/manifest.json",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "No protected baseline files" in result.stdout

        # A genuine match (a real "manifest.json" at any depth) must
        # still be caught.
        (repo / "baselines" / "sub").mkdir()
        (repo / "baselines" / "sub" / "manifest.json").write_text(
            "old", encoding="utf-8"
        )
        base_sha2 = _commit_all(repo, "add real manifest")
        (repo / "baselines" / "sub" / "manifest.json").write_text(
            "new", encoding="utf-8"
        )
        head_sha2 = _commit_all(repo, "change real manifest")

        result2 = self._run(
            repo,
            base_sha=base_sha2,
            head_sha=head_sha2,
            protected_paths="baselines/**/manifest.json",
        )
        assert result2.returncode == 1
        assert "baselines/sub/manifest.json" in result2.stdout

    def test_unrelated_directory_sharing_a_leaf_name_is_not_matched(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "src" / "abi").mkdir(parents=True)
        (repo / "src" / "abi" / "note.md").write_text("old", encoding="utf-8")
        base_sha = _commit_all(repo, "base")
        (repo / "src" / "abi" / "note.md").write_text("new", encoding="utf-8")
        head_sha = _commit_all(repo, "unrelated change")

        result = self._run(
            repo, base_sha=base_sha, head_sha=head_sha, protected_paths="abi/**"
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_missing_protected_paths_is_a_usage_error(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "f.txt").write_text("a", encoding="utf-8")
        base_sha = _commit_all(repo, "base")
        (repo / "f.txt").write_text("b", encoding="utf-8")
        head_sha = _commit_all(repo, "change")

        result = self._run(
            repo, base_sha=base_sha, head_sha=head_sha, protected_paths=""
        )
        assert result.returncode == 1
        assert "resolved to no patterns" in result.stdout + result.stderr

    def test_missing_base_or_head_sha_is_a_usage_error(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "f.txt").write_text("a", encoding="utf-8")
        _commit_all(repo, "base")

        result = self._run(repo, base_sha="", head_sha="", protected_paths="abi/**")
        assert result.returncode == 1
        assert "could not be resolved" in result.stdout + result.stderr

    def test_a_renamed_protected_file_is_still_caught(self, tmp_path: Path) -> None:
        # Regression for the self-approval gap a rename can otherwise open:
        # with git's default rename detection, `git diff --name-only` shows
        # only the destination path for a detected rename, and if that
        # destination falls outside every protected glob, the source path
        # -- the one that actually matches -- never appears in the diff at
        # all. The check step passes `--no-renames` specifically so both
        # paths are always listed.
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "abi").mkdir()
        content = "x" * 500 + "\n"
        (repo / "abi" / "libfoo.abicheck.json").write_text(content, encoding="utf-8")
        base_sha = _commit_all(repo, "base")

        # Move the protected baseline out to an unprotected path, with a
        # small edit -- similar enough content for git to detect this as a
        # rename rather than an unrelated add+delete.
        (repo / "abi" / "libfoo.abicheck.json").unlink()
        (repo / "notes.md").write_text(content + "y\n", encoding="utf-8")
        head_sha = _commit_all(repo, "rename baseline out of the protected dir")

        # Confirm the fixture actually exercises rename detection before
        # trusting the result below.
        diff_with_renames = _git(
            repo, "diff", "--name-only", f"{base_sha}...{head_sha}"
        )
        assert diff_with_renames == "notes.md", (
            "fixture did not produce a detected rename: " + diff_with_renames
        )

        result = self._run(
            repo, base_sha=base_sha, head_sha=head_sha, protected_paths="abi/**"
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "abi/libfoo.abicheck.json" in result.stdout

    def test_multiple_protected_path_patterns(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "abi").mkdir()
        (repo / "baselines").mkdir()
        (repo / "abi" / "a.json").write_text("old", encoding="utf-8")
        (repo / "baselines" / "b.json").write_text("old", encoding="utf-8")
        base_sha = _commit_all(repo, "base")
        (repo / "baselines" / "b.json").write_text("new", encoding="utf-8")
        head_sha = _commit_all(repo, "change second pattern's file")

        result = self._run(
            repo,
            base_sha=base_sha,
            head_sha=head_sha,
            protected_paths="abi/**\nbaselines/**",
        )
        assert result.returncode == 1
        assert "baselines/b.json" in result.stdout

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "A literal newline is not a legal NTFS filename character -- "
            "writing this fixture file fails with OSError [Errno 22] on a "
            "Windows runner (confirmed in CI) before this test's own logic "
            "ever runs. POSIX filesystems (what this workflow's real "
            "ubuntu-latest runner uses) allow it, which is the actual "
            "regression being guarded here."
        ),
    )
    def test_protected_path_containing_a_newline_is_still_caught(
        self, tmp_path: Path
    ) -> None:
        # Regression (Codex review): ordinary newline-delimited
        # `git diff --name-only` output C-quotes a pathname containing a
        # control character (a literal newline here), so the matcher
        # would previously see the quoted, escaped string
        # ("abi/weird\\nfile.json") rather than the real path -- which
        # never matches "abi/**" -- letting a changed protected file with
        # this shape of name evade the check entirely. The script now
        # uses `git diff -z` (NUL-delimited, real bytes) via a temp file
        # instead of an env var.
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "abi").mkdir()
        weird_name = "abi/weird\nfile.json"
        (repo / weird_name).write_text("old", encoding="utf-8")
        base_sha = _commit_all(repo, "base")
        (repo / weird_name).write_text("new", encoding="utf-8")
        head_sha = _commit_all(repo, "change the newline-named file")

        # Confirm the fixture actually exercises a real embedded newline
        # in the tracked pathname before trusting the result below.
        tracked = _git(repo, "ls-files")
        assert "\\n" in tracked or "\n" in weird_name

        result = self._run(
            repo, base_sha=base_sha, head_sha=head_sha, protected_paths="abi/**"
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "weird" in result.stdout and "file.json" in result.stdout
