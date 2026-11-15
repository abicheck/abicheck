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

"""Enums the new side declares and the old side does not.

A record the new side adds is ``TYPE_ADDED`` (``diff_types._diff_types``); a
whole enum the old side had and the new side lacks is ``TYPE_REMOVED``
(``diff_types._diff_enums``). An enum, though, lives in ``snap.enums``, not
``snap.types``, so the record loop never saw an added one and the addition
vanished from the report. This module states the missing direction, with
the same namespace-aware matching the enum detector uses.
"""

from __future__ import annotations

from ..diff_helpers import lookup_matched_type, make_change
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.entities import EnumType
from .type_map_pair import TypeMap

__all__ = ["added_enum_changes"]


def added_enum_changes(
    old_map: TypeMap[EnumType], new_map: TypeMap[EnumType]
) -> list[Change]:
    """``TYPE_ADDED`` for each enum of *new_map* with no counterpart in *old_map*."""
    return [
        make_change(
            ChangeKind.TYPE_ADDED,
            symbol=e.name,
            name=e.name,
            entity_id=e.entity_id,
        )
        for e in new_map.values()
        if lookup_matched_type(new_map, old_map, e) is None
    ]
