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

"""The AST finding that kept a DWARF duplicate, chosen deterministically.

The cross-kind dedup (:mod:`abicheck.compare.finding_dedup`) drops a DWARF
finding when an AST finding agrees with it. This module records, per matching
key, which AST finding stands for each transition, so the drop can name the
finding that kept it (ADR-093 D5). The choice depends only on the set of
findings, never on their order: ties resolve to the least (symbol, field).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..model.change import Change

#: The DWARF finding's transition as the matcher compares it (``None`` means
#: the kind carries no independent transition).
Transition = tuple[object, object] | None


def identity(c: Change) -> tuple[str, str]:
    """The order used to pick one of several AST findings that all agree."""
    return (c.symbol, c.field_name or "")


class RepresentativeIndex:
    """Per matching key, the AST finding that stands for each transition."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[object, ...], dict[Transition, Change]] = {}

    def add(self, key: tuple[object, ...], transition: Transition, c: Change) -> None:
        slots = self._by_key.setdefault(key, {})
        current = slots.get(transition)
        if current is None or identity(c) < identity(current):
            slots[transition] = c

    def pick(self, key: tuple[object, ...], dwarf_transition: Transition) -> Change:
        """The AST finding that kept a DWARF finding with *dwarf_transition*.

        Called only after the matcher succeeded, so the key has findings. With a
        concrete transition, the finding that agrees on it is the one; otherwise
        any finding of the key will do, and the least one is taken.
        """
        slots = self._by_key[key]
        if dwarf_transition is not None and dwarf_transition in slots:
            return slots[dwarf_transition]
        return min(slots.values(), key=identity)
