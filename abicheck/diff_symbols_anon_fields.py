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

"""Anonymous struct/union member diffing helpers, split out of
``diff_symbols.py``.

Purely a file-size mitigation (`abicheck/diff_symbols.py`'s own ADR-063
Phase 2 `entity_id=` wiring pushed it past the architecture-debt ledger's
no-growth baseline for that file) -- no behavior change, and this group was
already fully self-contained (its only dependencies are leaf modules
``diff_helpers.py``/``checker_policy.py``/``checker_types.py``/``model.py``,
never a ``diff_symbols.py``-local helper), so it moves out unchanged rather
than requiring a deeper refactor.

**Deliberately does NOT carry the ``@registry.detector("anon_fields")``
registration itself** (Codex review, PR #980) -- ``registry.detector()``
stamps an incrementing counter at *decoration* time, and ``run_all()``
executes detectors in that order, so registration order fixes the order
findings appear in every JSON/text report
(``tests/test_detector_discovery.py::
test_param_qualifier_detectors_keep_their_registration_position`` pins the
exact same invariant for the ``diff_param_qualifiers`` split). Moving the
decorated ``_diff_anon_fields`` function itself here would move its
registration to whenever this leaf module is first imported, not its
original position among ``diff_symbols.py``'s own detectors. So only the
loop-body helpers move; ``_diff_anon_fields`` itself stays defined (and
decorated) in ``diff_symbols.py`` at its original source position, calling
:func:`check_anon_fields_for_type` here -- the identical split shape
``diff_param_qualifiers.py`` already established for ``param_restrict``/
``param_va_list``. Leaf module: must not import from ``diff_symbols`` to
avoid an import cycle (mirrors ``diff_symbols_variables.py``'s own
identical rule).
"""

from __future__ import annotations

from typing import Any

from .checker_policy import ChangeKind
from .checker_types import Change
from .diff_helpers import make_change
from .model.identity import EntityId


def _is_anon_field(f: Any) -> bool:
    """Return True for compiler-generated anonymous/unnamed fields."""
    return not f.name or f.name.startswith("__anon")


def check_anon_field_at_offset(
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


def check_anon_fields_for_type(name: str, t_old: Any, t_new: Any) -> list[Change]:
    """Compare anonymous fields by offset for a single matched type pair."""
    old_by_offset = _anon_fields_by_offset(t_old.fields)
    new_by_offset = _anon_fields_by_offset(t_new.fields)

    if not old_by_offset and not new_by_offset:
        return []

    entity_id = t_old.entity_id or t_new.entity_id
    changes: list[Change] = []
    for offset, f_old in old_by_offset.items():
        ch = check_anon_field_at_offset(
            name, offset, f_old, new_by_offset, entity_id=entity_id
        )
        if ch is not None:
            changes.append(ch)
    return changes
