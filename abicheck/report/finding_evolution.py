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

"""``Change.evolution`` (ADR-068 D3 / plan §5 P2), projected into the report.

This package's own compute/render split (``abicheck/report/AGENTS.md``): the
``compute_*`` half here reads one already-resolved plain value off a
:class:`~abicheck.checker_types.Change` and decides nothing; the ``render_*``
half only writes it into an existing JSON-shaped mapping (a change's own
report entry) when present, mirroring every other optional per-change
annotation ``reporter._change_annotation_fields`` already carries (e.g.
``correlated_change_kind``, ``symbol_binding``).

A finding not produced by a check migrated onto the ``FindingEvolution``
model (every existing detector, unchanged) carries ``evolution=None`` — the
key is omitted from its JSON entry entirely, not emitted as ``null``, the
same "absent means not applicable" convention every sibling optional field
in :mod:`abicheck.reporter` already follows.
"""

from __future__ import annotations

from typing import Any


def compute_change_evolution(change: Any) -> str | None:
    """The wire value (``FindingEvolution.value``) of *change*'s evolution.

    ``None`` when the producing check has not opted into the
    ``FindingEvolution`` model — every finding kind except the checks named
    in ``workflows.crosscheck_evolution.MIGRATED_CROSSCHECKS`` today.
    Duck-typed via ``getattr`` (not a ``Change`` type annotation), matching
    every sibling reader in ``reporter._change_annotation_fields`` — some
    callers there pass a bare test double with no ``evolution`` attribute
    at all, not just a real :class:`~abicheck.checker_types.Change`.
    """
    evolution = getattr(change, "evolution", None)
    return evolution.value if evolution is not None else None


def render_change_evolution(entry: dict[str, object], value: str | None) -> None:
    """Write *value* into *entry* (a change's JSON projection) when present.

    Mutates *entry* in place, the same shape every sibling optional-field
    renderer in ``reporter._change_annotation_fields`` uses — a no-op when
    *value* is ``None``, so an unmigrated finding's JSON entry carries no
    ``evolution`` key at all.
    """
    if value is not None:
        entry["evolution"] = value


__all__ = ["compute_change_evolution", "render_change_evolution"]
