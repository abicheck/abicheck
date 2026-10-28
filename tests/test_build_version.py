"""``abicheck --version`` names the checkout commit a dev build ran.

**Bug class:** a build identity that cannot tell two builds apart. After the
0.7.0 release, every main commit still reported ``abicheck 0.7.0``, so a
field report on ``main 0101b71ab`` was indistinguishable from the release.

**General invariant:** for any state git can leave a checkout in -- loose
ref, packed ref, detached HEAD, linked worktree -- ``_checkout_commit``
returns exactly ``git rev-parse HEAD`` (the oracle, a separate program), and
it returns ``None`` for a repository that is not abicheck's, so an installed
wheel never borrows a host repository's commit.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import abicheck
from abicheck import _checkout_commit

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

_ABICHECK_PYPROJECT = '[project]\nname = "abicheck"\nversion = "0.7.0"\n'


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(path: Path, pyproject: str = _ABICHECK_PYPROJECT) -> Path:
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    (path / "pyproject.toml").write_text(pyproject)
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "c1")
    (path / "f").write_text("x")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "c2")
    return path


def _loose(repo: Path) -> Path:
    return repo


def _packed(repo: Path) -> Path:
    _git(repo, "pack-refs", "--all")
    assert not (repo / ".git/refs/heads/main").exists()
    return repo


def _detached(repo: Path) -> Path:
    _git(repo, "checkout", "-q", "--detach", "HEAD~1")
    return repo


def _worktree(repo: Path) -> Path:
    wt = repo.parent / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "side", str(wt), "HEAD~1")
    return wt


@pytest.mark.parametrize("state", [_loose, _packed, _detached, _worktree])
def test_checkout_commit_matches_git_rev_parse(tmp_path: Path, state) -> None:
    root = state(_repo(tmp_path / "repo"))
    assert _checkout_commit(root) == _git(root, "rev-parse", "HEAD")


def test_a_foreign_repository_is_never_read(tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo", pyproject='[project]\nname = "other"\n')
    assert _checkout_commit(root) is None


def test_no_checkout_means_plain_release_number(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(_ABICHECK_PYPROJECT)
    assert _checkout_commit(tmp_path) is None  # pyproject but no .git
    assert _checkout_commit(tmp_path / "missing") is None


@pytest.mark.parametrize(
    ("sha", "suffix"), [("0123456789abcdef", "+g012345678"), (None, "")]
)
def test_build_version_is_release_plus_commit_or_release(
    monkeypatch, sha: str | None, suffix: str
) -> None:
    monkeypatch.setattr(abicheck, "_checkout_commit", lambda root: sha)
    assert abicheck.build_version() == abicheck.__version__ + suffix
