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
)
