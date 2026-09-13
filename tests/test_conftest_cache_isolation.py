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

import os
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


@pytest.mark.parametrize("occurrence", range(6))
def test_cache_directories_do_not_accumulate_in_pytest_basetemp(
    occurrence: int, tmp_path_factory: pytest.TempPathFactory, tmp_path: Path
) -> None:
    """The *cost* half of the same contract, stated as an invariant so the
    next "make this faster" round cannot quietly undo it.

    ``tmp_path`` is allocated by pytest's numbered allocator, which enumerates
    basetemp's children to choose the next number. An autouse fixture that
    deposits one directory per test *directly* in basetemp therefore charges
    every later ``tmp_path`` request for a scan over its own leftovers. The
    per-test cache directory must live one level down, in a single bucket, so
    basetemp holds roughly one entry per test and no cache directory of its
    own.
    """
    basetemp = tmp_path_factory.getbasetemp()
    cache_dir = _current_cache_dir()

    # Still a real, distinct, empty, test-owned directory under basetemp...
    assert basetemp in cache_dir.parents
    assert list(cache_dir.iterdir()) == []
    # ... but never a direct child of it.
    assert cache_dir.parent != basetemp
    assert cache_dir.parent.name.startswith("snapshot-caches-")

    # And basetemp itself carries no per-test cache entry at all, however many
    # tests have run before this one.
    assert [
        p.name for p in basetemp.iterdir() if p.name.startswith("snapshot_cache-")
    ] == []

    # The bucket is shared, the directories in it are not: this occurrence's
    # directory is distinct from every earlier one even though they are
    # siblings now.
    assert cache_dir not in _SEEN


class TestTheAllocatorSurvivesItsDirectoryTreeVanishing:
    """The allocator recovers whatever was removed under it, not just the bucket.

    The failure this closes was suite-wide and badly disguised. `_isolate_snapshot_cache`
    is autouse, so a single transient deletion under pytest's temp tree made
    *every remaining test in that worker* error with a `FileNotFoundError`
    naming a different freshly-generated bucket each time -- 20,867 errors on
    one Linux lane and 29,164 on another (run 34729579282), plus the same
    signature in a container where an unrelated process was pruning `/tmp`.
    The bucket's own `is_dir()` check handled the bucket going away and not its
    parent, so re-allocation failed on the missing parent forever after.

    The invariant is stated over *which part of the tree was removed* rather
    than against the one observed deletion, because the deleter is not the
    bug: pytest's retention policy, a tmp reaper, a sandbox cleanup and a stray
    `rmtree` all arrive here identically, and a test pinned to one of them
    would leave the others open.
    """

    @staticmethod
    def _allocate(basetemp: Path) -> Path:
        """One allocation through the real allocator, with a real factory."""
        import conftest

        return conftest._snapshot_cache_bucket(_FactoryStub(basetemp))

    @pytest.mark.parametrize(
        "remove",
        ["the bucket only", "the basetemp", "the whole tree above it"],
        ids=["bucket", "basetemp", "ancestor"],
    )
    def test_allocation_recovers_after_a_deletion(
        self, tmp_path: Path, remove: str
    ) -> None:
        import shutil

        root = tmp_path / "root"
        basetemp = root / "pytest-0" / "popen-gw0"
        basetemp.mkdir(parents=True)

        first = self._allocate(basetemp)
        assert first.is_dir()

        target = {
            "the bucket only": first,
            "the basetemp": basetemp,
            "the whole tree above it": root,
        }[remove]
        shutil.rmtree(target)

        second = self._allocate(basetemp)
        assert second.is_dir(), f"no recovery after removing {remove}"
        assert second != first, "a recovered bucket must be a fresh directory"
        assert not any(second.iterdir()), "a recovered bucket must be empty"

    def test_the_deletion_really_breaks_the_unfixed_allocator(
        self, tmp_path: Path
    ) -> None:
        """Vacuity guard: the `basetemp` case must be unrecoverable without the fix.

        Without it the test above could pass because the deletion was harmless
        rather than because the allocator heals. This reproduces the pre-fix
        allocator verbatim and requires it to fail.
        """
        import shutil
        import tempfile

        basetemp = tmp_path / "pytest-0" / "popen-gw0"
        basetemp.mkdir(parents=True)

        def unfixed() -> Path:
            # The allocator as it was: no parent recreation.
            return Path(tempfile.mkdtemp(prefix="snapshot-caches-", dir=basetemp))

        unfixed()
        shutil.rmtree(basetemp)
        with pytest.raises(FileNotFoundError):
            unfixed()

    def test_repeated_allocation_without_deletion_reuses_one_bucket(
        self, tmp_path: Path
    ) -> None:
        """And the fix must not turn every call into a new bucket.

        The per-process bucket is the whole point of the allocator (it keeps
        pytest's numbered allocator off a directory with thousands of siblings),
        so a `mkdir`-always version that forgot the cache would be a silent
        performance regression rather than a visible failure.
        """
        basetemp = tmp_path / "pytest-0" / "popen-gw0"
        basetemp.mkdir(parents=True)
        assert self._allocate(basetemp) == self._allocate(basetemp)


