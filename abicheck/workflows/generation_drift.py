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

"""Cross-generation operand handling for one comparison (ADR-062 D2).

Joins the storage-owned generation stamp
(:func:`~abicheck.storage.generation_migrations.extractor_generation_of`)
with the policy-owned demotion
(:func:`~abicheck.policy.generation_drift_demotion.demote_generation_spelling_drift`),
and owns the opt-in ``--require-same-generation`` refusal.
"""

from __future__ import annotations

from ..model.change import Change
from ..model.snapshot import AbiSnapshot
from ..policy.generation_drift_demotion import demote_generation_spelling_drift
from ..storage.generation_migrations import (
    extractor_generation_of,
    pending_extent_shift,
)

__all__ = [
    "demote_cross_generation_drift",
    "generation_mismatch_message",
    "operand_extractor_generations",
]


def operand_extractor_generations(
    old: AbiSnapshot, new: AbiSnapshot
) -> tuple[int, int]:
    """``(old, new)`` extractor generations; a live dump reads as current and
    an unstated stamp as 0."""
    return (
        extractor_generation_of(old.fact_generations),
        extractor_generation_of(new.fact_generations),
    )


def demote_cross_generation_drift(
    changes: list[Change], old: AbiSnapshot, new: AbiSnapshot
) -> list[Change]:
    # Identical stamps (the common case: two live dumps, both ``None``) are
    # one extractor generation by construction -- skip resolving them, which
    # ran once per compared pair (per package member) for a known no-op.
    if old.fact_generations == new.fact_generations:
        return changes
    old_generation, new_generation = operand_extractor_generations(old, new)
    return demote_generation_spelling_drift(
        changes,
        old,
        new,
        old_generation,
        new_generation,
        extent_shift=pending_extent_shift(old_generation, new_generation),
    )


def generation_mismatch_message(old: AbiSnapshot, new: AbiSnapshot) -> str | None:
    """The ``--require-same-generation`` usage error, or ``None`` when both
    operands were extracted by the same extractor generation."""
    old_generation, new_generation = operand_extractor_generations(old, new)
    if old_generation == new_generation:
        return None

    def _label(generation: int) -> str:
        return str(generation) if generation else "unstated (pre-stamp)"

    return (
        "--require-same-generation: the operands were extracted by different "
        f"extractor generations (old {_label(old_generation)}, new "
        f"{_label(new_generation)}); re-dump baseline with this abicheck "
        "build before comparing"
    )
