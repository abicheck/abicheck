from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import patch

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "restore_git_mtimes.py"
_SPEC = importlib.util.spec_from_file_location("restore_git_mtimes", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
restore_git_mtimes = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(restore_git_mtimes)


def _fake_git_log(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git"], returncode=0, stdout=stdout, stderr=""
    )


def test_last_commit_mtimes_takes_newest_seen_timestamp_per_path() -> None:
    # git log is newest-first: a path touched by two commits must resolve to
    # the newer (first-seen) timestamp, not the older one.
    log_output = "@@@200\nexamples/case01/v1.h\nexamples/case01/v2.h\n@@@100\nexamples/case01/v1.h\n"
    with patch("subprocess.run", return_value=_fake_git_log(log_output)) as mock_run:
        mtimes = restore_git_mtimes._last_commit_mtimes(["examples"])

    assert mtimes == {
        "examples/case01/v1.h": 200,
        "examples/case01/v2.h": 200,
    }
    args = mock_run.call_args.args[0]
    assert args[:2] == ["git", "log"]
    assert args[-1] == "examples"


def test_last_commit_mtimes_ignores_blank_lines_between_commits() -> None:
    log_output = "@@@50\n\nexamples/case02/v1.h\n\n"
    with patch("subprocess.run", return_value=_fake_git_log(log_output)):
        mtimes = restore_git_mtimes._last_commit_mtimes(["examples"])

    assert mtimes == {"examples/case02/v1.h": 50}


def test_restore_mtimes_sets_utime_and_skips_missing_files(tmp_path: Path) -> None:
    present = tmp_path / "present.h"
    present.write_text("content")
    log_output = f"@@@1000\n{present.name}\nmissing.h\n"

    with (
        patch("subprocess.run", return_value=_fake_git_log(log_output)),
        patch.object(restore_git_mtimes, "REPO_DIR", tmp_path),
    ):
        touched = restore_git_mtimes.restore_mtimes(["."])

    # Only the file that actually exists on disk is touched; the git-log
    # entry for a since-deleted file is silently skipped, not an error.
    assert touched == 1
    assert int(present.stat().st_mtime) == 1000


def test_restore_git_mtimes_reproduces_deterministic_timestamp_from_real_repo_history() -> (
    None
):
    # End-to-end against this actual repo: touching an already-committed
    # tracked file to an arbitrary mtime and then restoring it must land back
    # on its real last-commit timestamp, not an arbitrary or "current time"
    # value — otherwise a persisted CI cache keyed on this would never hit.
    import os
    import time

    tracked = restore_git_mtimes.REPO_DIR / "pyproject.toml"
    before = os.stat(tracked).st_mtime
    os.utime(tracked, (time.time(), time.time()))
    try:
        mtimes = restore_git_mtimes._last_commit_mtimes(["pyproject.toml"])
        assert "pyproject.toml" in mtimes
        committed_ts = mtimes["pyproject.toml"]

        touched = restore_git_mtimes.restore_mtimes(["pyproject.toml"])

        assert touched == 1
        assert int(os.stat(tracked).st_mtime) == committed_ts
    finally:
        os.utime(tracked, (before, before))
