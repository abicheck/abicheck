# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Closure-marker renumbering of a snapshot's L5/surface graph.

`closure_identity.renumber_anonymous_closure_identities` rewrites every
closure marker's ``:<line>:<col>`` discriminator to a stable ``#N`` ordinal
across the flat identity fields. The graph (``AbiSnapshot.surface_graph``,
aliased by ``build_source.source_graph``) spells the same closures in its
node ids, labels, edge endpoints and attributes, so without this module a
closure read as ``Foo<(lambda:x.h#1)>`` in the flat snapshot and
``Foo<(lambda:x.h:20:5)>`` in its graph node.

**Numbering.** A closure the flat fields name gets the flat ordinal. A
closure only the graph names (an internal helper visible only in L5) is
numbered after every flat ordinal of its ``(marker, header)`` group, in
source position order -- the rule hybrid conflict-only markers already use
(`_extend_ordinals_with_conflict_only_markers`) -- so adding the graph never
moves a flat ordinal. A raw ``(lambda at <path>:...)`` spelling is stripped
first, as on the flat load path.

**Laziness (ADR-062 D18).** A stored graph is decoded on first access
(`model.lazy_graph.PendingGraph`). Its pending decoder is wrapped so the
renumbering runs when it decodes; nothing is decoded at load. The wrapped
decode and an eager one are the same function -- decode, then rewrite the
graph's ``to_dict`` form and decode that -- so the two cannot differ.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..model.lazy_graph import PendingGraph, set_pending_graph

__all__ = ["renumber_graph", "renumber_snapshot_graphs"]

#: Given every string of a graph, the rewrite to apply to each, or ``None``
#: when nothing changes. Supplied by ``closure_identity`` (which owns the
#: ordinal rules), so this module imports nothing from it.
RewriteFactory = Callable[[list[str]], "Callable[[str], str] | None"]
#: Cheap substring test for any closure/anonymous marker spelling.
_MARKER_HINTS = ("(lambda", "(unnamed", "(anonymous")


def _strings(value: Any, out: list[str]) -> None:
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                out.append(key)
            _strings(item, out)
    elif isinstance(value, list | tuple):
        for item in value:
            _strings(item, out)


def _rewrite(value: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        return {
            (fn(k) if isinstance(k, str) else k): _rewrite(v, fn)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [_rewrite(item, fn) for item in value]
    return value


def _hinted(text: str) -> bool:
    return any(h in text for h in _MARKER_HINTS)


def _may_hold(graph: Any) -> bool:
    """Whether any node or edge of *graph* can spell a marker -- a cheap
    pre-check, so the common marker-free graph is never re-encoded. An
    entity's ``attrs`` mirror its merged facts', so facts need no scan."""
    for node in getattr(graph, "nodes", ()):
        if _hinted(node.id) or _hinted(node.label) or _hinted(str(node.attrs)):
            return True
    for edge in getattr(graph, "edges", ()):
        if _hinted(edge.src) or _hinted(edge.dst) or _hinted(str(edge.attrs)):
            return True
    return _hinted(str(getattr(graph, "identity_aliases", "")))


def renumber_graph(graph: Any, make_rewrite: RewriteFactory) -> Any:
    """*graph* (a decoded `SourceGraphSummary`) with its closure markers
    rewritten by *make_rewrite*; *graph* itself when it names no marker.
    Never mutates *graph*."""
    if graph is None or not hasattr(graph, "to_dict") or not _may_hold(graph):
        return graph
    document = graph.to_dict()
    strings: list[str] = []
    _strings(document, strings)
    if not any(_hinted(s) for s in strings):
        return graph
    rewrite = make_rewrite(strings)
    if rewrite is None:
        return graph
    rewritten = _rewrite(document, rewrite)
    if rewritten == document:
        return graph
    # Content-derived: recomputed from the renumbered nodes and edges.
    rewritten.pop("graph_id", None)
    rewritten.pop("indexes", None)
    return type(graph).from_dict(rewritten)


def _wrap(cell: PendingGraph, make_rewrite: RewriteFactory) -> PendingGraph:
    def decode() -> Any:
        return renumber_graph(cell.resolve(), make_rewrite)

    return PendingGraph(decode)


def renumber_snapshot_graphs(snapshot: Any, make_rewrite: RewriteFactory) -> None:
    """Renumber *snapshot*'s graph (and ``build_source.source_graph`` when it
    is a distinct graph) in place with *make_rewrite*: a still-pending stored
    graph on first decode, a decoded one now."""
    build_source = getattr(snapshot, "build_source", None)
    targets: list[tuple[Any, str]] = [(snapshot, "surface_graph")]
    if build_source is not None:
        targets.append((build_source, "source_graph"))
    replaced: dict[int, Any] = {}
    for owner, name in targets:
        raw = owner.__dict__.get(name)
        if raw is None:
            continue
        key = id(raw)
        if key not in replaced:
            if type(raw) is PendingGraph and not raw.decoded:
                replaced[key] = _wrap(raw, make_rewrite)
            else:
                value = raw.resolve() if type(raw) is PendingGraph else raw
                replaced[key] = renumber_graph(value, make_rewrite)
        new = replaced[key]
        if new is raw:
            continue
        if type(new) is PendingGraph:
            set_pending_graph(owner, name, new)
        else:
            setattr(owner, name, new)
