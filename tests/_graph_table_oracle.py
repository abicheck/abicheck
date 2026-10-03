"""Independent re-inflation of a columnar graph table into the pre-v49
``SourceGraphSummary.to_dict()`` shape -- the oracle
``storage.graph_table_codec._decode_entities`` (the production decoder, which
builds entities directly) is checked against.

It was ``storage.graph_table_codec.graph_table_to_legacy_dict`` until
production stopped calling it (dead-code plan, Stage D). It reuses the
codec's column readers, which validate structure, and differs from the
production path only in building each entity as a plain dict first.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from abicheck.storage.graph_table_codec import (
    _EDGE_COLUMNS,
    _NODE_COLUMNS,
    _SCALAR_FIELDS,
    _columns,
    _Reader,
    is_graph_table,
)


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
