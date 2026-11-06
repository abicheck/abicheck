# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""A build pack's L5 graph no longer displaces the header graph's public closure.

Formerly known gap "A `--build-info` dump's L5 graph displaces the header
graph": ``resolve_l5_source_graph`` returned the pack's graph (no
``SOURCE_DECLARES`` edges) as-is, so ``public_api_internal_dependency_added``
was never evaluated at build-source depth. The resolver now unions the
snapshot's header closure into the pack graph, borrowing nothing else.

The oracle below is an independent breadth-first walk over the hand-written
edge lists (it never calls the findings module's own closure helpers): an
entry is a public node a public header declares, an internal node is one with
``visibility == "private_header"``, and reach follows the *pack's* dependency
edges when a pack graph exists (else the header graph's).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_graph_findings import diff_source_graph_findings
from abicheck.evidence_depth import resolve_l5_source_graph, with_header_public_closure
from abicheck.model import AbiSnapshot
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.graph_facts import GraphEdge, GraphNode
from abicheck.model.source_graph import SourceGraphSummary

_DEP_KINDS = frozenset(
    {"TYPE_HAS_FIELD_TYPE", "TYPE_INHERITS", "DECL_HAS_TYPE", "DECL_CALLS_DECL"}
)
_HDR = "header://pub.h"
_PRIV_HDR = "header://detail.h"


def _vis(name: str) -> str:
    return "private_header" if name.startswith("detail_") else "public_header"


def _node(name: str) -> GraphNode:
    return GraphNode(
        id=f"type://{name}",
        kind="record_type",
        label=name,
        attrs={"qualified_name": name, "visibility": _vis(name)},
    )


def _field(src: str, dst: str) -> GraphEdge:
    return GraphEdge(
        src=f"type://{src}",
        dst=f"type://{dst}",
        kind="TYPE_HAS_FIELD_TYPE",
        attrs={"role": "field"},
    )


def _graph(
    types: list[str], fields: list[tuple[str, str]], *, closure: bool, passes: str
) -> SourceGraphSummary:
    g = SourceGraphSummary()
    for t in types:
        g.add_node(_node(t))
    for s, d in fields:
        g.add_edge(_field(s, d))
    if closure:
        g.add_node(GraphNode(id=_HDR, kind="header", label="pub.h"))
        g.add_node(GraphNode(id=_PRIV_HDR, kind="header", label="detail.h"))
        for t in types:
            src = _PRIV_HDR if _vis(t) == "private_header" else _HDR
            g.add_edge(GraphEdge(src=src, dst=f"type://{t}", kind="SOURCE_DECLARES"))
    g.extractor_passes = {passes: True}
    return g.finalize()


@dataclass(frozen=True)
class Side:
    types: tuple[str, ...]
    header_fields: tuple[tuple[str, str], ...] | None  # None: no header graph
    pack_fields: tuple[tuple[str, str], ...] | None  # None: no pack graph


def _snap(side: Side) -> AbiSnapshot:
    header = (
        _graph(
            list(side.types),
            list(side.header_fields),
            closure=True,
            passes="header_type_graph",
        )
        if side.header_fields is not None
        else None
    )
    pack = (
        BuildSourcePack(
            root="",
            source_graph=_graph(
                list(side.types),
                list(side.pack_fields),
                closure=False,
                passes="type_graph",
            ),
        )
        if side.pack_fields is not None
        else None
    )
    return AbiSnapshot(
        library="libfoo.so", version="1", build_source=pack, surface_graph=header
    )


def _oracle_reach(side: Side) -> dict[str, set[str]] | None:
    """entry -> internal names it reaches; None when no closure exists."""
    if side.header_fields is None:
        return None
    edges = side.pack_fields if side.pack_fields is not None else side.header_fields
    adj: dict[str, set[str]] = {}
    for s, d in edges:
        adj.setdefault(s, set()).add(d)
    out: dict[str, set[str]] = {}
    for entry in (t for t in side.types if _vis(t) == "public_header"):
        seen, stack = set(), [entry]
        while stack:
            for nxt in adj.get(stack.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        out[entry] = {t for t in seen if _vis(t) == "private_header"}
    return out


def _oracle_added(old: Side, new: Side) -> set[str]:
    o, n = _oracle_reach(old), _oracle_reach(new)
    if o is None or n is None:
        return set()
    return {e for e, r in n.items() if r - o.get(e, set())}


_T = ("Public", "Meta", "detail_Priv")
_OLD_FIELDS = (("Public", "Meta"),)
_NEW_FIELDS = (("Public", "detail_Priv"),)

SCENARIOS = {
    "header-only": (Side(_T, _OLD_FIELDS, None), Side(_T, _NEW_FIELDS, None)),
    "build-only": (Side(_T, None, _OLD_FIELDS), Side(_T, None, _NEW_FIELDS)),
    "both": (Side(_T, _OLD_FIELDS, _OLD_FIELDS), Side(_T, _NEW_FIELDS, _NEW_FIELDS)),
    "both-transitive": (
        Side(_T, _OLD_FIELDS, _OLD_FIELDS),
        Side(_T, (("Public", "Meta"),), (("Public", "Meta"), ("Meta", "detail_Priv"))),
    ),
    # The pack's edges win over the header graph's: the header says Public
    # newly reaches detail_Priv, the pack (the deeper evidence) says it does not.
    "conflict-pack-says-no": (
        Side(_T, _OLD_FIELDS, _OLD_FIELDS),
        Side(_T, _NEW_FIELDS, _OLD_FIELDS),
    ),
    "conflict-pack-says-yes": (
        Side(_T, _OLD_FIELDS, _OLD_FIELDS),
        Side(_T, _OLD_FIELDS, _NEW_FIELDS),
    ),
    "both-unchanged": (
        Side(_T, _NEW_FIELDS, _NEW_FIELDS),
        Side(_T, _NEW_FIELDS, _NEW_FIELDS),
    ),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_internal_dependency_added_matches_independent_oracle(name: str) -> None:
    old, new = SCENARIOS[name]
    so, sn = _snap(old), _snap(new)
    go = resolve_l5_source_graph(so, so.build_source)
    gn = resolve_l5_source_graph(sn, sn.build_source)
    assert go is not None and gn is not None
    found = {
        c.symbol
        for c in diff_source_graph_findings(go, gn)
        if c.kind == ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED
    }
    assert found == _oracle_added(old, new)


def test_bug_shape_build_pack_reports_what_header_only_reports() -> None:
    """The reported shape: the same pair with and without a build pack."""
    hdr = SCENARIOS["header-only"]
    both = SCENARIOS["both"]
    assert _oracle_added(*hdr) == {"Public"} == _oracle_added(*both)


def test_merge_borrows_only_the_closure_and_claims_no_coverage() -> None:
    side = Side(_T, _NEW_FIELDS, _OLD_FIELDS)
    snap = _snap(side)
    pack_graph = snap.build_source.source_graph
    header = snap.surface_graph
    before_pack, before_header = pack_graph.to_dict(), header.to_dict()

    merged = with_header_public_closure(pack_graph, header)

    assert merged is not pack_graph
    # Inputs untouched.
    assert pack_graph.to_dict() == before_pack
    assert header.to_dict() == before_header
    # Dependency edges are the pack's alone; the header's conflicting field
    # edge (Public -> detail_Priv) is not borrowed.
    dep = lambda g: {(e.src, e.dst, e.kind) for e in g.edges if e.kind in _DEP_KINDS}  # noqa: E731
    assert dep(merged) == dep(pack_graph)
    assert {e.kind for e in merged.edges} - {e.kind for e in pack_graph.edges} == {
        "SOURCE_DECLARES"
    }
    # Pass flags and every coverage row the pack recorded are unchanged.
    assert merged.extractor_passes == pack_graph.extractor_passes
    assert merged.narrowed_passes == pack_graph.narrowed_passes
    assert merged.degraded_passes == pack_graph.degraded_passes
    for key in (
        "call_edges",
        "type_edges",
        "reference_edges",
        "include_edges",
        "source_decls",
        "pass_flags_recorded",
    ):
        assert merged.coverage[key] == pack_graph.coverage[key], key
    # The manifest's L5 row is never stamped by the merge.
    assert snap.build_source.manifest.coverage_for("L5_source_graph") is None


def test_merge_is_identity_when_nothing_to_add() -> None:
    pack_graph = _graph(list(_T), list(_OLD_FIELDS), closure=True, passes="type_graph")
    header = _graph(
        list(_T), list(_OLD_FIELDS), closure=True, passes="header_type_graph"
    )
    assert with_header_public_closure(pack_graph, header) is pack_graph
    assert with_header_public_closure(pack_graph, None) is pack_graph


def test_merge_is_identity_when_header_has_no_closure() -> None:
    pack_graph = _graph(list(_T), list(_OLD_FIELDS), closure=False, passes="type_graph")
    header = _graph(
        list(_T), list(_OLD_FIELDS), closure=False, passes="header_type_graph"
    )
    assert with_header_public_closure(pack_graph, header) is pack_graph
    assert with_header_public_closure(pack_graph, pack_graph) is pack_graph


def test_closure_edge_with_endpoint_missing_from_header_nodes_still_merges() -> None:
    pack_graph = _graph(list(_T), list(_OLD_FIELDS), closure=False, passes="type_graph")
    header = SourceGraphSummary()
    header.add_edge(GraphEdge(src=_HDR, dst=f"type://{_T[0]}", kind="SOURCE_DECLARES"))
    merged = with_header_public_closure(pack_graph, header)
    assert merged is not pack_graph
    assert any(
        e.kind == "SOURCE_DECLARES" and e.dst == f"type://{_T[0]}" for e in merged.edges
    )
    assert not any(e.kind == "SOURCE_DECLARES" for e in pack_graph.edges)
