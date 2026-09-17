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

"""Bounded retention in ``AstAcquisitionScope``, without an id-reuse hole.

The scope retains parsed AST roots because some of its own acquisition keys
are built from ``id(root)``, and CPython reuses an address as soon as its
object is freed: an entry keyed on a dead object's id, served for a
*different* root that landed at the same address, is a silent wrong answer.
That is why retention exists at all, and why it cannot simply be bounded by
dropping objects.

So the property under test is not "memory is bounded" on its own -- it is
the conjunction:

    a group's object and every entry keyed off its id are released
    *together*, so no entry mentioning a freed id ever survives it,

which is what makes bounding safe. The id-reuse scenario is exercised
directly (free a group, force a new object onto the same address, and check
the new object is not served the old object's cached value) rather than
argued for in prose, because that is the failure this design exists to
prevent and it is invisible to every other kind of test.
"""

from __future__ import annotations

from abicheck.dumper_cache import (
    MAX_RETAINED_CONTEXT_GROUPS,
    AstAcquisitionScope,
    ast_acquisition_scope,
    run_ast_acquisition,
)


class _Root:
    """A stand-in for a parsed AST root: identity-bearing and reallocatable."""

    __slots__ = ("payload", "__weakref__")

    def __init__(self, payload: str = "") -> None:
        self.payload = payload


