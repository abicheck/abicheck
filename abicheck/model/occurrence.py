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

"""``OccurrenceId`` and ``canonical_key`` (ADR-063 D3/Phase 3).

Split out of ``model/identity.py`` rather than added there directly: that
module sits at the 800-line production cap (ADR-061's `modules.yaml`), and
this is new surface, not a fix to existing content — AGENTS.md's own
guidance is to extend a split-out sibling rather than grow the parent
toward the cap.

``OccurrenceId`` is :class:`~abicheck.model.identity.EntityId` plus an
optional disambiguator, for the one case a bare ``EntityId`` cannot resolve
on its own: two internal-linkage (``static``) declarations in different
translation units that share scope, leaf name, and signature. Both mangle
to the *identical* Itanium symbol (mangling carries no per-TU component),
so ``EntityId``'s own ``extra`` tuple — mangled name, or the
normalized-signature fallback — cannot tell them apart either; this is not
a new ambiguity, it is the identical one ADR-046/048's L5 source-graph
identity (``buildsource/entity_identity.py``) already resolves today, by
preferring a compiler-provided USR over a bare mangled name. A declaration
with a globally-unique identity at the ``EntityId`` level (the overwhelming
common case) carries an empty disambiguator.

**Deliberately not the same type as ``storage.entity_ids.OccurrenceId``.**
That module's own docstring already states why: it is "the packed-key wire
DTO this module has always been" — a *storage*-layer type wrapping its own,
separate ``EntityId`` DTO, carrying an observation kind/container/producer/
attribute tuple for wire persistence. A domain-layer ``OccurrenceId`` here
cannot be the same type without ``model`` importing from ``storage``,
reversing ADR-061's fixed ``storage -> model`` direction. The two are
bridged the same way the two ``EntityId``s already are (see
``storage.entity_ids``'s own module docstring) — not merged.
"""

from __future__ import annotations

from dataclasses import dataclass

from .identity import EntityId, _packed

__all__ = ["OccurrenceId", "canonical_key"]


@dataclass(frozen=True)
class OccurrenceId:
    """An :class:`EntityId` plus a disambiguator for the rare same-identity,
    distinct-declaration case (see module docstring). ``disambiguator`` is
    populated only when the underlying evidence carries a TU-context signal
    strong enough to distinguish two same-``EntityId`` declarations (e.g. an
    L5 USR) — empty otherwise, which is what makes :func:`canonical_key`
    reduce to exactly ``entity_id.key`` for every declaration this type
    does not apply to."""

    entity_id: EntityId
    disambiguator: str = ""


def canonical_key(value: EntityId | OccurrenceId) -> str:
    """Flat, collision-safe string for *value*, suitable as a graph node id
    (``model.graph.GraphNode.id`` for a ``declaration``/``type`` node,
    ADR-063 Phase 3 D5) or any other consumer needing an equality-consistent
    key. For a bare :class:`EntityId` this is exactly ``.key``. For an
    :class:`OccurrenceId` with no disambiguator this is *also* exactly the
    entity's own ``.key`` — not a packed pair with an empty second part —
    so a node keyed on ``canonical_key(occurrence_id)`` collides correctly
    with one keyed on ``canonical_key(entity_id)`` alone whenever the two
    name the same declaration, which is every declaration outside the rare
    case this type exists for. Only a genuinely populated disambiguator
    produces a different, packed key."""
    if isinstance(value, OccurrenceId):
        if not value.disambiguator:
            return value.entity_id.key
        return _packed(value.entity_id.key, value.disambiguator)
    return value.key
