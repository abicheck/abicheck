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

"""The header-scan memo never serves a result the scan would not produce now.

Oracle: the undecorated scan (``__wrapped__``) run against the current
files. Checked after every step of generated edit sequences -- in-place
rewrites that keep size and mtime, include targets appearing and
disappearing, and deletions -- alongside direct checks that the memo does
skip work on an unchanged set, which is its whole reason to exist.
"""

from __future__ import annotations

import os
import random
from pathlib import Path

import pytest

from abicheck import dumper_ast_config_cpp20 as cpp20
from abicheck.extract.header_scan_memo import memoize_header_scan
from abicheck.extract.quoted_include_expansion import _expand_with_quoted_includes

_SCAN = cpp20._find_cpp20_requirements
_CPP20 = "template <class T> concept Addable = requires(T a) { a + a; };\n"
_CPP17 = "template <class T> struct Addable { T value; };\n"


@pytest.fixture(autouse=True)
def _fresh_memo():
    _SCAN.cache_clear()
    yield
    _SCAN.cache_clear()


def _write_keep_stat(path: Path, text: str) -> None:
    """Rewrite *path* but restore its mtime -- the case mtime stamps miss."""
    st = path.stat() if path.exists() else None
    path.write_bytes(text.encode())
    if st is not None:
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def _check(headers: list[Path], **kwargs) -> None:
    got = _SCAN(headers, **kwargs)
    assert got == _SCAN.__wrapped__(headers, **kwargs)


def test_unchanged_set_is_served_without_rescanning(tmp_path, monkeypatch):
    h = tmp_path / "a.h"
    h.write_bytes(_CPP20.encode())
    first = _SCAN([h])
    calls = []
    real = cpp20._preprocess_headers
    monkeypatch.setattr(
        cpp20, "_preprocess_headers", lambda *a, **k: calls.append(1) or real(*a, **k)
    )
    assert _SCAN([h]) == first and first
    assert calls == []


def test_same_size_same_mtime_rewrite_is_a_miss(tmp_path):
    h = tmp_path / "a.h"
    h.write_bytes(_CPP20.encode())
    assert _SCAN([h])
    # Same length, same mtime, no C++20 syntax any more.
    _write_keep_stat(
        h, _CPP20.replace("concept", "xoncept").replace("requires", "xequires")
    )
    assert h.stat().st_size == len(_CPP20)
    _check([h])
    assert not _SCAN([h])


def test_include_target_appearing_later_is_a_miss(tmp_path):
    umbrella = tmp_path / "all.h"
    umbrella.write_bytes(b'#include "impl.h"\n')
    assert _SCAN([umbrella]) == []
    (tmp_path / "impl.h").write_bytes(_CPP20.encode())
    _check([umbrella])
    assert _SCAN([umbrella])


@pytest.mark.parametrize(
    ("spelling", "premade_dirs"),
    [
        ("sub/impl.h", ["sub"]),  # lookup dir exists, file does not
        ("../common.h", []),  # lookup dir is the including dir's parent
        ("a/b/impl.h", []),  # no directory on the way exists yet
        ("a/b/impl.h", ["a"]),  # part of the way exists
    ],
)
def test_include_resolving_later_outside_the_including_dir_is_a_miss(
    tmp_path, spelling, premade_dirs
):
    root = tmp_path / "inc"
    root.mkdir()
    for d in premade_dirs:
        (root / d).mkdir(parents=True)
    umbrella = root / "all.h"
    umbrella.write_bytes((f'#include "{spelling}"\n').encode())
    assert _SCAN([umbrella]) == []
    target = root / spelling
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_CPP20.encode())
    _check([umbrella])
    assert _SCAN([umbrella])


@pytest.mark.parametrize("seed", range(20))
def test_generated_nested_trees_match_a_fresh_scan(tmp_path, seed):
    """Includes crossing directories, with targets created and removed."""
    rng = random.Random(seed)
    dirs = ["", "sub", "sub/deep", "other"]
    names = [f"{d}/h{i}.h".lstrip("/") for d in dirs for i in range(2)]
    root = tmp_path / "tree"

    def include_text(from_name: str) -> str:
        here = Path(from_name).parent
        target = Path(rng.choice(names))
        rel = os.path.relpath(target, here)
        return f'#include "{Path(rel).as_posix()}"\n'

    def text_for(name: str) -> str:
        return rng.choice([_CPP20, _CPP17, include_text(name), include_text(name)])

    present = set(rng.sample(names, k=4))
    for n in names:
        if n in present:
            (root / n).parent.mkdir(parents=True, exist_ok=True)
            (root / n).write_bytes(text_for(n).encode())
    roots = [root / n for n in sorted(present)[:2]]
    for _ in range(10):
        _check(roots)
        n = rng.choice(names)
        target = root / n
        if target.exists() and target not in roots and rng.random() < 0.3:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_keep_stat(target, text_for(n))
    _check(roots)


def test_keyword_arguments_are_separate_entries(tmp_path):
    h = tmp_path / "a.h"
    h.write_bytes(("#ifdef __cplusplus\n" + _CPP20 + "#endif\n").encode())
    _check([h])
    _check([h], for_language_mode_decision=True)


def test_returned_list_is_a_copy(tmp_path):
    h = tmp_path / "a.h"
    h.write_bytes(_CPP20.encode())
    _SCAN([h]).clear()
    assert _SCAN([h])


@pytest.mark.parametrize("seed", range(30))
def test_generated_edit_sequences_match_a_fresh_scan(tmp_path, seed):
    rng = random.Random(seed)
    names = [f"h{i}.h" for i in range(4)]
    texts = [_CPP20, _CPP17, '#include "h{}.h"\n', "// nothing\n"]

    def render() -> str:
        t = rng.choice(texts)
        return t.format(rng.randrange(len(names))) if "{}" in t else t

    for n in names:
        (tmp_path / n).write_bytes(render().encode())
    roots = [tmp_path / n for n in rng.sample(names, k=2)]
    for _ in range(12):
        _check(roots)
        _check(roots, for_language_mode_decision=True)
        target = tmp_path / rng.choice(names)
        op = rng.random()
        if op < 0.2 and target.exists() and target not in roots:
            target.unlink()
        elif op < 0.6:
            _write_keep_stat(target, render())
        else:
            target.write_bytes(render().encode())
    _check(roots)


def test_memo_is_bounded(tmp_path):
    calls = []

    @memoize_header_scan(_expand_with_quoted_includes)
    def scan(paths: list[Path]) -> list[str]:
        calls.append(1)
        return [p.name for p in paths]

    files = []
    for i in range(80):
        f = tmp_path / f"f{i}.h"
        f.write_bytes(b"x\n")
        files.append(f)
        scan([f])
    calls.clear()
    scan([files[-1]])  # recent: still cached
    scan([files[0]])  # oldest: evicted
    assert calls == [1]
