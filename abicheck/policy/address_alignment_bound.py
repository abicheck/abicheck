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

"""Read an address-derived object alignment as the bound it is.

``exported_object_alignment_reduced`` compares the largest power of two
dividing each side's ``st_value``. That number is where the linker happened
to place the object, which is at least its declared alignment: an object
declared ``_Alignas(16)`` always sits on a 16-byte boundary, but an ``int``
placed at ``0x4010`` reads as 16-aligned too. So a lower placement bound on
the new side proves a reduction only against a requirement the old side
*declared*:

* the old side declares alignment ``D`` (``alignment_bits`` from header
  ``alignas``/``aligned`` or DWARF ``DW_AT_alignment``, or a header-declared
  arithmetic type's natural alignment, ``compare.declared_alignment``) and
  the new placement bound is below ``D``: the new object can no longer meet
  what consumers were promised -- the RISK stands;
* the old side declares ``D`` and the new placement still meets it: the
  observed drop is placement (rule :data:`DECLARED_STILL_MET_RULE`);
* the old side declares nothing: both numbers are placements, and the old
  one says nothing about what consumers rely on (rule
  :data:`UNDECLARED_RULE`);
* the old side's declaration is unknown -- castxml records only a lower
  bound for a variable whose header spells an alignment specifier
  (``extract/headers/castxml/variable_alignment``): the placements alone
  still prove nothing (rule :data:`UNKNOWN_DECLARATION_RULE`), and the
  reason says so instead of claiming nothing was declared.

Both demotions set ``effective_verdict`` to ``COMPATIBLE`` and keep the
finding with its rule (ADR-067: annotated, never removed). The section's
``sh_addralign`` is deliberately not consulted: a stripped or section-zeroed
image must yield the same facts as the intact one.
"""

from __future__ import annotations

from ..compare.declared_alignment import (
    declared_alignment_bits,
    declared_alignment_is_lower_bound,
)
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.change_catalog.registry import Verdict
from ..model.snapshot import AbiSnapshot

__all__ = [
    "DECLARED_STILL_MET_REASON",
    "DECLARED_STILL_MET_RULE",
    "UNDECLARED_REASON",
    "UNDECLARED_RULE",
    "UNKNOWN_DECLARATION_REASON",
    "UNKNOWN_DECLARATION_RULE",
    "bound_address_alignment_reductions",
]

UNDECLARED_RULE = "address-alignment-without-declared-requirement"
UNDECLARED_REASON = (
    "both alignments come from symbol addresses, which only bound the "
    "declared alignment from above; the old side declares no requirement "
    "the new placement could fail"
)
UNKNOWN_DECLARATION_RULE = "address-alignment-with-unknown-declared-requirement"
UNKNOWN_DECLARATION_REASON = (
    "both alignments come from symbol addresses, and the old side's declared "
    "alignment is unknown (its header spells an alignment specifier the header "
    "backend records only as a lower bound); the placements alone prove no "
    "reduction -- debug info (DW_AT_alignment) can"
)
DECLARED_STILL_MET_RULE = "address-alignment-meets-declared-requirement"
DECLARED_STILL_MET_REASON = (
    "the new address still meets the alignment the old side declares; the "
    "drop is a placement change"
)


def _bytes(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def bound_address_alignment_reductions(
    changes: list[Change], old: AbiSnapshot
) -> list[Change]:
    """Demote every ``exported_object_alignment_reduced`` in *changes* that
    the old side's declared alignment does not support; mutates and returns
    it. A finding a prior rule modulated is left alone."""
    for change in changes:
        if (
            change.kind is not ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED
            or change.effective_verdict is not None
        ):
            continue
        new_bound = _bytes(change.new_value)
        if new_bound is None:
            continue
        declared = declared_alignment_bits(old, change.symbol)
        if declared is None and declared_alignment_is_lower_bound(old, change.symbol):
            rule, reason = UNKNOWN_DECLARATION_RULE, UNKNOWN_DECLARATION_REASON
        elif declared is None:
            rule, reason = UNDECLARED_RULE, UNDECLARED_REASON
        elif new_bound * 8 >= declared:
            rule, reason = DECLARED_STILL_MET_RULE, DECLARED_STILL_MET_REASON
        else:
            continue
        change.effective_verdict = Verdict.COMPATIBLE
        change.modulation_rule = rule
        change.modulation_reason = reason
    return changes
