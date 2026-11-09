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

"""Qualify a lost stack canary by the protector setting each unit records.

``stack_canary_removed`` is observed from the symbol table alone: the old
binary calls ``__stack_chk_fail``, the new one does not. That is a hardening
regression only when the protector flag was weakened. An ``-O2`` rebuild
with ``-fstack-protector-strong`` still on in every unit may leave no frame
that needs a canary, and the symbol disappears with nothing weakened.

The per-unit census (``AbiSnapshot.cu_stack_protector``, read from each
CU's ``DW_AT_producer``) decides, through
:func:`~abicheck.model.stack_protector.compare_stack_protector`:

* ``unchanged`` -- every unit on both sides records its setting, and every
  new unit is protected and at least as strong as the strongest old unit
  (units are not matched across builds): the finding is
  demoted to ``COMPATIBLE`` with rule :data:`PROTECTOR_UNCHANGED_RULE`
  (ADR-067: annotated, never removed);
* ``weakened`` / ``partial`` / ``mixed`` -- the RISK stands, and
  ``review_evidence`` names the recorded settings;
* ``unknown`` -- no census on a side (no DWARF read: binary-only, stripped,
  an older snapshot), a unit with no recorded setting, or exported code no
  unit describes (an object linked without debug info): the RISK stands as
  before, with ``confidence`` lowered and ``review_evidence`` saying the flag
  could not be checked.
"""

from __future__ import annotations

from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.change_catalog.registry import Verdict
from ..model.evidence_status import Confidence
from ..model.snapshot import AbiSnapshot
from ..model.stack_protector import StackProtectorTransition, compare_stack_protector

__all__ = [
    "PROTECTOR_UNCHANGED_REASON",
    "PROTECTOR_UNCHANGED_RULE",
    "qualify_stack_canary_removal",
]

PROTECTOR_UNCHANGED_RULE = "stack-protector-unchanged-in-every-unit"
PROTECTOR_UNCHANGED_REASON = (
    "every compilation unit on both sides records a stack protector, every "
    "exported function lies in one of them, and no new unit is weaker than "
    "any old one; no frame needs a canary any more"
)

_NOTES: dict[StackProtectorTransition, str] = {
    StackProtectorTransition.WEAKENED: (
        "the recorded stack-protector setting is weaker in the new build"
    ),
    StackProtectorTransition.PARTIAL: (
        "a new compilation unit is built without a stack protector"
    ),
    StackProtectorTransition.MIXED: (
        "the builds mix stack-protector settings across units, and units are "
        "not matched across builds, so one unit's weakening cannot be ruled out"
    ),
    StackProtectorTransition.UNKNOWN: (
        "weaker evidence: no per-unit compile flags were recorded on both "
        "sides (no DWARF DW_AT_producer with switches), so whether the "
        "stack-protector flag changed is not established"
    ),
}


def qualify_stack_canary_removal(
    changes: list[Change], old: AbiSnapshot, new: AbiSnapshot
) -> list[Change]:
    """Apply the census to every ``stack_canary_removed`` in *changes*;
    mutates and returns it. A finding a prior rule modulated is left alone."""
    old_census = getattr(old, "cu_stack_protector", None)
    new_census = getattr(new, "cu_stack_protector", None)
    for change in changes:
        if (
            change.kind is not ChangeKind.STACK_CANARY_REMOVED
            or change.effective_verdict is not None
        ):
            continue
        transition = compare_stack_protector(old_census, new_census)
        evidence = dict(change.review_evidence or {})
        evidence.update(
            stack_protector=transition.value,
            old_units=dict(old_census) if old_census else None,
            new_units=dict(new_census) if new_census else None,
        )
        change.review_evidence = evidence
        if transition is StackProtectorTransition.UNCHANGED:
            change.effective_verdict = Verdict.COMPATIBLE
            change.modulation_rule = PROTECTOR_UNCHANGED_RULE
            change.modulation_reason = PROTECTOR_UNCHANGED_REASON
            continue
        evidence["note"] = _NOTES[transition]
        if transition is StackProtectorTransition.UNKNOWN:
            change.confidence = Confidence.MEDIUM
    return changes
