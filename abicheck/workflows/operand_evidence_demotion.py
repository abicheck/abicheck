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

"""The operand-evidence demotions one comparison applies before its verdict.

Each annotates, never removes (ADR-067 record before disposing), a finding
the operands' own evidence cannot establish:

* an undeclared respelling between operands of different extractor
  generations (``workflows.generation_drift``, ADR-062 D2);
* a removal resting on a partially read NEW export table
  (``policy.partial_export_read``, ADR-079 D4);
* a toolchain-owned observation of a rebuild: a dependency's coalesced
  (``WEAK``/``GNU_UNIQUE``) copy appearing or vanishing, or an import the old
  binary's version requirements already cover
  (``policy.toolchain_rebuild_drift``);
* a lost stack canary while every unit records the protector still on
  (``policy.stack_canary_provenance``);
* an address-derived object alignment drop that no declared requirement on
  the old side supports (``policy.address_alignment_bound``).
"""

from __future__ import annotations

from ..model.change import Change
from ..model.snapshot import AbiSnapshot
from ..policy.address_alignment_bound import bound_address_alignment_reductions
from ..policy.partial_export_read import demote_unestablished_export_removals
from ..policy.stack_canary_provenance import qualify_stack_canary_removal
from ..policy.toolchain_rebuild_drift import demote_toolchain_rebuild_drift
from .generation_drift import demote_cross_generation_drift

__all__ = ["demote_operand_evidence_gaps"]


def demote_operand_evidence_gaps(
    changes: list[Change], old: AbiSnapshot, new: AbiSnapshot
) -> list[Change]:
    """Apply every operand-evidence demotion to *changes*; mutates and
    returns it."""
    demote_cross_generation_drift(changes, old, new)
    demote_unestablished_export_removals(changes, new)
    qualify_stack_canary_removal(changes, old, new)
    bound_address_alignment_reductions(changes, old)
    from ..buildsource.export_accounting import toolchain_runtime_name_predicate

    return demote_toolchain_rebuild_drift(
        changes, old.elf, new.elf, toolchain_runtime_name_predicate((old, new))
    )
