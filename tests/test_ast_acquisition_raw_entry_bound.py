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

"""The *ungrouped* half of ``AstAcquisitionScope``'s table stays bounded.

``tests/test_ast_acquisition_retention.py`` covers the grouped half: an
id-keyed entry and the object its key names are released together. This file
covers the other half, which was unbounded -- a content-derived key (the
effective AST cache key ``dumper.py`` acquires the raw parsed root under)
whose ``Future`` *result is the root*. A measured six-member release retained
24 such roots while the grouped counters correctly reported eight retained
groups and sixteen releases.

The bug class this states as invariants is "a bounded cache whose bookkeeping
describes a different set than the one actually holding memory". So the
central test does not assert a counter: it takes a ``weakref`` to the
produced object and asserts the object is *actually reclaimed* after eviction
(``tests/regressions/manifest.py`` bug class ``cache-bookkeeping-vs-retention``).
A counters-only test passes against an implementation that decrements a
number and keeps the object.

Deliberately not asserted anywhere here: a process RSS figure. These are
growth and reclamation properties, which hold on any host; an absolute-RSS
assertion would be brittle and would not distinguish the two.
"""

from __future__ import annotations

import gc
import random
import threading
import weakref
from concurrent.futures import Future

import pytest

from abicheck.dumper_cache import (
    MAX_RETAINED_CONTEXT_GROUPS,
    MAX_RETAINED_RAW_ENTRIES,
    AstAcquisitionScope,
)


class _Root:
    """A stand-in for a parsed AST root: identity-bearing and weak-referenceable."""

    __slots__ = ("payload", "__weakref__")

    def __init__(self, payload: object = "") -> None:
        self.payload = payload


def _big(n: int) -> _Root:
    """A root whose payload size varies, for the mixed-size cases."""
    return _Root(["x"] * n)


class TestTheBoundItself:
    def test_distinct_content_keys_stop_accumulating(self) -> None:
        scope = AstAcquisitionScope()
        total = MAX_RETAINED_RAW_ENTRIES * 4
        for i in range(total):
            scope.run("castxml", f"content-{i}", lambda i=i: _Root(i))
        stats = scope.group_stats()
        assert stats["retained_raw_entries"] <= MAX_RETAINED_RAW_ENTRIES
        assert stats["released_raw_entries"] == total - stats["retained_raw_entries"]
        # And the entry table itself shrank with it -- the counter is not
        # tracking a set the table does not agree with.
        assert stats["entries"] == stats["retained_raw_entries"]

    @pytest.mark.parametrize("sizes", [(1, 1, 1), (1, 5000, 1), (5000, 1, 5000)])
    def test_the_bound_is_by_count_and_holds_for_mixed_sizes(
        self, sizes: tuple[int, ...]
    ) -> None:
        """Mixed-size contexts, since a real fan-out's header sets differ.

        The bound is deliberately a count, not a byte budget: the scope
        cannot size a parsed AST cheaply, and a byte budget it could not
        measure would be a worse guarantee than one it can.
        """
        scope = AstAcquisitionScope()
        for i in range(MAX_RETAINED_RAW_ENTRIES + len(sizes) + 2):
            size = sizes[i % len(sizes)]
            scope.run("castxml", f"k{i}", lambda size=size: _big(size))
        assert scope.group_stats()["retained_raw_entries"] <= MAX_RETAINED_RAW_ENTRIES

    def test_one_shared_header_set_is_never_evicted_or_reparsed(self) -> None:
        """The case the bound must not regress: every member shares a key.

        This is the common release shape (one header tree, many members),
        and single-flight is the whole reason the table exists. An LRU keeps
        it at one resident root and zero re-parses; a FIFO or a flush would
        re-parse per member.
        """
        scope = AstAcquisitionScope()
        calls = 0

        def produce() -> _Root:
            nonlocal calls
            calls += 1
            return _Root("shared")

        for _ in range(MAX_RETAINED_RAW_ENTRIES * 5):
            scope.run("castxml", "one-shared-key", produce)
        assert calls == 1
        assert scope.group_stats()["released_raw_entries"] == 0

    def test_a_recurring_key_survives_a_sweep_of_one_shot_keys(self) -> None:
        """LRU, not FIFO: use-order decides, not admission order."""
        scope = AstAcquisitionScope()
        calls = 0

        def hot() -> _Root:
            nonlocal calls
            calls += 1
            return _Root("hot")

        for i in range(MAX_RETAINED_RAW_ENTRIES * 3):
            scope.run("castxml", "hot", hot)
            scope.run("castxml", f"cold-{i}", lambda i=i: _Root(i))
        assert calls == 1, "the repeatedly-used entry was evicted and reparsed"


class TestActualReclamation:
    def test_an_evicted_result_is_really_collectable(self) -> None:
        """The point of the whole change, asserted against the object graph.

        A bookkeeping-only fix -- decrement a counter, keep the object --
        passes every counter assertion above and changes no memory at all.
        So this holds a ``weakref`` to the produced root, drops every other
        reference, and requires the referent to be gone once the entry is
        evicted.
        """
        scope = AstAcquisitionScope()
        first = _Root("evict-me")
        ref = weakref.ref(first)
        scope.run("castxml", "victim", lambda f=first: f)
        del first
        # Push it out with enough distinct, newer keys.
        for i in range(MAX_RETAINED_RAW_ENTRIES + 2):
            scope.run("castxml", f"filler-{i}", lambda i=i: _Root(i))
        assert ("castxml", "victim") not in scope._entries
        gc.collect()
        assert ref() is None, "the evicted entry's result was still reachable"

    def test_a_retained_result_is_deliberately_still_alive(self) -> None:
        """The complement, so the test above cannot pass vacuously.

        If ``_Root`` were collected for some unrelated reason, the
        reclamation assertion would hold against an implementation that
        never evicted anything. A retained entry must keep its result.
        """
        scope = AstAcquisitionScope()
        kept = _Root("keep-me")
        ref = weakref.ref(kept)
        scope.run("castxml", "kept", lambda k=kept: k)
        del kept
        gc.collect()
        assert ref() is not None
        assert scope._entries[("castxml", "kept")].result() is ref()


