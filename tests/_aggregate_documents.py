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

"""Fixtures shared by the two aggregate PR-comment test modules.

A non-`test_` helper rather than a cross-import between two `test_*.py`
files: `test_pr_comment_aggregate.py` and its
`..._members.py` sibling were split at the architecture gate's
per-test-file ceiling and must keep agreeing about what a member report and
a clean headline *are*. Two copies of these would let one module's idea of
a "clean" comment drift from the other's, which is exactly the disagreement
the split is not allowed to introduce.
"""

from __future__ import annotations

#: The phrases the renderer uses to claim a clean result. A comment
#: containing one of these for a document that established anything less is
#: the defect these two modules exist to keep closed.
CLEAN_HEADLINES = ("No ABI changes", "No compatibility impact detected")


def headline_of(body: str) -> str:
    """The rendered comment's headline line."""
    return next(line for line in body.splitlines() if line.startswith("## "))


def member_report(state: str) -> dict[str, object]:
    """A real per-target ``compare`` report for one of the analyzed states.

    ``clean`` carries no findings, ``additions`` one compatible finding and
    ``break`` one ABI break. Every one of them names the *same* symbol on
    purpose: a folded model must keep three targets' reports of one symbol
    attributable to three targets, which is the cross-platform matrix case
    ``aggregate`` exists for.
    """
    changes: list[dict[str, object]] = []
    verdict = "NO_CHANGE"
    if state == "break":
        verdict = "BREAKING"
        changes = [
            {
                "kind": "func_removed",
                "symbol": "shared_entry",
                "severity": "breaking",
                "description": "Function removed",
            }
        ]
    elif state == "additions":
        verdict = "COMPATIBLE"
        changes = [
            {
                "kind": "func_added",
                "symbol": "shared_entry",
                "severity": "compatible",
                "description": "Function added",
            }
        ]
    return {
        "library": "libunderthetest.so",
        "old_version": "baseline",
        "new_version": "candidate",
        "verdict": verdict,
        "changes": changes,
    }
