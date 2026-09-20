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

"""Bug classes about caches serving something other than the real answer.

A sibling of `manifest_performance.py`, and deliberately not part of it: the
classes there produce the right answer and the defect is its cost, while a
cache defect produces the *wrong* answer, or none, and is usually silent.
Invalidation, schema drift and "did the cheap path actually run" are one
subject; how often correct work is repeated is another.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["CACHING_BUG_CLASSES"]


CACHING_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="perf.derived_cache_must_match_what_recomputing_would_give",
        invariant=(
            "A cache that serves a *derived* artifact in place of the "
            "expensive input it came from must serve exactly what "
            "recomputing would have produced, and must refuse anything it "
            "cannot prove it understands. The two halves fail differently "
            "and both need their own guard: serving a stale or "
            "wrongly-shaped artifact is silent evidence loss (fewer graph "
            "edges, fewer findings, no error anywhere), while refusing too "
            "eagerly costs only a slow run. The guard is therefore a "
            "result-equality check over several generated inputs PLUS an "
            "observation that the cheap path actually engaged -- an "
            "equality assertion alone passes against an implementation "
            "that ignores the cache and recomputes, which is the behaviour "
            "being removed and which shows up only as memory nobody "
            "measured. Corollary on invalidation: a derived artifact's "
            "validity is its source's validity, so it is named after the "
            "source entry rather than keyed by a second, independently "
            "derived key -- a key that missed one input would serve a "
            "stale artifact, and the inputs are resolved inside the "
            "producer where a copy cannot see them."
        ),
        # The header-graph attach re-parsed an 822 MiB clang AST on every
        # warm run to recompute a 16.5 MiB projection it had already
        # produced identically. The schema is self-describing (the edge
        # dataclasses' own field lists) precisely because a hand-bumped
        # version integer is the kind of registry entry this repository has
        # repeatedly watched go stale.
        fixed_by=(1338,),
        seed_tests=("tests/test_header_graph_projection_cache.py",),
        public_surfaces=("cli", "python-api"),
        axes={
            "cache_state": ("cold", "warm", "sidecar-discarded"),
            "schema": ("current", "reordered-fields", "unknown-version", "corrupt"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The equality-plus-mechanism pair is enforced for the "
                    "header-graph projection cache only. The other derived "
                    "caches in this tree (the snapshot cache, the build "
                    "cache, the per-TU source-replay cache) assert their "
                    "own hit/miss behaviour but none of them pairs a "
                    "result-equality check with a count of the expensive "
                    "work actually skipped, so a silent recompute would "
                    "read as a pass there."
                ),
                reference="docs/contribute/measurements/header-graph-attach-memory.md",
            ),
        ),
    ),
)