class TestCoordinationWithProducersAndWaiters:
    def test_an_in_flight_entry_is_never_evicted(self) -> None:
        """A waiter is blocked on that exact ``Future`` object.

        Dropping it from the table would not break the blocked waiter (it
        holds the object), but a *later* caller would then start a second
        producer for work already in flight. More importantly, the eviction
        must not be able to remove an entry whose producer will still
        publish into it.
        """
        scope = AstAcquisitionScope()
        pending_key = ("castxml", "in-flight")
        pending: Future = Future()
        scope._entries[pending_key] = pending
        scope._touch_ungrouped_locked(pending_key)
        for i in range(MAX_RETAINED_RAW_ENTRIES * 3):
            scope.run("castxml", f"done-{i}", lambda i=i: _Root(i))
        assert scope._entries.get(pending_key) is pending
        pending.set_result(_Root("late"))

    def test_a_concurrent_waiter_still_gets_the_producers_result(self) -> None:
        """Single-flight survives the bound under real threads."""
        scope = AstAcquisitionScope()
        started = threading.Event()
        release = threading.Event()
        produced = _Root("shared")
        calls = 0

        def slow() -> _Root:
            nonlocal calls
            calls += 1
            started.set()
            release.wait(5)
            return produced

        got: list[object] = []

        def waiter() -> None:
            got.append(scope.run("castxml", "shared", slow))

        threads = [threading.Thread(target=waiter) for _ in range(4)]
        for t in threads:
            t.start()
        assert started.wait(5)
        release.set()
        for t in threads:
            t.join(10)
        assert calls == 1
        assert got == [produced] * 4

    def test_a_failed_producer_leaves_no_ungrouped_bookkeeping_behind(self) -> None:
        """A raising producer removes its own entry; the LRU must agree.

        A stale LRU key naming an entry that no longer exists would make the
        bound count phantoms and evict live entries early.
        """
        scope = AstAcquisitionScope()
        with pytest.raises(RuntimeError):
            scope.run("castxml", "boom", lambda: (_ for _ in ()).throw(RuntimeError()))
        assert ("castxml", "boom") not in scope._ungrouped
        assert ("castxml", "boom") not in scope._entries


class TestTheTwoHalvesTogether:
    def test_the_ungrouped_bound_never_touches_a_grouped_key(self) -> None:
        """A group's release is what keeps the id-keyed invariant.

        If the ungrouped sweep could remove a key a group owns, the group's
        key set would name an entry that no longer exists and the release
        accounting would drift. Keys are kept in exactly one of the two
        tables.
        """
        scope = AstAcquisitionScope()
        roots = [_Root(i) for i in range(MAX_RETAINED_CONTEXT_GROUPS)]
        for root in roots:
            scope.run("clang", repr(id(root)), lambda r=root: r.payload, group=root)
        grouped_keys = {("clang", repr(id(r))) for r in roots}
        for i in range(MAX_RETAINED_RAW_ENTRIES * 4):
            scope.run("castxml", f"content-{i}", lambda i=i: _Root(i))
        assert grouped_keys <= set(scope._entries)
        assert not (set(scope._ungrouped) & grouped_keys)

    def test_a_group_holds_its_object_even_when_the_content_entry_goes(self) -> None:
        """Why evicting a content-keyed entry cannot reopen the id-reuse hole.

        The same parsed root is reachable two ways: as a content-keyed
        result, and as the object a group is keyed on. Evicting the former
        must not free it, because the group's own strong reference is what
        makes ``id()``-derived keys safe.
        """
        scope = AstAcquisitionScope()
        root = _Root("both")
        ref = weakref.ref(root)
        scope.run("castxml", "content", lambda r=root: r)
        scope.run("clang", repr(id(root)), lambda: "derived", group=root)
        del root
        for i in range(MAX_RETAINED_RAW_ENTRIES + 2):
            scope.run("castxml", f"filler-{i}", lambda i=i: _Root(i))
        gc.collect()
        assert ("castxml", "content") not in scope._entries
        assert ref() is not None, "a group's retained object was freed by the raw sweep"

    @pytest.mark.parametrize("seed", range(12))
    def test_randomised_mixed_traffic_preserves_the_table_invariant(
        self, seed: int
    ) -> None:
        """Adversarial interleavings, checked against a stated invariant.

        The invariant is the conjunction the design rests on: every
        surviving entry is either owned by a still-retained group, or is an
        ungrouped key inside its own bound. A fixed scripted sequence would
        only foreclose the order it happens to name; this searches orders.
        """
        rng = random.Random(seed)
        scope = AstAcquisitionScope()
        held: list[_Root] = []
        for i in range(120):
            if rng.random() < 0.5:
                root = _Root(i)
                if rng.random() < 0.4:
                    held.append(root)
                scope.run("clang", repr(id(root)), lambda i=i: i, group=root)
            else:
                scope.run("castxml", f"c{rng.randrange(20)}", lambda i=i: _Root(i))
            live_group_keys: set[tuple[str, str]] = set()
            for _obj, keys in scope._groups.values():
                live_group_keys |= keys
            assert set(scope._entries) <= live_group_keys | set(scope._ungrouped)
            assert len(scope._ungrouped) <= MAX_RETAINED_RAW_ENTRIES
            assert len(scope._groups) <= MAX_RETAINED_CONTEXT_GROUPS