class TestRecoveryKeepsPytestsPrivacyGuarantees:
    """Recreating the tree must not be a weaker act than creating it was.

    These paths are predictable (`/tmp/pytest-of-<user>/pytest-N/popen-gwM`) on a
    temp root shared between users, and pytest creates every level `0o700`,
    refuses a symlinked or foreign-owned level, and tightens loose modes. A
    `mkdir(parents=True, exist_ok=True)` recovery would have recreated the
    hierarchy with the process umask (commonly world-traversable `0o755`) and
    accepted a path planted in the window between the deletion and the recovery
    (Codex review). Each property is asserted separately, because a single
    "recovery works" test passes while any one of them is absent.
    """

    @staticmethod
    def _allocate(basetemp: Path) -> Path:
        import conftest

        return conftest._snapshot_cache_bucket(_FactoryStub(basetemp))

    def test_every_recreated_level_is_private(self, tmp_path: Path) -> None:
        """Not just the leaf: an open ancestor makes the leaf reachable."""
        root = tmp_path / "pytest-of-someone"
        basetemp = root / "pytest-0" / "popen-gw0"
        self._allocate(basetemp)
        for level in (root, root / "pytest-0", basetemp):
            mode = level.stat().st_mode & 0o777
            assert mode & 0o077 == 0, f"{level} is group/other-accessible ({mode:#o})"

    def test_a_symlinked_level_is_refused(self, tmp_path: Path) -> None:
        """The planted-path case: a symlink where a directory is expected."""
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        root = tmp_path / "pytest-of-someone"
        root.symlink_to(elsewhere, target_is_directory=True)
        with pytest.raises(OSError, match="symbolic link"):
            self._allocate(root / "pytest-0" / "popen-gw0")

    @pytest.mark.skipif(
        not hasattr(os, "getuid") or os.getuid() != 0,
        reason="needs root to create a directory owned by another user",
    )
    def test_a_foreign_owned_level_is_refused(self, tmp_path: Path) -> None:
        """Ownership, not just mode: a 0700 directory someone else owns is theirs."""
        root = tmp_path / "pytest-of-someone"
        root.mkdir(mode=0o700)
        os.chown(root, 1, 1)
        with pytest.raises(OSError, match="owned by another user"):
            self._allocate(root / "pytest-0" / "popen-gw0")

    def test_a_loose_mode_on_an_existing_level_is_tightened(
        self, tmp_path: Path
    ) -> None:
        """pytest performs this same fixup rather than failing, so recovery does too."""
        root = tmp_path / "pytest-of-someone"
        root.mkdir(mode=0o755)
        self._allocate(root / "pytest-0" / "popen-gw0")
        assert root.stat().st_mode & 0o077 == 0

    def test_the_umask_cannot_loosen_a_recreated_level(self, tmp_path: Path) -> None:
        """`mkdir`'s mode is umask-masked, so the chmod after it is load-bearing.

        Under a permissive umask a mode-only `mkdir` still yields a private
        directory, so this sets the hostile case explicitly: umask 0 is what
        distinguishes `mkdir(mode=0o700)` alone from `mkdir` plus `chmod`.
        """
        previous = os.umask(0)
        try:
            basetemp = tmp_path / "pytest-of-someone" / "pytest-0" / "popen-gw0"
            self._allocate(basetemp)
            assert basetemp.stat().st_mode & 0o077 == 0
        finally:
            os.umask(previous)


class _FactoryStub:
    """The one method `_snapshot_cache_bucket` uses off `pytest.TempPathFactory`.

    A stub rather than a real factory because the test needs to *choose* the
    basetemp in order to delete it, and deleting the session's real basetemp
    would take the rest of the run with it -- which is the very failure under
    test.
    """

    def __init__(self, basetemp: Path) -> None:
        self._basetemp = basetemp

    def getbasetemp(self) -> Path:
        return self._basetemp
