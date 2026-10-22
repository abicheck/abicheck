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
"""Bug classes about measurement: a gate or budget that judges a sampled
quantity other than the one its name and documentation claim.

A sibling of `manifest_performance.py`, and deliberately not part of it: the
classes there are about how much work or memory the pipeline spends, while
a measurement defect is in the instrument. The budget is correct; the number
fed into it is not what the budget says it is.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["MEASUREMENT_BUG_CLASSES"]


MEASUREMENT_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="measurement.forked_tree_rss_overstates_memory",
        invariant=(
            "A memory budget on a process tree must judge the tree's physical "
            "footprint, not the sum of per-process resident set sizes. A forked "
            "child's inherited copy-on-write pages are resident in the parent "
            "and in every child, so summed RSS counts them once per process and "
            "overstates a forked tree by roughly its fan-out. A budget read off "
            "that sum fails a run whose real footprint is within the limit. "
            "Proportional set size divides each shared page among the processes "
            "mapping it, so the sum of PSS is the tree's footprint, and it is the "
            "quantity a tree budget must gate on."
        ),
        fixed_by=(1700, 1719),
        seed_tests=("tests/test_l2_scaling_perf_gate.py",),
        known_gaps=(
            KnownGap(
                description=(
                    "The seed test pins the L2 scaling gate's judgement on "
                    "hand-built points whose PSS and RSS are set directly. It "
                    "does not run a real forked release and check the sampled "
                    "PSS against the kernel's own accounting. Nothing enforces "
                    "that every memory budget under `scripts/` judges PSS: only "
                    "the L2 scaling gate is pinned here."
                ),
                reference="PR #1719",
            ),
        ),
    ),
)
