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
attribute keys, plus fields the decoder never reads (``indexes``,
``resolved``, ``conflicts``, ``occurrences`` -- all recomputed on load). This
encoding stores each distinct string once and refers to it by index:

```json
{
  "encoding": "graph-table/1",
  "strings": ["decl://f", "source_decl", ...],
  "attrs":   [[<key>, <value>, ...], ...],
  "facts":   [[<producer>, <confidence>, <attrs>], ...],
  "nodes": {"id": [...], "kind": [...], "label": [...], "attrs": [...],
            "provenance": [...], "confidence": [...], "facts": [...]},
  "edges": {"src": [...], "dst": [...], "kind": [...], "attrs": [...],
            "provenance": [...], "confidence": [...], "facts": [...]},
  "schema_version": 2, "graph_id": "...", "coverage": {...}, ...
}
```

* Every column holds one entry per node/edge, in graph order, so row *i* of
  every column describes the same entity.
* ``strings`` is the one string table. Every ``id``/``src``/``dst``/
  ``kind``/``label``/``provenance``/``confidence`` entry, and every
  ``producer``/``confidence`` in ``facts``, is an index into it.
* ``attrs`` is the table of distinct attribute dicts, each a flat
  ``[key, value, key, value, ...]`` list. A key is a string index. A value
  that is a string is a string index; any other JSON value is wrapped in a
  one-element list (``[3]``, ``[true]``, ``[null]``, ``[[...]]``), so the two
  cases never collide.
* ``facts`` is the table of distinct ``(producer, confidence, attrs)``
  triples. An entity's ``facts`` entry is one fact index, or a list of them
  when it carries several (or none).
* The small graph-level fields are stored as ``to_dict()`` stores them.

What is stored is exactly what ``SourceGraphSummary.from_dict`` reads, so
:func:`decode_graph_table` returns the graph ``from_dict(graph.to_dict())``
returns -- the property ``tests/test_graph_table_codec.py`` checks on
generated graphs, with ``from_dict`` as the oracle. It is also how the
decoder is built: it re-inflates the legacy per-entity dicts and hands them
to ``from_dict``, so every load-time migration ``from_dict`` applies (id
normalization, fact synthesis, coalescing) still applies.

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

_NODE_COLUMNS = ("id", "kind", "label", "attrs", "provenance", "confidence", "facts")
_EDGE_COLUMNS = ("src", "dst", "kind", "attrs", "provenance", "confidence", "facts")
#: Graph-level fields stored verbatim, as ``to_dict()`` writes them.
_SCALAR_FIELDS = (
    "schema_version",
    "graph_id",
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


def _graph_fields(graph: SourceGraphSummary) -> dict[str, Any]:
    """The graph-level fields exactly as ``to_dict()`` writes them, without
    building its node/edge lists or its (never read back) ``indexes``."""
    out: dict[str, Any] = {
        "schema_version": graph.schema_version,
        "graph_id": graph.graph_id or graph.compute_graph_id(),
        "coverage": dict(graph.coverage),
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


def encode_graph_table(graph: SourceGraphSummary) -> dict[str, Any]:
    """*graph* in this module's encoding (see the module docstring).

    Reads the node/edge objects directly -- never ``graph.to_dict()``, whose
    per-entity dicts are the transient cost this encoding exists to avoid.
    """
    table = _Interner()
    nodes: dict[str, list[Any]] = {name: [] for name in _NODE_COLUMNS}
    for node in graph.nodes:
        nodes["id"].append(table.string(node.id))
        nodes["kind"].append(table.string(node.kind))
        nodes["label"].append(table.string(node.label))
        nodes["attrs"].append(table.attr_dict(node.attrs))
        nodes["provenance"].append(table.string(node.provenance))
        nodes["confidence"].append(table.string(node.confidence))
        nodes["facts"].append(table.fact_list(node.facts))
    edges: dict[str, list[Any]] = {name: [] for name in _EDGE_COLUMNS}
    for edge in graph.edges:
        edges["src"].append(table.string(edge.src))
        edges["dst"].append(table.string(edge.dst))
        edges["kind"].append(table.string(edge.kind))
        edges["attrs"].append(table.attr_dict(edge.attrs))
        edges["provenance"].append(table.string(edge.provenance))
        edges["confidence"].append(table.string(edge.confidence))
        edges["facts"].append(table.fact_list(edge.facts))
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
            "attrs": read.attrs(a, "nodes.attrs"),
            "provenance": read.string(p, "nodes.provenance"),
            "confidence": read.string(c, "nodes.confidence"),
            "facts": read.facts(f, "nodes.facts"),
        }
        for i, k, lb, a, p, c, f in zip(*node_cols)
    ]
    edges = [
        {
            "src": read.string(s, "edges.src"),
            "dst": read.string(d, "edges.dst"),
            "edge": read.string(k, "edges.kind"),
            "attrs": read.attrs(a, "edges.attrs"),
            "provenance": read.string(p, "edges.provenance"),
            "confidence": read.string(c, "edges.confidence"),
            "facts": read.facts(f, "edges.facts"),
        }
        for s, d, k, a, p, c, f in zip(*edge_cols)
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
