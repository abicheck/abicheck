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

"""Demote a gcc-plugin byte-aligned bit-field read across extractor generation 60
(ADR-062 D2 fallback; bug class ``storage.cross_generation_spelling_drift``).

Before generation 60 the native gcc header plugin read ``DECL_BIT_FIELD``,
which GCC clears for a bit-field that sits on a byte boundary with a
whole-byte width (``unsigned a : 8`` at offset 0): such a field was stored as
a plain member. From generation 60 the plugin reads ``DECL_C_BIT_FIELD`` and
stores it as a bit-field of that width. A pre-60 gcc-plugin baseline compared
with a newer snapshot of the same header therefore shows
``FIELD_BITFIELD_CHANGED`` (``bits=None`` against ``bits=8``) that the header
does not cause.

A stored field does not say whether it was such a bit-field, so no load-time
rewrite is sound. Comparison annotates the finding instead: only across that
generation boundary, only when the stale operand is a gcc-plugin snapshot,
and only for the exact stale shape (not a bit-field on the stale side, a
whole-byte bit-field on the fresh side). The finding stays in the report with
its rule and reason (ADR-067 record before disposing); a real bit-field edit
of any other shape, or one between snapshots of the same generation, is
untouched.
"""

from __future__ import annotations

from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.change_catalog.registry import Verdict
from ..model.snapshot import AbiSnapshot
from .generation_drift_demotion import GENERATION_DRIFT_REASON, GENERATION_DRIFT_RULE

__all__ = ["GCC_BITFIELD_FIX_GENERATION", "demote_gcc_byte_bitfield_drift"]

#: The first extractor generation whose gcc plugin keeps a byte-aligned
#: bit-field marked as a bit-field.
GCC_BITFIELD_FIX_GENERATION = 60
_GCC_PRODUCER = "gcc-experimental"
#: The values ``diff_types`` writes on ``FIELD_BITFIELD_CHANGED`` for "not a
#: bit-field" and for a whole-byte bit-field width (up to a 128-bit unit).
_NOT_A_BITFIELD = "bits=None"
_WHOLE_BYTE_WIDTHS = frozenset(f"bits={8 * k}" for k in range(1, 17))


def demote_gcc_byte_bitfield_drift(
    changes: list[Change],
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_generation: int,
    new_generation: int,
) -> list[Change]:
    """Annotate the pre-60 gcc-plugin byte-aligned bit-field artifact as
    ``COMPATIBLE_WITH_RISK`` (see the module docstring)."""
    if old_generation == new_generation:
        return changes
    stale_is_old = old_generation < new_generation
    stale_generation = old_generation if stale_is_old else new_generation
    fresh_generation = new_generation if stale_is_old else old_generation
    stale = old if stale_is_old else new
    if not (
        stale_generation < GCC_BITFIELD_FIX_GENERATION <= fresh_generation
        and stale.ast_producer == _GCC_PRODUCER
    ):
        return changes
    for change in changes:
        if (
            change.kind is not ChangeKind.FIELD_BITFIELD_CHANGED
            or change.effective_verdict is not None
        ):
            continue
        stale_value, fresh_value = (
            (change.old_value, change.new_value)
            if stale_is_old
            else (change.new_value, change.old_value)
        )
        if stale_value != _NOT_A_BITFIELD or fresh_value not in _WHOLE_BYTE_WIDTHS:
            continue
        change.effective_verdict = Verdict.COMPATIBLE_WITH_RISK
        change.modulation_rule = GENERATION_DRIFT_RULE
        change.modulation_reason = (
            f"{GENERATION_DRIFT_REASON} (old operand extractor generation "
            f"{old_generation or 'unstated'}, new {new_generation or 'unstated'}; "
            f"the gcc plugin before generation {GCC_BITFIELD_FIX_GENERATION} "
            f"stored a byte-aligned bit-field ({fresh_value}) as a plain member)"
        )
    return changes
