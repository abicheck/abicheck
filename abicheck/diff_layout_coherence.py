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

"""DWARF-vs-header-AST layout-backfill coherence, surfaced at compare time
(P0 evidence-coherence audit).

``dumper_layout_backfill.backfill_dwarf_layout()`` already refuses to merge
a header record's layout from DWARF unless it can corroborate the two
describe the same declaration (see that module's docstring); a refusal
never lets incorrect data into the snapshot, it just leaves that one
record's layout blank rather than trusted. That refusal was previously
invisible: ``AbiSnapshot.dwarf_layout_coherence`` (schema v16) makes it a
structured, machine-readable fact, and this module turns a "mismatch"
outcome on either side of a comparison into an ordinary, reportable
finding — the same "explain, don't silently hide" treatment every other
evidence-quality signal in this codebase gets (ADR-028 D3: never itself
BREAKING, an artifact diff still proves any real shipped break).
"""

from __future__ import annotations

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_helpers import make_change
from .model import AbiSnapshot


def _mismatch_change(snapshot: AbiSnapshot, *, side: str) -> Change:
    names = list(snapshot.dwarf_layout_coherence_mismatches)
    preview = ", ".join(names[:5])
    if len(names) > 5:
        preview += f", and {len(names) - 5} more"
    return make_change(
        ChangeKind.HEADER_BINARY_CONTEXT_MISMATCH,
        symbol=snapshot.library,
        description=(
            f"The {side} snapshot's DWARF-vs-header-AST layout backfill found "
            f"{len(names)} record(s) with a uniquely-named DWARF counterpart "
            f"that could not be corroborated as the same declaration "
            f"(disagreeing kind, or no field/base overlap): {preview}. Each "
            "such record's layout stays header-only/incomplete rather than "
            "merged -- this does not itself indicate an ABI change, but any "
            "real layout difference on these specific records is invisible "
            "to this comparison."
        ),
        affected_symbols=names,
    )


@registry.detector(
    "dwarf_layout_coherence",
    requires_support=lambda o, n: (
        o.dwarf_layout_coherence == "mismatch"
        or n.dwarf_layout_coherence == "mismatch",
        "neither snapshot has a DWARF-vs-header-AST layout coherence mismatch",
    ),
    # A trigger, not an evidence gate: both snapshots *state* their coherence,
    # and `matched` on both sides is a conclusive answer -- there is genuinely
    # nothing here to report, which is not a coverage limitation.
    support_is_trigger=True,
)
def _diff_dwarf_layout_coherence(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    changes: list[Change] = []
    if old.dwarf_layout_coherence == "mismatch":
        changes.append(_mismatch_change(old, side="old"))
    if new.dwarf_layout_coherence == "mismatch":
        changes.append(_mismatch_change(new, side="new"))
    return changes
