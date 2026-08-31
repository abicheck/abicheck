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

"""`check_report.augment_report`'s exit-block upgrade path.

Split out of `check_report.py` (Codex review: that module sits at the
800-line production cap; a new helper here couldn't otherwise fit) --
`check_report.py`'s own docstring index still owns the description of
`augment_report` as a whole, this module is purely its exit-block upgrade
implementation.

`_stamp_schema_version` bumps an older report's `report_schema_version`/
`scan_schema_version` to the current value unconditionally, but `augment_
report` only shallow-copies the report otherwise -- a report produced
before the current schema would then claim it while its `exit` block(s)
don't actually satisfy it. Two distinct gaps, both fixed here:

- An older report's `exit`/`diff.exit` is *present* but missing one or
  more keys the current schema requires -- backfill every missing
  `*_contribution` key with its documented default (``0``). The field set
  is read from :class:`~abicheck.policy.exit_decision.ExitDecision` itself
  rather than hand-copied here (Codex review, fresh evidence: an earlier
  revision's own hand-copied five-field list already missed
  `crosscheck_promotion_contribution`, added one schema version *before*
  those five -- report_schema_version 2.41 introduced the block itself
  with only the first three PR G1 fields, so a genuine 2.41 report is
  missing six keys total, not five) -- deriving it from the dataclass
  means a sixth future field can never be missed here again the same way.
- A pre-1.22 `NOT_COMPARABLE` scan report's `diff` has *no* `exit` key at
  all (`{"reason": ...}` was the whole shape before ADR-064 stage 1b wired
  that outcome) -- synthesize the same decision `scan_engine.py` itself
  now persists for it, rather than leaving the promised block absent.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from ..policy.exit_decision import ExitDecision

#: Every `exit`-block key an older report's block can be missing, derived
#: from `ExitDecision`'s own fields rather than hand-copied (see this
#: module's own docstring for why a hand-copied list already went stale
#: once). `code`/`reasons` are excluded -- they are never independently
#: backfilled as `0`; a block missing either of those is malformed, not
#: merely old.
_EXIT_CONTRIBUTION_FIELDS = tuple(
    f.name for f in dataclasses.fields(ExitDecision) if f.name.endswith("_contribution")
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
            **{f: exit_block.get(f, 0) for f in _EXIT_CONTRIBUTION_FIELDS},
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
                **{f: diff_exit.get(f, 0) for f in _EXIT_CONTRIBUTION_FIELDS},
            },
        }
    elif "exit" not in diff and "reason" in diff:
        # Pre-1.22 NOT_COMPARABLE scan diff -- no `exit` key existed at all
        # (Codex review, fresh evidence).
        from ..exit_decision import resolve_scan_exit_decision

        not_comparable_decision = resolve_scan_exit_decision(not_comparable=True)
        if not_comparable_decision is not None:
            out["diff"] = {**diff, "exit": not_comparable_decision.to_dict()}
