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

"""Deterministic parallel probing + ordered-diagnostics primitives.

Factored out of :mod:`abicheck.buildsource.preprocessor_scan`'s S2 parallel
probe pool after a PR review caught **two independent bug classes** in a
hand-rolled ``ThreadPoolExecutor`` probe loop there — both generic footguns
of "read a bounded number of external resources (subprocess output, files,
…) via a thread pool, then report what failed," not specific to any one
extractor:

1. **Nondeterministic diagnostic ordering.** A shared mutable diagnostics
   list appended to directly from worker threads records entries in
   subprocess-*completion* order, not the caller's *input* order — so a
   consumer reading ``diagnostics[0]`` (e.g. an "all failed, here's a
   sample" report) sees a different result across runs of identical,
   pinned inputs. ``ThreadPoolExecutor.map``'s own *result* list is always
   input-ordered; it is only side-channel state a worker mutates directly
   (not the mapped return value) that inherits completion-order instead.
2. **Materializing raw results before parsing.** A worker returning large
   raw data (subprocess stdout, a full dump, …) instead of a small parsed
   result means ``list(pool.map(...))`` — which fully realizes before the
   caller processes anything — holds *every* worker's full raw payload in
   memory simultaneously, not one at a time the way a serial loop
   naturally would.

This module packages the two fixes as reusable primitives instead of
leaving each caller to rediscover and hand-roll them independently — see
``buildsource/preprocessor_scan.py`` for the worked example (its own
``_run_probes``/``_note_diagnostic``/``_reorder_batch_diagnostics`` were the
original, since-generalized fix). A leaf module: no imports from elsewhere
in this package except :mod:`abicheck.deadline` (itself a leaf), so any
extractor — ``buildsource`` or otherwise — can depend on it without risking
an import cycle.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TypeVar

from . import deadline

_Item = TypeVar("_Item")
_Result = TypeVar("_Result")


def run_parallel_probes(
    items: list[_Item],
    probe: Callable[[_Item], tuple[_Item, _Result]],
    *,
    jobs: int,
) -> list[tuple[_Item, _Result]]:
    """Run ``probe`` over ``items``, preserving *input order* in the result.

    Serial (a plain list comprehension) when ``jobs <= 1`` or there is at
    most one item; otherwise a ``ThreadPoolExecutor`` with ``jobs`` workers.
    Use this for I/O-bound probes (a subprocess wait, a file read) — not
    CPU/RAM-heavy work, which wants a process pool sized by
    :mod:`abicheck.process_resources` instead (see
    ``buildsource/source_replay.py``'s L4 extraction for that shape).

    ``probe`` **must** parse its own raw external-resource read down to a
    small structured ``_Result`` before returning — never the raw payload
    itself (bug class 2 above). This function's own contract can't enforce
    that (the type system can't distinguish "small" from "large"), so it is
    the caller's responsibility; see the module docstring's worked example.

    The active :mod:`abicheck.deadline` scope (if any) is captured once
    here and re-established inside each worker — ``contextvars`` don't
    cross a ``ThreadPoolExecutor`` boundary on their own (mirrors
    ``source_replay._deadline_bound_worker``'s identical reasoning for the
    L4 process/thread pool).
    """
    if jobs <= 1 or len(items) <= 1:
        return [probe(item) for item in items]
    deadline_ts = deadline.current_deadline_ts()

    def _bound(item: _Item) -> tuple[_Item, _Result]:
        with deadline.with_deadline_ts(deadline_ts):
            return probe(item)

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(_bound, items))


@dataclass
class OrderedDiagnostics:
    """Thread-safe diagnostics collector, queryable live, reorderable after.

    ``messages`` reflects worker-*completion* order as each is recorded (so
    a caller reading it mid-flight, or after a purely serial run, sees
    exactly what happened, in the order it happened) — but a *parallel*
    batch's contribution to it is only deterministic once reordered back to
    *input* order via ``reorder()`` (bug class 1 above). Fixes a real
    regression: a naive parallel refactor that leaves worker threads
    appending straight to a flat list makes ``messages[0]`` — read by a
    caller like "all failed, here's one sample" reporting — vary run to run
    for identical, pinned inputs, purely from subprocess-completion-time
    jitter.

    Usage, mirroring ``run_parallel_probes``:

    .. code-block:: python

        diag = OrderedDiagnostics()

        def probe(item):
            if <failure>:
                diag.record(item.id, f"... failed for {item.id}: ...")
                return item, None
            return item, <parsed result>

        mark = diag.mark()
        results = run_parallel_probes(items, probe, jobs=jobs)
        diag.reorder(mark, (item.id for item in items))
        # diag.messages[mark:] is now in `items`' own order.
    """

    messages: list[str] = field(default_factory=list)
    _by_unit: dict[str, list[str]] = field(default_factory=dict)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, compare=False, repr=False
    )

    def record(self, unit: str, message: str) -> None:
        """Record *message* for *unit*. Safe to call concurrently."""
        with self._lock:
            self.messages.append(message)
            self._by_unit.setdefault(unit, []).append(message)

    def mark(self) -> int:
        """A position in ``messages`` to later pass to :meth:`reorder`."""
        return len(self.messages)

    def reorder(self, mark: int, unit_order: Iterable[str]) -> None:
        """Re-sort the messages recorded since *mark* into *unit_order*.

        A no-op only when *nothing* was recorded since *mark* — there is
        nothing to reorder or to consume from ``_by_unit``. Deliberately
        **not** short-circuited for exactly one new message either (an
        earlier revision did, on the reasoning that one message has only one
        possible order): that left its ``_by_unit`` entry unconsumed, so a
        *later*, unrelated batch that also happens to record for the same
        unit would ``pop()`` both the stale message and the new one,
        silently duplicating the stale one into the later batch's reordered
        slice (Codex review, fresh evidence — this collector is designed to
        be reused across many ``mark()``/``reorder()`` cycles on one
        instance, e.g. one extractor's several sequential probe batches, so
        this is a real, not merely theoretical, reuse pattern). Always
        popping keeps every call's ``_by_unit`` contribution fully consumed,
        which is what makes reuse safe.

        A unit contributing more than one message (a caller probing it more
        than once in the same batch) keeps those messages in the order
        :meth:`record` saw them, just relocated as a group to that unit's
        position in *unit_order*. A unit named in *unit_order* that never
        recorded anything contributes nothing (``dict.pop(unit, [])`` —
        never a KeyError). A recorded unit *not* named in *unit_order* (a
        caller passing a stale/partial sequence) is silently dropped from
        ``messages`` — pass the exact sequence the batch was dispatched with.
        """
        if len(self.messages) <= mark:
            return
        ordered: list[str] = []
        for unit in unit_order:
            ordered.extend(self._by_unit.pop(unit, []))
        self.messages[mark:] = ordered
