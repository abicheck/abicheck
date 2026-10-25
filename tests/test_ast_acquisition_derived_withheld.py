# SPDX-License-Identifier: Apache-2.0
"""A derived-AST substitution answers its own consumer, never a sibling's.

Regression for ``compare`` crashing with ``AttributeError: 'object' object
has no attribute 'get'`` inside a forked side-resolution child. The
header-graph prefetch thread and the primary castxml dump's vector-spelling
join both acquire the same clang AST key in one request. When the prefetch
led, its ``derived_ast_scope`` took a stored projection instead of the tree,
the producer returned the private superseded marker in place of the AST, and
the request's acquisition table published that marker -- to a concurrent
waiter and to every later caller of the key. The vector join, which needs the
tree, then walked a bare ``object()``.

The class: whatever a producer returns because of its *caller's* context is
not a value of the key, so the shared table must not serve it to anyone else.
Exercised over every order the two consumers can meet in (derived first then
tree, tree first then derived, a tree consumer waiting on a derived leader),
each in this process and in a forked side child, with the oracle being the
tree a scope-free producer builds.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from concurrent.futures import Future
from contextvars import copy_context
from pathlib import Path
from typing import Any

import pytest

from abicheck.storage.derived_ast import (
    DerivedAstArtifact,
    derived_ast_scope,
    offer_derived_ast_source,
)
from abicheck.storage.header_ast_cache import (
    ast_acquisition_scope,
    run_ast_acquisition_offering_entry,
)
from abicheck.workflows.side_isolation import run_isolated

_TREE = {"kind": "TranslationUnitDecl", "inner": [{"kind": "TypedefDecl"}]}
_PROJECTION = "stored-projection"

Acquired = tuple[Any, str, bool]


def _clang_like_producer(entry: Path, runs: list[str]) -> Callable[[], Acquired]:
    """What ``clang_header_dump``'s coordinated re-entry does: offer the cache
    entry to an open derived scope (``load_cached_ast``) and return the marker
    the offer hands back, or else the decoded tree."""

    def produce() -> Acquired:
        runs.append("produced")
        superseded = offer_derived_ast_source(entry, is_cache_entry=True)
        root = superseded if superseded is not None else dict(_TREE)
        return root, "ctx", False

    return produce


def _loader(path: Path, *, tree_in_hand: bool = False) -> str:
    """A final consumer with a stored projection: it takes the offer whether
    or not a tree is already in hand (a sidecar beats projecting one)."""
    return _PROJECTION


def _derived(entry: Path, runs: list[str]) -> tuple[Acquired, DerivedAstArtifact]:
    with derived_ast_scope(_loader) as art:
        got = run_ast_acquisition_offering_entry(
            "clang", "k", entry, _clang_like_producer(entry, runs)
        )
    return got, art


def _tree_consumer(entry: Path, runs: list[str]) -> Acquired:
    return run_ast_acquisition_offering_entry(
        "clang", "k", entry, _clang_like_producer(entry, runs)
    )


def _sequential(entry: Path, derived_first: bool) -> dict[str, Any]:
    runs: list[str] = []
    with ast_acquisition_scope():
        if derived_first:
            (_marker, *_), art = _derived(entry, runs)
            tree = _tree_consumer(entry, runs)
            again = _tree_consumer(entry, runs)
        else:
            tree = _tree_consumer(entry, runs)
            (_marker, *_), art = _derived(entry, runs)
            again = _tree_consumer(entry, runs)
    return {
        "tree": tree[0],
        "again": again[0],
        "again_is_tree": again[0] is tree[0],
        "derived": (art.used, art.value),
        "runs": len(runs),
    }


def _tree_waits_on_derived_leader(entry: Path) -> dict[str, Any]:
    """The field shape: the derived consumer leads (the prefetch thread), the
    tree consumer arrives while it is still producing and waits on it."""
    runs: list[str] = []
    leading = threading.Event()
    release = threading.Event()
    inner = _clang_like_producer(entry, runs)

    def slow_leader() -> Acquired:
        leading.set()
        assert release.wait(10)
        return inner()

    out: dict[str, Any] = {}

    def lead() -> None:
        with derived_ast_scope(_loader) as art:
            run_ast_acquisition_offering_entry("clang", "k", entry, slow_leader)
        out["derived"] = (art.used, art.value)

    def follow() -> None:
        out["tree"] = _tree_consumer(entry, runs)[0]

    # Observe the follower blocking on the leader's future, so the leader
    # settles only once the waiter path -- not a later lookup -- is the one
    # under test.
    waiting = threading.Event()
    original_result = Future.result

    def observed_result(self: Future[Any], timeout: float | None = None) -> Any:
        waiting.set()
        return original_result(self, timeout)

    Future.result = observed_result  # type: ignore[method-assign]
    try:
        with ast_acquisition_scope():
            leader = threading.Thread(target=copy_context().run, args=(lead,))
            leader.start()
            assert leading.wait(10)
            follower = threading.Thread(target=copy_context().run, args=(follow,))
            follower.start()
            assert waiting.wait(10)
            release.set()
            leader.join(10)
            follower.join(10)
    finally:
        Future.result = original_result  # type: ignore[method-assign]
    out["runs"] = len(runs)
    return out


@pytest.fixture
def entry(tmp_path: Path) -> Path:
    path = tmp_path / "entry.json"
    path.write_text("{}")
    return path


def _in(where: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    if where == "forked-child":
        if not sys.platform.startswith("linux"):
            pytest.skip("side isolation forks; Linux only")
        (result,) = run_isolated([fn], concurrent=False)
        return result
    return fn()


@pytest.mark.parametrize("where", ["in-process", "forked-child"])
@pytest.mark.parametrize("derived_first", [True, False])
def test_tree_consumer_never_receives_the_derived_marker(
    where: str, derived_first: bool, entry: Path
) -> None:
    got = _in(where, lambda: _sequential(entry, derived_first))
    assert got["tree"] == _TREE and got["again"] == _TREE
    # The derived consumer still took the cheap form: the fix withholds the
    # marker from others rather than giving up the substitution.
    assert got["derived"] == (True, _PROJECTION)
    # Derived first: the marker was not stored, so the tree consumer produced
    # once and the third call reused that real tree. Tree first: one produce
    # serves all three, the derived consumer substituting only its own copy.
    assert got["again_is_tree"]
    assert got["runs"] == (2 if derived_first else 1)


@pytest.mark.parametrize("where", ["in-process", "forked-child"])
def test_waiter_on_a_derived_leader_reacquires_a_real_tree(
    where: str, entry: Path
) -> None:
    got = _in(where, lambda: _tree_waits_on_derived_leader(entry))
    assert got["tree"] == _TREE
    assert got["derived"] == (True, _PROJECTION)
    assert got["runs"] == 2


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="forks")
def test_child_failure_carries_the_childs_traceback() -> None:
    """The forwarded exception names where in the child it was raised; a
    pickled exception alone keeps only its message."""

    def deep_in_the_child() -> None:
        {}["missing"]  # noqa: B018 -- the failure under test

    with pytest.raises(KeyError) as info:
        run_isolated([deep_in_the_child], concurrent=False)
    notes = "\n".join(getattr(info.value, "__notes__", []))
    assert "Raised in the side-resolution child" in notes
    assert "deep_in_the_child" in notes


def test_failed_producer_keeps_its_traceback_for_the_leader() -> None:
    """The table strips the frames it hands to waiters, but the producer's own
    caller must still see where the producer failed."""

    def failing_producer() -> Acquired:
        raise ValueError("castxml exploded")

    with ast_acquisition_scope():
        with pytest.raises(ValueError) as info:
            run_ast_acquisition_offering_entry(
                "clang", "k", Path("/nonexistent"), failing_producer
            )
    frames = [f.name for f in info.traceback]
    assert "failing_producer" in frames
