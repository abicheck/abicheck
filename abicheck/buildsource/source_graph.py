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
  ``graph compare`` command for explanation and triage.

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
from collections import deque
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

#: L5 edge kinds that express a decl/type dependency (ADR-041 P0): a call, a
#: non-call reference to a global/constant, a parameter/field type, or a base
#: class. ``crosscheck.py``'s intra-version ``public_to_internal_dependency``
#: check and this module's version-over-version internal-dependency diff both
#: read exactly this set, so the two stay in lockstep on what "a public entity
#: reaches an internal one" means — a struct's private field type or base
#: class is exactly the "not a call at all" risk ADR-041 opens with.
DEPENDENCY_EDGE_KINDS: frozenset[str] = frozenset({
    "DECL_CALLS_DECL", "DECL_REFERENCES_DECL", "DECL_HAS_TYPE",
    "TYPE_HAS_FIELD_TYPE", "TYPE_INHERITS",
})


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
    #: Which named extractor passes ran to completion (``"call_graph"`` /
    #: ``"type_graph"``), independent of how many edges they produced (ADR-041
    #: P0 slice 2 follow-up, second Codex review). Edge *presence* alone cannot
    #: distinguish "the pass ran and found nothing" from "the pass never ran" —
    #: a project where no public struct happens to have a private field would
    #: look identical to one whose type-graph pass never executed, even though
    #: only the second is actually missing evidence. Set by
    #: ``inline._fold_call_graph``/``_fold_type_graph`` right after a
    #: successful extraction (regardless of edge count); absent/``False`` means
    #: "unknown whether it ran" (e.g. a hand-built or pre-slice-2 graph), so
    #: readers fall back to edge-presence inference for those.
    extractor_passes: dict[str, bool] = field(default_factory=dict)
    #: Which named extractor passes ran, but only over a *narrowed* scope
    #: (``changed_paths``/``scoped_units`` restricting ``_fold_call_graph``/
    #: ``_fold_type_graph`` to a subset of compile units — eleventh Codex
    #: review). A narrowed pass never sets ``extractor_passes`` for that name
    #: (it did not examine the whole project), but it still serializes
    #: whatever edges it *did* collect from the subset it saw. Those edges
    #: must not be treated as full-family coverage when compared against a
    #: side that ran a confirmed *full* pass — a baseline scoped to a few
    #: changed TUs having one ``TYPE_HAS_FIELD_TYPE`` edge says nothing about
    #: whether the rest of the project's dependencies were ever inspected, so
    #: comparing it as if that kind were fully covered lets unrelated,
    #: never-examined dependencies read as "newly added". Set alongside (in
    #: place of) ``extractor_passes`` by ``inline._fold_call_graph``/
    #: ``_fold_type_graph`` when the local ``narrowed`` flag is ``True``.
    narrowed_passes: dict[str, bool] = field(default_factory=dict)

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
        # A pass that ran but found zero edges is still "collected" (ADR-041 P0
        # slice 2 follow-up): edge presence alone reads identically to "the
        # pass never ran", which is the exact coverage-honesty gap
        # ``extractor_passes`` closes. Fall back to edge presence alone when
        # the flag is absent (a hand-built or pre-slice-2 graph).
        call_pass_ran = self.extractor_passes.get("call_graph", False)
        type_pass_ran = self.extractor_passes.get("type_graph", False)
        has_calls = call_pass_ran or any(e.kind == "DECL_CALLS_DECL" for e in self.edges)
        has_includes = any(e.kind == "COMPILE_UNIT_INCLUDES_FILE" for e in self.edges)
        #: ADR-041 P0: TYPE_INHERITS/TYPE_HAS_FIELD_TYPE/DECL_HAS_TYPE describe
        #: type-level dependencies; DECL_REFERENCES_DECL a non-call decl reference.
        #: Both come from ``type_graph.py`` (folded alongside the call graph) or an
        #: external backend (``graph_backends.py``), so "collected" is tracked
        #: separately from the call graph — a graph can have calls but no type
        #: edges (e.g. an older pack) and coverage must say so honestly.
        type_edge_kinds = ("TYPE_INHERITS", "TYPE_HAS_FIELD_TYPE", "DECL_HAS_TYPE")
        has_type_edges = type_pass_ran or any(e.kind in type_edge_kinds for e in self.edges)
        has_reference_edges = type_pass_ran or any(
            e.kind == "DECL_REFERENCES_DECL" for e in self.edges
        )
        self.coverage = {
            "targets": kinds.get("target", 0),
            "compile_units": kinds.get("compile_unit", 0),
            "source_decls": kinds.get("source_decl", 0),
            "binary_symbol_mappings": edge_kinds.get("SOURCE_DECL_MAPS_TO_SYMBOL", 0),
            "include_edges": {"collected": has_includes, "count": edge_kinds.get("COMPILE_UNIT_INCLUDES_FILE", 0)},
            "call_edges": {"collected": has_calls, "count": edge_kinds.get("DECL_CALLS_DECL", 0)},
            "type_edges": {
                "collected": has_type_edges,
                "count": sum(edge_kinds.get(k, 0) for k in type_edge_kinds),
            },
            "reference_edges": {
                "collected": has_reference_edges,
                "count": edge_kinds.get("DECL_REFERENCES_DECL", 0),
            },
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
            "extractor_passes": dict(self.extractor_passes),
            "narrowed_passes": dict(self.narrowed_passes),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SourceGraphSummary:
        # Defensive ``.get`` parsing so a newer/hand-edited summary never aborts
        # a load (evidence/CLAUDE.md forward-compat rule). ``indexes`` are derived
        # and intentionally not read back — they are recomputed from nodes/edges.
        # ``extractor_passes`` defaults to {} for a pre-slice-2 pack (additive
        # field, no schema_version bump needed — same "unknown edge kinds/
        # fields are ignored/defaulted" forward-compat rule as ADR-041 P0 slice 1).
        return cls(
            schema_version=int(d.get("schema_version", SOURCE_GRAPH_VERSION)),
            graph_id=str(d.get("graph_id", "")),
            nodes=[GraphNode.from_dict(n) for n in d.get("nodes", [])],
            edges=[GraphEdge.from_dict(e) for e in d.get("edges", [])],
            coverage=dict(d.get("coverage", {})),
            external_graph_refs=[dict(r) for r in d.get("external_graph_refs", [])],
            extractor_passes={
                str(k): bool(v) for k, v in dict(d.get("extractor_passes", {})).items()
            },
            narrowed_passes={
                str(k): bool(v) for k, v in dict(d.get("narrowed_passes", {})).items()
            },
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


#: Graph node kinds a type entity (as opposed to a function/variable
#: ``source_decl``) can carry. Mirrors ``crosscheck._DECL_NODE_KINDS`` minus
#: ``source_decl``.
_TYPE_ENTITY_KINDS: frozenset[str] = frozenset({"record_type", "enum_type", "typedef"})

#: Graph node kinds that carry a declaration/type visibility we can classify as
#: public or internal. Shared with ``crosscheck.py``'s intra-version
#: ``public_to_internal_dependency`` check (ADR-041 P0 slice 2, fourth Codex
#: review) so the two never classify a node differently.
DECL_NODE_KINDS: frozenset[str] = frozenset({"source_decl"}) | _TYPE_ENTITY_KINDS

#: Node visibilities that put an entity *on* the public source surface. Mirrors
#: ``source_link._is_public`` (which the L5 graph's ``visibility`` attr is
#: derived from): ``generated`` means a generated header **under the public
#: roots** — a public, consumer-visible entity — so it is NOT an internal
#: dependency.
PUBLIC_VISIBILITIES: frozenset[str] = frozenset({"public_header", "generated"})

#: Node visibilities that make an entity *internal* (not public surface): a
#: project-private header or an implementation ("source") file. System headers
#: are third-party (excluded), and ``generated`` is public (above).
INTERNAL_VISIBILITIES: frozenset[str] = frozenset({"private_header", "source"})

#: Visibilities that carry no provenance. The built-in call/type-graph
#: extractors create dependency-target nodes with **no** ``visibility`` attr
#: when the target isn't part of the linked L4 surface. Such a node is
#: internal *only when the project also declares it* (``decl_to_file``) or the
#: extractor marked it ``defined_in_project`` — caller/reference presence
#: alone is unsound (a third-party header-inline symbol whose body is reached
#: also appears as a dependency target), so a bare node with no project
#: provenance is treated as a third-party/system target and not flagged.
UNANNOTATED_VISIBILITIES: frozenset[str] = frozenset({"", "unknown"})

#: Mangled-name prefixes / substrings that mark a standard-library or
#: compiler-internal decl. The call/type graphs resolve targets into ``std::``/
#: ``__gnu_cxx``/cxxabi helpers, which carry no visibility either; without this
#: an unannotated stdlib target would be mis-read as a project-internal
#: dependency and a public API merely using ``std::`` would light up. Mirrors
#: the stdlib/compiler filtering the dumper already applies to exported
#: symbols.
_SYSTEM_NAME_PREFIXES = (
    "_ZSt", "_ZNSt", "_ZNKSt", "_ZNSa", "_ZN9__gnu_cxx", "_ZNK9__gnu_cxx",
    "_ZN6__cxxabiv", "_Znw", "_Zna", "_Zdl", "_Zda", "__",
)
_SYSTEM_NAME_SUBSTRINGS = ("std::", "__gnu_cxx::", "__cxxabiv")


def looks_like_system_name(name: str) -> bool:
    """Whether *name* is a standard-library / compiler-internal decl spelling."""
    if name.startswith(_SYSTEM_NAME_PREFIXES):
        return True
    return any(sub in name for sub in _SYSTEM_NAME_SUBSTRINGS)


def decl_declaring_files(graph: SourceGraphSummary) -> dict[str, str]:
    """Map each decl/type id to its declaring file via ``SOURCE_DECLARES`` edges."""
    node_by_id = {n.id: n for n in graph.nodes}
    decl_to_file: dict[str, str] = {}
    for e in graph.edges:
        if e.kind != "SOURCE_DECLARES":
            continue
        header = node_by_id.get(e.src)
        if header is not None and header.label:
            decl_to_file.setdefault(e.dst, header.label)
    return decl_to_file


def is_public_dependency_node(
    node_id: str, node_by_id: dict[str, GraphNode], exported_decls: set[str]
) -> bool:
    """Whether *node_id* is public: exported-symbol-mapped or public-header visible.

    Shared with ``crosscheck.py``'s ``_is_public_decl`` (ADR-041 P0 slice 2).
    """
    if node_id in exported_decls:
        return True
    node = node_by_id.get(node_id)
    if node is None or node.kind not in DECL_NODE_KINDS:
        return False
    return str(node.attrs.get("visibility", "")) in PUBLIC_VISIBILITIES


def is_internal_dependency_node(
    node_id: str,
    node_by_id: dict[str, GraphNode],
    exported_decls: set[str],
    decl_to_file: dict[str, str],
) -> bool:
    """Whether *node_id* is a project-internal decl/type consumers cannot see.

    "Not declared by a public header" alone is not internal — a third-party or
    standard-library type used as a field/parameter type is *also* not
    declared by any project header, and must not be conflated with a genuinely
    private project entity (ADR-041 P0 slice 2, fourth Codex review). Requires
    positive evidence instead: an explicit ``private_header``/``source``
    visibility, or — for an unannotated node — project-file provenance
    (``decl_to_file``/``defined_in_project``) plus a non-system-looking name.
    Shared with ``crosscheck.py``'s ``_is_internal_decl`` (same algorithm, same
    source of truth) so the intra-version and inter-version checks classify a
    node identically.
    """
    node = node_by_id.get(node_id)
    if node is None or node.kind not in DECL_NODE_KINDS:
        return False
    if node_id in exported_decls:
        return False
    vis = str(node.attrs.get("visibility", ""))
    if vis in INTERNAL_VISIBILITIES:
        return True
    if vis in UNANNOTATED_VISIBILITIES:
        has_provenance = node_id in decl_to_file or bool(node.attrs.get("defined_in_project"))
        if not has_provenance:
            return False
        return not looks_like_system_name(node.label or "")
    return False


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
    foundation the ``graph compare`` command renders and that a later phase maps
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
    """Localize an exported symbol through the graph (ADR-031 D8 `graph explain`).

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


def _public_types(graph: SourceGraphSummary) -> set[str]:
    """Type (``record_type``/``enum_type``/``typedef``) ids that are genuinely public.

    The type-level analogue of :func:`_public_decls` — but "declared by a
    ``header``-kind node" alone is not enough (sixth Codex review):
    ``_augment_with_source_abi``'s ``header_declares`` creates a ``header``
    node for *every* declaring file regardless of whether it is a public or a
    private-project header — privacy lives on the type's own ``visibility``
    attr (from ``ent.visibility``), not the node kind. Without the visibility
    check, a private type is treated as a dependency-closure *entry*
    (:func:`_dependency_reachability`), so a private type that gains a private
    field/base of its own could wrongly emit ``PUBLIC_API_INTERNAL_DEPENDENCY_ADDED``
    even though no public API is involved.
    """
    kinds = _kind_map(graph)
    node_by_id = {n.id: n for n in graph.nodes}
    out: set[str] = set()
    for e in graph.edges:
        if e.kind != "SOURCE_DECLARES":
            continue
        if kinds.get(e.src) != "header" or kinds.get(e.dst) not in _TYPE_ENTITY_KINDS:
            continue
        node = node_by_id.get(e.dst)
        if node is not None and str(node.attrs.get("visibility", "")) in PUBLIC_VISIBILITIES:
            out.add(e.dst)
    return out


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


def _dependency_reachability(
    graph: SourceGraphSummary, edge_kinds: frozenset[str]
) -> dict[str, frozenset[str]]:
    """For each public entry (exported decl or public type), what it reaches.

    Generalizes :func:`_public_entry_call_reachability` from ``DECL_CALLS_DECL``
    alone to *edge_kinds* (normally :data:`DEPENDENCY_EDGE_KINDS`, or the
    old/new-common subset a version diff must restrict to — see
    :func:`_common_dependency_edge_kinds`): a public struct's private base class
    (``TYPE_INHERITS``) or private field type (``TYPE_HAS_FIELD_TYPE``), a
    function's private parameter type (``DECL_HAS_TYPE``), and a body reading a
    private constant (``DECL_REFERENCES_DECL``) are exactly the "not a call at
    all" risks ADR-041 opens with — a call-only closure never sees them.

    Entries are every node :func:`is_public_dependency_node` accepts: a decl
    backing an exported symbol (``SOURCE_DECL_MAPS_TO_SYMBOL``), *or* any
    decl/type node with public-header visibility — not exported-symbol-backed
    decls alone (tenth Codex review). A public inline/template/constexpr
    function or a public variable declared in a public header commonly has no
    exported binary symbol of its own (inlined at every call site, or never
    emitted standalone), so restricting entries to
    ``SOURCE_DECL_MAPS_TO_SYMBOL`` missed exactly the ADR's own headline
    example — ``inline int f() { return detail::SECRET; }`` — whenever ``f``
    isn't separately exported. ``crosscheck.py``'s intra-version check already
    treats a ``visibility="public_header"`` decl as public
    (``is_public_dependency_node``, shared since the fourth review); this
    closure now uses the identical rule, so a public type is no longer a
    special case (:func:`_public_types` is unused here now — public-header
    visibility already covers it).
    Returns ``{}`` when *edge_kinds* is empty or the graph carries none of them,
    so callers can skip the comparison entirely.
    """
    adjacency: dict[str, list[str]] = {}
    for e in graph.edges:
        if e.kind in edge_kinds:
            adjacency.setdefault(e.src, []).append(e.dst)
    if not adjacency:
        return {}
    exported_decls = {
        e.src for e in graph.edges if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"
    }
    node_by_id = {n.id: n for n in graph.nodes}
    entries = {
        n.id for n in graph.nodes
        if is_public_dependency_node(n.id, node_by_id, exported_decls)
    }
    out: dict[str, frozenset[str]] = {}
    for entry in entries:
        seen: set[str] = set()
        stack = list(adjacency.get(entry, []))
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(adjacency.get(node, []))
        out[entry] = frozenset(seen)
    return out


def _dependency_path(
    graph: SourceGraphSummary, edge_kinds: frozenset[str], entry: str, target: str
) -> list[GraphEdge] | None:
    """One concrete shortest edge chain from *entry* to *target* over *edge_kinds*.

    ADR-041 P0 roadmap item 3 ("graph explain proof path"): a reachability
    fact (:func:`_dependency_reachability` already proved *target* is
    reachable from *entry*) is a bare assertion until a reader can see *how* —
    the actual edge-by-edge chain, not just an endpoint list. BFS over the
    same *edge_kinds* adjacency, tracking one predecessor edge per node so the
    chain can be reconstructed once *target* is reached (shortest, not every
    path — one witness is enough to explain a finding). Returns ``[]`` when
    *entry* == *target*, or ``None`` if no such path exists (defensive; should
    not happen for a (entry, target) pair sourced from
    :func:`_dependency_reachability`'s own output).
    """
    if entry == target:
        return []
    adjacency: dict[str, list[GraphEdge]] = {}
    for e in graph.edges:
        if e.kind in edge_kinds:
            adjacency.setdefault(e.src, []).append(e)
    visited = {entry}
    queue: deque[str] = deque([entry])
    came_from: dict[str, GraphEdge] = {}
    while queue:
        node = queue.popleft()
        if node == target:
            break
        for e in adjacency.get(node, []):
            if e.dst in visited:
                continue
            visited.add(e.dst)
            came_from[e.dst] = e
            queue.append(e.dst)
    if target not in came_from:
        return None
    path: list[GraphEdge] = []
    cur = target
    while cur != entry:
        e = came_from[cur]
        path.append(e)
        cur = e.src
    path.reverse()
    return path


def _format_dependency_path(graph: SourceGraphSummary, path: list[GraphEdge]) -> str:
    """Render a :func:`_dependency_path` result as a human-readable chain.

    E.g. ``pub() --[DECL_CALLS_DECL]--> helper() --[DECL_HAS_TYPE]--> detail::Impl``.
    Returns ``""`` for an empty path (entry == target).
    """
    if not path:
        return ""
    labels = _label_map(graph)
    parts = [labels.get(path[0].src, path[0].src)]
    for e in path:
        parts.append(f"--[{e.kind}]--> {labels.get(e.dst, e.dst)}")
    return " ".join(parts)


#: Dependency edge kinds grouped by the single extractor pass that emits them
#: together, keyed by the same pass name ``inline._fold_call_graph`` /
#: ``inline._fold_type_graph`` stamp onto ``SourceGraphSummary.extractor_passes``
#: (each is one AST walk). Coverage must be judged at this pass granularity,
#: not per exact edge kind (second Codex review): ``type_graph.
#: augment_graph_with_types`` folds all four type/reference kinds from one
#: pass, so a baseline that already has (say) a ``DECL_HAS_TYPE`` edge but
#: never happened to have a ``TYPE_HAS_FIELD_TYPE`` one ran the *same* pass as
#: a new side that has both — the first ``TYPE_HAS_FIELD_TYPE`` edge there is
#: a real new dependency, not a collector-coverage artifact, and must not be
#: dropped just because that exact kind is new.
_DEPENDENCY_EDGE_FAMILIES: dict[str, frozenset[str]] = {
    "call_graph": frozenset({"DECL_CALLS_DECL"}),
    "type_graph": frozenset({
        "DECL_REFERENCES_DECL", "DECL_HAS_TYPE", "TYPE_HAS_FIELD_TYPE", "TYPE_INHERITS",
    }),
}


def _dependency_kinds_covered(graph: SourceGraphSummary, edge_kinds: frozenset[str]) -> bool:
    """Whether *graph* has evidence for any kind in *edge_kinds*: an edge, or its
    extractor pass recorded as having run (:data:`_DEPENDENCY_EDGE_FAMILIES`).

    A pass can run to completion and still emit zero edges of its family (e.g.
    no public struct anywhere had a private field yet), which reads identically
    to "the pass never ran" if edge presence is the only signal (third Codex
    review). ``SourceGraphSummary.extractor_passes`` (set by
    ``inline._fold_call_graph``/``_fold_type_graph`` right after a successful
    extraction, regardless of edge count) breaks that tie; absent that record
    (a hand-built or pre-slice-2 graph) this falls back to edge presence alone.
    """
    if any(e.kind in edge_kinds for e in graph.edges):
        return True
    return any(
        graph.extractor_passes.get(pass_name, False) and (family & edge_kinds)
        for pass_name, family in _DEPENDENCY_EDGE_FAMILIES.items()
    )


def _common_dependency_edge_kinds(
    old: SourceGraphSummary, new: SourceGraphSummary
) -> frozenset[str]:
    """Dependency edge kinds whose *extractor pass* ran on both sides (Codex review).

    A collector improvement — e.g. the ADR-041 P0 type-graph pass running for
    the first time on the *new* side while the baseline only ever ran the call
    graph — must not read as a newly-added dependency: a single "any dependency
    edge present" gate (as the call-only closure could get away with) lets
    every target reachable *only* through a kind absent from the other side
    look newly internal, when it is really a coverage artifact, not a code
    change.

    Widening credit from one kind to its whole family (:data:`_DEPENDENCY_EDGE_FAMILIES`)
    is only sound when both sides *confirm* the same uniform extractor pass ran
    (``extractor_passes``) — that pass always examines every kind in its
    family together, so one kind's absence there really is "found nothing," not
    missing coverage. Without that confirmation, widening from mere edge
    *presence* is unsound (fifth Codex review): a Kythe/CodeQL-ingested pack
    (``graph_backends.py``) only ever produces `DECL_REFERENCES_DECL` for a
    non-call ref, never the Clang type graph's other three kinds, so a single
    such edge is not evidence that a base-class or field-type check ever ran.

    Falls back to a *per-kind* check in that case — but a confirmed pass on
    only *one* side still counts as evidence for that side, for the exact
    kinds the other side has edges of (ninth Codex review): a mixed-format
    comparison — e.g. an old pack that ran the type-graph pass and confirmed
    zero type edges, against a pre-slice-2 new pack with no pass marker but a
    first `TYPE_HAS_FIELD_TYPE` edge — must not skip just because *both*
    markers aren't present. A kind is common when each side either has an
    edge of that exact kind, or has confirmed its family's pass ran (a
    confirmed pass's *absence* of a kind is a real, verified zero) — never
    widened to a *sibling* kind neither side actually exhibits an edge of.

    A side whose pass ran *narrowed* (``narrowed_passes``, e.g. a PR/``--since``
    scan folding only the changed compile units) never sets ``extractor_passes``
    for that name, so it always falls to the per-kind branch above — but its
    edges are only representative of the narrow subset it actually walked, not
    the whole project. This function only ever feeds an *additions* closure
    (:func:`_internal_dependency_findings` computes newly-reachable targets in
    ``new`` that were absent from ``old``'s reach), so the false-positive risk
    is one-directional: it lives entirely in whether **``old``'s absence** of a
    kind is trustworthy evidence the dependency truly did not exist before, not
    in ``new``'s own scope. A narrowed **old** side's edge of a given kind must
    not count as coverage for that kind unless ``new`` is narrowed the same way
    (eleventh/twelfth Codex review): a baseline scoped to a few changed TUs
    having one ``TYPE_HAS_FIELD_TYPE`` edge from that subset says nothing about
    dependencies elsewhere in the project — whether the other side is a
    confirmed *full* pass that saw the rest of the project (eleventh review),
    or simply carries no pass marker at all, e.g. a pre-slice-2/externally-
    ingested pack whose true scope is unknown (twelfth review: "the other side
    lacks a full-pass bit" is not evidence it was equally narrow). Only
    symmetric narrowing — both sides scoped the same way, e.g. the common
    PR-diff workflow comparing two runs narrowed to the same changed TUs — is
    trusted to leave the pre-existing per-kind comparison unaffected.

    A narrowed **new** side's edge needs no such guard (thirteenth Codex
    review): whatever ``new`` observed in the TUs it did walk is real evidence
    of a genuinely new dependency there whenever ``old``'s own evidence for
    that kind is trustworthy (a confirmed full pass, or a matching narrow
    scope) — ``new`` being narrower than ``old`` can only ever cause a *missed*
    addition (an accepted false negative outside the TUs it examined), never a
    false positive, so gating ``new``'s presence on its own narrowing (as an
    earlier revision of this fix did, symmetrically with ``old``) wrongly
    dropped real additions a fully-covered ``old`` baseline had already proven
    absent everywhere.
    """
    common: set[str] = set()
    for pass_name, family in _DEPENDENCY_EDGE_FAMILIES.items():
        old_pass = old.extractor_passes.get(pass_name, False)
        new_pass = new.extractor_passes.get(pass_name, False)
        if old_pass and new_pass:
            common |= family
            continue
        old_narrowed = old.narrowed_passes.get(pass_name, False)
        new_narrowed = new.narrowed_passes.get(pass_name, False)
        old_kinds = {e.kind for e in old.edges if e.kind in family}
        new_kinds = {e.kind for e in new.edges if e.kind in family}
        for kind in family:
            # Only OLD's negative evidence needs the narrowing guard — see the
            # docstring's one-directional-risk note (thirteenth Codex review).
            old_present = (kind in old_kinds) and not (old_narrowed and not new_narrowed)
            new_present = kind in new_kinds
            old_has = old_present or old_pass
            new_has = new_present or new_pass
            if old_has and new_has:
                common.add(kind)
    return frozenset(common)


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


def _public_entry_internal_reach(
    graph: SourceGraphSummary, edge_kinds: frozenset[str]
) -> set[tuple[str, str]]:
    """``(public_entry, internal_target)`` pairs the entry reaches via a dependency edge.

    An *internal* target is a decl/type node reachable from a public entry
    (exported decl or public type) via the *edge_kinds* closure
    (:func:`_dependency_reachability` — the version diff passes
    :func:`_common_dependency_edge_kinds` here, not the full
    :data:`DEPENDENCY_EDGE_KINDS`) with positive internal provenance
    (:func:`is_internal_dependency_node`) — "not declared by a public header"
    alone is not internal, or a third-party/stdlib type used as a field/
    parameter type would wrongly light up (ADR-041 P0 slice 2, fourth Codex
    review). This covers calls, non-call references, and the field/base/
    parameter type edges ADR-041 P0 added, not calls alone. Returns ``set()``
    when *edge_kinds* is empty, the graph carries none of them, or there is no
    public closure at all, so the version diff skips rather than flagging
    noise on an evidence-poor side.
    """
    reach = _dependency_reachability(graph, edge_kinds)
    if not reach:
        return set()
    if not (_public_decls(graph) or _public_types(graph)):
        return set()
    node_by_id = {n.id: n for n in graph.nodes}
    exported_decls = {
        e.src for e in graph.edges if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"
    }
    decl_to_file = decl_declaring_files(graph)
    out: set[tuple[str, str]] = set()
    for entry, reachable in reach.items():
        for target in reachable:
            if is_internal_dependency_node(target, node_by_id, exported_decls, decl_to_file):
                out.add((entry, target))
    return out


def _target_dependency_edges(graph: SourceGraphSummary) -> set[tuple[str, str]]:
    """``(target, dependency_target)`` pairs from ``TARGET_DEPENDS_ON``."""
    return {
        (e.src, e.dst)
        for e in graph.edges
        if e.kind == "TARGET_DEPENDS_ON"
    }


#: Node kinds that represent a declaring file (the graph builder emits
#: ``SOURCE_DECLARES`` from a ``header`` node — labelled with the declaration's
#: ``source_location`` path, whose ``origin`` attr says whether it is a header
#: or a source file — so accepting only ``source`` nodes would leave the owner
#: map empty on every real graph (Codex review).
_DECLARING_FILE_KINDS: frozenset[str] = frozenset({"source", "header", "generated_file"})


def _symbol_owner_source(graph: SourceGraphSummary) -> dict[str, str]:
    """Map each exported ``binary_symbol`` id → the file that declares it.

    The owner is the file node that ``SOURCE_DECLARES`` the ``source_decl`` which
    ``SOURCE_DECL_MAPS_TO_SYMBOL`` the symbol. Production graphs attach that edge
    from a ``header`` node (``build_source_graph``/``header_declares``), so any
    declaring-file node kind counts (:data:`_DECLARING_FILE_KINDS`), keyed to the
    file's node id. A symbol with no unambiguous single declaring file is omitted,
    so the version diff never guesses.
    """
    kinds = _kind_map(graph)
    symbol_to_decls: dict[str, list[str]] = {}
    for e in graph.edges:
        if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL":
            symbol_to_decls.setdefault(e.dst, []).append(e.src)
    decl_to_files: dict[str, list[str]] = {}
    for e in graph.edges:
        if e.kind == "SOURCE_DECLARES" and kinds.get(e.src) in _DECLARING_FILE_KINDS:
            decl_to_files.setdefault(e.dst, []).append(e.src)
    out: dict[str, str] = {}
    for symbol, decls in symbol_to_decls.items():
        owners = {src for decl in decls for src in decl_to_files.get(decl, [])}
        if len(owners) == 1:
            out[symbol] = next(iter(owners))
    return out


def _mapping_drift_findings(
    old: SourceGraphSummary,
    new: SourceGraphSummary,
    old_labels: dict[str, str],
    new_labels: dict[str, str],
    boundary: str,
) -> list[Change]:
    """Source↔binary mapping drift for declarations present in both graphs."""
    from ..checker_policy import ChangeKind
    from ..checker_types import Change

    findings: list[Change] = []
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
    return findings


def _public_reachability_findings(
    old: SourceGraphSummary,
    new: SourceGraphSummary,
    old_labels: dict[str, str],
    new_labels: dict[str, str],
    boundary: str,
) -> list[Change]:
    """Public-reachability closure changes (declarations entering/leaving it)."""
    from ..checker_policy import ChangeKind
    from ..checker_types import Change

    findings: list[Change] = []
    # Only when both sides have a closure — an empty baseline would otherwise
    # flag every declaration.
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
    return findings


def _generated_public_closure_findings(
    old: SourceGraphSummary,
    new: SourceGraphSummary,
    new_labels: dict[str, str],
    boundary: str,
) -> list[Change]:
    """Generated files that newly entered the public declaration closure."""
    from ..checker_policy import ChangeKind
    from ..checker_types import Change

    findings: list[Change] = []
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
    return findings


def _call_reachability_findings(
    old: SourceGraphSummary,
    new: SourceGraphSummary,
    new_labels: dict[str, str],
    boundary: str,
) -> list[Change]:
    """Implementation reachable from an exported entry changed (phase 6).

    Per ADR-041 P0 roadmap item 3 ("graph explain proof path"), the
    description names one concrete example call chain (:func:`_dependency_path`
    restricted to ``DECL_CALLS_DECL``) into a newly-reachable (or, if none was
    added, a newly-unreachable) callee, not just the before/after counts.
    """
    from ..checker_policy import ChangeKind
    from ..checker_types import Change

    findings: list[Change] = []
    # Needs Clang call edges. Quality signal only — reported for entries
    # present in both graphs whose approximate call-reachable set differs.
    old_reach = _public_entry_call_reachability(old)
    new_reach = _public_entry_call_reachability(new)
    call_kinds = frozenset({"DECL_CALLS_DECL"})
    for entry in sorted(old_reach.keys() & new_reach.keys()):
        if old_reach[entry] != new_reach[entry]:
            label = new_labels.get(entry, entry)
            old_n, new_n = len(old_reach[entry]), len(new_reach[entry])
            added = sorted(new_reach[entry] - old_reach[entry])
            example = ""
            for target in added:
                path = _dependency_path(new, call_kinds, entry, target)
                if path:
                    example = f" Example newly-reachable path: {_format_dependency_path(new, path)}."
                    break
            findings.append(Change(
                kind=ChangeKind.CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED,
                symbol=label,
                description=(
                    f"Implementation statically reachable from exported entry "
                    f"{label!r} changed ({old_n} → {new_n} known static callees, "
                    "approximate). Source-graph quality signal: the code behind a "
                    "stable public symbol moved; not an ABI break." + example
                ),
                old_value=f"{old_n} reachable",
                new_value=f"{new_n} reachable",
                source_location=boundary,
            ))
    return findings


def _include_graph_drift_findings(
    old: SourceGraphSummary,
    new: SourceGraphSummary,
    old_labels: dict[str, str],
    new_labels: dict[str, str],
    boundary: str,
) -> list[Change]:
    """Public headers entering/leaving the compiled include graph."""
    from ..checker_policy import ChangeKind
    from ..checker_types import Change

    findings: list[Change] = []
    # Needs COMPILE_UNIT_INCLUDES_FILE edges from a depfile/-M include extractor.
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
    return findings


def _build_option_reach_findings(
    old: SourceGraphSummary,
    new: SourceGraphSummary,
    new_labels: dict[str, str],
    boundary: str,
) -> list[Change]:
    """A changed ABI-relevant build option that now reaches a public symbol."""
    from ..checker_policy import ChangeKind
    from ..checker_types import Change

    findings: list[Change] = []
    # Added BUILD_OPTION_AFFECTS_SYMBOL edges, grouped by option.
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


def _has_internal_reach_coverage(g: SourceGraphSummary, edge_kinds: frozenset[str]) -> bool:
    """Whether a graph carries evidence for *edge_kinds* (:func:`_dependency_kinds_covered`)
    and a public closure."""
    return _dependency_kinds_covered(g, edge_kinds) and bool(_public_decls(g) or _public_types(g))


#: source_diff.py findings whose old/new value is literally a body_hash or
#: type_hash (ADR-041 P0 roadmap item 2) — the narrow subset of the nine
#: source-replay findings that prove a *public* decl's own implementation
#: changed, as opposed to e.g. a default-argument or macro-value change.
_BODY_OR_TYPE_HASH_CHANGE_KINDS = frozenset({
    "inline_body_changed", "template_body_changed", "public_typedef_target_changed",
})


def _public_decl_source_changes(
    source_diff_changes: list[Change] | None,
) -> dict[str, Change]:
    """Map a public decl's ``symbol`` (qualified name) to its own body/type-hash
    change (:data:`_BODY_OR_TYPE_HASH_CHANGE_KINDS`), from ``source_diff.diff_source_abi``'s
    output — the L4 half of ADR-041 P0 roadmap item 2's correlation.
    """
    if not source_diff_changes:
        return {}
    return {
        c.symbol: c
        for c in source_diff_changes
        if c.symbol and c.kind.value in _BODY_OR_TYPE_HASH_CHANGE_KINDS
    }


def _internal_dependency_findings(
    old: SourceGraphSummary,
    new: SourceGraphSummary,
    new_labels: dict[str, str],
    boundary: str,
    source_diff_changes: list[Change] | None = None,
) -> list[Change]:
    """A public entry that newly reaches an internal declaration/type.

    "Reaches" spans the ADR-041 P0 dependency-edge family
    (:data:`DEPENDENCY_EDGE_KINDS`): a call, a non-call reference to a
    global/constant, or a field/base/parameter type — a public struct that
    gained a private field type is caught here exactly like a function that
    gained a call into internal code. Per ADR-041 P0 roadmap item 3 ("graph
    explain proof path"), the description names the concrete edge chain
    (:func:`_dependency_path`) proving each dependency, not just the endpoints.

    Per ADR-041 P0 roadmap item 2, when ``source_diff_changes`` is supplied
    (the L4 ``source_diff.diff_source_abi`` findings for the same version
    pair) and the same public entry *also* has its own body/type_hash changed
    this version (:func:`_public_decl_source_changes`), the description notes
    it — correlating "X's own implementation changed" with "X now reaches
    internal Y" into one finding instead of two disjoint ones a reader has to
    connect manually.
    """
    from ..checker_policy import ChangeKind
    from ..checker_types import Change

    own_changes = _public_decl_source_changes(source_diff_changes)
    findings: list[Change] = []
    # The version-over-version analogue of the intra-version
    # public-to-internal cross-check. Restrict the closure to edge kinds
    # collected on *both* sides (_common_dependency_edge_kinds) — otherwise a
    # collector improvement (e.g. the type-graph pass running for the first
    # time on the new side) would make every target newly reachable only
    # through that new kind look like a newly-added dependency, when it is
    # really a coverage artifact (Codex review). Then gate on *both* graphs
    # carrying at least one common-kind edge AND a public closure
    # (SOURCE_DECLARES), so an evidence-poor baseline (dependency edges but no
    # public closure, or no semantic pass at all) cannot make every
    # pre-existing internal dependency look newly added (earlier Codex review).
    common_kinds = _common_dependency_edge_kinds(old, new)
    if _has_internal_reach_coverage(old, common_kinds) and _has_internal_reach_coverage(
        new, common_kinds
    ):
        new_internal = _public_entry_internal_reach(new, common_kinds)
        # Exclude a pair whose *edge* already existed in the old graph, even if
        # the old side never classified its target as internal (eighth Codex
        # review): a Kythe/older-pack target with no SOURCE_DECLARES/
        # defined_in_project provenance is unclassifiable there, so
        # _public_entry_internal_reach(old, ...) silently drops it — but the
        # dependency itself is not new, only the classification evidence
        # improved. Raw reachability (ignoring classification) is the
        # authority on whether the edge is new.
        old_reach = _dependency_reachability(old, common_kinds)
        newly_internal = {
            (entry, target)
            for entry, target in new_internal
            if target not in old_reach.get(entry, frozenset())
        }
    else:
        newly_internal = set()
    reached_by_entry: dict[str, list[str]] = {}
    for entry, target in newly_internal:
        reached_by_entry.setdefault(entry, []).append(target)
    for entry in sorted(reached_by_entry):
        label = new_labels.get(entry, entry)
        raw_targets = sorted(reached_by_entry[entry])
        targets = [new_labels.get(t, t) for t in raw_targets]
        proof_paths = [
            _format_dependency_path(new, path)
            for t in raw_targets
            if (path := _dependency_path(new, common_kinds, entry, t))
        ]
        proof = f" Proof path(s): {'; '.join(proof_paths)}." if proof_paths else ""
        own_change = own_changes.get(label)
        correlation = (
            f" This entry's own implementation also changed this version "
            f"({own_change.kind.value}: {own_change.old_value!r} → "
            f"{own_change.new_value!r}) — likely the source of the new dependency."
            if own_change is not None
            else ""
        )
        findings.append(Change(
            kind=ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED,
            symbol=label,
            description=(
                f"Public entry {label!r} now reaches internal (non-public) "
                f"declaration(s)/type(s) {', '.join(sorted(targets))} that it did not "
                "before (via a call, reference, or field/base/parameter type). "
                "The public surface has taken on an undeclared dependency; a "
                "change to that internal entity becomes a hidden risk. "
                "Source-graph evidence to review." + proof + correlation
            ),
            old_value="no internal dependency",
            new_value=f"reaches {len(targets)} internal decl(s)/type(s)",
            source_location=boundary,
        ))
    return findings


def _target_dependency_findings(
    old: SourceGraphSummary,
    new: SourceGraphSummary,
    new_labels: dict[str, str],
    boundary: str,
) -> list[Change]:
    """A new inter-target build/link dependency (added TARGET_DEPENDS_ON edge)."""
    from ..checker_policy import ChangeKind
    from ..checker_types import Change

    findings: list[Change] = []
    added_target_deps = _target_dependency_edges(new) - _target_dependency_edges(old)
    for target, dep in sorted(added_target_deps):
        tlabel = new_labels.get(target, target)
        dlabel = new_labels.get(dep, dep)
        findings.append(Change(
            kind=ChangeKind.TARGET_DEPENDENCY_ADDED,
            symbol=tlabel,
            description=(
                f"Target {tlabel!r} gained a build/link dependency on {dlabel!r}. "
                "The shipped artifact may now require an additional library at "
                "load time and takes on that dependency's ABI transitively. "
                "Source-graph evidence to review; the DT_NEEDED diff proves any "
                "concrete new load-time dependency."
            ),
            old_value="no dependency",
            new_value=dlabel,
            source_location=boundary,
        ))
    return findings


def _symbol_owner_findings(
    old: SourceGraphSummary,
    new: SourceGraphSummary,
    old_labels: dict[str, str],
    new_labels: dict[str, str],
    boundary: str,
) -> list[Change]:
    """An exported symbol whose *declaring* file moved between versions."""
    from ..checker_policy import ChangeKind
    from ..checker_types import Change

    findings: list[Change] = []
    # The symbol's public declaration relocated to a different header / source
    # file although its name/signature are unchanged. NB: this is the
    # declaration owner, not the definition TU — the call-graph `def_file`
    # provenance cannot be used here because add_node is first-writer-wins and
    # the exported decl node is always created by the source-ABI pass before
    # the call-graph augmentation, so its def_file attr is dropped (Codex
    # review).
    old_owner, new_owner = _symbol_owner_source(old), _symbol_owner_source(new)
    for symbol in sorted(set(old_owner) & set(new_owner)):
        if old_owner[symbol] != new_owner[symbol]:
            label = new_labels.get(symbol, symbol)
            findings.append(Change(
                kind=ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED,
                symbol=label,
                description=(
                    f"Exported symbol {label!r} is now declared by a different "
                    f"file ({old_labels.get(old_owner[symbol], old_owner[symbol])} "
                    f"→ {new_labels.get(new_owner[symbol], new_owner[symbol])}). The "
                    "name and signature are unchanged, so the artifact diff is "
                    "quiet, but the file owning the declaration moved — review for "
                    "include-path, inlining, or ODR effects. Source-graph evidence."
                ),
                old_value=old_labels.get(old_owner[symbol], old_owner[symbol]),
                new_value=new_labels.get(new_owner[symbol], new_owner[symbol]),
                source_location=boundary,
            ))
    return findings


def diff_source_graph_findings(
    old: SourceGraphSummary,
    new: SourceGraphSummary,
    source_diff_changes: list[Change] | None = None,
) -> list[Change]:
    """Map the graph delta onto ADR-031 D6 secondary risk findings.

    Aggregates the per-family helpers below, each producing RISK-tier
    ``ChangeKind``s stamped with the ``[L5_SOURCE_GRAPH]`` evidence boundary so
    they read as graph-derived, not an artifact diff:

    - ``SOURCE_TO_BINARY_MAPPING_CHANGED`` (:func:`_mapping_drift_findings`);
    - ``PUBLIC_REACHABILITY_CHANGED`` (:func:`_public_reachability_findings`);
    - ``GENERATED_HEADER_REACHES_PUBLIC_API``
      (:func:`_generated_public_closure_findings`);
    - ``CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED``
      (:func:`_call_reachability_findings`);
    - ``INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT``
      (:func:`_include_graph_drift_findings`);
    - ``BUILD_OPTION_REACHES_PUBLIC_SYMBOL``
      (:func:`_build_option_reach_findings`);
    - ``PUBLIC_API_INTERNAL_DEPENDENCY_ADDED``
      (:func:`_internal_dependency_findings`);
    - ``TARGET_DEPENDENCY_ADDED`` (:func:`_target_dependency_findings`);
    - ``EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED``
      (:func:`_symbol_owner_findings`).

    Per ADR-028 D3 / ADR-031 D6 these explain and prioritize; the caller folds
    them into the verdict pipeline as ordinary RISK changes that never override
    an artifact-proven break.

    ``source_diff_changes`` is the optional L4 ``source_diff.diff_source_abi``
    finding list for the same version pair (ADR-041 P0 roadmap item 2) — when
    supplied, ``_internal_dependency_findings`` correlates a public entry's own
    body/type_hash change with it newly reaching an internal dependency,
    instead of leaving a reader to connect the two disjoint findings.
    Omitted (``None``) by callers with no L4 surface diff (e.g. `graph diff`),
    which get the uncorrelated description exactly as before.
    """
    boundary = f"[{EVIDENCE_TIER_L5}]"
    old_labels, new_labels = _label_map(old), _label_map(new)

    findings: list[Change] = []
    findings += _mapping_drift_findings(old, new, old_labels, new_labels, boundary)
    findings += _public_reachability_findings(old, new, old_labels, new_labels, boundary)
    findings += _generated_public_closure_findings(old, new, new_labels, boundary)
    findings += _call_reachability_findings(old, new, new_labels, boundary)
    findings += _include_graph_drift_findings(old, new, old_labels, new_labels, boundary)
    findings += _build_option_reach_findings(old, new, new_labels, boundary)
    findings += _internal_dependency_findings(
        old, new, new_labels, boundary, source_diff_changes
    )
    findings += _target_dependency_findings(old, new, new_labels, boundary)
    findings += _symbol_owner_findings(old, new, old_labels, new_labels, boundary)
    return findings
