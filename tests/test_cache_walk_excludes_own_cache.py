"""The D11 content walk never folds abicheck's own cache tree.

Bug class ``cache.key_identity_must_equal_parse_input_content``: with the
cache root under an include root (``XDG_CACHE_HOME`` inside a project tree),
the lock and AST files the parse writes changed the root's digest mid-parse
and the clang backend discarded the result as unstable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.extract.cache_input_digest import tree_digest


@pytest.mark.parametrize("env", ["XDG_CACHE_HOME", "LOCALAPPDATA"])
@pytest.mark.parametrize(
    "written",
    ["locks/k.lock", "clang/ab.json", "snapshots/x.json.gz", "deep/a/b/c"],
)
def test_own_cache_writes_do_not_change_the_root_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: str, written: str
) -> None:
    monkeypatch.setenv(env, str(tmp_path))
    (tmp_path / "api.h").write_text("int f(void);\n")
    before = tree_digest(tmp_path)
    out = tmp_path / "abi_check" / written
    out.parent.mkdir(parents=True)
    out.write_text("payload")
    assert tree_digest(tmp_path) == before


@pytest.mark.parametrize("where", ["sub/abi_check", "abi_check"])
def test_a_project_dir_named_abi_check_is_still_hashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    cache_base = tmp_path / "elsewhere"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_base))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    root = tmp_path / "inc"
    (root / where).mkdir(parents=True)
    header = root / where / "cfg.h"
    header.write_text("#define A 1\n")
    before = tree_digest(root)
    header.write_text("#define A 2\n")
    assert tree_digest(root) != before


def test_a_header_edit_beside_the_cache_still_changes_the_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    (tmp_path / "abi_check" / "locks").mkdir(parents=True)
    header = tmp_path / "api.h"
    header.write_text("int f(void);\n")
    before = tree_digest(tmp_path)
    header.write_text("int f(int);\n")
    assert tree_digest(tmp_path) != before
