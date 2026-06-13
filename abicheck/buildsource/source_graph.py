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

"""Source / implementation graph summary (ADR-031 L5).

abicheck's own normalized, ABI/API-relevant graph. Stored compactly as
``graph/source_graph_summary.json`` inside an evidence pack (ADR-028 D8): the
primary snapshot only ever keeps a coverage row + reference, never the full
graph (ADR-031 D1, D7).

This module implements the MVP scope of the ADR:

- **Phase 1** — the node/edge schema, the compact ``SourceGraphSummary``
  container, content addressing, and round-trip (de)serialization.
- **Phase 2** — :func:`build_source_graph`, which folds an ADR-029
  :class:`~abicheck.buildsource.build_evidence.BuildEvidence` into a
  target/source/header/compile-unit/build-option graph.
- A structural :func:`diff_source_graph` (Phase 5 seed) that powers the
  ``compare-graph`` command for explanation and triage.

Every edge carries provenance and a confidence label (ADR-031 D2, D9): a graph
fact must always say *how* it was derived so a reader never mistakes graph
absence for safety. Deeper layers — public-reachability / type / include /
call graphs (Phases 3-4, 6) and external backends like Kythe/CodeQL (Phase 7) —
extend this same schema; per ADR-031 D6 graph diffs *explain and prioritize* and
must never, on their own, silently decide or suppress an artifact-proven ABI
break (ADR-028 D3).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .build_evidence import BuildEvidence, Confidence

if TYPE_CHECKING:
    from ..checker_types import Change
    from .source_abi import SourceAbiSurface, SourceEntity

#: Evidence-boundary label stamped on every source-graph finding (ADR-031 D9),
#: mirroring ``DataLayer.L5_SOURCE_GRAPH``. It keeps a graph-derived risk
#: visibly distinct from an artifact-proven shipped-ABI break (ADR-028 D3).
EVIDENCE_TIER_L5 = "L5_SOURCE_GRAPH"

#: Source-graph schema version, independent of the pack/build/source/snapshot
#: versions (ADR-028 D8 versioning). Bump on any breaking change to
#: ``SourceGraphSummary``, :class:`GraphNode`, or :class:`GraphEdge`.
SOURCE_GRAPH_VERSION: int = 1

#: Node kinds the graph schema understands (ADR-031 D2). Unknown kinds from a
#: newer/hand-edited summary are preserved on load, never rejected.
NODE_KINDS: frozenset[str] = frozenset({
    "file", "header", "source", "compile_unit", "target", "link_unit",
    "binary_symbol", "debug_type", "source_decl", "record_type", "enum_type",
    "typedef", "macro", "build_option", "toolchain", "generated_file",
    "external_dependency",
})

#: Edge kinds the graph schema understands (ADR-031 D2).
EDGE_KINDS: frozenset[str] = frozenset({
    "TARGET_HAS_SOURCE", "TARGET_HAS_PUBLIC_HEADER", "TARGET_DEPENDS_ON",
    "COMPILE_UNIT_BUILDS_SOURCE", "COMPILE_UNIT_USES_OPTION",
    "COMPILE_UNIT_INCLUDES_FILE", "FILE_GENERATED_FROM",
    "SOURCE_DECLARES", "SOURCE_DEFINES", "DECL_HAS_TYPE",
    "DECL_CALLS_DECL", "DECL_REFERENCES_DECL",
    "TYPE_HAS_FIELD_TYPE", "TYPE_INHERITS",
    "BINARY_EXPORTS_SYMBOL", "SOURCE_DECL_MAPS_TO_SYMBOL",
    "SOURCE_TYPE_MAPS_TO_DEBUG_TYPE",
    "BUILD_OPTION_AFFECTS_DECL", "BUILD_OPTION_AFFECTS_SYMBOL",
    "FINDING_LOCALIZES_TO_DECL", "FINDING_CAUSED_BY_OPTION",
})

#: Confidence labels (ADR-031 D9). Mirrors the evidence-model vocabulary so the
#: coverage report and graph speak the same language.
CONF_HIGH = "high"
CONF_REDUCED = "reduced"
CONF_UNKNOWN = "unknown"


def _conf_from_build(conf: Confidence) -> str:
    """Map an ADR-029 build-evidence confidence onto a graph confidence label."""
    if conf == Confidence.HIGH:
        return CONF_HIGH
    if conf == Confidence.REDUCED:
        return CONF_REDUCED
    return CONF_UNKNOWN


@dataclass
class GraphNode:
    """A single ABI/API-relevant graph node (ADR-031 D2)."""

    id: str
    kind: str                       # one of NODE_KINDS (preserved even if unknown)
    label: str = ""                 # human-readable name/path (redacted upstream)
    attrs: dict[str, Any] = field(default_factory=dict)
    provenance: str = ""            # how this node was derived, e.g. "build_evidence"
    confidence: str = CONF_UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "attrs": dict(self.attrs),
            "provenance": self.provenance,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphNode:
        return cls(
            id=str(d["id"]),
            kind=str(d.get("kind", "file")),
            label=str(d.get("label", "")),
            attrs=dict(d.get("attrs", {})),
            provenance=str(d.get("provenance", "")),
            confidence=str(d.get("confidence", CONF_UNKNOWN)),
        )


@dataclass
class GraphEdge:
    """A directed edge between two nodes, with provenance + confidence (D2, D9).

    ``attrs`` carries edge-kind-specific labels — most importantly the
    ``call_kind``/``resolution`` pair for ``DECL_CALLS_DECL`` edges (ADR-031 D4),
    which a future call-graph extractor populates.
    """

    src: str
    dst: str
    kind: str                       # one of EDGE_KINDS (preserved even if unknown)
    provenance: str = ""
    confidence: str = CONF_UNKNOWN
    attrs: dict[str, Any] = field(default_factory=dict)

    def key(self) -> tuple[str, str, str]:
        """Identity of an edge for diffing/de-duplication: (src, dst, kind)."""
        return (self.src, self.dst, self.kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge": self.kind,
            "src": self.src,
            "dst": self.dst,
            "provenance": self.provenance,
            "confidence": self.confidence,
            "attrs": dict(self.attrs),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphEdge:
        return cls(
            src=str(d["src"]),
            dst=str(d["dst"]),
            kind=str(d.get("edge", d.get("kind", ""))),
            provenance=str(d.get("provenance", "")),
            confidence=str(d.get("confidence", CONF_UNKNOWN)),
            attrs=dict(d.get("attrs", {})),
        )


@dataclass
class SourceGraphSummary:
    """Compact, ABI/API-relevant source/implementation graph (ADR-031 D7).

    Deliberately small: a report must never need to load a huge full graph to
    compare core ABI snapshots (D7). The ``coverage`` block makes the graph's
    extent — and what it does *not* cover (e.g. call edges) — explicit so graph
    absence is never read as safety (D9). For very large projects the same
    schema can be chunked/externalized; ``external_graph_refs`` points at any
    deep backend store (Kythe/CodeQL, Phase 7).
    """

    schema_version: int = SOURCE_GRAPH_VERSION
    graph_id: str = ""              # "sha256:..." content hash of nodes+edges
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    external_graph_refs: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # De-dup indexes for O(1) add_node/add_edge. Built from whatever the
        # constructor (or from_dict) seeded so incremental building stays cheap.
        self._node_ids: set[str] = {n.id for n in self.nodes}
        self._edge_keys: set[tuple[str, str, str]] = {e.key() for e in self.edges}

    # -- mutation helpers ---------------------------------------------------

    def add_node(self, node: GraphNode) -> None:
        """Add a node, de-duplicating by id (first writer wins on facts)."""
        if node.id not in self._node_ids:
            self.nodes.append(node)
            self._node_ids.add(node.id)

    def add_edge(self, edge: GraphEdge) -> None:
        """Add an edge, de-duplicating by (src, dst, kind)."""
        if edge.key() not in self._edge_keys:
            self.edges.append(edge)
            self._edge_keys.add(edge.key())

    def has_node(self, node_id: str) -> bool:
        """Whether a node with ``node_id`` is already in the graph."""
        return node_id in self._node_ids

    def indexes(self) -> dict[str, dict[str, list[str]]]:
        """Build the lookup indexes (ADR-031 D7) on demand.

        Lightweight reverse maps so a finding can be localized without a full
        scan: by target, by file/source/header, by binary symbol, by source
        decl. Computed from the current nodes/edges so they never drift.
        """
        by_target: dict[str, list[str]] = {}
        by_file: dict[str, list[str]] = {}
        by_binary_symbol: dict[str, list[str]] = {}
        by_source_decl: dict[str, list[str]] = {}
        kind_by_id = {n.id: n.kind for n in self.nodes}
        for e in self.edges:
            src_kind = kind_by_id.get(e.src, "")
            dst_kind = kind_by_id.get(e.dst, "")
            if src_kind == "target":
                by_target.setdefault(e.src, []).append(e.dst)
            if dst_kind in ("file", "header", "source", "generated_file"):
                by_file.setdefault(e.dst, []).append(e.src)
            if dst_kind == "binary_symbol" or src_kind == "binary_symbol":
                sym = e.dst if dst_kind == "binary_symbol" else e.src
                other = e.src if dst_kind == "binary_symbol" else e.dst
                by_binary_symbol.setdefault(sym, []).append(other)
            if dst_kind == "source_decl" or src_kind == "source_decl":
                decl = e.dst if dst_kind == "source_decl" else e.src
                other = e.src if dst_kind == "source_decl" else e.dst
                by_source_decl.setdefault(decl, []).append(other)
        return {
            "by_target": {k: sorted(set(v)) for k, v in by_target.items()},
            "by_file": {k: sorted(set(v)) for k, v in by_file.items()},
            "by_binary_symbol": {k: sorted(set(v)) for k, v in by_binary_symbol.items()},
            "by_source_decl": {k: sorted(set(v)) for k, v in by_source_decl.items()},
        }

    def compute_graph_id(self) -> str:
        """Stable ``sha256:<hex>`` over the canonical node+edge set.

        Order-independent (nodes/edges are sorted) so the same logical graph
        always hashes identically regardless of construction order.
        """
        canonical = {
            "schema_version": self.schema_version,
            "nodes": sorted((n.id, n.kind) for n in self.nodes),
            "edges": sorted(e.key() for e in self.edges),
        }
        blob = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(blob).hexdigest()

    def finalize(self) -> SourceGraphSummary:
        """Fill ``graph_id`` and the structural ``coverage`` counts; return self."""
        self.graph_id = self.compute_graph_id()
        kinds: dict[str, int] = {}
        for n in self.nodes:
            kinds[n.kind] = kinds.get(n.kind, 0) + 1
        edge_kinds: dict[str, int] = {}
        for e in self.edges:
            edge_kinds[e.kind] = edge_kinds.get(e.kind, 0) + 1
        has_calls = any(e.kind == "DECL_CALLS_DECL" for e in self.edges)
        has_includes = any(e.kind == "COMPILE_UNIT_INCLUDES_FILE" for e in self.edges)
        self.coverage = {
            "targets": kinds.get("target", 0),
            "compile_units": kinds.get("compile_unit", 0),
            "source_decls": kinds.get("source_decl", 0),
            "binary_symbol_mappings": edge_kinds.get("SOURCE_DECL_MAPS_TO_SYMBOL", 0),
            "include_edges": {"collected": has_includes, "count": edge_kinds.get("COMPILE_UNIT_INCLUDES_FILE", 0)},
            "call_edges": {"collected": has_calls, "count": edge_kinds.get("DECL_CALLS_DECL", 0)},
            "node_kinds": dict(sorted(kinds.items())),
            "edge_kinds": dict(sorted(edge_kinds.items())),
        }
        return self

    # -- (de)serialization --------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "graph_id": self.graph_id or self.compute_graph_id(),
            "coverage": dict(self.coverage),
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "indexes": self.indexes(),
            "external_graph_refs": [dict(r) for r in self.external_graph_refs],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SourceGraphSummary:
        # Defensive ``.get`` parsing so a newer/hand-edited summary never aborts
        # a load (evidence/CLAUDE.md forward-compat rule). ``indexes`` are derived
        # and intentionally not read back — they are recomputed from nodes/edges.
        return cls(
            schema_version=int(d.get("schema_version", SOURCE_GRAPH_VERSION)),
            graph_id=str(d.get("graph_id", "")),
            nodes=[GraphNode.from_dict(n) for n in d.get("nodes", [])],
            edges=[GraphEdge.from_dict(e) for e in d.get("edges", [])],
            coverage=dict(d.get("coverage", {})),
            external_graph_refs=[dict(r) for r in d.get("external_graph_refs", [])],
        )


# ── node-id helpers ───────────────────────────────────────────────────────
#
# Build-evidence entities already carry stable ids ("target://", "cu://").
# File/header/option nodes are keyed by their (already-redacted) path/flag so
# the same file referenced by two targets folds to one node.


def _source_node_id(path: str) -> str:
    return f"source://{path}"


def _header_node_id(path: str) -> str:
    return f"header://{path}"


def _option_node_id(flag: str) -> str:
    return f"build_option://{flag}"


def _decl_node_id(identity: str) -> str:
    return f"decl://{identity}"


def _type_node_id(identity: str) -> str:
    return f"type://{identity}"


def _symbol_node_id(symbol: str) -> str:
    return f"binary_symbol://{symbol}"


def _macro_node_id(name: str) -> str:
    return f"macro://{name}"


def _debug_type_node_id(name: str) -> str:
    return f"debug_type://{name}"


#: SourceEntity.kind → graph type-node kind. Records/classes/unions all map to
#: ``record_type``; enums and typedefs get their own node kind so reachability
#: queries can distinguish them (ADR-031 D2).
_TYPE_NODE_KINDS: dict[str, str] = {"enum": "enum_type", "typedef": "typedef"}


def _type_node_kind(decl_kind: str) -> str:
    return _TYPE_NODE_KINDS.get(decl_kind, "record_type")


# ── Phase 2: build the graph from ADR-029 BuildEvidence ─────────────────────


def build_source_graph(
    build: BuildEvidence, source_abi: SourceAbiSurface | None = None
) -> SourceGraphSummary:
    """Fold ADR-029 build evidence (+ optional L4 source surface) into a graph.

    **Phase 2** emits the build-level slice from *build*:

    - ``target`` nodes, with ``TARGET_HAS_SOURCE`` / ``TARGET_HAS_PUBLIC_HEADER``
      / ``TARGET_DEPENDS_ON`` edges;
    - ``compile_unit`` nodes, with ``COMPILE_UNIT_BUILDS_SOURCE`` edges and
      ``COMPILE_UNIT_USES_OPTION`` edges to the ABI-relevant flags they carry;
    - ``source`` / ``header`` / ``generated_file`` nodes (a source listed in
      ``build.generated_files`` is typed ``generated_file``).

    **Phases 3-4** — when an ADR-030 ``source_abi`` surface is supplied — add the
    public-reachability and source↔binary slices: ``source_decl`` / type / macro
    nodes declared by public headers (``SOURCE_DECLARES``), their
    ``SOURCE_DECL_MAPS_TO_SYMBOL`` / ``SOURCE_TYPE_MAPS_TO_DEBUG_TYPE`` mappings,
    and ``BINARY_EXPORTS_SYMBOL`` edges from the owning target. Together they
    yield the target → public-header → decl → exported-symbol closure that
    reachability triage needs.

    Deeper call edges and external backends (Phases 6-7) extend the same graph.
    """
    graph = SourceGraphSummary()
    generated = set(build.generated_files)

    def file_node(path: str, *, header: bool = False) -> str:
        if not path:
            return ""
        if path in generated:
            node_id = _source_node_id(path)
            graph.add_node(GraphNode(
                id=node_id, kind="generated_file", label=path,
                provenance="build_evidence", confidence=CONF_REDUCED,
                attrs={"generated": True},
            ))
            return node_id
        if header:
            node_id = _header_node_id(path)
            graph.add_node(GraphNode(
                id=node_id, kind="header", label=path,
                provenance="build_evidence", confidence=CONF_HIGH,
            ))
            return node_id
        node_id = _source_node_id(path)
        graph.add_node(GraphNode(
            id=node_id, kind="source", label=path,
            provenance="build_evidence", confidence=CONF_HIGH,
        ))
        return node_id

    known_targets = {t.id for t in build.targets}
    for tgt in build.targets:
        conf = _conf_from_build(tgt.confidence)
        graph.add_node(GraphNode(
            id=tgt.id, kind="target", label=tgt.name or tgt.id,
            provenance="build_evidence", confidence=conf,
            attrs={"kind": tgt.kind.value, "visibility": tgt.visibility,
                   "build_system": tgt.build_system},
        ))
        for src in tgt.source_files:
            sid = file_node(src)
            graph.add_edge(GraphEdge(
                src=tgt.id, dst=sid, kind="TARGET_HAS_SOURCE",
                provenance="build_evidence", confidence=conf,
            ))
        for hdr in tgt.public_headers:
            hid = file_node(hdr, header=True)
            graph.add_edge(GraphEdge(
                src=tgt.id, dst=hid, kind="TARGET_HAS_PUBLIC_HEADER",
                provenance="build_evidence", confidence=conf,
            ))
        for dep in tgt.dependencies:
            # Reference an external dependency explicitly when it is not one of
            # our own targets, so the graph distinguishes intra-project edges
            # from third-party ones (informative for reachability triage).
            if dep not in known_targets:
                graph.add_node(GraphNode(
                    id=dep, kind="external_dependency", label=dep,
                    provenance="build_evidence", confidence=CONF_REDUCED,
                ))
            graph.add_edge(GraphEdge(
                src=tgt.id, dst=dep, kind="TARGET_DEPENDS_ON",
                provenance="build_evidence", confidence=conf,
            ))

    for cu in build.compile_units:
        graph.add_node(GraphNode(
            id=cu.id, kind="compile_unit", label=cu.output or cu.source or cu.id,
            provenance="build_evidence", confidence=CONF_HIGH,
            attrs={"language": cu.language, "standard": cu.standard,
                   "target_id": cu.target_id},
        ))
        if cu.source:
            sid = file_node(cu.source)
            graph.add_edge(GraphEdge(
                src=cu.id, dst=sid, kind="COMPILE_UNIT_BUILDS_SOURCE",
                provenance="build_evidence", confidence=CONF_HIGH,
            ))
        for flag in cu.abi_relevant_flags:
            oid = _option_node_id(flag)
            graph.add_node(GraphNode(
                id=oid, kind="build_option", label=flag,
                provenance="build_evidence", confidence=CONF_HIGH,
                attrs={"abi_relevant": True},
            ))
            graph.add_edge(GraphEdge(
                src=cu.id, dst=oid, kind="COMPILE_UNIT_USES_OPTION",
                provenance="build_evidence", confidence=CONF_HIGH,
            ))

    if source_abi is not None:
        _augment_with_source_abi(graph, source_abi)
        _link_options_to_symbols(graph)

    return graph.finalize()


def _link_options_to_symbols(graph: SourceGraphSummary) -> None:
    """Add ``BUILD_OPTION_AFFECTS_SYMBOL`` edges (ADR-031 D2, build→symbol flow).

    Connects each ABI-relevant build option to the exported symbols it can
    affect, via the path *option ← compile_unit (target) → exported symbol*.
    Only meaningful once the L4 surface has contributed ``BINARY_EXPORTS_SYMBOL``
    edges, so it is a no-op for a build-only graph.
    """
    target_syms: dict[str, list[str]] = {}
    for e in graph.edges:
        if e.kind == "BINARY_EXPORTS_SYMBOL":
            target_syms.setdefault(e.src, []).append(e.dst)
    if not target_syms:
        return
    cu_target = {
        n.id: str(n.attrs.get("target_id", ""))
        for n in graph.nodes
        if n.kind == "compile_unit"
    }
    for e in list(graph.edges):
        if e.kind != "COMPILE_UNIT_USES_OPTION":
            continue
        target = cu_target.get(e.src, "")
        for sym in target_syms.get(target, []):
            graph.add_edge(GraphEdge(
                src=e.dst, dst=sym, kind="BUILD_OPTION_AFFECTS_SYMBOL",
                provenance="build_evidence+source_abi", confidence=CONF_REDUCED,
            ))


# ── Phases 3-4: enrich the graph from the ADR-030 L4 source surface ─────────


def _augment_with_source_abi(graph: SourceGraphSummary, surface: SourceAbiSurface) -> None:
    """Fold a linked L4 source surface into *graph* (Phases 3-4).

    Adds the public-reachability slice (declarations/types/macros, each linked
    to the public header that declares it) and the source↔binary slice (decl →
    exported symbol, type → debug type, target → exported symbol). All edges are
    tagged ``provenance="source_abi"`` so a reachability claim always discloses
    that it rests on source-replay evidence, not a binary diff (ADR-031 D9).
    """
    target_id = surface.target_id
    if target_id and not graph.has_node(target_id):
        # The surface may name a target the build evidence did not enumerate
        # (e.g. binary+headers-only collection). Materialize it so its symbols
        # have an owner in the graph.
        graph.add_node(GraphNode(
            id=target_id, kind="target", label=target_id,
            provenance="source_abi", confidence=CONF_REDUCED,
        ))

    decl_to_sym: dict[str, str] = surface.mappings.get("source_decl_to_binary_symbol", {})
    type_to_dbg: dict[str, str] = surface.mappings.get("source_type_to_debug_type", {})

    def export_symbol(symbol: str, confidence: str) -> str:
        sid = _symbol_node_id(symbol)
        graph.add_node(GraphNode(
            id=sid, kind="binary_symbol", label=symbol,
            provenance="source_abi", confidence=CONF_HIGH,
        ))
        if target_id:
            graph.add_edge(GraphEdge(
                src=target_id, dst=sid, kind="BINARY_EXPORTS_SYMBOL",
                provenance="source_abi", confidence=confidence,
            ))
        return sid

    def header_declares(entity: SourceEntity, node_id: str, confidence: str) -> None:
        loc = entity.source_location
        if loc is None or not loc.path:
            return
        hid = _header_node_id(loc.path)
        # add_node keeps the first writer's facts, so a build-evidence header
        # node (HIGH confidence) is not downgraded by this source_abi one.
        graph.add_node(GraphNode(
            id=hid, kind="header", label=loc.path,
            provenance="source_abi", confidence=confidence,
            attrs={"origin": loc.origin},
        ))
        graph.add_edge(GraphEdge(
            src=hid, dst=node_id, kind="SOURCE_DECLARES",
            provenance="source_abi", confidence=confidence,
        ))

    # Represent every exported symbol the surface mapped, so the target's export
    # set is visible even for symbols whose declaration was not reachable.
    for symbol in decl_to_sym.values():
        if symbol:
            export_symbol(symbol, CONF_REDUCED)

    declarations = (
        *surface.reachable_declarations,
        *surface.reachable_templates,
        *surface.reachable_inline_bodies,
    )
    for ent in declarations:
        did = _decl_node_id(ent.identity())
        conf = ent.confidence.value
        graph.add_node(GraphNode(
            id=did, kind="source_decl", label=ent.qualified_name or ent.identity(),
            provenance="source_abi", confidence=conf,
            attrs={"decl_kind": ent.kind, "visibility": ent.visibility},
        ))
        header_declares(ent, did, conf)
        # decl_to_sym is keyed by entity identity (the mangled name for C++, so
        # overloads stay distinct) by both link_source_abi and
        # relink_surface_exports — look it up the same way, not by qualified_name,
        # or the SOURCE_DECL_MAPS_TO_SYMBOL edge is never created for C++.
        symbol = decl_to_sym.get(ent.identity(), "")
        if symbol:
            graph.add_edge(GraphEdge(
                src=did, dst=_symbol_node_id(symbol),
                kind="SOURCE_DECL_MAPS_TO_SYMBOL",
                provenance="source_abi", confidence=conf,
            ))

    for ent in surface.reachable_types:
        tid = _type_node_id(ent.identity())
        conf = ent.confidence.value
        graph.add_node(GraphNode(
            id=tid, kind=_type_node_kind(ent.kind),
            label=ent.qualified_name or ent.identity(),
            provenance="source_abi", confidence=conf,
            attrs={"decl_kind": ent.kind, "visibility": ent.visibility},
        ))
        header_declares(ent, tid, conf)
        debug_type = type_to_dbg.get(ent.qualified_name, "")
        if debug_type:
            bid = _debug_type_node_id(debug_type)
            graph.add_node(GraphNode(
                id=bid, kind="debug_type", label=debug_type,
                provenance="source_abi", confidence=CONF_REDUCED,
            ))
            graph.add_edge(GraphEdge(
                src=tid, dst=bid, kind="SOURCE_TYPE_MAPS_TO_DEBUG_TYPE",
                provenance="source_abi", confidence=CONF_REDUCED,
            ))

    for ent in surface.reachable_macros:
        mid = _macro_node_id(ent.qualified_name or ent.identity())
        conf = ent.confidence.value
        graph.add_node(GraphNode(
            id=mid, kind="macro", label=ent.qualified_name or ent.identity(),
            provenance="source_abi", confidence=conf,
        ))
        header_declares(ent, mid, conf)


# ── Phase 5 (seed): structural graph-to-graph diff ──────────────────────────


@dataclass
class GraphSummaryDiff:
    """Structural delta between two :class:`SourceGraphSummary` snapshots.

    A pure structural diff (which nodes/edges entered or left the graph) — the
    foundation the ``compare-graph`` command renders and that a later phase maps
    onto the ADR-031 D6 secondary findings. Per ADR-028 D3 / ADR-031 D6 these
    deltas *explain and prioritize*; they never decide an ABI break on their own.
    """

    added_nodes: list[GraphNode] = field(default_factory=list)
    removed_nodes: list[GraphNode] = field(default_factory=list)
    added_edges: list[GraphEdge] = field(default_factory=list)
    removed_edges: list[GraphEdge] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added_nodes or self.removed_nodes
                    or self.added_edges or self.removed_edges)

    def to_dict(self) -> dict[str, Any]:
        return {
            "added_nodes": [n.to_dict() for n in self.added_nodes],
            "removed_nodes": [n.to_dict() for n in self.removed_nodes],
            "added_edges": [e.to_dict() for e in self.added_edges],
            "removed_edges": [e.to_dict() for e in self.removed_edges],
            "counts": {
                "added_nodes": len(self.added_nodes),
                "removed_nodes": len(self.removed_nodes),
                "added_edges": len(self.added_edges),
                "removed_edges": len(self.removed_edges),
            },
        }


def localize_symbol(graph: SourceGraphSummary, symbol: str) -> dict[str, Any]:
    """Localize an exported symbol through the graph (ADR-031 D8 `explain-finding`).

    Given a (mangled) binary symbol, walk the graph to report what produced and
    reaches it: the exporting target(s), the source declaration(s) it maps to,
    the public header(s) that declare those decls, the ABI-relevant build
    option(s) that feed it, and the static callees of its declarations. Every
    fact is graph-derived (provenance/confidence live on the edges), so the
    result is explanatory, never an ABI verdict (ADR-031 D6).
    """
    labels = _label_map(graph)
    kinds = _kind_map(graph)
    sym_id = _symbol_node_id(symbol)
    found = graph.has_node(sym_id)

    targets = sorted({e.src for e in graph.edges
                      if e.kind == "BINARY_EXPORTS_SYMBOL" and e.dst == sym_id})
    decls = sorted({e.src for e in graph.edges
                    if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL" and e.dst == sym_id})
    options = sorted({e.src for e in graph.edges
                      if e.kind == "BUILD_OPTION_AFFECTS_SYMBOL" and e.dst == sym_id})

    headers: set[str] = set()
    callees: set[str] = set()
    for decl in decls:
        headers |= {e.src for e in graph.edges
                    if e.kind == "SOURCE_DECLARES" and e.dst == decl}
        callees |= {e.dst for e in graph.edges
                    if e.kind == "DECL_CALLS_DECL" and e.src == decl}

    def names(ids: set[str] | list[str]) -> list[str]:
        return sorted(labels.get(i, i) for i in ids)

    return {
        "symbol": symbol,
        "found": found,
        "exported_by_targets": names(targets),
        "source_declarations": names(decls),
        "declared_in_headers": names(headers),
        "reached_by_build_options": names(options),
        "static_callees": names(callees),
        "header_kinds": {labels.get(h, h): kinds.get(h, "") for h in headers},
    }


def diff_source_graph(old: SourceGraphSummary, new: SourceGraphSummary) -> GraphSummaryDiff:
    """Compute the structural delta from *old* to *new* (Phase 5 seed)."""
    old_nodes = {n.id: n for n in old.nodes}
    new_nodes = {n.id: n for n in new.nodes}
    old_edges = {e.key(): e for e in old.edges}
    new_edges = {e.key(): e for e in new.edges}

    return GraphSummaryDiff(
        added_nodes=[new_nodes[i] for i in sorted(new_nodes.keys() - old_nodes.keys())],
        removed_nodes=[old_nodes[i] for i in sorted(old_nodes.keys() - new_nodes.keys())],
        added_edges=[new_edges[k] for k in sorted(new_edges.keys() - old_edges.keys())],
        removed_edges=[old_edges[k] for k in sorted(old_edges.keys() - new_edges.keys())],
    )


# ── Phase 5: graph-derived secondary risk findings (ADR-031 D6) ─────────────


def _label_map(graph: SourceGraphSummary) -> dict[str, str]:
    return {n.id: (n.label or n.id) for n in graph.nodes}


def _kind_map(graph: SourceGraphSummary) -> dict[str, str]:
    return {n.id: n.kind for n in graph.nodes}


def _decl_to_symbol(graph: SourceGraphSummary) -> dict[str, str]:
    """``source_decl`` node id → exported ``binary_symbol`` node id it maps to."""
    return {
        e.src: e.dst
        for e in graph.edges
        if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"
    }


def _public_decls(graph: SourceGraphSummary) -> set[str]:
    """``source_decl`` ids reachable from a public header (``SOURCE_DECLARES``)."""
    kinds = _kind_map(graph)
    return {
        e.dst
        for e in graph.edges
        if e.kind == "SOURCE_DECLARES"
        and kinds.get(e.src) == "header"
        and kinds.get(e.dst) == "source_decl"
    }


def _generated_in_public_closure(graph: SourceGraphSummary) -> set[str]:
    """``generated_file`` ids that are exposed as a target's public header.

    A generated file in the public declaration closure is one a target lists as
    a public header (``TARGET_HAS_PUBLIC_HEADER`` → ``generated_file``) — e.g. a
    generated ``config.h``. That is the common, well-defined signal; richer
    "generated file declares a public entity" detection awaits the include-graph
    phase, which gives generated files and headers a shared identity.
    """
    kinds = _kind_map(graph)
    return {
        e.dst
        for e in graph.edges
        if e.kind == "TARGET_HAS_PUBLIC_HEADER" and kinds.get(e.dst) == "generated_file"
    }


def _public_entry_call_reachability(graph: SourceGraphSummary) -> dict[str, frozenset[str]]:
    """For each exported-entry decl, the impl decls statically reachable from it.

    Public entries are ``source_decl`` nodes with an outgoing
    ``SOURCE_DECL_MAPS_TO_SYMBOL`` edge (they back an exported symbol). The
    reachable set is the transitive closure over ``DECL_CALLS_DECL`` edges — an
    *approximate* implementation footprint (ADR-031 D4). Returns ``{}`` when the
    graph carries no call edges, so callers can skip the comparison entirely.
    """
    calls: dict[str, list[str]] = {}
    for e in graph.edges:
        if e.kind == "DECL_CALLS_DECL":
            calls.setdefault(e.src, []).append(e.dst)
    if not calls:
        return {}
    entries = {
        e.src for e in graph.edges if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"
    }
    out: dict[str, frozenset[str]] = {}
    for entry in entries:
        seen: set[str] = set()
        stack = list(calls.get(entry, []))
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(calls.get(node, []))
        out[entry] = frozenset(seen)
    return out


def _public_headers_in_include_graph(graph: SourceGraphSummary) -> set[str]:
    """Public-header node ids that actually appear in the compiled include graph.

    A public header (``TARGET_HAS_PUBLIC_HEADER`` target) that is also the target
    of a ``COMPILE_UNIT_INCLUDES_FILE`` edge — i.e. the build genuinely compiled
    a TU that included it. Returns ``set()`` when no include edges were collected.
    """
    included = {e.dst for e in graph.edges if e.kind == "COMPILE_UNIT_INCLUDES_FILE"}
    if not included:
        return set()
    public = {e.dst for e in graph.edges if e.kind == "TARGET_HAS_PUBLIC_HEADER"}
    return public & included


def _option_symbol_edges(graph: SourceGraphSummary) -> set[tuple[str, str]]:
    """``(build_option, binary_symbol)`` pairs from ``BUILD_OPTION_AFFECTS_SYMBOL``."""
    return {
        (e.src, e.dst)
        for e in graph.edges
        if e.kind == "BUILD_OPTION_AFFECTS_SYMBOL"
    }


def diff_source_graph_findings(
    old: SourceGraphSummary, new: SourceGraphSummary
) -> list[Change]:
    """Map the graph delta onto ADR-031 D6 secondary risk findings.

    Produces three RISK-tier ``ChangeKind``s, each stamped with the
    ``[L5_SOURCE_GRAPH]`` evidence boundary so it reads as graph-derived, not an
    artifact diff:

    - ``SOURCE_TO_BINARY_MAPPING_CHANGED`` — a declaration present in *both*
      graphs now maps to a different exported symbol;
    - ``PUBLIC_REACHABILITY_CHANGED`` — a declaration entered/left the
      public-header reachability closure;
    - ``GENERATED_HEADER_REACHES_PUBLIC_API`` — a generated file newly entered
      the public declaration closure.

    Per ADR-028 D3 / ADR-031 D6 these explain and prioritize; the caller folds
    them into the verdict pipeline as ordinary RISK changes that never override
    an artifact-proven break.
    """
    from ..checker_policy import ChangeKind
    from ..checker_types import Change

    findings: list[Change] = []
    boundary = f"[{EVIDENCE_TIER_L5}]"
    old_labels, new_labels = _label_map(old), _label_map(new)

    # 1) source↔binary mapping drift for declarations present in both graphs.
    old_map, new_map = _decl_to_symbol(old), _decl_to_symbol(new)
    old_decls = {n.id for n in old.nodes if n.kind == "source_decl"}
    new_decls = {n.id for n in new.nodes if n.kind == "source_decl"}
    for decl in sorted(old_decls & new_decls):
        old_sym, new_sym = old_map.get(decl, ""), new_map.get(decl, "")
        if old_sym != new_sym:
            label = new_labels.get(decl, decl)
            findings.append(Change(
                kind=ChangeKind.SOURCE_TO_BINARY_MAPPING_CHANGED,
                symbol=label,
                description=(
                    f"Declaration {label!r} maps to a different exported symbol "
                    f"than before ({old_sym or '<none>'} → {new_sym or '<none>'}). "
                    "Source-graph evidence: investigate the surface/export mapping; "
                    "this does not by itself prove an ABI break."
                ),
                old_value=old_labels.get(old_sym, old_sym),
                new_value=new_labels.get(new_sym, new_sym),
                source_location=boundary,
            ))

    # 2) public-reachability closure changes (only when both sides have a
    #    closure — an empty baseline would otherwise flag every declaration).
    old_pub, new_pub = _public_decls(old), _public_decls(new)
    if old_pub and new_pub:
        for decl in sorted(new_pub - old_pub):
            label = new_labels.get(decl, decl)
            findings.append(Change(
                kind=ChangeKind.PUBLIC_REACHABILITY_CHANGED,
                symbol=label,
                description=(
                    f"Declaration {label!r} entered the public-API reachability "
                    "closure (now declared by a public header). Source-graph "
                    "evidence to prioritize review."
                ),
                old_value="not reachable",
                new_value="reachable via public header",
                source_location=boundary,
            ))
        for decl in sorted(old_pub - new_pub):
            label = old_labels.get(decl, decl)
            findings.append(Change(
                kind=ChangeKind.PUBLIC_REACHABILITY_CHANGED,
                symbol=label,
                description=(
                    f"Declaration {label!r} left the public-API reachability "
                    "closure (no longer declared by a public header). Source-graph "
                    "evidence to prioritize review."
                ),
                old_value="reachable via public header",
                new_value="not reachable",
                source_location=boundary,
            ))

    # 3) generated files that newly entered the public declaration closure.
    newly_generated = _generated_in_public_closure(new) - _generated_in_public_closure(old)
    for gen in sorted(newly_generated):
        label = new_labels.get(gen, gen)
        findings.append(Change(
            kind=ChangeKind.GENERATED_HEADER_REACHES_PUBLIC_API,
            symbol=label,
            description=(
                f"Generated file {label!r} now participates in the public "
                "declaration closure (public header or declares a public entity). "
                "Verify its provenance and that the generated content is "
                "reproducible across builds."
            ),
            old_value="not in public closure",
            new_value="in public closure",
            source_location=boundary,
        ))

    # 4) implementation reachable from an exported entry changed (phase 6, needs
    #    Clang call edges). Quality signal only — reported for entries present in
    #    both graphs whose approximate call-reachable set differs.
    old_reach = _public_entry_call_reachability(old)
    new_reach = _public_entry_call_reachability(new)
    for entry in sorted(old_reach.keys() & new_reach.keys()):
        if old_reach[entry] != new_reach[entry]:
            label = new_labels.get(entry, entry)
            old_n, new_n = len(old_reach[entry]), len(new_reach[entry])
            findings.append(Change(
                kind=ChangeKind.CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED,
                symbol=label,
                description=(
                    f"Implementation statically reachable from exported entry "
                    f"{label!r} changed ({old_n} → {new_n} known static callees, "
                    "approximate). Source-graph quality signal: the code behind a "
                    "stable public symbol moved; not an ABI break."
                ),
                old_value=f"{old_n} reachable",
                new_value=f"{new_n} reachable",
                source_location=boundary,
            ))

    # 5) public headers entering/leaving the compiled include graph (needs
    #    COMPILE_UNIT_INCLUDES_FILE edges from a depfile/-M include extractor).
    old_inc, new_inc = _public_headers_in_include_graph(old), _public_headers_in_include_graph(new)
    if old_inc or new_inc:
        for hdr in sorted(new_inc - old_inc) + sorted(old_inc - new_inc):
            entered = hdr in new_inc
            label = (new_labels if entered else old_labels).get(hdr, hdr)
            findings.append(Change(
                kind=ChangeKind.INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT,
                symbol=label,
                description=(
                    f"Public header {label!r} {'entered' if entered else 'left'} "
                    "the compiled include graph. Consumers may pull in different "
                    "declarations/macros through it. Source-graph evidence to review."
                ),
                old_value="in include graph" if not entered else "not included",
                new_value="in include graph" if entered else "not included",
                source_location=boundary,
            ))

    # 6) a changed ABI-relevant build option that now reaches a public symbol
    #    (added BUILD_OPTION_AFFECTS_SYMBOL edges), grouped by option.
    added_opt_edges = _option_symbol_edges(new) - _option_symbol_edges(old)
    # Only a *changed* (newly introduced) ABI-relevant flag is interesting here:
    # a new target that merely reuses a pre-existing flag produces "added" edges
    # too, but that is covered by symbol-level diffs, not flag drift. Scope to
    # build-option nodes absent from the old graph (ADR-029 build_diff already
    # reports the drift; this localizes a *new* flag to the public surface).
    old_option_nodes = {n.id for n in old.nodes if n.kind == "build_option"}
    reached_by_option: dict[str, list[str]] = {}
    for opt, sym in added_opt_edges:
        if opt in old_option_nodes:
            continue
        reached_by_option.setdefault(opt, []).append(sym)
    for opt in sorted(reached_by_option):
        label = new_labels.get(opt, opt)
        n_syms = len(reached_by_option[opt])
        findings.append(Change(
            kind=ChangeKind.BUILD_OPTION_REACHES_PUBLIC_SYMBOL,
            symbol=label,
            description=(
                f"Build option {label!r} now feeds a compile unit producing "
                f"{n_syms} exported public symbol(s). A changed ABI-relevant flag "
                "localized to the public surface it can affect. Source-graph "
                "evidence to review."
            ),
            old_value="not reaching public symbols",
            new_value=f"reaches {n_syms} public symbol(s)",
            source_location=boundary,
        ))

    return findings
