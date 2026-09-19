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

import ast
import itertools
import os
import random
from pathlib import Path

import pytest

from abicheck.extract.cache_header_scan import (
    _cache_header_rel_parts,
    _path_suffix,
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
    "..h",
    "...hpp",
    ".hidden.h",
    "w.",
    "dir.h",
    "Ünïcode.hpp",
    "nested",
)


def _oracle_suffix(name: str) -> str:
    """The walk's documented suffix rule, derived independently of the walk.

    Deliberately **not** ``PurePath(name).suffix``. That looks like the
    obvious oracle and was one until CPython 3.14 changed how ``suffix``
    treats leading and trailing dots -- ``"..h"`` and ``"h."`` both answer
    differently there than on 3.13. Since this walk's entry set *is* part of
    the AST/snapshot cache key, tying the key to a definition that moves
    between interpreters would make the same header tree hash differently on
    two Pythons, which is exactly the stale-evidence failure the walk exists
    to prevent. So the rule is pinned here in the tests' own terms: the
    suffix starts at the last dot that has something before it *and*
    something after it.

    Stated with ``rpartition`` rather than the implementation's ``rfind``
    plus index arithmetic, so this stays a second derivation of the contract
    and not a copy of the code under test.
    """
    head, dot, tail = name.rpartition(".")
    return dot + tail if head and tail else ""


