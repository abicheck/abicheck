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

"""ADR-065 S3: the release fan-out's consumer for support-promise findings.

``policy.support_promise`` decides *what* a proven inventory change is;
this module places the answer where the release fan-out already looks. Each
finding becomes one ordinary per-component entry in ``library_results`` --
carrying a real :class:`~abicheck.checker_types.DiffResult` under
``_diff_result`` -- so the release's existing verdict fold, severity
aggregation, JSON/Markdown/JUnit renderers, disposition audit and
PR-comment projection all see it through the machinery they already run,
rather than through a second, parallel reporting path that would then have
to be taught to every renderer separately (this file's own "Finish the
workflow" rule).

The entry is deliberately *not* a comparison result: ``support_promise``
names it as what it is, and the component it names never reached a
completed comparison -- the acquisition record already says so, and
``ScopeAcquisitionRecord.unchecked_members`` already excludes a proven
removal or addition, so the completeness axis does not double-report it.
"""

from __future__ import annotations

from ..checker_types import Change, DiffResult
from ..model.scope_acquisition import ScopeAcquisitionRecord
from ..policy.support_promise import support_promise_changes

__all__ = ["support_promise_results"]

#: Which release verdict string a support-promise change contributes.
_RETIRED = "support_promise_component_retired"


def support_promise_results(
    record: ScopeAcquisitionRecord | None, policy: str | None
) -> list[dict[str, object]]:
    """One ``library_results`` entry per support-promise finding.

    Empty under the default ``off`` policy and for every run whose record
    proves no inventory change -- which keeps every pre-existing invocation
    byte-for-byte unchanged.
    """
    entries: list[dict[str, object]] = []
    for change in support_promise_changes(record, policy):
        retired = change.kind.value == _RETIRED
        verdict = "BREAKING" if retired else "NO_CHANGE"
        entries.append(
            {
                "library": change.symbol,
                "verdict": verdict,
                "breaking": 1 if retired else 0,
                "source_breaks": 0,
                "risk_changes": 0,
                "compatible_additions": 0 if retired else 1,
                "quality_issues": 0,
                "support_promise": "retired" if retired else "introduced",
                "reason": change.description,
                "_diff_result": _one_change_result(change, verdict),
            }
        )
    return entries


def _one_change_result(change: Change, verdict: str) -> DiffResult:
    """A minimal :class:`DiffResult` carrying exactly *change*, so the
    release's severity aggregation and finding renderers read this finding
    the same way they read a per-library one."""
    from ..policy.classification import Verdict

    return DiffResult(
        old_version="",
        new_version="",
        library=change.symbol,
        changes=[change],
        verdict=Verdict[verdict],
    )
