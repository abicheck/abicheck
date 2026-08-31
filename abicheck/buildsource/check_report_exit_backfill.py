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

"""`check_report.augment_report`'s ADR-064 exit-block upgrade path.

Split out of `check_report.py` (Codex review: that module sits at the
800-line production cap; a new helper here couldn't otherwise fit) --
`check_report.py`'s own docstring index still owns the description of
`augment_report` as a whole, this module is purely its exit-block upgrade
implementation.

`_stamp_schema_version` bumps an older report's `report_schema_version`/
`scan_schema_version` to the current value unconditionally, but `augment_
report` only shallow-copies the report otherwise -- a report produced
before ADR-064 stage 1b would then claim the current schema while its
`exit` block(s) don't actually satisfy it. Two distinct gaps, both fixed
here:

- A pre-2.47/1.22 report's `exit`/`diff.exit` is *present* but missing the
  five new keys -- backfill them with their documented default (``0``).
- A pre-1.22 `NOT_COMPARABLE` scan report's `diff` has *no* `exit` key at
  all (`{"reason": ...}` was the whole shape before stage 1b wired that
  outcome) -- synthesize the same decision `scan_engine.py` itself now
  persists for it, rather than leaving the promised block absent.
"""

from __future__ import annotations

from typing import Any

#: ADR-064 stage 1b's five new `exit`-block keys (report schema 2.47/1.22).
_ADR_064_EXIT_FIELDS = (
    "operational_error_contribution",
    "evidence_contract_error_contribution",
    "budget_overflow_contribution",
    "not_comparable_contribution",
    "removed_required_library_contribution",
)


def backfill_exit_block_fields(out: dict[str, Any]) -> None:
    """Upgrade an older report's `exit` block(s) in place on *out*.

    Rebinds each touched container to a copy first: a bare `out = dict(
    report)` shallow copy in `augment_report` leaves `out["exit"]`/
    `out["diff"]` aliasing the caller's own nested dicts, and mutating
    those in place would violate that function's own "report is never
    mutated" contract.
    """
    exit_block = out.get("exit")
    if isinstance(exit_block, dict):
        out["exit"] = {
            **exit_block,
            **{f: exit_block.get(f, 0) for f in _ADR_064_EXIT_FIELDS},
        }
    diff = out.get("diff")
    if not isinstance(diff, dict):
        return
    diff_exit = diff.get("exit")
    if isinstance(diff_exit, dict):
        out["diff"] = {
            **diff,
            "exit": {
                **diff_exit,
                **{f: diff_exit.get(f, 0) for f in _ADR_064_EXIT_FIELDS},
            },
        }
    elif "exit" not in diff and "reason" in diff:
        # Pre-1.22 NOT_COMPARABLE scan diff -- no `exit` key existed at all
        # (Codex review, fresh evidence).
        from ..exit_decision import resolve_scan_exit_decision

        not_comparable_decision = resolve_scan_exit_decision(not_comparable=True)
        if not_comparable_decision is not None:
            out["diff"] = {**diff, "exit": not_comparable_decision.to_dict()}
