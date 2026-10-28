"""``checkout_commit`` over hand-built ``.git`` layouts -- no git binary.

Companion to ``test_build_version.py`` (which uses real ``git`` as the
oracle where it is installed): these layouts are written byte for byte, so
every branch of the reader runs on any machine, including a CI lane whose
git-dependent tests do not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import abicheck
from abicheck.model import tool_version
from abicheck.model.tool_version import checkout_commit

SHA = "0123456789abcdef0123456789abcdef01234567"
_PYPROJECT = '[project]\nname = "abicheck"\n'


def _root(tmp_path: Path, pyproject: str = _PYPROJECT) -> Path:
    root = tmp_path / "co"
    (root / ".git/refs/heads").mkdir(parents=True)
    (root / "pyproject.toml").write_text(pyproject)
    return root


def test_loose_ref(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / ".git/HEAD").write_text("ref: refs/heads/main\n")
    (root / ".git/refs/heads/main").write_text(SHA + "\n")
    assert checkout_commit(root) == SHA


def test_packed_ref_beside_an_undecodable_one(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / ".git/HEAD").write_text("ref: refs/heads/main\n")
    (root / ".git/packed-refs").write_bytes(
        b"# pack-refs with: peeled fully-peeled sorted\n"
        + b"f" * 40
        + b" refs/heads/caf\xe9\n"
        + SHA.encode()
        + b" refs/heads/main\n"
    )
    assert checkout_commit(root) == SHA


def test_detached_head(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / ".git/HEAD").write_text(SHA + "\n")
    assert checkout_commit(root) == SHA


def test_linked_worktree_reads_refs_from_the_common_dir(tmp_path: Path) -> None:
    main = _root(tmp_path)
    (main / ".git/refs/heads/side").write_text(SHA + "\n")
    wt_git = main / ".git/worktrees/wt"
    wt_git.mkdir(parents=True)
    (wt_git / "HEAD").write_text("ref: refs/heads/side\n")
    (wt_git / "commondir").write_text("../..\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "pyproject.toml").write_text(_PYPROJECT)
    (wt / ".git").write_text(f"gitdir: {wt_git}\n")
    assert checkout_commit(wt) == SHA


@pytest.mark.parametrize(
    "case",
    [
        "foreign_project",
        "no_git",
        "ref_missing",
        "ref_missing_from_packed_refs",
        "bad_head_bytes",
        "bad_gitdir_file",
    ],
)
def test_anything_unreadable_yields_none(tmp_path: Path, case: str) -> None:
    root = _root(
        tmp_path,
        '[project]\nname = "other"\n' if case == "foreign_project" else _PYPROJECT,
    )
    (root / ".git/HEAD").write_text("ref: refs/heads/main\n")
    (root / ".git/refs/heads/main").write_text(SHA + "\n")
    if case == "no_git":
        (root / ".git/HEAD").unlink()
    elif case == "ref_missing":
        (root / ".git/refs/heads/main").unlink()
    elif case == "ref_missing_from_packed_refs":
        (root / ".git/refs/heads/main").unlink()
        (root / ".git/packed-refs").write_text(SHA + " refs/heads/other\n")
    elif case == "bad_head_bytes":
        (root / ".git/HEAD").write_bytes(b"ref: refs/heads/\xff\n")
    elif case == "bad_gitdir_file":
        import shutil

        shutil.rmtree(root / ".git")
        (root / ".git").write_text("no separator here\n")
    assert checkout_commit(root) is None


@pytest.mark.parametrize(("sha", "suffix"), [(SHA, "+g012345678"), (None, "")])
def test_build_version_is_release_plus_commit_or_release(
    monkeypatch: pytest.MonkeyPatch, sha: str | None, suffix: str
) -> None:
    monkeypatch.setattr(tool_version, "checkout_commit", lambda root: sha)
    assert tool_version.build_version() == abicheck.__version__ + suffix


def test_build_version_without_installed_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from importlib.metadata import PackageNotFoundError

    def missing(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(tool_version, "_pkg_version", missing)
    monkeypatch.setattr(tool_version, "checkout_commit", lambda root: None)
    assert tool_version.build_version() == "0.0.0.dev0"
