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

"""Per-member type attribution for a release fan-out.

Computes each member's export surfaces once and asks
:func:`abicheck.policy.member_type_attribution.attribute_type` about every
type its findings name. Lives in ``workflows`` because the release fan-out
(a frontend) may not import ``policy`` directly (ADR-061).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from ..policy.member_type_attribution import attribute_type

if TYPE_CHECKING:
    from ..model import AbiSnapshot

__all__ = ["member_type_attribution"]


def member_type_attribution(
    symbols: Iterable[str],
    old_snapshot: AbiSnapshot | None,
    new_snapshot: AbiSnapshot | None,
) -> Mapping[str, str]:
    """``{type name: attribution}`` for each of *symbols* naming a type.

    The export surface is computed only when at least one symbol names a
    type either snapshot carries, so a member with no type findings pays
    nothing.
    """
    snapshots = [s for s in (old_snapshot, new_snapshot) if s is not None]
    known: set[str] = set()
    for snap in snapshots:
        known.update(t.name for t in snap.declarations.types)
        known.update(e.name for e in snap.declarations.enums)
        known.update(snap.declarations.typedefs)
    wanted = sorted({sym for sym in symbols if sym in known})
    if not wanted:
        return {}
    from ..export_surface import compute_export_surface

    surfaces = [compute_export_surface(snap) for snap in snapshots]
    out: dict[str, str] = {}
    for name in wanted:
        verdict = attribute_type(name, surfaces)
        if verdict is not None:
            out[name] = verdict
    return out
