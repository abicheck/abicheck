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

"""Wire form of ``AbiSnapshot.extraction_scope`` and each declaration's
``ownership_fact`` (snapshot schema v52, ADR-075 D1/D2).

The per-entity decision is not written per entity: a real snapshot has tens
of thousands of declarations and a handful of distinct
``(owner, contract, rule_id)`` triples, so the block holds one interned
``decisions`` table and, per declaration list, one integer per declaration
(``-1`` for an unclassified one). The generic dataclass walk still visits
``ownership_fact``; :func:`encode_extraction_scope` removes it from every
entity dict so the list encoding is byte-identical to a pre-v52 snapshot's.

Decoding attaches decisions by position. A list whose recorded length
differs from the snapshot's own list is left unattached -- every
declaration in it stays unclassified -- rather than risk giving one
declaration another's owner. Nothing here guesses: a snapshot without the
block loads with ``extraction_scope=None`` and no decisions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ..model.extraction_scope import EntityOwnership, ExtractionScope, ownership_of
from ..model.fact import Fact

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot

__all__ = ["decode_extraction_scope", "encode_extraction_scope"]

_LISTS = ("functions", "variables", "types", "enums")
_FIELD = "ownership_fact"
_KEY = "extraction_scope"


def encode_extraction_scope(d: dict[str, Any], snap: AbiSnapshot) -> None:
    """Rewrite *d* (the encoded *snap*) in place: strip per-entity facts,
    write the interned block, or drop the key for an unrecorded snapshot."""
    for name in _LISTS:
        for entry in d.get(name) or ():
            if isinstance(entry, dict):
                entry.pop(_FIELD, None)
    scope = snap.extraction_scope
    if scope is None:
        d.pop(_KEY, None)
        return
    table: dict[tuple[str, str, str], int] = {}
    per_list: dict[str, list[int]] = {}
    for name in _LISTS:
        indices: list[int] = []
        for decl in getattr(snap, name):
            decision = ownership_of(decl)
            if decision is None:
                indices.append(-1)
                continue
            key = (decision.owner, decision.contract, decision.rule_id)
            indices.append(table.setdefault(key, len(table)))
        if any(i >= 0 for i in indices):
            per_list[name] = indices
    block = scope.to_dict()
    block["entity_ownership"] = {
        "decisions": [list(k) for k in table],
        **per_list,
    }
    d[_KEY] = block


def decode_extraction_scope(d: Mapping[str, Any], snap: AbiSnapshot) -> None:
    """Attach the recorded scope and per-entity decisions to *snap*."""
    block = d.get(_KEY)
    if not isinstance(block, Mapping):
        return
    snap.extraction_scope = ExtractionScope.from_dict(block)
    ownership = block.get("entity_ownership")
    if not isinstance(ownership, Mapping):
        return
    decisions: list[Fact[EntityOwnership]] = []
    for raw in ownership.get("decisions") or ():
        # Three strings exactly: a bare string is a Sequence too, and str()
        # would coerce a non-string field into a decision nobody recorded.
        if (
            isinstance(raw, Sequence)
            and not isinstance(raw, (str, bytes))
            and len(raw) == 3
            and all(isinstance(part, str) for part in raw)
        ):
            decisions.append(Fact.present(EntityOwnership(raw[0], raw[1], raw[2])))
        else:
            # Keep positions aligned; an unreadable entry is unclassified.
            decisions.append(Fact.not_collected("malformed ownership decision"))
    for name in _LISTS:
        indices = ownership.get(name)
        decls = getattr(snap, name)
        if not isinstance(indices, Sequence) or len(indices) != len(decls):
            continue
        for decl, index in zip(decls, indices):
            # `bool` is an `int`: `true` must not select decisions[1].
            if (
                isinstance(index, int)
                and not isinstance(index, bool)
                and 0 <= index < len(decisions)
            ):
                decl.ownership_fact = decisions[index]
