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

"""Anonymous struct/union member diffing, split out of ``diff_symbols.py``.

Purely a file-size mitigation (`abicheck/diff_symbols.py`'s own ADR-063
Phase 2 `entity_id=` wiring pushed it past the architecture-debt ledger's
no-growth baseline for that file) -- no behavior change, and this group was
already fully self-contained (its only dependencies are leaf modules
`diff_helpers.py`/`checker_policy.py`/`checker_types.py`/`model.py`, never a
`diff_symbols.py`-local helper), so it moves out unchanged rather than
requiring a deeper refactor. Leaf module: must not import from
``diff_symbols`` to avoid an import cycle (mirrors
``diff_symbols_variables.py``'s own identical rule) -- ``diff_symbols`` is
this module's sole caller, importing ``_diff_anon_fields`` purely to trigger
this module's own ``@registry.detector`` registration at import time, the
same mechanism every other split-out detector sibling already relies on.
"""

from __future__ import annotations

from typing import Any

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_helpers import build_type_map, lookup_matched_type, make_change
from .model import AbiSnapshot, is_abi_surface_type_name, stdlib_namespaces_excluded
from .model.identity import EntityId


def _is_anon_field(f: Any) -> bool:
    """Return True for compiler-generated anonymous/unnamed fields."""
    return not f.name or f.name.startswith("__anon")


def _check_anon_field_at_offset(
    name: str,
    offset: int,
    f_old: Any,
    new_by_offset: dict[int, Any],
    *,
    entity_id: EntityId | None = None,
) -> Change | None:
    """Compare a single anonymous field (by offset) to what the new type has.

    *entity_id* is the containing record's own identity (ADR-063 Phase 2) —
    a field has no ``EntityId`` of its own, so, like
    ``diff_symbols._check_field_access_changes``, the finding is attributed
    to the record it belongs to.
    """
    f_new = new_by_offset.get(offset)
    if f_new is None:
        return make_change(
            ChangeKind.ANON_FIELD_CHANGED,
            symbol=name,
            description=f"Anonymous field removed at offset {offset} in {name}",
            old_value=f_old.type,
            entity_id=entity_id,
        )
    if f_old.type != f_new.type:
        return make_change(
            ChangeKind.ANON_FIELD_CHANGED,
            symbol=name,
            description=f"Anonymous field type changed at offset {offset} in {name}",
            old_value=f_old.type,
            new_value=f_new.type,
            entity_id=entity_id,
        )
    return None


def _anon_fields_by_offset(fields: list[Any]) -> dict[int, Any]:
    """Index anonymous fields (no name or __anon prefix) by their bit offset."""
    return {
        f.offset_bits: f
        for f in fields
        if _is_anon_field(f) and f.offset_bits is not None
    }


def _check_anon_fields_for_type(name: str, t_old: Any, t_new: Any) -> list[Change]:
    """Compare anonymous fields by offset for a single matched type pair."""
    old_by_offset = _anon_fields_by_offset(t_old.fields)
    new_by_offset = _anon_fields_by_offset(t_new.fields)

    if not old_by_offset and not new_by_offset:
        return []

    entity_id = t_old.entity_id or t_new.entity_id
    changes: list[Change] = []
    for offset, f_old in old_by_offset.items():
        ch = _check_anon_field_at_offset(
            name, offset, f_old, new_by_offset, entity_id=entity_id
        )
        if ch is not None:
            changes.append(ch)
    return changes


@registry.detector("anon_fields")
def _diff_anon_fields(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect changes in anonymous struct/union members."""
    changes: list[Change] = []
    excl = stdlib_namespaces_excluded(old, new)
    old_map = build_type_map(
        t for t in old.types if is_abi_surface_type_name(t.name, exclude_stdlib=excl)
    )
    new_map = build_type_map(
        t for t in new.types if is_abi_surface_type_name(t.name, exclude_stdlib=excl)
    )

    for t_old in old_map.values():
        t_new = lookup_matched_type(old_map, new_map, t_old)
        if t_new is None:
            continue
        # Bare, not the qualified matching key.
        name = t_old.name
        changes.extend(_check_anon_fields_for_type(name, t_old, t_new))

    return changes
