# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""A relative spelling's cached alias belongs to the directory it was
resolved from, never to whichever directory asked first."""

from __future__ import annotations

import itertools

from abicheck.extract.path_aliases import (
    canonical_spelling,
    clear_path_alias_caches,
    source_header_alias_segments,
)


def test_relative_spelling_resolves_per_working_directory(tmp_path, monkeypatch):
    rels = ["old/include", "old/include/lib/api.hpp", "./old/include"]
    roots = []
    for name in ("a", "b", "c"):
        root = tmp_path / name
        (root / "old/include/lib").mkdir(parents=True)
        (root / "old/include/lib/api.hpp").write_text("")
        roots.append(root)
    # Interleave directories and spellings so any cache sharing would show.
    for root, rel in itertools.product(roots * 2, rels):
        monkeypatch.chdir(root)
        expected = str((root / rel).resolve())
        assert canonical_spelling(rel) == expected
        # Oracle: the segments of this directory's own resolved path.
        assert source_header_alias_segments(expected)[
            0
        ] in source_header_alias_segments(rel)


def test_absolute_spelling_is_independent_of_the_working_directory(
    tmp_path, monkeypatch
):
    target = tmp_path / "t"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    answers = set()
    for cwd in (tmp_path, target):
        monkeypatch.chdir(cwd)
        answers.add(canonical_spelling(str(link)))
    assert answers == {str(target.resolve())}


def test_clear_still_drops_both_caches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "d").mkdir()
    assert canonical_spelling("d") == str((tmp_path / "d").resolve())
    (tmp_path / "d").rmdir()
    clear_path_alias_caches()
    assert canonical_spelling("d") is None
