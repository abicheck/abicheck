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

"""Bug classes about *how often* correct work is performed.

A sibling of `manifest_evidence.py`/`manifest_guards.py`/
`manifest_report.py`/`manifest_tool_surface.py` (see `manifest.py` for what
this registry is and is not). The classes here produce the right answer:
what makes them defects is the cost of producing it, and -- the reason they
belong in a *regression* registry at all -- the fact that nothing anywhere
measured that cost, so the regression was invisible to a passing suite and
was found only by profiling a real corpus.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["PERFORMANCE_BUG_CLASSES"]


PERFORMANCE_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="perf.pure_content_digest_recomputed_per_consumer",
        invariant=(
            "A pure, expensive, content-derived value reached by several "
            "independent consumers within one run must be computed at "
            "most once per distinct input, not once per consumer. The "
            "guard is a count of the expensive work actually performed "
            "during a real run, bounded by the number of distinct inputs "
            "— never an assertion that one named consumer uses the cache, "
            "which forecloses exactly that call site and says nothing "
            "about the next one added."
        ),
        # v19 oneAPI scan: every graph-shaped compare regressed ~1.7-1.8x
        # against v18 (oneTBB pair 431s -> 740s, self-compare 406s -> 726s).
        # cProfile put 404s of a 620s run inside
        # `serialization.snapshot_content_digest` at n=6 over two snapshots;
        # v18 was n=2. A fourth `same_persisted_content` caller had been
        # added without anyone noticing the third, and nothing anywhere
        # measured the count.
        fixed_by=(1245,),
        seed_tests=("tests/test_snapshot_digest_recomputation.py",),
        public_surfaces=("cli", "python-api"),
        axes={
            "front_end": ("cli", "typed_api"),
            "pairing": ("content-identical", "genuinely-different"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The bound is enforced for the pairwise `compare` "
                    "paths that open a digest scope. Other snapshot-digest "
                    "consumers outside one — `workflows/"
                    "bundle_stored_pair_compare.py`'s stored-pair route — "
                    "are correct but unmemoized, and no gate fails if a "
                    "future front end forgets to open a scope."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
        ),
    ),
    BugClass(
        id="perf.shared_resource_gate_keyed_on_a_per_caller_value",
        invariant=(
            "A process-wide bound on a scarce resource must be one object "
            "whose accounting cannot be replaced while a holder is still "
            "using it, and must be sized from the *host* budget alone — "
            "never from a value that varies per caller (a unit count, a "
            "per-request worker request). The guard is a count of the "
            "resource actually held concurrently while two callers that "
            "resolve to *different* sizes overlap; a single-caller test, or "
            "one where both callers happen to resolve the same size, passes "
            "against a gate that is no gate at all. Two corollaries, each "
            "its own defect found on the same gate: every path that takes "
            "the resource goes through it -- a *serial* path is one holder, "
            "not none, and exempting it admits one over the cap -- and "
            "waiting for it is bounded by whatever deadlines bound the work "
            "itself, or one caller's long hold makes another overrun a "
            "budget it was supposed to be held to. And the bound must be "
            "re-read, not cached in the object: a limit baked in at "
            "construction cannot notice the budget it came from shrinking, "
            "so in a long-lived process every caller narrows itself "
            "correctly while the stale gate keeps admitting the old number."
        ),
        # Found in review on the PR that introduced the bounded-parallel
        # `clang -M` include-map pass. The gate was keyed on each pool's own
        # resolved worker count and rebuilt whenever that differed — and the
        # pool size is `min(host_limit, unit_count)`, so the ordinary case of
        # two sides with differing header counts (`service.compare` resolves
        # old and new concurrently) rebuilt it mid-flight: the first pool kept
        # an orphaned semaphore and both admitted their full quota at once, a
        # 2-slot plus a 4-slot gate against an intended cap of 4. Every test
        # written for the feature passed, because each drove one extractor, or
        # two whose unit counts matched.
        fixed_by=(1275,),
        seed_tests=("tests/test_include_graph_parallel.py",),
        public_surfaces=("cli", "python-api"),
        axes={
            "caller_count": ("one-pool", "two-concurrent-pools"),
            "resolved_size": ("equal-sizes", "differing-sizes"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The invariant is enforced for the `clang -M` "
                    "include-map gate only. The other concurrent pools in "
                    "this codebase (L4 source replay, the L5 call/type "
                    "graph passes, the per-TU manifest dump) each size "
                    "themselves from the same host probe but hold no shared "
                    "gate at all, so two of *those* running concurrently "
                    "still oversubscribe — the pre-existing hazard "
                    "`service_compare_pipeline.resolve_sides_sequentially` "
                    "documents rather than bounds."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
        ),
    ),
)
