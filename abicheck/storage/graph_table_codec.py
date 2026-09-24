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

"""Compact, interned, columnar encoding of a stored graph (schema v49;
evidence-entity-model Phase 5b).

Measured on real oneDAL (``libonedal_core``, 49,872 nodes / 102,388 edges),
the ``SourceGraphSummary.to_dict()`` form was a 79 MB section that
compressed 28:1: it is dominated by repeated ids, kinds, producer names and
attribute keys, plus fields the loader always recomputes. This encoding
stores each distinct string once and refers to it by index:

```json
{
  "encoding": "graph-table/1",
  "strings": ["decl://f", "source_decl", ...],
  "attrs":   [[<key>, <value>, ...], ...],
  "facts":   [[<producer>, <confidence>, <attrs>], ...],
  "nodes": {"id": [...], "kind": [...], "label": [...], "facts": [...]},
  "edges": {"src": [...], "dst": [...], "kind": [...], "facts": [...]},
  "schema_version": 2, "coverage": {...}, ...
}
```

* Every column holds one entry per node/edge, in graph order, so row *i* of
  every column describes the same entity.
* ``strings`` is the one string table. Every ``id``/``src``/``dst``/
  ``kind``/``label`` entry, and every ``producer``/``confidence`` in
  ``facts``, is an index into it.
* ``attrs`` is the table of distinct attribute dicts, each a flat
  ``[key, value, key, value, ...]`` list with keys sorted. A key is a string
  index. A value that is a string is a string index; any other JSON value is
  wrapped in a one-element list (``[3]``, ``[true]``, ``[null]``,
  ``[[...]]``), so the two cases never collide.
* ``facts`` is the table of distinct ``(producer, confidence, attrs)``
  triples. An entity's ``facts`` entry is one fact index, or a non-empty
  list of them when it carries several.
* The small graph-level fields are stored as ``to_dict()`` stores them.

**Observed evidence only (Phase 5c).** Nothing the loader rederives is
written: not ``indexes``/``graph_id``/``occurrences`` (recomputed by
``finalize``), not an entity's ``resolved``/``conflicts``/``attrs``/
``provenance``/``confidence`` (all rederived from its facts by
``ensure_facts_and_resolve``). Nor is a fact whose producer is recomputable
from the snapshot's own records
(``model.graph_evidence_class.RECOMPUTABLE_FACT_PRODUCERS`` -- the
public-surface builder's projections); a node or edge left with no other
fact is not written at all. A reader needing those projections builds them
on demand from the records, as ``policy.public_surface_closure`` already
does.

Decoding re-inflates the legacy per-entity dicts and hands them to
``SourceGraphSummary.from_dict``, so every load-time migration ``from_dict``
applies (id normalization, fact synthesis, coalescing) still applies, and
for a graph without recomputable facts the result equals
``from_dict(graph.to_dict())`` -- the property
``tests/test_graph_table_codec.py`` checks on generated graphs, with
``from_dict`` as the oracle.

A document without the ``encoding`` key is the pre-v49 ``to_dict()`` form;
:func:`is_graph_table` tells the two apart and the caller decodes that one
with ``from_dict`` exactly as before.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary

__all__ = [
    "GRAPH_TABLE_ENCODING",
    "decode_graph_table",
    "encode_graph_table",
    "graph_table_to_legacy_dict",
    "is_graph_table",
]

GRAPH_TABLE_ENCODING = "graph-table/1"

#: The persisted columns. An entity's ``attrs``/``provenance``/``confidence``
#: are not among them: ``ensure_facts_and_resolve`` rederives all three from
#: ``facts`` on every load, so a stored copy was never read (Phase 5c).
_NODE_COLUMNS = ("id", "kind", "label", "facts")
_EDGE_COLUMNS = ("src", "dst", "kind", "facts")
#: Graph-level fields stored verbatim, as ``to_dict()`` writes them.
_SCALAR_FIELDS = (
    "schema_version",
    "coverage",
    "external_graph_refs",
    "extractor_passes",
    "narrowed_passes",
    "narrowed_scope",
    "degraded_passes",
    "entity_resolver",
)
#: Exact JSON scalar types whose values need no deep copy on decode.
_SCALARS = frozenset({str, int, float, bool, type(None)})


def is_graph_table(payload: Mapping[str, Any]) -> bool:
    """Whether *payload* is this encoding rather than the pre-v49 form."""
    return payload.get("encoding") == GRAPH_TABLE_ENCODING


class _Interner:
    def __init__(self) -> None:
        self.strings: list[str] = []
        self._strings: dict[str, int] = {}
        self.attrs: list[list[Any]] = []
        self._attrs: dict[str, int] = {}
        self.facts: list[list[int]] = []
        self._facts: dict[tuple[int, int, int], int] = {}

    def string(self, value: str) -> int:
        index = self._strings.get(value)
        if index is None:
            index = self._strings[value] = len(self.strings)
            self.strings.append(value)
        return index

    def attr_dict(self, attrs: Mapping[str, Any]) -> int:
        flat: list[Any] = []
        # Sorted: the D8 section wrapper's canonical form sorted these keys
        # in the pre-v49 form too, and derived state (conflict order) follows
        # key order, so the table must present them the same way.
        for key, value in sorted(attrs.items()):
            flat.append(self.string(key))
            flat.append(self.string(value) if type(value) is str else [value])
        # A value's own repr is not a key: [1] and [True] must stay distinct
        # entries, and so must [1] and [1.0], which JSON writes differently.
        fingerprint = repr(
            [(type(v), v) if type(v) is not list else _typed(v) for v in flat]
        )
        index = self._attrs.get(fingerprint)
        if index is None:
            index = self._attrs[fingerprint] = len(self.attrs)
            self.attrs.append(flat)
        return index

    def fact(self, producer: str, confidence: str, attrs: Mapping[str, Any]) -> int:
        key = (self.string(producer), self.string(confidence), self.attr_dict(attrs))
        index = self._facts.get(key)
        if index is None:
            index = self._facts[key] = len(self.facts)
            self.facts.append(list(key))
        return index

    def fact_list(self, facts: list[Any]) -> int | list[int]:
        indexes = [self.fact(f.producer, f.confidence, f.attrs) for f in facts]
        return indexes[0] if len(indexes) == 1 else indexes


def _typed(value: Any) -> Any:
    """A structure whose ``repr`` distinguishes values JSON distinguishes
    (``True`` vs ``1``, ``1`` vs ``1.0``) but Python equality does not."""
    if type(value) is list:
        return ("list", [_typed(v) for v in value])
    if type(value) is dict:
        return ("dict", [(k, _typed(v)) for k, v in value.items()])
    return (type(value).__name__, value)


#: The ``coverage`` entries ``SourceGraphSummary.finalize`` recomputes from
#: the loaded nodes/edges and passes, overwriting whatever was stored: a
#: top-level key maps to ``None``, a per-kind section to the sub-keys it
#: owns. Only what finalize does *not* own (a forward-compatible unknown
#: key) is persisted. ``tests/test_graph_table_codec.py`` checks that
#: finalize over the stripped form reproduces the full one.
_FINALIZE_OWNED_COVERAGE: dict[str, tuple[str, ...] | None] = {
    "targets": None,
    "compile_units": None,
    "source_decls": None,
    "binary_symbol_mappings": None,
    "node_kinds": None,
    "edge_kinds": None,
    "include_edges": ("collected", "count"),
    "call_edges": ("collected", "count"),
    "type_edges": ("collected", "count"),
    "reference_edges": ("collected", "count"),
}


def _observed_coverage(coverage: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in coverage.items():
        owned = _FINALIZE_OWNED_COVERAGE.get(key, ())
        if owned is None:
            continue
        if owned and isinstance(value, Mapping):
            rest = {k: v for k, v in value.items() if k not in owned}
            if rest:
                out[key] = rest
            continue
        out[key] = value
    return out


def _graph_fields(graph: SourceGraphSummary) -> dict[str, Any]:
    """The graph-level fields exactly as ``to_dict()`` writes them, without
    building its node/edge lists or its (never read back) ``indexes``."""
    out: dict[str, Any] = {
        "schema_version": graph.schema_version,
        "coverage": _observed_coverage(graph.coverage),
        "external_graph_refs": [dict(r) for r in graph.external_graph_refs],
        "extractor_passes": dict(graph.extractor_passes),
        "narrowed_passes": dict(graph.narrowed_passes),
        "narrowed_scope": {k: sorted(v) for k, v in graph.narrowed_scope.items()},
        "degraded_passes": dict(graph.degraded_passes),
    }
    resolver = graph.entity_resolver
    if resolver.aliases or resolver.conflicts:
        out["entity_resolver"] = resolver.to_dict()
    return out


def _persisted_facts(entity: Any) -> list[Any]:
    """*entity*'s facts minus those a recomputable producer wrote
    (``model.graph_evidence_class.RECOMPUTABLE_FACT_PRODUCERS``). An entity
    constructed without facts is given the one fact ``ensure_facts_and_
    resolve`` would synthesize for it on load, so nothing it carries is
    lost by not storing its ``attrs``/``provenance``/``confidence``."""
    from ..model.graph_evidence_class import RECOMPUTABLE_FACT_PRODUCERS
    from ..model.graph_facts import GraphFact

    facts = entity.facts or [
        GraphFact(
            producer=entity.provenance,
            confidence=entity.confidence,
            attrs=dict(entity.attrs),
        )
    ]
    return [f for f in facts if f.producer not in RECOMPUTABLE_FACT_PRODUCERS]


def encode_graph_table(graph: SourceGraphSummary) -> dict[str, Any]:
    """*graph* in this module's encoding (see the module docstring).

    Persists observed evidence only (evidence-entity-model Phase 5c): a fact
    from a recomputable producer -- a projection of the snapshot's own
    records -- is dropped, and so is a node or edge left with no other fact.
    Reads the node/edge objects directly -- never ``graph.to_dict()``, whose
    per-entity dicts are the transient cost this encoding exists to avoid.
    """
    table = _Interner()
    nodes: dict[str, list[Any]] = {name: [] for name in _NODE_COLUMNS}
    for node in graph.nodes:
        facts = _persisted_facts(node)
        if not facts:
            continue
        nodes["id"].append(table.string(node.id))
        nodes["kind"].append(table.string(node.kind))
        nodes["label"].append(table.string(node.label))
        nodes["facts"].append(table.fact_list(facts))
    edges: dict[str, list[Any]] = {name: [] for name in _EDGE_COLUMNS}
    for edge in graph.edges:
        facts = _persisted_facts(edge)
        if not facts:
            continue
        edges["src"].append(table.string(edge.src))
        edges["dst"].append(table.string(edge.dst))
        edges["kind"].append(table.string(edge.kind))
        edges["facts"].append(table.fact_list(facts))
    out: dict[str, Any] = {"encoding": GRAPH_TABLE_ENCODING, **_graph_fields(graph)}
    out["strings"] = table.strings
    out["attrs"] = table.attrs
    out["facts"] = table.facts
    out["nodes"] = nodes
    out["edges"] = edges
    return out


class _Reader:
    """Index resolution with the bounds and type checks a corrupt or
    hand-edited document needs: every failure is a ``ValueError`` naming the
    column, never an ``IndexError`` or a silently wrong value."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.strings = _list(payload, "strings")
        self.attr_rows = _list(payload, "attrs")
        self.fact_rows = _list(payload, "facts")
        for position, value in enumerate(self.strings):
            if type(value) is not str:
                raise ValueError(f"graph table: strings[{position}] is not a string")

    def string(self, index: Any, where: str) -> str:
        return _at(self.strings, index, where)  # type: ignore[no-any-return]

    def attrs(self, index: Any, where: str) -> dict[str, Any]:
        flat = _at(self.attr_rows, index, where)
        if type(flat) is not list or len(flat) % 2:
            raise ValueError(f"graph table: {where} names a malformed attrs row")
        out: dict[str, Any] = {}
        for position in range(0, len(flat), 2):
            key = self.string(flat[position], f"{where} key")
            value = flat[position + 1]
            if type(value) is list and len(value) == 1:
                literal = value[0]
                out[key] = (
                    literal if type(literal) in _SCALARS else copy.deepcopy(literal)
                )
            else:
                out[key] = self.string(value, f"{where} value")
        return out

    def fact(self, index: Any, where: str) -> dict[str, Any]:
        row = _at(self.fact_rows, index, where)
        if type(row) is not list or len(row) != 3:
            raise ValueError(f"graph table: {where} names a malformed fact row")
        return {
            "producer": self.string(row[0], f"{where} producer"),
            "confidence": self.string(row[1], f"{where} confidence"),
            "attrs": self.attrs(row[2], f"{where} attrs"),
        }

    def facts(self, entry: Any, where: str) -> list[dict[str, Any]]:
        if type(entry) is list:
            if not entry:
                # The encoder always stores at least one fact; with none,
                # the loader would synthesize one from empty defaults.
                raise ValueError(f"graph table: {where} entry has no facts")
            return [self.fact(i, where) for i in entry]
        return [self.fact(entry, where)]


