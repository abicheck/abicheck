# Copyright 2026 Nikolay Petrov
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

"""Whether a type-entity finding's ``symbol`` names a type the surface knows.

A leaf beside :mod:`abicheck.surface` (which imports it), so the surface
classifier can route such a finding to type reachability without an import
cycle through :mod:`abicheck.policy.finding_type_candidates`.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..model.change import Change
from ..model.change_catalog.assembled import REGISTRY
from ..model.execution_cache import memoized

__all__ = ["names_a_known_type"]


def names_a_known_type(change: Change, sym: str, all_types: Iterable[str]) -> bool:
    """Whether a type-entity *change*'s ``symbol`` is a type on either side.

    ``TYPE_LEVEL_KIND_NAMES`` lists kinds known to carry a type name; this
    closes the rest of the catalog's type/enum kinds without guessing what
    their ``symbol`` holds: a finding is routed to type reachability only
    when its symbol really is a known type name. A class's inlined,
    unexported constructor shares that bare name, so the symbol-level path
    would otherwise demote a reachable type's finding as ``not-exported``.
    """
    if not sym or sym not in all_types:
        return False
    if change.entity_discriminator is not None:
        return str(change.entity_discriminator) in ("type", "enum")
    return _type_level_kind(change.kind.value)


@memoized
def _type_level_kind(kind_value: str) -> bool:
    """Whether the catalog files *kind_value* under a type or enum entity."""
    return str(REGISTRY.entity_for(kind_value)) in ("type", "enum")