class TestGroupedRelease:
    def test_an_entry_is_released_with_the_object_its_key_names(self) -> None:
        scope = AstAcquisitionScope()
        roots = [_Root(f"r{i}") for i in range(MAX_RETAINED_CONTEXT_GROUPS + 3)]
        for root in roots:
            scope.run("backend", repr(id(root)), lambda r=root: r.payload, group=root)
        stats = scope.group_stats()
        assert stats["retained_groups"] == MAX_RETAINED_CONTEXT_GROUPS
        assert stats["released_groups"] == 3
        # The entries released are exactly the released groups' own, so the
        # two counts stay in step rather than one lagging the other.
        assert stats["entries"] == MAX_RETAINED_CONTEXT_GROUPS

    def test_no_entry_survives_its_group(self) -> None:
        """The invariant that makes bounding safe, stated over the whole table.

        Checked structurally: every remaining key must belong to a group
        that is still retained. A release that dropped an object while
        leaving its entry behind would show up here for any eviction order.
        """
        scope = AstAcquisitionScope()
        roots = [_Root(f"r{i}") for i in range(MAX_RETAINED_CONTEXT_GROUPS * 3)]
        for root in roots:
            scope.run("backend", repr(id(root)), lambda r=root: r.payload, group=root)
        live_keys = set()
        for _obj, keys in scope._groups.values():
            live_keys |= keys
        assert set(scope._entries) <= live_keys

    def test_a_reused_address_is_never_served_the_released_value(self) -> None:
        """The hazard itself, exercised rather than reasoned about.

        A released group's object is freed, so CPython may hand its address
        straight to the next allocation -- and CPython's small-object
        allocator makes that the *likely* outcome, not a rare one. If the
        released entry had survived, a new root at that address would be
        served the old root's cached value under a key that is merely its
        address: a wrong answer with no error anywhere.

        The assertion is therefore about the value, not the key: whatever
        address the replacement lands on, it must never observe
        ``"original"``. A test that instead expected a recomputation under
        the old key string would be wrong precisely when the addresses *do*
        coincide -- the interesting case -- because the key is then
        legitimately the replacement's own.
        """
        scope = AstAcquisitionScope()
        first = _Root("original")
        first_address = id(first)
        key = repr(first_address)
        assert scope.run("b", key, lambda f=first: f.payload, group=first) == "original"
        # Release it the way overflow would, then drop the last reference.
        scope._release_group_locked(first_address)
        del first

        reused = False
        for _ in range(200):
            replacement = _Root("replacement")
            got = scope.run(
                "b",
                repr(id(replacement)),
                lambda r=replacement: r.payload,
                group=replacement,
            )
            assert got == "replacement", "a freed root's value was served"
            if id(replacement) == first_address:
                reused = True
                break
            del replacement
        # Asserted, not merely hoped for: measured at 200/200 on the first
        # retry for a slotted object of this size, because CPython's
        # small-object allocator hands back the block it just freed. If
        # this ever stops holding, the check above has quietly stopped
        # exercising the reuse case and should fail rather than pass on a
        # scenario it no longer reaches.
        assert reused, "no address reuse occurred, so the hazard was not exercised"

    def test_a_group_with_an_in_flight_entry_is_not_released_at_all(self) -> None:
        """Both halves or neither -- the object as well as the entries.

        This is the finding an earlier version of this test missed. It
        asserted only that the in-flight Future survived (it must: a waiter
        is blocked on that exact object), and did not notice that the
        *group* had been popped anyway. That left the object unretained
        while a key mentioning its id lived on -- so once freed and its
        address reused, `run` would serve the stale entry: a wrong answer,
        and unrecoverable, since the key set that could have identified the
        orphan went with the group.
        """
        from concurrent.futures import Future

        scope = AstAcquisitionScope()
        root = _Root("x")
        pending_key = ("b", "pending")
        pending: Future = Future()
        scope._entries[pending_key] = pending
        scope._touch_group_locked(root)
        scope._groups[id(root)][1].add(pending_key)
        assert scope._release_group_locked(id(root)) is False
        assert pending_key in scope._entries
        assert not pending.done()
        # The half the old test did not check: the object is still held, so
        # its id cannot be reused while that entry names it.
        assert id(root) in scope._groups
        assert scope._groups[id(root)][0] is root
        assert scope.group_stats()["released_groups"] == 0

    def test_no_surviving_entry_is_ever_left_without_its_group(self) -> None:
        """The invariant behind the above, over a mixed table.

        Some entries complete, some do not; whatever the release policy
        does, every key left in the table must still belong to a retained
        group. Stated over the table rather than one group, so a future
        policy change that releases more aggressively is checked too.
        """
        from concurrent.futures import Future

        scope = AstAcquisitionScope()
        roots = [_Root(f"r{i}") for i in range(MAX_RETAINED_CONTEXT_GROUPS * 3)]
        for i, root in enumerate(roots):
            scope.run("b", repr(id(root)), lambda r=root: r.payload, group=root)
            if i % 3 == 0:  # leave every third group holding a pending entry
                key = ("b", f"pending-{i}")
                scope._entries[key] = Future()
                scope._groups[id(root)][1].add(key)
                scope._evict_groups_locked()
        owned = set()
        for _obj, keys in scope._groups.values():
            owned |= keys
        assert set(scope._entries) <= owned

    def test_the_bound_yields_to_correctness_when_nothing_is_releasable(
        self,
    ) -> None:
        """Exceeding the bound is the right answer, not a bug.

        If every candidate group holds an in-flight producer, releasing any
        of them would be the hazard above. Retention above the bound is
        recovered as soon as a producer finishes, so the cost is temporary
        memory; the alternative cost is a wrong answer.
        """
        from concurrent.futures import Future

        scope = AstAcquisitionScope()
        roots = [_Root(f"r{i}") for i in range(MAX_RETAINED_CONTEXT_GROUPS + 4)]
        for i, root in enumerate(roots):
            scope._touch_group_locked(root)
            key = ("b", f"pending-{i}")
            scope._entries[key] = Future()
            scope._groups[id(root)][1].add(key)
            scope._evict_groups_locked()
        assert scope.group_stats()["retained_groups"] == len(roots)
        assert scope.group_stats()["released_groups"] == 0
        # Completing one makes exactly that group releasable again.
        first_key = ("b", "pending-0")
        scope._entries[first_key].set_result("done")
        scope._evict_groups_locked()
        assert scope.group_stats()["released_groups"] == 1

    def test_a_content_keyed_entry_without_a_group_is_never_released(self) -> None:
        """An effective AST cache key stays valid regardless of residency.

        Only id-derived entries are group-bound; a content-derived one must
        not be collaterally dropped when an unrelated root is evicted.
        """
        scope = AstAcquisitionScope()
        calls = {"n": 0}

        def produce() -> str:
            calls["n"] += 1
            return "content"

        assert scope.run("castxml", "sha256:abc", produce) == "content"
        for i in range(MAX_RETAINED_CONTEXT_GROUPS * 2):
            root = _Root(f"r{i}")
            scope.run("b", repr(id(root)), lambda r=root: r.payload, group=root)
        assert scope.run("castxml", "sha256:abc", produce) == "content"
        assert calls["n"] == 1, "a content-keyed entry was evicted with a group"

    def test_releasing_an_unknown_group_reports_nothing_released(self) -> None:
        """An unknown token is not an error, and not a release either.

        Eviction is driven from a snapshot of the group keys
        (``list(self._groups)``), so a token can legitimately be gone by
        the time the release runs. It must then report ``False`` rather
        than count a release that did not happen -- otherwise the eviction
        loop would treat the bound as satisfied without having freed
        anything, and spin or stop early depending on the order.
        """
        scope = AstAcquisitionScope()
        assert scope._release_group_locked(987654321) is False
        assert scope.group_stats()["released_groups"] == 0