def _oracle(directory: Path) -> list[Path]:
    """The documented contract, evaluated independently of the implementation."""
    return sorted(
        p
        for p in directory.rglob("*")
        if _oracle_suffix(p.name).lower() in CACHE_HEADER_SUFFIXES
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


def test_path_suffix_matches_the_contract_over_a_generated_name_space() -> None:
    """``_path_suffix`` implements the walk's own rule, not ``os.path.splitext``.

    The two look interchangeable and are not: ``splitext`` skips leading dots,
    so it reports no suffix for ``"..h"``/``"...h"``/``"..hpp"`` where the rule
    reports ``".h"``/``".hpp"``. Because the entry set is part of the cache
    key, the walk's first version silently dropped such a header from both the
    AST and snapshot keys -- an edit to it would then reuse stale cached
    evidence.

    The oracle is ``_oracle_suffix``, not ``PurePath.suffix``: see its
    docstring for why a stdlib definition that changed in 3.14 cannot be what
    a *cache key* is pinned to.

    Enumerated exhaustively over a small dot-heavy alphabet rather than pinning
    those three names, because the bug class is "two similar-looking functions
    are not the same function": any name where they disagree is a cache-key
    divergence, and only an enumeration finds the next one.
    """
    alphabet = ".hHp_0"
    checked = 0
    disagreeing: list[str] = []
    for length in range(1, 6):
        for letters in itertools.product(alphabet, repeat=length):
            name = "".join(letters)
            if name in (".", ".."):  # not legal directory entry names
                continue
            checked += 1
            if _path_suffix(name) != _oracle_suffix(name):
                disagreeing.append(name)
    assert checked > 9000, "the enumeration must actually be exhaustive"
    assert disagreeing == []
    # Vacuity guard: the oracle really does distinguish these from splitext,
    # so a helper that simply called splitext would have been caught above.
    assert [os.path.splitext(n)[1] for n in ("..h", "...h", "..hpp")] == ["", "", ""]
    assert [_oracle_suffix(n) for n in ("..h", "...h", "..hpp")] == [
        ".h",
        ".h",
        ".hpp",
    ]


def test_a_leading_dot_header_is_still_a_cache_key_entry(tmp_path: Path) -> None:
    """The concrete regression: a ``..h`` file must be walked, like ``rglob``."""
    (tmp_path / "..h").write_text("x", encoding="utf-8")
    (tmp_path / "...hpp").write_text("x", encoding="utf-8")
    (tmp_path / "plain.h").write_text("x", encoding="utf-8")
    walked = iter_cache_header_files(tmp_path)
    assert walked == _oracle(tmp_path)
    assert {p.name for p in walked} == {"..h", "...hpp", "plain.h"}


class TestScanCountersAreConcurrencySafe:
    """The counters explain *parallel* runs, so they must survive parallelism.

    **Bug class.** Diagnostic state shared across worker threads, mutated
    with a read-modify-write (``+=``) and read without a lock. A
    particularly self-defeating shape: these counters exist to attribute
    cost in a release fan-out, which is precisely the parallel case, so
    single-threaded runs report correctly and the runs the instrumentation
    was *built for* silently under-count. Nothing fails; a number is just
    quietly wrong, and it is a number someone will use to decide where to
    optimize.

    **Why this is asserted structurally, not behaviourally.** The obvious
    test -- record from N threads, check the total -- was written first and
    **passed identically with the lock removed**, at 16 threads, 320,000
    increments and a 1 microsecond switch interval. So did a reader-side
    test looking for a snapshot that mixed two updates. Under CPython's GIL
    the interleaving window for this shape is real but not reliably
    reachable, so such a test asserts nothing while appearing to guard
    something, which is the vacuous-test failure this repository documents
    at length. Rather than ship it, the invariant is pinned where it is
    actually decidable: in the source.

    The lock is still correct and still required. The GIL is an
    implementation detail, not a language guarantee, and it is exactly the
    guarantee a free-threaded build removes -- this project's CI already
    runs a 3.14 lane, where the race becomes ordinary rather than exotic.

    **General invariant**: every statement that mutates or reads counter
    state does so inside a ``with _COUNTERS_LOCK`` block. Checked over the
    real module AST, so it covers methods added later, not just the four
    that exist today.
    """

    @staticmethod
    def _counter_methods() -> list[ast.FunctionDef]:
        import abicheck.extract.cache_header_scan as mod

        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        cls = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "_ScanCounters"
        )
        return [n for n in cls.body if isinstance(n, ast.FunctionDef)]

    def test_every_counter_method_takes_the_lock(self) -> None:
        """Vacuity guard included: there must BE methods to check."""
        methods = self._counter_methods()
        checked = [m for m in methods if m.name != "__init__"]
        assert len(checked) >= 3, (
            "expected the counter class to still have record/snapshot/reset; "
            f"found {[m.name for m in methods]}"
        )
        for method in checked:
            guards = [
                item
                for node in ast.walk(method)
                if isinstance(node, ast.With)
                for item in node.items
                if isinstance(item.context_expr, ast.Name)
                and item.context_expr.id == "_COUNTERS_LOCK"
            ]
            assert guards, (
                f"_ScanCounters.{method.name} touches shared counter state "
                "without taking _COUNTERS_LOCK. Under a free-threaded build "
                "that is a lost update, and the counters under-report exactly "
                "the parallel runs they exist to explain."
            )

    def test_no_counter_field_is_mutated_outside_the_class(self) -> None:
        """The lock only helps if nothing bypasses the class to bump a field."""
        import abicheck.extract.cache_header_scan as mod

        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        fields = {"calls", "directories_traversed", "entries_returned"}
        inside = {
            id(n)
            for cls in ast.walk(tree)
            if isinstance(cls, ast.ClassDef) and cls.name == "_ScanCounters"
            for n in ast.walk(cls)
        }
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AugAssign)):
                continue
            if id(node) in inside:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                assert not (
                    isinstance(target, ast.Attribute) and target.attr in fields
                ), (
                    f"{target.attr} is mutated outside _ScanCounters, so it "
                    "bypasses _COUNTERS_LOCK"
                )

    def test_reset_clears_in_place_rather_than_rebinding(self) -> None:
        """A rebind would discard a concurrent worker's in-flight update.

        Non-vacuous, unlike the throughput tests above: it fails immediately
        against the original ``global _COUNTERS; _COUNTERS = _ScanCounters()``
        implementation. The module-level object must survive a reset, so a
        worker already inside ``record`` bumps the same counters the next
        reader sees rather than an orphaned one.
        """
        import abicheck.extract.cache_header_scan as mod

        before = mod._COUNTERS
        mod._COUNTERS.record("/x", 1, 1)
        assert mod.header_scan_statistics()["calls"] >= 1
        mod.reset_header_scan_statistics()
        assert mod._COUNTERS is before, "reset rebound the counter object"
        assert mod.header_scan_statistics()["calls"] == 0
