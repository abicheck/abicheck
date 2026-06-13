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

"""ADR-033 D5 — content-addressed L3 BuildEvidence cache."""
from __future__ import annotations

import json
from pathlib import Path

from abicheck.buildsource.build_cache import (
    BuildEvidenceCache,
    compute_build_cache_key,
)
from abicheck.buildsource.build_evidence import BuildEvidence
from abicheck.buildsource.inline import collect_inline_pack


def _cdb(tree: Path, arg: str = "-c") -> Path:
    tree.mkdir(parents=True, exist_ok=True)
    (tree / "f.cpp").write_text("int f(){return 0;}\n")
    db = tree / "compile_commands.json"
    db.write_text(json.dumps([{
        "directory": str(tree), "file": "f.cpp",
        "arguments": ["c++", arg, "f.cpp"],
    }]))
    return db


def test_cache_roundtrip_and_rate(tmp_path):
    cache = BuildEvidenceCache(tmp_path / "c")
    assert cache.hit_rate is None
    assert cache.get("k") is None          # miss
    cache.put("k", BuildEvidence())
    assert cache.get("k") is not None      # hit
    assert cache.get(None) is None         # uncacheable, not counted
    assert (cache.hits, cache.misses) == (1, 1)
    assert cache.hit_rate == 0.5


def test_key_is_content_addressed(tmp_path):
    db = _cdb(tmp_path / "a")
    k1 = compute_build_cache_key(db, "generic")
    db.write_text(db.read_text() + " ")    # any content change
    k2 = compute_build_cache_key(db, "generic")
    assert k1 and k2 and k1 != k2          # edit ⇒ different key ⇒ miss
    assert compute_build_cache_key(tmp_path / "missing.json", "generic") is None


def test_key_distinguishes_trees_with_identical_relative_db(tmp_path):
    """Codex review: two trees with byte-identical relative compile DBs must not
    share a cache entry — the resolved DB location is part of the key."""
    a = tmp_path / "treeA"
    b = tmp_path / "treeB"
    a.mkdir()
    b.mkdir()
    body = json.dumps([{"file": "f.cpp", "arguments": ["c++", "-c", "f.cpp"]}])
    (a / "compile_commands.json").write_text(body)
    (b / "compile_commands.json").write_text(body)  # identical bytes, different root
    ka = compute_build_cache_key(a / "compile_commands.json", "generic")
    kb = compute_build_cache_key(b / "compile_commands.json", "generic")
    assert ka and kb and ka != kb


def test_corrupt_entry_is_a_miss(tmp_path):
    cache = BuildEvidenceCache(tmp_path / "c")
    cache.cache_dir.mkdir(parents=True)
    (cache.cache_dir / "build-bad.json").write_text("{ not json")
    assert cache.get("bad") is None
    assert cache.misses == 1


def test_inline_collection_uses_cache(tmp_path):
    tree = tmp_path / "src"
    _cdb(tree)
    cache_dir = tmp_path / "bc"
    collect_inline_pack(sources=tree, build_info=None, layers=("L3",),
                        build_cache_dir=cache_dir)
    pack = collect_inline_pack(sources=tree, build_info=None, layers=("L3",),
                               build_cache_dir=cache_dir)
    details = [e.detail for e in pack.manifest.extractors if e.name == "compile_commands"]
    assert any("cached" in d for d in details)