def _list(payload: Mapping[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if type(value) is not list:
        raise ValueError(f"graph table: {key!r} must be a list")
    return value


def _at(rows: list[Any], index: Any, where: str) -> Any:
    if type(index) is not int or not 0 <= index < len(rows):
        raise ValueError(f"graph table: {where} index {index!r} is out of range")
    return rows[index]


def _columns(
    payload: Mapping[str, Any], key: str, names: tuple[str, ...]
) -> tuple[list[Any], ...]:
    block = payload.get(key)
    if not isinstance(block, Mapping):
        raise ValueError(f"graph table: {key!r} must be an object")
    columns = tuple(_list(block, name) for name in names)
    lengths = {len(c) for c in columns}
    if len(lengths) > 1:
        raise ValueError(f"graph table: {key!r} columns have different lengths")
    return columns


def graph_table_to_legacy_dict(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Re-inflate *payload* into the pre-v49 ``to_dict()`` shape, minus the
    fields ``from_dict`` never reads. Raises ``ValueError`` for any
    structural corruption."""
    if not is_graph_table(payload):
        raise ValueError(
            f"graph table: unsupported encoding {payload.get('encoding')!r}"
        )
    read = _Reader(payload)
    node_cols = _columns(payload, "nodes", _NODE_COLUMNS)
    edge_cols = _columns(payload, "edges", _EDGE_COLUMNS)
    nodes = [
        {
            "id": read.string(i, "nodes.id"),
            "kind": read.string(k, "nodes.kind"),
            "label": read.string(lb, "nodes.label"),
            "facts": read.facts(f, "nodes.facts"),
        }
        for i, k, lb, f in zip(*node_cols)
    ]
    edges = [
        {
            "src": read.string(src, "edges.src"),
            "dst": read.string(d, "edges.dst"),
            "edge": read.string(k, "edges.kind"),
            "facts": read.facts(f, "edges.facts"),
        }
        for src, d, k, f in zip(*edge_cols)
    ]
    out: dict[str, Any] = {
        name: payload[name] for name in _SCALAR_FIELDS if name in payload
    }
    out["nodes"] = nodes
    out["edges"] = edges
    return out


def decode_graph_table(payload: Mapping[str, Any]) -> SourceGraphSummary:
    """The graph *payload* encodes (see :func:`graph_table_to_legacy_dict`)."""
    from ..model.graph_identity import identity_normalization_memo
    from ..model.source_graph import SourceGraphSummary

    with identity_normalization_memo():
        return SourceGraphSummary.from_dict(graph_table_to_legacy_dict(payload))
