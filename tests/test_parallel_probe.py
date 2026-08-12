# Copyright 2026 Nikolay Petrov
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

"""Tests for abicheck.parallel_probe — the generalized fix for two bug
classes an S2 preprocessor-pre-scan PR review found in a hand-rolled
``ThreadPoolExecutor`` probe loop (see that module's own docstring):

1. Nondeterministic diagnostic ordering (a shared list appended to from
   worker threads records completion order, not input order).
2. Materializing raw results before parsing (a worker returning large raw
   data instead of a small parsed result means the fully-realized
   ``pool.map`` result list holds every payload in memory at once).

A follow-up audit (this same review round) found the identical bug-1 shape
copy-pasted across SIX ``Clang*GraphExtractor`` classes
(``call_graph.py``, ``callback_graph.py``, ``override_graph.py``,
``macro_graph.py``, ``type_graph.py``, ``template_graph_extractor.py``) --
confirming this is a systemic pattern, not a one-off, which is why the fix
lives here as a reusable primitive rather than staying local to
``preprocessor_scan.py``. These tests exercise the primitive directly,
decoupled from any one caller's domain logic, per this repo's own
"primitive-level property tests" convention (AGENTS.md).
"""

from __future__ import annotations

import threading
import time

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.parallel_probe import OrderedDiagnostics, run_parallel_probes

# ── run_parallel_probes: basic contract ─────────────────────────────────────


def test_serial_when_jobs_is_one() -> None:
    calls: list[int] = []

    def probe(item: int) -> tuple[int, int]:
        calls.append(item)
        return item, item * 2

    results = run_parallel_probes([1, 2, 3], probe, jobs=1)
    assert results == [(1, 2), (2, 4), (3, 6)]
    assert calls == [1, 2, 3]  # serial: called in input order, no threads


def test_serial_when_single_item_even_with_jobs_many() -> None:
    def probe(item: int) -> tuple[int, int]:
        return item, item * 2

    assert run_parallel_probes([5], probe, jobs=8) == [(5, 10)]


def test_empty_items_returns_empty() -> None:
    def probe(item: int) -> tuple[int, int]:
        raise AssertionError("must not be called for an empty item list")

    assert run_parallel_probes([], probe, jobs=4) == []


def test_parallel_preserves_input_order_in_result() -> None:
    # The pool.map() result list is input-ordered by construction, even
    # though workers may finish in a different order -- verified here by
    # deliberately staggering each item's sleep so LATER items finish
    # FIRST, then checking the returned list is still in input order.
    n = 10

    def probe(item: int) -> tuple[int, int]:
        time.sleep((n - item) * 0.005)  # reverse-staggered
        return item, item * item

    results = run_parallel_probes(list(range(n)), probe, jobs=8)
    assert results == [(i, i * i) for i in range(n)]


def test_parallel_actually_uses_multiple_threads() -> None:
    # Sanity check that jobs>1 really dispatches concurrently rather than
    # silently falling back to serial -- distinct worker thread idents seen
    # across items.
    seen_threads: set[int] = set()
    lock = threading.Lock()

    def probe(item: int) -> tuple[int, None]:
        with lock:
            seen_threads.add(threading.get_ident())
        time.sleep(0.02)
        return item, None

    run_parallel_probes(list(range(8)), probe, jobs=4)
    assert len(seen_threads) > 1


def test_deadline_scope_propagates_into_workers() -> None:
    # contextvars don't cross a ThreadPoolExecutor boundary on their own --
    # run_parallel_probes must re-establish the active deadline inside each
    # worker (mirrors source_replay._deadline_bound_worker's identical
    # reasoning for the L4 pool).
    from abicheck import deadline

    seen_remaining: list[float | None] = []
    lock = threading.Lock()

    def probe(item: int) -> tuple[int, None]:
        with lock:
            seen_remaining.append(deadline.remaining())
        return item, None

    with deadline.deadline_scope(120.0):
        run_parallel_probes(list(range(4)), probe, jobs=4)

    assert len(seen_remaining) == 4
    assert all(r is not None and r <= 120.5 for r in seen_remaining)


