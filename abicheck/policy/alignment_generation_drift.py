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

"""Demote the pre-74 clang builtin-alignment artifact across extractor
generation 74 (ADR-062 D2 fallback; bug class
``storage.cross_generation_spelling_drift``).

Before generation 74 the clang header backend probed a builtin's natural
alignment with the host's ABI, whatever ``-m32`` or another non-host target
the parse ran with. An ``extern long value`` parsed for i386 therefore
recorded the x86-64 alignment (8 bytes) in a pre-74 snapshot and the i386
alignment (4 bytes) from generation 74. ``VAR_ALIGNMENT_CHANGED`` compares
those two values and reports a change the input did not cause.

Across the generation-74 boundary only, the finding is annotated as
``COMPATIBLE_WITH_RISK`` when the stale operand's recorded alignment is the
LP64 (host) natural alignment of the variable's builtin or pointer type, and
the fresh operand's is the ILP32 (target) natural alignment of that same type.
The stale value is exactly what the pre-74 probe produced, so the pair is the
artifact and no other alignment. A real change (an explicit ``alignas`` or
``aligned`` attribute, a different type, a value that is neither natural
alignment) keeps its verdict, as does any change between operands of the same
generation.

The finding stays in the report with its rule and reason (ADR-067 record
before disposing).
"""

from __future__ import annotations

from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.change_catalog.registry import Verdict
from ..model.declarations import Variable
from ..model.snapshot import AbiSnapshot
from ..model.type_indirection import indirection_shape
from .generation_drift_demotion import GENERATION_DRIFT_REASON, GENERATION_DRIFT_RULE

__all__ = ["ALIGNMENT_FIX_GENERATION", "demote_pre74_builtin_alignment_drift"]

#: The first extractor generation whose clang builtin probe measures the
#: parse's own target instead of the host.
ALIGNMENT_FIX_GENERATION = 74

#: Natural alignment in bytes: (LP64 host, ILP32 target) for the builtin
#: spellings a variable of these types is recorded under.
_NATURAL_BYTES: dict[str, tuple[int, int]] = {
    "long": (8, 4),
    "unsigned long": (8, 4),
    "signed long": (8, 4),
    "long double": (16, 4),
    "double": (8, 4),
    "long long": (8, 4),
    "unsigned long long": (8, 4),
    "size_t": (8, 4),
    "ssize_t": (8, 4),
    "ptrdiff_t": (8, 4),
    "uintptr_t": (8, 4),
    "intptr_t": (8, 4),
}
#: Any pointer type aligns to its pointer size.
_POINTER_BYTES = (8, 4)
_QUALIFIERS = frozenset({"const", "volatile"})
_CLANG_PRODUCER = "clang"


def _natural_bits(type_spelling: str) -> tuple[int, int] | None:
    """``(LP64, ILP32)`` natural alignment in bits, or None when the type is
    not one of the builtins the pre-74 probe measured."""
    words = [w for w in type_spelling.split() if w not in _QUALIFIERS]
    spelling = " ".join(words)
    if spelling in _NATURAL_BYTES:
        lp64, ilp32 = _NATURAL_BYTES[spelling]
        return lp64 * 8, ilp32 * 8
    shape = indirection_shape(type_spelling)
    if shape is None or not shape.closed or shape.levels != ("*",):
        return None
    lp64, ilp32 = _POINTER_BYTES
    return lp64 * 8, ilp32 * 8


def _variables(snapshot: AbiSnapshot) -> dict[str, Variable]:
    return {v.mangled: v for v in snapshot.declarations.variables}


def demote_pre74_builtin_alignment_drift(
    changes: list[Change],
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_generation: int,
    new_generation: int,
) -> list[Change]:
    """Annotate the pre-74 host-ABI builtin alignment as
    ``COMPATIBLE_WITH_RISK`` (see the module docstring)."""
    if old_generation == new_generation:
        return changes
    stale_is_old = old_generation < new_generation
    stale_generation = old_generation if stale_is_old else new_generation
    fresh_generation = new_generation if stale_is_old else old_generation
    if not stale_generation < ALIGNMENT_FIX_GENERATION <= fresh_generation:
        return changes
    stale, fresh = (old, new) if stale_is_old else (new, old)
    # Only the clang builtin probe measured the host ABI. A CastXML, debug-info
    # or unknown-producer snapshot's alignment is not that probe's output, so
    # it keeps its verdict.
    if stale.ast_producer != _CLANG_PRODUCER or fresh.ast_producer != _CLANG_PRODUCER:
        return changes
    stale_vars, fresh_vars = _variables(stale), _variables(fresh)
    for change in changes:
        if (
            change.kind is not ChangeKind.VAR_ALIGNMENT_CHANGED
            or change.effective_verdict is not None
        ):
            continue
        stale_var = stale_vars.get(change.symbol)
        fresh_var = fresh_vars.get(change.symbol)
        if stale_var is None or fresh_var is None:
            continue
        if stale_var.type != fresh_var.type:
            continue
        natural = _natural_bits(stale_var.type)
        if natural is None:
            continue
        lp64_bits, ilp32_bits = natural
        if (stale_var.alignment_bits, fresh_var.alignment_bits) != (
            lp64_bits,
            ilp32_bits,
        ):
            continue
        change.effective_verdict = Verdict.COMPATIBLE_WITH_RISK
        change.modulation_rule = GENERATION_DRIFT_RULE
        change.modulation_reason = (
            f"{GENERATION_DRIFT_REASON} (old operand extractor generation "
            f"{old_generation or 'unstated'}, new {new_generation or 'unstated'}; "
            f"the clang builtin probe before generation {ALIGNMENT_FIX_GENERATION} "
            f"measured the host ABI ({lp64_bits} bits for {stale_var.type!r}) "
            f"instead of the parse's target ({ilp32_bits} bits))"
        )
    return changes
