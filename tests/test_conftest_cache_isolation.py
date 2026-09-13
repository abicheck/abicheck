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
import shutil
import tempfile
from pathlib import Path

# The *same* module object pytest itself loaded, not a second copy: `tests/` has
# no `__init__.py`, so pytest's prepend import mode puts this file's directory on
# `sys.path` and loads the conftest as the top-level module `conftest` -- verified
# by identity, which matters because the allocator caches its bucket in a
# module-level dict. Imported here rather than inside each test so the tests read
# as ordinary calls.
import conftest  # noqa: E402
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

        return conftest._snapshot_cache_bucket(_FactoryStub(basetemp))

    @pytest.mark.parametrize(
        "remove",
        ["the bucket only", "the basetemp", "the whole tree above it"],
        ids=["bucket", "basetemp", "ancestor"],
    )
    def test_allocation_recovers_after_a_deletion(
        self, tmp_path: Path, remove: str
    ) -> None:

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


class TestValidationStopsAtPytestsOwnRoot:
    """Nothing above `pytest-of-<user>` is ours to check or to repair.

    Validating every ancestor reaches `/tmp` and `/`, and that is worse than the
    bug the recovery fixes (Codex review). On an ordinary non-root runner the
    ownership check raises on root-owned `/tmp` during the very first autouse
    allocation, so every test in the session errors. Running as root it is worse
    still: the group/other fixup would strip `/tmp` from its usual `01777` and
    break the machine's shared temp directory for everyone.

    So the boundary is asserted directly, with a stand-in for `/tmp` whose mode
    and ownership are deliberately *wrong* by the rules that apply inside
    pytest's root.
    """

    @staticmethod
    def _allocate(basetemp: Path) -> Path:

        return conftest._snapshot_cache_bucket(_FactoryStub(basetemp))

    @staticmethod
    def _tree(tmp_path: Path) -> tuple[Path, Path]:
        """A `/tmp`-like ancestor holding a pytest root, returned as (system, leaf)."""
        system = tmp_path / "tmp-like"
        system.mkdir(mode=0o755)
        return system, system / "pytest-of-someone" / "pytest-0" / "popen-gw0"

    def test_a_system_ancestors_mode_is_left_alone(self, tmp_path: Path) -> None:
        """The destructive half: `/tmp` must keep its world-writable mode."""
        system, leaf = self._tree(tmp_path)
        before = system.stat().st_mode
        self._allocate(leaf)
        assert system.stat().st_mode == before

    def test_a_sticky_world_writable_ancestor_keeps_its_sticky_bit(
        self, tmp_path: Path
    ) -> None:
        """Stated as the real `/tmp` shape, 01777, since that is what would break."""
        system = tmp_path / "tmp-like-sticky"
        system.mkdir()
        system.chmod(0o1777)
        self._allocate(system / "pytest-of-someone" / "pytest-0" / "popen-gw0")
        assert system.stat().st_mode & 0o7777 == 0o1777

    @pytest.mark.skipif(
        not hasattr(os, "getuid") or os.getuid() != 0,
        reason="needs root to create a directory owned by another user",
    )
    def test_a_foreign_owned_system_ancestor_does_not_raise(
        self, tmp_path: Path
    ) -> None:
        """The every-test-errors half: `/tmp` is root's, and a test run is not."""
        system, leaf = self._tree(tmp_path)
        os.chown(system, 1, 1)
        assert self._allocate(leaf).is_dir()

    def test_the_pytest_root_itself_is_still_validated(self, tmp_path: Path) -> None:
        """The boundary is inclusive: the marker directory is pytest's, so ours."""
        system, leaf = self._tree(tmp_path)
        root = system / "pytest-of-someone"
        root.mkdir(mode=0o755)
        self._allocate(leaf)
        assert root.stat().st_mode & 0o077 == 0

    def test_with_an_explicit_basetemp_only_that_directory_is_owned(self) -> None:
        """`--basetemp` has no marker; pytest creates just that one directory.

        Deliberately NOT built under `tmp_path`: by default `tmp_path` lives
        inside `/tmp/pytest-of-<user>/pytest-N/...`, so a path under it always
        has a marker ancestor and this case would silently become the
        marker-found one. The first version of this test did exactly that and
        passed only because the suite was being run with `--basetemp` pointed
        outside `/tmp` -- it failed the moment the same test ran under pytest's
        default temp root, which is how the false premise surfaced.
        """

        root = Path(tempfile.mkdtemp(prefix="marker-free-"))
        try:
            assert not any(
                level.name.startswith("pytest-of-") for level in (root, *root.parents)
            ), f"{root} is not marker-free, so this case would not be exercised"
            parent = root / "chosen-by-the-caller"
            parent.mkdir(mode=0o755)
            basetemp = parent / "basetemp"
            assert conftest._pytest_owned_levels(basetemp) == [basetemp]
            self._allocate(basetemp)
            assert parent.stat().st_mode & 0o777 == 0o755
            assert basetemp.stat().st_mode & 0o077 == 0
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_the_owned_region_is_the_marker_and_everything_under_it(self) -> None:
        """The boundary as a pure function, independent of any filesystem."""

        leaf = Path("/tmp/pytest-of-someone/pytest-3/popen-gw2")
        assert conftest._pytest_owned_levels(leaf) == [
            Path("/tmp/pytest-of-someone"),
            Path("/tmp/pytest-of-someone/pytest-3"),
            leaf,
        ]
        assert Path("/tmp") not in conftest._pytest_owned_levels(leaf)
        assert Path("/") not in conftest._pytest_owned_levels(leaf)


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