# ── OrderedDiagnostics: basic contract ──────────────────────────────────────


def test_record_appends_in_call_order() -> None:
    diag = OrderedDiagnostics()
    diag.record("a", "fail a")
    diag.record("b", "fail b")
    assert diag.messages == ["fail a", "fail b"]


def test_mark_and_reorder_restores_input_order() -> None:
    diag = OrderedDiagnostics()
    mark = diag.mark()
    # Simulate three workers finishing out of their true input order (b,
    # then a, then c) even though the caller's real input order is a, b, c.
    diag.record("b", "fail b")
    diag.record("a", "fail a")
    diag.record("c", "fail c")
    assert diag.messages == ["fail b", "fail a", "fail c"]  # completion order

    diag.reorder(mark, ["a", "b", "c"])  # true input order

    assert diag.messages == ["fail a", "fail b", "fail c"]


def test_reorder_leaves_pre_mark_messages_alone() -> None:
    diag = OrderedDiagnostics()
    diag.messages.append("preexisting message")
    mark = diag.mark()
    diag.record("b", "fail b")
    diag.record("a", "fail a")

    diag.reorder(mark, ["a", "b"])

    assert diag.messages == ["preexisting message", "fail a", "fail b"]


def test_reorder_noop_for_zero_messages() -> None:
    diag = OrderedDiagnostics()
    mark = diag.mark()
    diag.reorder(mark, ["a", "b", "c"])  # 0 new messages
    assert diag.messages == []


def test_reorder_single_message_result_unchanged() -> None:
    diag = OrderedDiagnostics()
    mark = diag.mark()
    diag.record("a", "only failure")
    diag.reorder(mark, ["a", "b", "c"])  # 1 new message
    assert diag.messages == ["only failure"]


def test_reorder_consumes_by_unit_even_for_a_single_message_batch() -> None:
    # Codex review, fresh evidence: an earlier revision short-circuited
    # reorder() for a 0-or-1-message batch WITHOUT consuming that message's
    # _by_unit entry. OrderedDiagnostics is designed to be reused across
    # many mark()/reorder() cycles on one instance (e.g. one extractor's
    # several sequential probe batches) -- if a later, unrelated batch also
    # records for the SAME unit, reorder() would pop BOTH the stale message
    # from the earlier single-message batch and the new one, silently
    # duplicating the stale message into the later batch's reordered slice.
    diag = OrderedDiagnostics()

    # Batch 1: exactly one message, for unit "a" -- the shape that used to
    # leak.
    mark1 = diag.mark()
    diag.record("a", "first failure for a")
    diag.reorder(mark1, ["a"])
    assert diag.messages == ["first failure for a"]

    # Batch 2: unrelated batch, but unit "a" fails again (a real scenario --
    # the same compile unit/header appears in a later probe run). Recorded
    # out of the batch's own input order (b before a) to also exercise the
    # reorder itself, not just the leak.
    mark2 = diag.mark()
    diag.record("b", "second failure for b")
    diag.record("a", "second failure for a")
    diag.reorder(mark2, ["a", "b"])

    # Batch 1's message must appear exactly once, at its own position;
    # batch 2's contribution must be exactly its own two messages, in "a",
    # "b" order -- never "first failure for a" resurfacing inside batch 2.
    assert diag.messages == [
        "first failure for a",
        "second failure for a",
        "second failure for b",
    ]


def test_reorder_a_unit_absent_from_unit_order_is_dropped() -> None:
    # Documented contract: a recorded unit not named in unit_order is
    # silently dropped from `messages` -- the caller is expected to pass
    # the exact sequence the batch was dispatched with.
    diag = OrderedDiagnostics()
    mark = diag.mark()
    diag.record("a", "fail a")
    diag.record("stale", "fail stale")
    diag.record("b", "fail b")

    diag.reorder(mark, ["a", "b"])  # "stale" omitted

    assert diag.messages == ["fail a", "fail b"]


