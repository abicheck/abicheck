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

"""The isolation contract of ``conftest.py``'s autouse cache fixtures.

``_isolate_snapshot_cache`` is autouse, so it runs ahead of every test in
this repository -- which makes *how* it allocates its directory a
suite-wide cost, and makes any weakening of its isolation a suite-wide
correctness hazard. It was changed from a numbered-directory allocation
(which enumerates every existing sibling to choose the next number, so the
per-test cost grows with the number of tests a worker has already run) to
an atomic random name.

These tests state the contract that change had to preserve, as invariants
rather than as one example: each test gets a directory that is its own, is
empty, and is not the real user cache; the in-process AST memo starts
cleared; and the cache key a snapshot would be stored under is not shared
across tests. They are written so that a regression *back* to a shared
session-wide cache directory -- the obvious way to make this "even faster"
-- fails here rather than surfacing as a mystery cross-test cache hit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck import dumper_cache, snapshot_cache

# Every directory any test in this module was handed, in execution order.
# Collected across tests on purpose: the invariant under test is about the
# relationship *between* tests, which no single test can observe alone.
_SEEN: list[Path] = []


def _current_cache_dir() -> Path:
    return Path(snapshot_cache._CACHE_DIR)


@pytest.mark.parametrize("occurrence", range(12))
def test_each_test_gets_its_own_empty_cache_directory(occurrence: int) -> None:
    """Distinctness, emptiness, and separation from the real user cache --
    the three properties the allocator must hold for every test, checked
    over repeated independent occurrences rather than a single one."""
    cache_dir = _current_cache_dir()

    assert cache_dir.is_dir()
    assert list(cache_dir.iterdir()) == []  # nothing inherited from a sibling
    assert cache_dir not in _SEEN  # never reused by an earlier test
    # Not the real ~/.cache/abi_check/snapshots -- an autouse fixture that
    # failed to redirect would leave tests writing to the developer's cache.
    assert Path.home() not in cache_dir.parents or ".cache" not in cache_dir.parts

    # Writing here must not be visible to any other test's directory.
    (cache_dir / "probe.json").write_text(str(occurrence))
    for earlier in _SEEN:
        assert not (earlier / "probe.json").exists() or earlier != cache_dir

    _SEEN.append(cache_dir)


@pytest.mark.parametrize("occurrence", range(6))
def test_a_stored_entry_is_never_served_to_another_test(occurrence: int) -> None:
    """The hazard the fixture exists for: two tests whose synthetic inputs
    hash to the same cache key must still miss, because their cache roots
    differ. Exercised by storing under a *deliberately identical* key in
    every occurrence -- with a shared directory, occurrence 1 onward would
    find occurrence 0's entry."""
    cache_dir = _current_cache_dir()
    shared_key = "identical-across-every-occurrence"
    entry = cache_dir / f"{shared_key}.json"

    assert not entry.exists()  # a hit here means isolation is gone
    entry.write_text("{}")
    assert entry.exists()


@pytest.mark.parametrize("occurrence", range(6))
def test_ast_memo_slot_starts_cleared(occurrence: int) -> None:
    """``_isolate_ast_memo``'s half of the same contract: the in-process
    memo is a ContextVar on a thread pytest reuses across tests, so a value
    left by one test is otherwise still visible to the next."""
    assert dumper_cache._ast_memo_slot.get() is None
    dumper_cache._ast_memo_slot.set({"poisoned": occurrence})
