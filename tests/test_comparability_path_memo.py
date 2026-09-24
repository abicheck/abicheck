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

"""The extraction-contract path memo changes cost, never a fingerprint field.

``comparability_fields`` memoizes ``Path.resolve()`` and each path's ancestor
set for the duration of one ``_compute_profile_fields``/``_compute_scope_fields``
call. The oracle is the same function run *without* the memo
(``__wrapped__`` with no memo active is the pre-memo code path, which resolves
every path afresh) -- not a second implementation of the field rules. Trees
are generated with nested and overlapping include roots, symlinked
directories, ``..`` spellings, duplicates and system-bucket files, since those
are the shapes where resolved identity and spelled identity diverge.
"""

from __future__ import annotations

import os
import random
from pathlib import Path

import pytest

from abicheck import comparability_fields as cf
from abicheck.comparability_fields import IncludeDir


def _tree(root: Path, rng: random.Random) -> dict[str, list[Path]]:
    """A small project with headers, include roots, symlinks and a sysroot."""
    dirs = [
        root / "proj" / "include",
        root / "proj" / "include" / "sub",
        root / "proj" / "gen",
        root / "shared" / "include" / "detail",
        root / "sys" / "usr" / "include",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for d in dirs:
        for i in range(rng.randint(1, 4)):
            f = d / f"h{i}.h"
            f.write_text(f"// {d.name} {i} {rng.random()}\n", encoding="utf-8")
            files.append(f)
    link = root / "proj" / "linked"
    if not link.exists():
        os.symlink(root / "shared" / "include", link)
    # Spellings of the same files that resolve identically but differ as text.
    aliases = [link / "detail" / p.name for p in files if p.parent.name == "detail"]
    aliases += [p.parent / ".." / p.parent.name / p.name for p in files[:3]]
    return {"dirs": dirs + [link, root / "proj"], "files": files + aliases}


def _case(tmp_path: Path, seed: int) -> dict:
    rng = random.Random(seed)
    tree = _tree(tmp_path, rng)
    headers = [p for p in tree["files"] if "sys" not in p.parts]
    declared = rng.sample(headers, k=rng.randint(1, min(6, len(headers))))
    declared += rng.sample(
        declared, k=rng.randint(0, min(2, len(declared)))
    )  # duplicates
    includes = []
    for d in rng.sample(tree["dirs"], k=rng.randint(1, len(tree["dirs"]))):
        label = "support" if rng.random() < 0.15 else None
        includes.append(IncludeDir(d, label))
    includes += rng.sample(
        includes, k=rng.randint(0, min(2, len(includes)))
    )  # repeated -I
    depfile = rng.sample(tree["files"], k=rng.randint(1, len(tree["files"])))
    depfile += rng.sample(depfile, k=rng.randint(0, min(3, len(depfile))))
    public = rng.sample(headers, k=rng.randint(0, min(3, len(headers))))
    return {
        "declared": declared,
        "includes": includes,
        "depfile": depfile,
        "public": public,
        "dirs": tree["dirs"],
    }


def _profile(fn, case: dict) -> dict[str, str]:
    return fn(
        compiler_family="gcc",
        compiler_version="13",
        abi_dialect=None,
        language_standard="c++17",
        target_triple="x86_64-linux-gnu",
        pointer_width=64,
        endianness="little",
        macro_ops=[("D", "X=1")],
        pass_through_flags=["-fno-rtti"],
        declared_headers=case["declared"],
        declared_includes=case["includes"],
        depfile_resolved_paths=case["depfile"],
        generated_driver_path=None,
        public_header_paths=case["public"],
        frontend_context_kind=None,
    )


@pytest.mark.parametrize("seed", range(40))
def test_profile_fields_identical_with_and_without_memo(tmp_path, seed):
    case = _case(tmp_path, seed)
    assert cf._PATH_MEMO.get() is None
    expected = _profile(cf._compute_profile_fields.__wrapped__, case)
    assert _profile(cf._compute_profile_fields, case) == expected


@pytest.mark.parametrize("seed", range(40))
def test_scope_fields_identical_with_and_without_memo(tmp_path, seed):
    case = _case(tmp_path, seed)
    public_dirs = case["dirs"][: 1 + seed % 3]
    args = (case["declared"], case["public"], public_dirs, None)
    expected = cf._compute_scope_fields.__wrapped__(*args)
    assert cf._compute_scope_fields(*args) == expected


def test_ancestor_predicate_agrees_with_memo_on_every_pair(tmp_path):
    case = _case(tmp_path, 7)
    paths = case["dirs"] + case["depfile"]
    plain = {(a, b): cf._is_ancestor_or_equal(a, b) for a in paths for b in paths}
    token = cf._PATH_MEMO.set({})
    try:
        memo = {(a, b): cf._is_ancestor_or_equal(a, b) for a in paths for b in paths}
    finally:
        cf._PATH_MEMO.reset(token)
    assert memo == plain
    assert any(plain.values()) and not all(plain.values())  # non-vacuous


def test_memo_is_scoped_to_one_computation(tmp_path):
    case = _case(tmp_path, 3)
    _profile(cf._compute_profile_fields, case)
    assert cf._PATH_MEMO.get() is None


def test_memo_sees_a_filesystem_change_between_computations(tmp_path):
    """A symlink retargeted between two calls changes the answer."""
    (tmp_path / "a" / "inc").mkdir(parents=True)
    (tmp_path / "b" / "inc").mkdir(parents=True)
    header = tmp_path / "a" / "inc" / "x.h"
    header.write_text("int x;\n", encoding="utf-8")
    link = tmp_path / "root"
    os.symlink(tmp_path / "a", link)
    case = {
        "declared": [link / "inc" / "x.h"],
        "includes": [IncludeDir(tmp_path / "a")],
        "depfile": [link / "inc" / "x.h"],
        "public": [],
        "dirs": [],
    }
    first = _profile(cf._compute_profile_fields, case)
    link.unlink()
    os.symlink(tmp_path / "b", link)
    (tmp_path / "b" / "inc" / "x.h").write_text("int x;\n", encoding="utf-8")
    second = _profile(cf._compute_profile_fields, case)
    assert second == _profile(cf._compute_profile_fields.__wrapped__, case)
    assert first["include_sequence"] != second["include_sequence"]