def test_reorder_a_unit_absent_from_messages_contributes_nothing() -> None:
    diag = OrderedDiagnostics()
    mark = diag.mark()
    diag.record("a", "fail a")
    diag.record("b", "fail b")

    diag.reorder(mark, ["a", "never-recorded", "b"])

    assert diag.messages == ["fail a", "fail b"]


def test_reorder_groups_multiple_messages_per_unit_together() -> None:
    diag = OrderedDiagnostics()
    mark = diag.mark()
    diag.record("b", "b msg 1")
    diag.record("a", "a msg 1")
    diag.record("b", "b msg 2")

    diag.reorder(mark, ["a", "b"])

    # "a"'s message(s) first, then ALL of "b"'s, in the order record() saw
    # them -- a unit's own multiple messages are never individually
    # reordered relative to each other.
    assert diag.messages == ["a msg 1", "b msg 1", "b msg 2"]


# ── end-to-end: real thread pool, deterministic ordering ────────────────────


def test_ordered_diagnostics_deterministic_under_real_thread_pool() -> None:
    # Combines run_parallel_probes + OrderedDiagnostics the way a real
    # caller (e.g. preprocessor_scan.py) does: real ThreadPoolExecutor
    # workers, deliberately staggered so LATER-input items finish FIRST,
    # confirming messages[0] is stable across repeated runs of identical,
    # pinned inputs regardless of actual completion order.
    n = 8
    items = list(range(n))

    def make_probe(diag: OrderedDiagnostics):
        def probe(item: int) -> tuple[int, None]:
            time.sleep((n - item) * 0.01)  # reverse-staggered
            diag.record(str(item), f"failed for {item}")
            return item, None

        return probe

    first_messages = set()
    for _ in range(5):
        diag = OrderedDiagnostics()
        mark = diag.mark()
        run_parallel_probes(items, make_probe(diag), jobs=4)
        diag.reorder(mark, (str(i) for i in items))
        first_messages.add(diag.messages[0])

    assert first_messages == {"failed for 0"}


# ── property-based: reorder always restores input order ────────────────────
# Marked `slow` per-test (not file-wide `pytestmark`) so the fast default
# suite still runs every example-based test above; only these two
# Hypothesis-driven ones are excluded from it.

_UNIT_NAMES = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
    min_size=1,
    max_size=4,
)


@pytest.mark.slow
@given(units=st.lists(_UNIT_NAMES, min_size=0, max_size=12, unique=True))
@settings(max_examples=200)
def test_reorder_restores_input_order_for_any_unit_set(units: list[str]) -> None:
    """For ANY set of units and ANY completion (recording) order, reorder()
    restores the caller's original input order -- the core contract this
    whole module exists for, checked against arbitrary inputs rather than
    a handful of hand-picked examples."""
    diag = OrderedDiagnostics()
    mark = diag.mark()
    # Record in a shuffled ("completion") order distinct from `units`'
    # declared ("input") order.
    shuffled = list(reversed(units))
    for unit in shuffled:
        diag.record(unit, f"msg-{unit}")

    diag.reorder(mark, units)

    assert diag.messages == [f"msg-{u}" for u in units]


@pytest.mark.slow
@given(
    units=st.lists(_UNIT_NAMES, min_size=1, max_size=8, unique=True),
    data=st.data(),
)
@settings(max_examples=200)
def test_reorder_restores_input_order_for_a_random_failing_subset(
    units: list[str], data: st.DataObject
) -> None:
    """Only a random SUBSET of units fails (the common real-world shape --
    most probes succeed, a few fail) -- reorder() must still restore that
    subset into its own relative input order, regardless of which subset
    failed or the order failures were recorded in."""
    failing = data.draw(
        st.lists(st.sampled_from(units), max_size=len(units), unique=True)
    )
    # Record in reverse of the FAILING list's own order, simulating
    # out-of-order completion among just the failures.
    diag = OrderedDiagnostics()
    mark = diag.mark()
    for unit in reversed(failing):
        diag.record(unit, f"msg-{unit}")

    diag.reorder(mark, units)

    expected = [f"msg-{u}" for u in units if u in failing]
    assert diag.messages == expected
