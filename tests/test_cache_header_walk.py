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

"""Contract tests for ``extract.cache_header_scan.iter_cache_header_files``.

The function is the scan half of two AST/snapshot cache keys
(``dumper_ast_config._cache_key``, ``snapshot_cache._hash_include_dir_headers``),
so the property that matters is not "it finds headers" but **exactly which
entries it yields, in exactly which order** -- a change to either silently
invalidates (or, worse, silently *reuses*) every cache entry on disk.

The oracle is the ``sorted(directory.rglob("*"))`` filter the function's own
docstring names as its contract, evaluated independently of the implementation
under test, over generated trees rather than one hand-picked layout: the
traversal was rewritten for speed (``os.scandir`` + a parts-tuple sort instead
of ``rglob`` + a ``Path`` sort), and the interesting failure modes of such a
rewrite are all *ordering* and *edge-entry* ones that a fixed happy-path tree
cannot reach -- component-wise vs. string ordering, symlinked directories,
suffix-named directories, dotfiles, case folding, unicode names.
"""

from __future__ import annotations

import os
import random
from pathlib import Path

import pytest

from abicheck.extract.cache_header_scan import (
    _cache_header_rel_parts,
    iter_cache_header_files,
)
from abicheck.header_utils import CACHE_HEADER_SUFFIXES

# Names chosen so each one reaches a distinct branch of the walk/sort: a
# matching and a non-matching suffix, upper/mixed case, a name that is *only*
# a suffix (``.h`` -- ``Path(".h").suffix`` is empty, so it must NOT match), a
# trailing dot, a directory that itself carries a header suffix, a name that
# is a strict prefix of a sibling (``b`` vs ``b.h`` -- the component-wise
# ordering case), and a non-ASCII name.
_NAMES = (
    "a",
    "b",
    "b.h",
    "b.hpp",
    "A.H",
    "z.inl",
    "z.tcc",
    "z.txt",
    ".h",
    ".hidden.h",
    "w.",
    "dir.h",
    "Ünïcode.hpp",
    "nested",
)


def _oracle(directory: Path) -> list[Path]:
    """The documented contract, evaluated independently of the implementation."""
    return sorted(
        p for p in directory.rglob("*") if p.suffix.lower() in CACHE_HEADER_SUFFIXES
    )


def _build_tree(root: Path, rng: random.Random, depth: int = 0) -> None:
    for name in _NAMES:
        if rng.random() < 0.35:
            continue
        child = root / name
        if depth < 3 and rng.random() < 0.45:
            child.mkdir(exist_ok=True)
            _build_tree(child, rng, depth + 1)
        elif not child.exists():
            child.write_text("x", encoding="utf-8")


@pytest.mark.parametrize("seed", range(12))
def test_matches_sorted_rglob_on_generated_trees(tmp_path: Path, seed: int) -> None:
    """Entries *and* their order match the oracle for any generated layout."""
    root = tmp_path / f"tree{seed}"
    root.mkdir()
    _build_tree(root, random.Random(seed))
    assert iter_cache_header_files(root) == _oracle(root)


def test_component_wise_order_not_string_order(tmp_path: Path) -> None:
    """``a/b/c.h`` precedes ``a/b.h`` -- parts compare, not whole strings.

    Pinned separately from the generated sweep because this is the one
    ordering difference that flips every cache key at once while looking like
    a cosmetic cleanup: ``sorted(str(p) for p in ...)`` puts ``a/b.h`` first.
    """
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c.h").write_text("x", encoding="utf-8")
    (tmp_path / "a" / "b.h").write_text("x", encoding="utf-8")
    walked = [str(p.relative_to(tmp_path)) for p in iter_cache_header_files(tmp_path)]
    assert walked == [os.path.join("a", "b", "c.h"), os.path.join("a", "b.h")]
    assert walked != sorted(walked), "a plain string sort must not reproduce this"
    assert iter_cache_header_files(tmp_path) == _oracle(tmp_path)


def test_directory_named_like_a_header_is_still_an_entry(tmp_path: Path) -> None:
    """A *directory* whose name ends in a header suffix is a hashed input.

    ``rglob("*")`` matches it, so the pre-existing cache keys fold its path
    and mtime in; a "regular files only" narrowing would silently drop that.
    """
    (tmp_path / "gen.h").mkdir()
    (tmp_path / "gen.h" / "real.h").write_text("x", encoding="utf-8")
    assert iter_cache_header_files(tmp_path) == _oracle(tmp_path)
    assert tmp_path / "gen.h" in iter_cache_header_files(tmp_path)


def test_suffix_only_name_does_not_match(tmp_path: Path) -> None:
    """``.h`` is a dotfile with no suffix, not a header (``Path(".h").suffix``)."""
    (tmp_path / ".h").write_text("x", encoding="utf-8")
    (tmp_path / "real.h").write_text("x", encoding="utf-8")
    assert iter_cache_header_files(tmp_path) == [tmp_path / "real.h"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_symlinked_directory_is_not_descended_and_a_loop_terminates(
    tmp_path: Path,
) -> None:
    """Symlinked dirs are not followed (so a loop terminates), file links are entries."""
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "deep.h").write_text("x", encoding="utf-8")
    (root / "loop").symlink_to(root, target_is_directory=True)
    (root / "dir.h").symlink_to(root, target_is_directory=True)
    (root / "alias.h").symlink_to(root / "sub" / "deep.h")
    (root / "broken.hpp").symlink_to(root / "does-not-exist")

    walked = iter_cache_header_files(root)  # must terminate
    assert walked == _oracle(root)
    rel = {str(p.relative_to(root)) for p in walked}
    assert "alias.h" in rel and "broken.hpp" in rel
    assert os.path.join("dir.h", "sub", "deep.h") not in rel


def test_missing_and_unreadable_roots_degrade_to_empty(tmp_path: Path) -> None:
    """A root that cannot be listed yields nothing, exactly as ``rglob`` does."""
    missing = tmp_path / "nope"
    assert iter_cache_header_files(missing) == _oracle(missing) == []
    not_a_dir = tmp_path / "file.h"
    not_a_dir.write_text("x", encoding="utf-8")
    assert iter_cache_header_files(not_a_dir) == _oracle(not_a_dir) == []


def test_relative_and_dot_roots_keep_the_root_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``Path(".")``/relative roots yield the same spellings ``rglob`` does.

    The rewrite joins each entry back onto the caller's own ``directory``
    object; a root with no parts (``Path(".")``) is the case where that can
    silently grow or lose a ``./`` prefix, which would change the hashed
    ``str(f)``.
    """
    (tmp_path / "inc").mkdir()
    (tmp_path / "inc" / "a.h").write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for spelling in (Path("."), Path("inc"), Path("./inc")):
        assert iter_cache_header_files(spelling) == _oracle(spelling)


def test_scan_half_returns_relative_parts(tmp_path: Path) -> None:
    """``_cache_header_rel_parts`` yields root-relative part tuples, unordered."""
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.h").write_text("x", encoding="utf-8")
    (tmp_path / "y.hpp").write_text("x", encoding="utf-8")
    assert set(_cache_header_rel_parts(tmp_path)) == {("a", "x.h"), ("y.hpp",)}
