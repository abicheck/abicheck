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

"""Bug classes about *shared mutable state under the release fan-out*.

Its own module for the reason ``manifest_guards.py``/``manifest_report.py``/
``manifest_test_harness.py`` are: ``manifest.py`` sits at its
``architecture/debt.yaml`` ``no_growth`` baseline and is an assembly point
over per-axis entry lists, so a new class goes in a sibling rather than
growing it.

The axis this module names is distinct from ``manifest_performance.py``'s,
which is about how much a concurrent run *costs*. These are about whether a
concurrent run is *correct*: the release fan-out dispatches every member
through a ``ThreadPoolExecutor`` in one address space, so every process-wide
memo, counter and index is shared by construction, and the resulting defects
are invisible to every functional test that runs one comparison at a time.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["CONCURRENCY_BUG_CLASSES"]


CONCURRENCY_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="concurrency.compound_cache_operation_under_fan_out",
        invariant=(
            "A process-wide memo the release fan-out's worker threads share "
            "must make every *compound* state transition atomic, not merely "
            "every individual container operation. The GIL already makes a "
            "single dict/OrderedDict call atomic, which is exactly why this "
            "class is invisible to inspection and to single-threaded tests: "
            "the defect is always a sequence -- read-then-reorder, "
            "test-then-insert, insert-then-account-then-evict, "
            "compute-then-publish -- where another worker's own correct, "
            "individually-atomic operation lands in the gap. Four "
            "properties follow, and each needs its own assertion because no "
            "one of them implies the others. (1) A cache *read* is a pure "
            "read: it may report a miss, but it may never raise, and it may "
            "never answer anything but the real value for its key -- so a "
            "``KeyError`` from a recency update after a concurrent eviction "
            "is a defect, and catching it and answering an empty result is "
            "not a fix but a second, worse one, because an empty result is "
            "a legitimate cached answer and the two must stay "
            "distinguishable. (2) Accounting is derivable: after any "
            "interleaving, retained-byte totals, entry counts and "
            "reference counts must equal what a fresh derivation from the "
            "entries actually present would produce, which is why the "
            "oracle re-derives them rather than re-running the "
            "implementation's own arithmetic. (3) Expensive work stays "
            "outside the lock and publication is rechecked afterwards: "
            "duplicate computation on simultaneous misses is allowed and "
            "must be *stated*, while duplicate publication is not, since "
            "downstream identity (an ``id()``-keyed token, a 'one pattern "
            "per vocabulary' assumption) depends on a single winner. A "
            "guard for this must observe the mechanism -- that both "
            "configurations genuinely ran concurrently -- not merely that "
            "their outputs agree. (4) A lifecycle reset (``clear``) that "
            "can overlap in-flight work needs a stated contract: here a "
            "generation counter, so a pre-reset computation completes and "
            "serves its caller but is never resurrected into the new "
            "epoch. A run that is correct once is no evidence at all for "
            "this class -- the failure is nondeterministic by nature, so "
            "the regression must *force* the interleaving with barriers or "
            "mapping hooks, and prove it forced it."
        ),
        # `compare/spelling_match_cache.py`'s MATCH_CACHE and
        # VOCABULARY_CACHE are module globals every release member thread
        # reads and writes. `_MatchCache.get` did `_entries.get(key)` and
        # then `_entries.move_to_end(key)` as two steps; a sibling worker's
        # `put` evicted that key in between and the read raised
        # `KeyError((id(pattern), text, start, end))`. Observed on a real
        # six-member oneDAL release comparison, which reported
        # libonedal.so, libonedal_core.so and libonedal_dpc.so as failed
        # with `verdict: ERROR` / `operational: extraction_error` /
        # `scope: incomplete` on one run and completed cleanly on the next.
        # The cache's own test suite -- equivalence, eviction, accounting,
        # immutability -- passed at that revision, because every one of its
        # tests ran on one thread.
        fixed_by=(),
        seed_tests=(
            "tests/test_spelling_match_cache_concurrency.py",
            "tests/test_compare_release_concurrency_integration.py",
        ),
        public_surfaces=("cli",),
        axes={
            "transition": (
                "lookup-then-recency-update",
                "test-then-insert",
                "insert-then-account-then-evict",
                "compute-then-publish",
                "clear-across-in-flight-computation",
            ),
            "cache": ("match", "vocabulary"),
            "result_shape": ("positive", "empty", "oversized-bypass"),
            "workers": ("1", "2", "4", "8"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The workflow-level regression builds a four-member "
                    "compiled C++ release, not the six-member oneDAL bundle "
                    "whose run produced the report. Its members share type "
                    "spellings deliberately so several workers land on the "
                    "same match-cache keys, but its vocabularies are orders "
                    "of magnitude smaller than oneDAL's (whose largest "
                    "compiled alternation is ~3.3M characters), so it "
                    "reaches far less eviction pressure per unit of work "
                    "than the real workload does. The forced-interleaving "
                    "tests do not depend on that scale; the end-to-end "
                    "stability claim does, and remains verified only at "
                    "fixture scale."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
            KnownGap(
                description=(
                    "Only the two type-spelling caches are covered. The "
                    "invariant is stated for any process-wide memo the "
                    "fan-out shares, and no audit of the remaining "
                    "module-global mutable state reachable from a member "
                    "comparison was performed as part of this fix -- so a "
                    "sibling instance of this same class elsewhere in the "
                    "engine would not be caught by these seed tests."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
)
