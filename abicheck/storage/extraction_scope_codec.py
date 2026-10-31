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

from ..model.availability import FactStatus
from ..model.extraction_scope import EntityOwnership, ExtractionScope, ownership_of
from ..model.fact import Fact

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot

__all__ = ["decode_extraction_scope", "encode_extraction_scope"]

_LISTS = ("functions", "variables", "types", "enums")
_FIELD = "ownership_fact"
_KEY = "extraction_scope"
#: Schema v67: the per-entity decisions of a snapshot with no recorded scope.
#: Written only in that case, never as a scope: ADR-075 reads an unrecorded
#: scope as unrecorded, and a decision without one must not change that.
_UNSCOPED_KEY = "ownership_without_scope"


def encode_extraction_scope(d: dict[str, Any], snap: AbiSnapshot) -> None:
    """Rewrite *d* (the encoded *snap*) in place: strip per-entity facts,
    write the interned block, or -- with no recorded scope -- the decisions
    under :data:`_UNSCOPED_KEY` (and no scope key at all)."""
    for name in _LISTS:
        for entry in d.get(name) or ():
            if isinstance(entry, dict):
                entry.pop(_FIELD, None)
    ownership = _ownership_block(snap)
    scope = snap.extraction_scope
    if scope is None:
        d.pop(_KEY, None)
        if ownership is not None:
            d[_UNSCOPED_KEY] = {"entity_ownership": ownership}
        return
    block = scope.to_dict()
    if ownership is not None:
        block["entity_ownership"] = ownership
    d[_KEY] = block


def _ownership_block(snap: AbiSnapshot) -> dict[str, Any] | None:
    """The interned decision table, per-list indices and non-PRESENT statuses
    of *snap*'s declarations; ``None`` when no declaration carries a fact."""
    table: dict[tuple[str, str, str], int] = {}
    per_list: dict[str, list[int]] = {}
    for name in _LISTS:
        indices: list[int] = []
        for decl in getattr(snap.declarations, name):
            decision = ownership_of(decl)
            if decision is None:
                indices.append(-1)
                continue
            key = (decision.owner, decision.contract, decision.rule_id)
            indices.append(table.setdefault(key, len(table)))
        if any(i >= 0 for i in indices):
            per_list[name] = indices
    statuses = _encode_statuses(snap)
    if not per_list and not statuses:
        return None
    ownership: dict[str, Any] = {
        "decisions": [list(k) for k in table],
        **per_list,
    }
    if statuses:
        ownership["statuses"] = statuses
    return ownership


def _encode_statuses(snap: AbiSnapshot) -> dict[str, list[list[Any]]]:
    """Each declaration whose ``ownership_fact`` is a non-``PRESENT`` ``Fact``:
    ``[position, status, producer, diagnostics]``, per declaration list.

    A ``PRESENT`` decision is the interned triple above and never appears here;
    an unset (``None``) fact is absent, which is what a decoder reads back.
    Persisting the status (schema v67) is what lets a stored ``PARTIAL`` or
    ``FAILED`` decision stay the decision it was, instead of reading as
    unclassified.
    """
    out: dict[str, list[list[Any]]] = {}
    for name in _LISTS:
        rows: list[list[Any]] = []
        for index, decl in enumerate(getattr(snap.declarations, name)):
            fact = getattr(decl, _FIELD, None)
            if not isinstance(fact, Fact) or fact.status is FactStatus.PRESENT:
                continue
            rows.append(
                [index, fact.status.value, fact.producer, list(fact.diagnostics)]
            )
        if rows:
            out[name] = rows
    return out


def decode_extraction_scope(d: Mapping[str, Any], snap: AbiSnapshot) -> None:
    """Attach the recorded scope and per-entity decisions to *snap*."""
    decode_extraction_scope_record(d, snap)
    attach_extraction_scope_ownership(d, snap)


def decode_extraction_scope_record(d: Mapping[str, Any], snap: AbiSnapshot) -> None:
    """Only the recorded :class:`ExtractionScope` (a snapshot field). Reads
    no declaration, so a lazily loaded snapshot decodes it eagerly."""
    block = d.get(_KEY)
    if isinstance(block, Mapping):
        snap.extraction_scope = ExtractionScope.from_dict(block)


def attach_extraction_scope_ownership(d: Mapping[str, Any], snap: AbiSnapshot) -> None:
    """Only the per-entity decisions, attached to *snap*'s declarations --
    the half a lazily loaded snapshot defers with its declaration store."""
    block = d.get(_KEY)
    if isinstance(block, Mapping):
        _attach_ownership(block.get("entity_ownership"), snap)
        return
    unscoped = d.get(_UNSCOPED_KEY)
    if isinstance(unscoped, Mapping):
        _attach_ownership(unscoped.get("entity_ownership"), snap)


def _attach_ownership(ownership: Any, snap: AbiSnapshot) -> None:
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
        decls = getattr(snap.declarations, name)
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
    _decode_statuses(ownership.get("statuses"), snap)


def _decode_statuses(raw: Any, snap: AbiSnapshot) -> None:
    """Attach the recorded non-``PRESENT`` facts; a malformed row is skipped
    (the declaration stays unset, which every reader treats as unknown)."""
    if not isinstance(raw, Mapping):
        return
    for name in _LISTS:
        rows = raw.get(name)
        decls = getattr(snap.declarations, name)
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            continue
        for row in rows:
            if not (
                isinstance(row, Sequence) and not isinstance(row, str) and len(row) == 4
            ):
                continue
            index, status, producer, diagnostics = row
            if not (isinstance(index, int) and not isinstance(index, bool)):
                continue
            if not (0 <= index < len(decls)):
                continue
            try:
                member = FactStatus(status)
            except ValueError:
                continue
            if member is FactStatus.PRESENT or not isinstance(diagnostics, list):
                continue
            decls[index].ownership_fact = Fact(
                status=member,
                diagnostics=tuple(str(x) for x in diagnostics),
                producer=producer if isinstance(producer, str) else None,
            )