class TestRetainSemantics:
    def test_retain_is_idempotent_per_object(self) -> None:
        """Called once per member parse, with the same root under single-flight."""
        scope = AstAcquisitionScope()
        root = _Root()
        for _ in range(50):
            scope.retain(root)
        assert scope.group_stats()["retained_groups"] == 1

    def test_retain_keeps_the_object_alive(self) -> None:
        import weakref

        scope = AstAcquisitionScope()
        root = _Root()
        ref = weakref.ref(root)
        scope.retain(root)
        del root
        assert ref() is not None, "the retained object was collected"

    def test_reuse_refreshes_a_group_so_the_cold_one_is_evicted(self) -> None:
        """LRU, not FIFO: the root a fan-out keeps coming back to must stay.

        Under FIFO the most-used root would be evicted on schedule and
        re-parsed repeatedly, which is the opposite of what the bound is
        for.
        """
        scope = AstAcquisitionScope()
        hot = _Root("hot")
        scope.retain(hot)
        cold = _Root("cold")
        scope.retain(cold)
        for i in range(MAX_RETAINED_CONTEXT_GROUPS - 2):
            filler = _Root(f"f{i}")
            scope.retain(filler)
            scope.retain(hot)  # keep touching the hot one
        extra = _Root("extra")
        scope.retain(extra)
        assert id(hot) in scope._groups
        assert id(cold) not in scope._groups

    def test_retaining_does_not_disturb_an_existing_group_s_keys(self) -> None:
        scope = AstAcquisitionScope()
        root = _Root()
        scope.run("b", repr(id(root)), lambda: "v", group=root)
        keys_before = set(scope._groups[id(root)][1])
        scope.retain(root)
        assert scope._groups[id(root)][1] == keys_before


class TestStats:
    def test_group_stats_reports_the_three_quantities_a_trace_needs(self) -> None:
        scope = AstAcquisitionScope()
        assert scope.group_stats() == {
            "retained_groups": 0,
            "entries": 0,
            "released_groups": 0,
        }
        root = _Root()
        scope.run("b", repr(id(root)), lambda: "v", group=root)
        assert scope.group_stats() == {
            "retained_groups": 1,
            "entries": 1,
            "released_groups": 0,
        }


class TestThroughThePublicEntryPoint:
    def test_run_ast_acquisition_forwards_the_group(self) -> None:
        with ast_acquisition_scope() as scope:
            root = _Root()
            run_ast_acquisition("b", repr(id(root)), lambda: "v", group=root)
            assert scope.group_stats()["retained_groups"] == 1

    def test_without_a_group_nothing_is_retained(self) -> None:
        with ast_acquisition_scope() as scope:
            run_ast_acquisition("b", "content-key", lambda: "v")
            stats = scope.group_stats()
            assert stats["retained_groups"] == 0
            assert stats["entries"] == 1

    def test_outside_a_scope_the_producer_simply_runs(self) -> None:
        calls = {"n": 0}

        def produce() -> str:
            calls["n"] += 1
            return "v"

        assert run_ast_acquisition("b", "k", produce) == "v"
        assert run_ast_acquisition("b", "k", produce) == "v"
        assert calls["n"] == 2, "an un-scoped call must not be cached"

    def test_a_re_run_after_release_recomputes_rather_than_failing(self) -> None:
        """Releasing costs time, never correctness -- the premise of bounding."""
        with ast_acquisition_scope() as scope:
            roots = [_Root(f"r{i}") for i in range(MAX_RETAINED_CONTEXT_GROUPS + 2)]
            for root in roots:
                run_ast_acquisition(
                    "b", repr(id(root)), lambda r=root: r.payload, group=root
                )
            assert scope.group_stats()["released_groups"] == 2
            # The first root is still referenced by `roots`, so its id is
            # stable; re-running it must produce the same value again.
            assert (
                run_ast_acquisition(
                    "b",
                    repr(id(roots[0])),
                    lambda: roots[0].payload,
                    group=roots[0],
                )
                == "r0"
            )
