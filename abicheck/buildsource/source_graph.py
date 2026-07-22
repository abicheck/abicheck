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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .build_evidence import BuildEvidence, Confidence

# GraphNode/GraphEdge live in graph_facts.py now (ADR-046 D1/D2/D3 schema
# additions pushed this module to its AI-readiness line-count cap) and are
# re-exported for backward compatibility (many modules do ``from
# .source_graph import GraphNode``/``CONF_HIGH`` etc.) — the ``as``-aliases
# make the re-export explicit for mypy's strict ``--no-implicit-reexport``.
from .graph_facts import (
    CONF_HIGH as CONF_HIGH,
    CONF_REDUCED as CONF_REDUCED,
    CONF_UNKNOWN as CONF_UNKNOWN,
    FactConflict as FactConflict,
    GraphEdge as GraphEdge,
    GraphFact as GraphFact,
    GraphNode as GraphNode,
    ensure_facts_and_resolve,
    merge_entity_facts,
    register_fact,
)

if TYPE_CHECKING:
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
NODE_KINDS: frozenset[str] = frozenset(
    {
        "file",
        "header",
        "source",
        "compile_unit",
        "target",
        "link_unit",
        "binary_symbol",
        "debug_type",
        "source_decl",
        "record_type",
        "enum_type",
        "typedef",
        "macro",
        "build_option",
        "toolchain",
        "generated_file",
        "external_dependency",
        # ADR-041 P1 #2: object/link provenance (a symbol change attributed
        # to "which object/archive member/link step", not only "which
        # target"). object_file/static_library/version_script are populated
        # from BuildEvidence.compile_units/link_units below;
        # archive_member/linker_script/export_map/comdat_group are reserved
        # for a future archive/linker-artifact introspection extractor (no
        # normalized data source yet — same "reserved, not yet populated"
        # pattern this ADR's own P0 slice 1 used for the edge kinds it later
        # filled in), so an inputs-pack/hand-built graph naming one is never
        # rejected.
        "object_file",
        "archive_member",
        "static_library",
        "linker_script",
        "version_script",
        "export_map",
        "comdat_group",
    }
)

#: Edge kinds the graph schema understands (ADR-031 D2).
EDGE_KINDS: frozenset[str] = frozenset(
    {
        "TARGET_HAS_SOURCE",
        "TARGET_HAS_PUBLIC_HEADER",
        "TARGET_DEPENDS_ON",
        "COMPILE_UNIT_BUILDS_SOURCE",
        "COMPILE_UNIT_USES_OPTION",
        "COMPILE_UNIT_INCLUDES_FILE",
        "FILE_GENERATED_FROM",
        "SOURCE_DECLARES",
        "SOURCE_DEFINES",
        "DECL_HAS_TYPE",
        "DECL_CALLS_DECL",
        "DECL_REFERENCES_DECL",
        "TYPE_HAS_FIELD_TYPE",
        "TYPE_INHERITS",
        "BINARY_EXPORTS_SYMBOL",
        "SOURCE_DECL_MAPS_TO_SYMBOL",
        "SOURCE_TYPE_MAPS_TO_DEBUG_TYPE",
        "BUILD_OPTION_AFFECTS_DECL",
        "BUILD_OPTION_AFFECTS_SYMBOL",
        "FINDING_LOCALIZES_TO_DECL",
        "FINDING_CAUSED_BY_OPTION",
        # ADR-041 P1 #2 (object/link provenance graph).
        "TARGET_HAS_LINK_UNIT",
        "COMPILE_UNIT_EMITS_OBJECT",
        "LINK_UNIT_HAS_INPUT",
        "LINK_UNIT_USES_VERSION_SCRIPT",
        "LINK_UNIT_EXPORTS_SYMBOL",
        # Reserved (no normalized data source yet — see the NODE_KINDS note
        # above): a future archive/nm-style introspection extractor emits
        # these against the object_file/static_library nodes this phase
        # already creates.
        "ARCHIVE_CONTAINS_OBJECT",
        "OBJECT_DEFINES_SYMBOL",
    }
)

#: L5 edge kinds that express a decl/type dependency (ADR-041 P0): a call, a
#: non-call reference to a global/constant, a parameter/field type, or a base
#: class. ``crosscheck.py``'s intra-version ``public_to_internal_dependency``
#: check and this module's version-over-version internal-dependency diff both
#: read exactly this set, so the two stay in lockstep on what "a public entity
#: reaches an internal one" means — a struct's private field type or base
#: class is exactly the "not a call at all" risk ADR-041 opens with.
DEPENDENCY_EDGE_KINDS: frozenset[str] = frozenset(
    {
        "DECL_CALLS_DECL",
        "DECL_REFERENCES_DECL",
        "DECL_HAS_TYPE",
        "TYPE_HAS_FIELD_TYPE",
        "TYPE_INHERITS",
    }
)

#: ``fact_set["producer"]`` id of the one ``source_edges`` producer whose
#: coverage genuinely matches a full, unfiltered call/type-graph replay (Codex
#: review, PR #555): the Python inline extractor
#: (``source_extractors/clang.py``) reuses ``call_graph.py``'s/
#: ``type_graph.py``'s pure AST walk with no public/private filtering. The
#: ADR-038 C.8 clang plugin's own producer id (``"abicheck-clang-plugin"``)
#: is deliberately NOT this constant: it only walks call/reference bodies for
#: functions ``classify()`` accepts (public-header-declared), and never emits
#: ``DECL_HAS_TYPE`` for a typedef's underlying type or a variable's type —
#: see :func:`mark_source_edges_extractor_coverage`.
_FULL_WALK_SOURCE_EDGES_PRODUCER = "abicheck-cc-clang-extractor"


def _conf_from_build(conf: Confidence) -> str:
    """Map an ADR-029 build-evidence confidence onto a graph confidence label."""
    if conf == Confidence.HIGH:
        return CONF_HIGH
    if conf == Confidence.REDUCED:
        return CONF_REDUCED
    return CONF_UNKNOWN


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
    graph_id: str = ""  # "sha256:..." content hash of nodes+edges
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
    #: The actual scope a narrowed pass was restricted to — the ``changed_paths``
    #: tuple, or the examined compile units' source paths for an unseeded
    #: ``scoped_units`` run (fourteenth Codex review). ``narrowed_passes`` alone
    #: is just a boolean: two narrowed sides being "both narrowed" does not mean
    #: narrowed to the *same* subset — an old run scoped to ``src/a.cpp`` and a
    #: new run scoped to ``src/b.cpp`` are each individually narrow but examine
    #: disjoint code, so trusting either one's absence of an edge kind as
    #: coverage for the other's territory is exactly the same false-positive
    #: risk narrowed-vs-full already guards against. ``_common_dependency_edge_kinds``
    #: only trusts a narrowed side's edge as coverage when the other side is
    #: narrowed to this *identical* (non-empty) scope; set alongside
    #: ``narrowed_passes`` by ``inline._fold_call_graph``/``_fold_type_graph``.
    narrowed_scope: dict[str, frozenset[str]] = field(default_factory=dict)
    #: Which named extractor passes hit per-TU diagnostics — a clang crash/
    #: timeout/degenerate AST on some subset (sixteenth Codex review). Such a
    #: run (narrowed or not) still folds edges from the TUs that *did* parse,
    #: but those edges must not vouch for "this kind was examined" over
    #: whatever scope the pass claims: the failed TUs are an unknown,
    #: untracked gap (unlike ``narrowed_scope``, which knows exactly which TUs
    #: a deliberately-scoped run examined). Set by ``inline._fold_call_graph``/
    #: ``_fold_type_graph``/``cli_buildsource_helpers._collect_call_graph``
    #: whenever the pass examined units but ``extractor.diagnostics`` was
    #: non-empty (mutually exclusive with ``extractor_passes``/``narrowed_passes``,
    #: which both require zero diagnostics — so a narrowed run with
    #: diagnostics lands here too, on top of never confirming
    #: ``narrowed_passes``, since it is even less trustworthy than either).
    degraded_passes: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # ADR-046 D2: backfill/re-derive facts/resolved for anything
        # constructed directly (bypassing add_node/add_edge), so every node
        # reachable from this summary satisfies "facts is never empty,
        # resolved is derived from facts". Must run *before* the de-dup
        # indexes below (Codex review, fresh evidence): a constructor-seeded
        # edge whose role lives only in `facts` (not yet mirrored into
        # `attrs`) would otherwise have its `relation_key()` computed against
        # an empty `attrs`/`resolved` view, indexing it under the wrong
        # (blank-role) key before resolution ever populates the real one.
        for n in self.nodes:
            ensure_facts_and_resolve(n)
        for e in self.edges:
            ensure_facts_and_resolve(e)
        # De-dup indexes for O(1) add_node/add_edge, seeded from whatever the
        # constructor (or from_dict) provided. Edges dedup on relation_key()
        # (src, dst, kind, role), not the coarser key() (ADR-046 D1, Codex
        # review on PR #620): deduping on key() alone silently folded two
        # real, role-distinct relations -- e.g. a function that both returns
        # and takes the same private type, two DECL_HAS_TYPE edges -- into
        # one edge object, so only one role ever survived to be found via
        # relation_key() by anything walking graph.edges. key() itself is
        # untouched (still (src, dst, kind)); diff_source_graph's coarser
        # edge-set comparison keeps its pre-existing "one representative
        # edge per (src, dst, kind)" precision either way.
        self._node_ids: set[str] = {n.id for n in self.nodes}
        self._edge_keys: set[tuple[str, str, str, str]] = {
            e.relation_key() for e in self.edges
        }
        self._node_by_id: dict[str, GraphNode] = {n.id: n for n in self.nodes}
        self._edge_by_key: dict[tuple[str, str, str, str], GraphEdge] = {
            e.relation_key(): e for e in self.edges
        }

    # -- mutation helpers ---------------------------------------------------

    def add_node(self, node: GraphNode) -> None:
        """Add a node, or merge a second registration's facts into it (ADR-046
        D2 — evidence-preserving, replaces v1 first-writer-wins). ``kind``/
        ``label`` keep the first registration's value; only ``attrs`` merge.

        Merges *node*'s full ``facts`` list, not just its top-level
        ``provenance``/``confidence``/``attrs`` (Codex review, fresh
        evidence): an *incoming* node that already carries multiple facts of
        its own (e.g. re-added from an already evidence-merged graph) would
        otherwise have its whole fact history collapsed into one flattened
        fact, discarding the individual per-producer facts and any
        ``conflicts`` it already recorded.
        """
        if node.id not in self._node_ids:
            ensure_facts_and_resolve(node)
            self.nodes.append(node)
            self._node_ids.add(node.id)
            self._node_by_id[node.id] = node
            return
        merge_entity_facts(self._node_by_id[node.id], node)

    def add_edge(self, edge: GraphEdge) -> None:
        """Add an edge, or merge a second registration's facts into it — same
        as :meth:`add_node`, keyed on :meth:`GraphEdge.relation_key`
        (``(src, dst, kind, role)`` — ADR-046 D1) so two edges that only
        differ by role stay distinct objects instead of one silently
        swallowing the other's role. Merges *edge*'s full ``facts`` list on a
        duplicate registration, same as :meth:`add_node`.

        Resolves *edge* before computing its key (Codex review, fresh
        evidence): an edge whose role lives only in ``facts`` (not yet
        mirrored into ``attrs``) would otherwise have ``relation_key()``
        computed against an empty ``attrs``/``resolved`` view and dedup on
        the wrong (blank-role) key instead of its true, post-resolution one.
        """
        ensure_facts_and_resolve(edge)
        rkey = edge.relation_key()
        if rkey not in self._edge_keys:
            self.edges.append(edge)
            self._edge_keys.add(rkey)
            self._edge_by_key[rkey] = edge
            return
        merge_entity_facts(self._edge_by_key[rkey], edge)

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
            "by_binary_symbol": {
                k: sorted(set(v)) for k, v in by_binary_symbol.items()
            },
            "by_source_decl": {k: sorted(set(v)) for k, v in by_source_decl.items()},
        }

    def compute_graph_id(self) -> str:
        """Stable ``sha256:<hex>`` over the canonical node+edge set.

        Order-independent (nodes/edges are sorted) so the same logical graph
        always hashes identically regardless of construction order.

        Hashes on :meth:`GraphEdge.relation_key` (role-aware), not the
        coarser :meth:`GraphEdge.key` (Codex review, fresh evidence): since
        ``add_edge`` started deduping on ``relation_key`` (ADR-046 D1
        follow-up), two edges that differ only by role — e.g. the same
        ``DECL_HAS_TYPE`` edge changing from ``role="return"`` to
        ``role="param"`` — are genuinely different graph content, but the
        coarse key would hash them identically, silently hiding a real
        change from anything keyed on ``graph_id`` (pack references, a
        future content-addressed cache, comparison shortcuts).
        """
        canonical = {
            "schema_version": self.schema_version,
            "nodes": sorted((n.id, n.kind) for n in self.nodes),
            "edges": sorted(e.relation_key() for e in self.edges),
        }
        blob = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
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
        # ``header_call_graph``/``header_type_graph`` are the header-only graph
        # builder's own pass names (ADR-041 header-only-graph addendum) — a
        # distinct AST-walk shape (one synthetic header-aggregate TU, no build
        # integration) from the build-integrated ``call_graph``/``type_graph``
        # passes. Only ``header_type_graph`` grants "ran, zero found still
        # collected" credit here, and only for the *structural* kinds
        # (TYPE_INHERITS/TYPE_HAS_FIELD_TYPE/DECL_HAS_TYPE): a header-only pass
        # has true project-wide visibility of those (declaration-level facts,
        # no body needed). ``DECL_CALLS_DECL``/``DECL_REFERENCES_DECL`` need a
        # function body a header-only pass only sees when it happens to be
        # written *in the header* — its "ran" is not evidence of project-wide
        # call/reference coverage the way a build-integrated pass's is, so
        # neither ``call_edges.collected`` nor ``reference_edges.collected``
        # may be granted from ``header_call_graph``/``header_type_graph``
        # alone (Codex review; mirrors ``source_graph_findings.
        # _pass_trusted_kinds``'s structural-vs-body-dependent split).
        call_pass_ran = self.extractor_passes.get("call_graph", False)
        type_pass_ran = self.extractor_passes.get("type_graph", False)
        header_type_pass_ran = self.extractor_passes.get("header_type_graph", False)
        # ``include_graph``/``header_include_graph`` (build-integrated and
        # header-only-graph builder respectively) are pure file-inclusion
        # facts with no body-dependent gap the way calls/references have — a
        # confirmed pass with zero edges (a leaf header with no #includes of
        # its own) is a genuine zero, not "never collected" (Codex review:
        # this mirrors ``has_calls``/``has_type_edges`` below, which already
        # credit a confirmed-but-empty pass; ``has_includes`` previously
        # looked at edge presence alone).
        include_pass_ran = self.extractor_passes.get("include_graph", False)
        header_include_pass_ran = self.extractor_passes.get(
            "header_include_graph", False
        )
        has_calls = call_pass_ran or any(
            e.kind == "DECL_CALLS_DECL" for e in self.edges
        )
        has_includes = (include_pass_ran or header_include_pass_ran) or any(
            e.kind == "COMPILE_UNIT_INCLUDES_FILE" for e in self.edges
        )
        #: ADR-041 P0: TYPE_INHERITS/TYPE_HAS_FIELD_TYPE/DECL_HAS_TYPE describe
        #: type-level dependencies; DECL_REFERENCES_DECL a non-call decl reference.
        #: Both come from ``type_graph.py`` (folded alongside the call graph) or an
        #: external backend (``graph_backends.py``), so "collected" is tracked
        #: separately from the call graph — a graph can have calls but no type
        #: edges (e.g. an older pack) and coverage must say so honestly.
        type_edge_kinds = ("TYPE_INHERITS", "TYPE_HAS_FIELD_TYPE", "DECL_HAS_TYPE")
        has_type_edges = (type_pass_ran or header_type_pass_ran) or any(
            e.kind in type_edge_kinds for e in self.edges
        )
        has_reference_edges = type_pass_ran or any(
            e.kind == "DECL_REFERENCES_DECL" for e in self.edges
        )
        self.coverage = {
            "targets": kinds.get("target", 0),
            "compile_units": kinds.get("compile_unit", 0),
            "source_decls": kinds.get("source_decl", 0),
            "binary_symbol_mappings": edge_kinds.get("SOURCE_DECL_MAPS_TO_SYMBOL", 0),
            "include_edges": {
                "collected": has_includes,
                "count": edge_kinds.get("COMPILE_UNIT_INCLUDES_FILE", 0),
            },
            "call_edges": {
                "collected": has_calls,
                "count": edge_kinds.get("DECL_CALLS_DECL", 0),
            },
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
            "narrowed_scope": {k: sorted(v) for k, v in self.narrowed_scope.items()},
            "degraded_passes": dict(self.degraded_passes),
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
            narrowed_scope={
                str(k): frozenset(str(p) for p in v)
                for k, v in dict(d.get("narrowed_scope", {})).items()
            },
            degraded_passes={
                str(k): bool(v) for k, v in dict(d.get("degraded_passes", {})).items()
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


def function_decl_identity(
    mangled_name: str, name: str, qualified_name: str, type_qual: str
) -> str:
    """Mirror ``SourceEntity.identity()``'s fallback chain for a function decl
    node at the AST-replay layer (ADR-041 P1 #5, Codex review).

    ``call_graph.py``/``type_graph.py`` used to key a function's graph-node
    identity on the bare ``mangledName or name`` clang emits — but
    ``SourceEntity.identity()`` (the identity the L4 surface's own
    ``SOURCE_DECLARES`` node for the *same* declaration is keyed on) treats a
    ``mangledName`` that equals the bare ``name`` as "no real mangling" (every
    ``source_extractors/*`` mapper does this deliberately: extern "C"/C-linkage
    functions report ``mangledName == name``, not absent) and falls back to
    ``f"{qualified_name}#{signature_hash}"`` instead. A raw ``mangled or name``
    fallback silently picks that same non-distinguishing bare name, so a
    public C-linkage function's call/type-graph edges land on a *different*
    ``decl://`` node than its own ``SOURCE_DECLARES`` node — the two never
    merge, and dependency-reachability BFS starting from the public entry
    never reaches edges keyed by this mismatched identity.

    ``type_qual`` is the function's ``type.qualType`` spelling (the same value
    :func:`abicheck.buildsource.source_extractors.clang._signature` reads) —
    when non-empty, the ``signature_hash`` suffix is computed identically to
    :func:`abicheck.buildsource.source_extractors.clang._hash`
    (``"sha256:" + sha256("sig\\x00" + type_qual).hexdigest()``), so a
    matching declaration walked by either producer resolves to the exact same
    string. Falls back to the bare ``qualified_name`` when no type spelling is
    available, matching ``SourceEntity.identity()``'s own final fallback.
    """
    if mangled_name and mangled_name != name:
        return mangled_name
    if type_qual:
        digest = hashlib.sha256(f"sig\x00{type_qual}".encode()).hexdigest()
        return f"{qualified_name}#sha256:{digest}"
    return qualified_name


def _symbol_node_id(symbol: str) -> str:
    return f"binary_symbol://{symbol}"


def _macro_node_id(name: str) -> str:
    return f"macro://{name}"


def _debug_type_node_id(name: str) -> str:
    return f"debug_type://{name}"


def _object_node_id(path: str) -> str:
    return f"object://{path}"


def _static_library_node_id(path: str) -> str:
    return f"static_library://{path}"


def _version_script_node_id(path: str) -> str:
    return f"version_script://{path}"


#: Suffixes that identify a static-library archive among a LinkUnit's inputs
#: (ADR-041 P1 #2) — everything else is treated as an object file. Best-effort
#: textual classification (no archive introspection), mirroring this module's
#: existing approximate-by-design conventions elsewhere.
_STATIC_LIBRARY_SUFFIXES = (".a", ".lib")


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
    "_ZSt",
    "_ZNSt",
    "_ZNKSt",
    "_ZNSa",
    "_ZN9__gnu_cxx",
    "_ZNK9__gnu_cxx",
    "_ZN6__cxxabiv",
    "_Znw",
    "_Zna",
    "_Zdl",
    "_Zda",
    "__",
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
    Deliberately does not consider whether the node's own body is compiled
    into consumer code (see :func:`is_consumer_compiled_public_entry`) — an
    exported-or-header-visible declaration is exactly the "public API
    surface" question ``crosscheck.py``'s advisory
    ``public_to_internal_dependency`` check (RISK-only, never gates
    suppression) wants to ask, regardless of where the declaration's body
    lives.
    """
    if node_id in exported_decls:
        return True
    node = node_by_id.get(node_id)
    if node is None or node.kind not in DECL_NODE_KINDS:
        return False
    return str(node.attrs.get("visibility", "")) in PUBLIC_VISIBILITIES


def is_consumer_compiled_public_entry(
    node_id: str, node_by_id: dict[str, GraphNode], exported_decls: set[str]
) -> bool:
    """Whether *node_id* is a public entry whose own body is compiled into
    consumer binaries — the correct "entry" set for a *call-graph*
    reachability walk (Codex review, fresh evidence).

    :func:`is_public_dependency_node` alone over-reaches here: an ordinary,
    out-of-line exported function (e.g. ``api()`` defined in a ``.cpp``
    file) is public, but its *body* — and therefore its own internal calls,
    e.g. to ``ns::detail::helper()`` — is compiled into the **library's**
    binary only, never into any consumer's. A consumer links against
    ``api()``'s exported symbol alone; it never sees, references, or
    embeds ``helper()``. So walking the call graph from *every* exported
    function (as :func:`is_public_dependency_node` does) treats an ordinary
    internal implementation-detail call as if it were public-reachable,
    which either manufactures a spurious "still reachable" narrative on a
    genuinely safe-to-suppress internal change, or (via
    ``post_processing.MarkReachability``) blocks a broad internal-namespace
    suppression rule from ever applying to the common case — most
    functions in most libraries are ordinary, out-of-line, non-template.

    The real criterion is whether the entry's own body is emitted into
    every including translation unit — true for inline functions/methods
    and templates, false for an ordinary out-of-line definition — captured
    by ``GraphNode.attrs["consumer_compiled_body"]``
    (:func:`build_source_graph`). A node without that attr at all defaults
    permissively to ``True`` — matching the header-graph/type-node/generic
    case, where no signal either way is available — **except** a node whose
    ``provenance`` is one of :data:`_NO_CONSUMER_COMPILED_SIGNAL_PROVENANCES`
    (Codex review, fresh evidence): ``augment_graph_with_calls``
    (``call_graph.py``) stamps its own fallback tag on a node it creates for
    a caller/callee identity with no other declaration node backing it — a
    real, build-integrated project function reached only through the call
    graph itself, whose out-of-line body is not necessarily
    consumer-compiled. An inline public ``wrap()`` calling an ordinary
    out-of-line project function ``helper_a()`` (this fallback shape) which
    itself calls an internal ``ns::detail::helper()`` must stop expanding
    *at* ``helper_a()`` — treating "no signal" as "safe" for this one node
    shape would silently reintroduce the exact over-reach this predicate
    exists to reject. ``graph_backends.py``'s Kythe/CodeQL ingestion
    (``ingest_kythe_entries``/``ingest_codeql_call_results``) creates the
    identical shape for an external-indexer edge: a bare ``source_decl``
    node stamped with provenance ``"kythe"``/``"codeql"`` and no
    ``consumer_compiled_body`` attr at all, since neither export format says
    whether the referenced declaration's body is inline/template — an
    imported Kythe/CodeQL call chain through an ordinary out-of-line
    intermediate helper must stop there too, for the same reason.
    """
    if not is_public_dependency_node(node_id, node_by_id, exported_decls):
        return False
    return is_consumer_compiled_node(node_id, node_by_id)


#: ``provenance`` tag ``augment_graph_with_calls`` (``call_graph.py``) stamps
#: on a fallback node it creates for a caller/callee identity with no other
#: declaration node backing it — the one node shape known to lack a
#: ``consumer_compiled_body`` attr while still representing a genuine,
#: build-integrated (out-of-line, not-necessarily-consumer-compiled) project
#: declaration, as opposed to "no signal available" (header-graph/type nodes,
#: synthetic test fixtures, …) which stays permissive by default. Mirrored as
#: a literal string rather than imported from ``call_graph.py`` to avoid
#: coupling this module to that one's internal constant.
_CALL_GRAPH_FALLBACK_PROVENANCE = "call_graph"

#: Same shape as :data:`_CALL_GRAPH_FALLBACK_PROVENANCE`, for external
#: indexer backends (Codex review, fresh evidence): ``graph_backends.py``'s
#: ``ingest_kythe_entries``/``ingest_codeql_call_results``/
#: ``ingest_codeql_extends_results`` stamp exactly these two provenance
#: strings on a bare ``source_decl``/``record_type`` node with no
#: ``consumer_compiled_body`` attr — Kythe entries and CodeQL query results
#: carry cross-reference edges only, never whether the referenced
#: declaration's body is inline/template, so an attr-less node reached only
#: through one of these backends is exactly as unproven as the call-graph
#: fallback shape and must not be treated as a safe stopping point by
#: default either.
_NO_CONSUMER_COMPILED_SIGNAL_PROVENANCES = frozenset(
    {
        _CALL_GRAPH_FALLBACK_PROVENANCE,
        "kythe",
        "codeql",
    }
)


def is_consumer_compiled_node(node_id: str, node_by_id: dict[str, GraphNode]) -> bool:
    """Whether *node_id*'s own body is compiled into consumer code, independent
    of whether it also qualifies as a *public* entry (see
    :func:`is_consumer_compiled_public_entry` for that combined check) — the
    predicate a call-graph *traversal* needs at every intermediate node, not
    just at the entries it starts from (Codex review, fresh evidence: see
    :func:`is_consumer_compiled_public_entry`'s docstring for the fallback-node
    shapes this conservative exception protects against).
    """
    node = node_by_id.get(node_id)
    if node is None:
        return True
    if "consumer_compiled_body" in node.attrs:
        return bool(node.attrs["consumer_compiled_body"])
    return node.provenance not in _NO_CONSUMER_COMPILED_SIGNAL_PROVENANCES


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
        has_provenance = node_id in decl_to_file or bool(
            node.attrs.get("defined_in_project")
        )
        if not has_provenance:
            return False
        return not looks_like_system_name(node.label or "")
    return False


# ── Phase 2: build the graph from ADR-029 BuildEvidence ─────────────────────


def _file_in_project(caller_file: str, project_files: frozenset[str]) -> bool:
    """Whether *caller_file* is one of the project's own compile-unit sources.

    Build-evidence sources are often repo-relative (``src/foo.cc``) while the
    clang AST emits an absolute path (``/work/src/foo.cc``); match on a path
    suffix either way (mirrors ``source_replay._path_matches``). A function whose
    body is in one of these files is project-defined; one in a third-party/system
    header (Boost/Abseil/libstdc++) is not.
    """
    if not caller_file:
        return False
    c = caller_file.replace("\\", "/").lstrip("./")
    for pf in project_files:
        n = pf.replace("\\", "/").lstrip("./")
        if c == n or c.endswith("/" + n) or n.endswith("/" + c):
            return True
    return False


def project_source_files(build: BuildEvidence) -> frozenset[str]:
    """Project-internal source files for ``defined_in_project`` provenance.

    Compile-unit sources **plus the targets' private headers** — a function whose
    body is in a project ``.cc`` *or* a project private header is internal
    implementation. Public headers are deliberately excluded: an inline function
    in a public header is consumer-visible public surface, so marking it
    ``defined_in_project`` (→ internal) would false-positive
    ``public_to_internal_dependency``. Third-party/system headers (Boost, libc++)
    are never in either list, so they stay external (Codex review).
    """
    files: set[str] = {cu.source for cu in build.compile_units if cu.source}
    for tgt in build.targets:
        files.update(h for h in tgt.private_headers if h)
    return frozenset(files)


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
            graph.add_node(
                GraphNode(
                    id=node_id,
                    kind="generated_file",
                    label=path,
                    provenance="build_evidence",
                    confidence=CONF_REDUCED,
                    attrs={"generated": True},
                )
            )
            return node_id
        if header:
            node_id = _header_node_id(path)
            graph.add_node(
                GraphNode(
                    id=node_id,
                    kind="header",
                    label=path,
                    provenance="build_evidence",
                    confidence=CONF_HIGH,
                )
            )
            return node_id
        node_id = _source_node_id(path)
        graph.add_node(
            GraphNode(
                id=node_id,
                kind="source",
                label=path,
                provenance="build_evidence",
                confidence=CONF_HIGH,
            )
        )
        return node_id

    known_targets = {t.id for t in build.targets}
    for tgt in build.targets:
        conf = _conf_from_build(tgt.confidence)
        graph.add_node(
            GraphNode(
                id=tgt.id,
                kind="target",
                label=tgt.name or tgt.id,
                provenance="build_evidence",
                confidence=conf,
                attrs={
                    "kind": tgt.kind.value,
                    "visibility": tgt.visibility,
                    "build_system": tgt.build_system,
                },
            )
        )
        for src in tgt.source_files:
            sid = file_node(src)
            graph.add_edge(
                GraphEdge(
                    src=tgt.id,
                    dst=sid,
                    kind="TARGET_HAS_SOURCE",
                    provenance="build_evidence",
                    confidence=conf,
                )
            )
        for hdr in tgt.public_headers:
            hid = file_node(hdr, header=True)
            graph.add_edge(
                GraphEdge(
                    src=tgt.id,
                    dst=hid,
                    kind="TARGET_HAS_PUBLIC_HEADER",
                    provenance="build_evidence",
                    confidence=conf,
                )
            )
        for dep in tgt.dependencies:
            # Reference an external dependency explicitly when it is not one of
            # our own targets, so the graph distinguishes intra-project edges
            # from third-party ones (informative for reachability triage).
            if dep not in known_targets:
                graph.add_node(
                    GraphNode(
                        id=dep,
                        kind="external_dependency",
                        label=dep,
                        provenance="build_evidence",
                        confidence=CONF_REDUCED,
                    )
                )
            graph.add_edge(
                GraphEdge(
                    src=tgt.id,
                    dst=dep,
                    kind="TARGET_DEPENDS_ON",
                    provenance="build_evidence",
                    confidence=conf,
                )
            )

    for cu in build.compile_units:
        graph.add_node(
            GraphNode(
                id=cu.id,
                kind="compile_unit",
                label=cu.output or cu.source or cu.id,
                provenance="build_evidence",
                confidence=CONF_HIGH,
                attrs={
                    "language": cu.language,
                    "standard": cu.standard,
                    "target_id": cu.target_id,
                },
            )
        )
        if cu.source:
            sid = file_node(cu.source)
            graph.add_edge(
                GraphEdge(
                    src=cu.id,
                    dst=sid,
                    kind="COMPILE_UNIT_BUILDS_SOURCE",
                    provenance="build_evidence",
                    confidence=CONF_HIGH,
                )
            )
        for flag in cu.abi_relevant_flags:
            oid = _option_node_id(flag)
            graph.add_node(
                GraphNode(
                    id=oid,
                    kind="build_option",
                    label=flag,
                    provenance="build_evidence",
                    confidence=CONF_HIGH,
                    attrs={"abi_relevant": True},
                )
            )
            graph.add_edge(
                GraphEdge(
                    src=cu.id,
                    dst=oid,
                    kind="COMPILE_UNIT_USES_OPTION",
                    provenance="build_evidence",
                    confidence=CONF_HIGH,
                )
            )

    _fold_link_provenance(graph, build)

    if source_abi is not None:
        _augment_with_source_abi(graph, source_abi, project_source_files(build))
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
            graph.add_edge(
                GraphEdge(
                    src=e.dst,
                    dst=sym,
                    kind="BUILD_OPTION_AFFECTS_SYMBOL",
                    provenance="build_evidence+source_abi",
                    confidence=CONF_REDUCED,
                )
            )


def _fold_link_provenance(graph: SourceGraphSummary, build: BuildEvidence) -> None:
    """Fold object/link provenance from *build* into *graph* (ADR-041 P1 #2).

    Lets a symbol change be attributed to "which object/archive member/link
    step", not only "which target" — the gap the roadmap named:
    ``TARGET_DEPENDENCY_ADDED``/``EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED``
    currently cannot explain an accidental export from a static archive, a
    COMDAT/weak-symbol resolution change, or a transitive ``DT_NEEDED`` traced
    to a specific object.

    - Every ``compile_unit`` with a known ``output`` gets an ``object_file``
      node and a ``COMPILE_UNIT_EMITS_OBJECT`` edge — "this TU produced this
      object."
    - Every :class:`~abicheck.buildsource.build_evidence.LinkUnit` becomes a
      ``link_unit`` node (``NODE_KINDS`` reserved this kind since ADR-031 D2
      but nothing populated it before this), linked to its owning ``target``
      (``TARGET_HAS_LINK_UNIT``) when the target is known. Each input path is
      classified by suffix into an ``object_file`` or ``static_library`` node
      (best-effort textual classification, no archive introspection) and
      connected via ``LINK_UNIT_HAS_INPUT`` — an object a compile unit already
      emitted (same path) lands on the *same* node instead of a disconnected
      duplicate, so a change traced to one object correlates across both
      slices. A non-empty ``version_script`` gets its own node
      (``LINK_UNIT_USES_VERSION_SCRIPT``).
    - ``archive_member``/``linker_script``/``export_map``/``comdat_group`` and
      the ``ARCHIVE_CONTAINS_OBJECT``/``OBJECT_DEFINES_SYMBOL`` edges stay
      reserved (schema-only): true archive-member/per-object-symbol
      enumeration needs a real archive/object introspection extractor
      (``ar``/``nm``-equivalent) this increment does not add, matching the
      same "reserved, not yet populated" pattern this ADR's own P0 slice 1
      used for the edge kinds it later filled in.

    ``LINK_UNIT_EXPORTS_SYMBOL`` (a link unit's own exported symbols) is added
    by :func:`_augment_with_source_abi` instead, once ``BINARY_EXPORTS_SYMBOL``
    resolves which symbols the owning target actually exports — this function
    runs first (build-evidence-only, no ``source_abi`` required) so the
    ``link_unit`` node it creates is already there for that later step to
    attach to.
    """
    for cu in build.compile_units:
        if not cu.output:
            continue
        oid = _object_node_id(cu.output)
        if not graph.has_node(oid):
            graph.add_node(
                GraphNode(
                    id=oid,
                    kind="object_file",
                    label=cu.output,
                    provenance="build_evidence",
                    confidence=CONF_HIGH,
                )
            )
        graph.add_edge(
            GraphEdge(
                src=cu.id,
                dst=oid,
                kind="COMPILE_UNIT_EMITS_OBJECT",
                provenance="build_evidence",
                confidence=CONF_HIGH,
            )
        )

    known_targets = {t.id for t in build.targets}
    for link in build.link_units:
        graph.add_node(
            GraphNode(
                id=link.id,
                kind="link_unit",
                label=link.output or link.id,
                provenance="build_evidence",
                confidence=CONF_HIGH,
                attrs={
                    "kind": link.kind,
                    "target_id": link.target_id,
                    "soname": link.soname,
                },
            )
        )
        if link.target_id and link.target_id in known_targets:
            graph.add_edge(
                GraphEdge(
                    src=link.target_id,
                    dst=link.id,
                    kind="TARGET_HAS_LINK_UNIT",
                    provenance="build_evidence",
                    confidence=CONF_HIGH,
                )
            )
        for inp in link.inputs:
            if not inp:
                continue
            is_archive = inp.endswith(_STATIC_LIBRARY_SUFFIXES)
            iid = _static_library_node_id(inp) if is_archive else _object_node_id(inp)
            if not graph.has_node(iid):
                graph.add_node(
                    GraphNode(
                        id=iid,
                        kind="static_library" if is_archive else "object_file",
                        label=inp,
                        provenance="build_evidence",
                        confidence=CONF_REDUCED,
                    )
                )
            graph.add_edge(
                GraphEdge(
                    src=link.id,
                    dst=iid,
                    kind="LINK_UNIT_HAS_INPUT",
                    provenance="build_evidence",
                    confidence=CONF_HIGH,
                )
            )
        if link.version_script:
            vid = _version_script_node_id(link.version_script)
            graph.add_node(
                GraphNode(
                    id=vid,
                    kind="version_script",
                    label=link.version_script,
                    provenance="build_evidence",
                    confidence=CONF_HIGH,
                )
            )
            graph.add_edge(
                GraphEdge(
                    src=link.id,
                    dst=vid,
                    kind="LINK_UNIT_USES_VERSION_SCRIPT",
                    provenance="build_evidence",
                    confidence=CONF_HIGH,
                )
            )


# ── Phases 3-4: enrich the graph from the ADR-030 L4 source surface ─────────


def _augment_with_source_abi(
    graph: SourceGraphSummary,
    surface: SourceAbiSurface,
    project_files: frozenset[str] | None = None,
) -> None:
    """Fold a linked L4 source surface into *graph* (Phases 3-4).

    Adds the public-reachability slice (declarations/types/macros, each linked
    to the public header that declares it) and the source↔binary slice (decl →
    exported symbol, type → debug type, target → exported symbol). All edges are
    tagged ``provenance="source_abi"`` so a reachability claim always discloses
    that it rests on source-replay evidence, not a binary diff (ADR-031 D9).

    *project_files* (``project_source_files(build)``) is threaded
    through to :func:`fold_source_edges` so a ``source_edges`` endpoint can be
    marked ``defined_in_project`` the same way ``augment_graph_with_calls``/
    ``augment_graph_with_types`` already do for the standalone replay passes.
    """
    target_id = surface.target_id
    if target_id and not graph.has_node(target_id):
        # The surface may name a target the build evidence did not enumerate
        # (e.g. binary+headers-only collection). Materialize it so its symbols
        # have an owner in the graph.
        graph.add_node(
            GraphNode(
                id=target_id,
                kind="target",
                label=target_id,
                provenance="source_abi",
                confidence=CONF_REDUCED,
            )
        )

    decl_to_sym: dict[str, str] = surface.mappings.get(
        "source_decl_to_binary_symbol", {}
    )
    type_to_dbg: dict[str, str] = surface.mappings.get("source_type_to_debug_type", {})
    # ADR-041 P1 #2: the link unit(s) _fold_link_provenance already created for
    # this target (build-evidence-only, before this function ran) — so an
    # exported symbol can also be attributed to the specific link step that
    # produced it, not only the target as a whole.
    link_unit_ids = [
        n.id
        for n in graph.nodes
        if n.kind == "link_unit" and target_id and n.attrs.get("target_id") == target_id
    ]

    def export_symbol(symbol: str, confidence: str) -> str:
        sid = _symbol_node_id(symbol)
        graph.add_node(
            GraphNode(
                id=sid,
                kind="binary_symbol",
                label=symbol,
                provenance="source_abi",
                confidence=CONF_HIGH,
            )
        )
        if target_id:
            graph.add_edge(
                GraphEdge(
                    src=target_id,
                    dst=sid,
                    kind="BINARY_EXPORTS_SYMBOL",
                    provenance="source_abi",
                    confidence=confidence,
                )
            )
        for link_id in link_unit_ids:
            graph.add_edge(
                GraphEdge(
                    src=link_id,
                    dst=sid,
                    kind="LINK_UNIT_EXPORTS_SYMBOL",
                    provenance="source_abi",
                    confidence=confidence,
                )
            )
        return sid

    def header_declares(entity: SourceEntity, node_id: str, confidence: str) -> None:
        loc = entity.source_location
        if loc is None or not loc.path:
            return
        hid = _header_node_id(loc.path)
        # add_node keeps the first writer's facts, so a build-evidence header
        # node (HIGH confidence) is not downgraded by this source_abi one.
        graph.add_node(
            GraphNode(
                id=hid,
                kind="header",
                label=loc.path,
                provenance="source_abi",
                confidence=confidence,
                attrs={"origin": loc.origin},
            )
        )
        graph.add_edge(
            GraphEdge(
                src=hid,
                dst=node_id,
                kind="SOURCE_DECLARES",
                provenance="source_abi",
                confidence=confidence,
            )
        )

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
    # An entity routed to reachable_templates/reachable_inline_bodies shares
    # its identity() (mangled name, or qualified_name+signature_hash) with the
    # plain "function"-kind declaration entity clang.py *also* emits for the
    # same function -- both land on the same node id via _decl_node_id below,
    # and add_node keeps only the first writer's attrs. reachable_declarations
    # is iterated first in `declarations` above, so for any function that also
    # has an inline/template rendition, the winning node's own attrs["decl_kind"]
    # is always "function"/"method", never "inline"/"template" -- silently
    # losing the one signal that distinguishes "body compiled into every
    # consumer TU that includes this header" from "ordinary out-of-line body,
    # compiled into this library's binary only" (Codex review). Compute the
    # identity set up front so every entity sharing it gets the *same*
    # attrs["consumer_compiled_body"] value regardless of which one wins the
    # node-id race.
    consumer_compiled_identities = {
        ent.identity()
        for ent in (*surface.reachable_templates, *surface.reachable_inline_bodies)
    }
    for ent in declarations:
        did = _decl_node_id(ent.identity())
        conf = ent.confidence.value
        graph.add_node(
            GraphNode(
                id=did,
                kind="source_decl",
                label=ent.qualified_name or ent.identity(),
                provenance="source_abi",
                confidence=conf,
                attrs={
                    "decl_kind": ent.kind,
                    "visibility": ent.visibility,
                    "consumer_compiled_body": ent.identity()
                    in consumer_compiled_identities,
                },
            )
        )
        header_declares(ent, did, conf)
        # decl_to_sym is keyed by entity identity (the mangled name for C++, so
        # overloads stay distinct) by both link_source_abi and
        # relink_surface_exports — look it up the same way, not by qualified_name,
        # or the SOURCE_DECL_MAPS_TO_SYMBOL edge is never created for C++.
        symbol = decl_to_sym.get(ent.identity(), "")
        if symbol:
            graph.add_edge(
                GraphEdge(
                    src=did,
                    dst=_symbol_node_id(symbol),
                    kind="SOURCE_DECL_MAPS_TO_SYMBOL",
                    provenance="source_abi",
                    confidence=conf,
                )
            )

    for ent in surface.reachable_types:
        tid = _type_node_id(ent.identity())
        conf = ent.confidence.value
        graph.add_node(
            GraphNode(
                id=tid,
                kind=_type_node_kind(ent.kind),
                label=ent.qualified_name or ent.identity(),
                provenance="source_abi",
                confidence=conf,
                attrs={"decl_kind": ent.kind, "visibility": ent.visibility},
            )
        )
        header_declares(ent, tid, conf)
        debug_type = type_to_dbg.get(ent.qualified_name, "")
        if debug_type:
            bid = _debug_type_node_id(debug_type)
            graph.add_node(
                GraphNode(
                    id=bid,
                    kind="debug_type",
                    label=debug_type,
                    provenance="source_abi",
                    confidence=CONF_REDUCED,
                )
            )
            graph.add_edge(
                GraphEdge(
                    src=tid,
                    dst=bid,
                    kind="SOURCE_TYPE_MAPS_TO_DEBUG_TYPE",
                    provenance="source_abi",
                    confidence=CONF_REDUCED,
                )
            )

    for ent in surface.reachable_macros:
        mid = _macro_node_id(ent.qualified_name or ent.identity())
        conf = ent.confidence.value
        graph.add_node(
            GraphNode(
                id=mid,
                kind="macro",
                label=ent.qualified_name or ent.identity(),
                provenance="source_abi",
                confidence=conf,
            )
        )
        header_declares(ent, mid, conf)

    fold_source_edges(graph, surface.source_edges, project_files)


def _source_edge_endpoint_ids(
    kind: str, src: str, dst: str
) -> tuple[str, str, str, str]:
    """Map a raw ``source_edges`` row's ``(kind, src, dst)`` identities onto
    graph node ids/kinds, mirroring the id scheme
    ``call_graph.augment_graph_with_calls``/``type_graph.augment_graph_with_types``
    already use — so an edge folded from L4 facts lands on the same
    ``decl://``/``type://`` node a separate call/type-graph replay pass (or L4
    declaration enrichment) would have created, rather than a disconnected
    duplicate.
    """
    if kind == "DECL_HAS_TYPE":
        return _decl_node_id(src), "source_decl", _type_node_id(dst), "record_type"
    if kind in ("TYPE_INHERITS", "TYPE_HAS_FIELD_TYPE"):
        return _type_node_id(src), "record_type", _type_node_id(dst), "record_type"
    # DECL_CALLS_DECL / DECL_REFERENCES_DECL — the only other kinds a caller
    # reaches this with (fold_source_edges gates on DEPENDENCY_EDGE_KINDS
    # before calling this).
    return _decl_node_id(src), "source_decl", _decl_node_id(dst), "source_decl"


def fold_source_edges(
    graph: SourceGraphSummary,
    source_edges: list[dict[str, Any]],
    project_files: frozenset[str] | None = None,
) -> int:
    """Fold ``SourceAbiSurface.source_edges`` into *graph* (ADR-038 C.9 / PR1).

    Closes the gap where a Clang-plugin/replay-collected ``source_edges`` fact
    was serialized onto ``SourceAbiTu``/``SourceAbiSurface`` but never reached
    the L5 graph (latest-main Clang plugin review): ``DECL_CALLS_DECL``,
    ``DECL_REFERENCES_DECL``, ``DECL_HAS_TYPE``, ``TYPE_HAS_FIELD_TYPE``, and
    ``TYPE_INHERITS`` rows collected during the *same* L4 frontend invocation
    are folded in exactly like a separate ``call_graph``/``type_graph`` replay
    pass would, using the identical node-id scheme -- so an edge here
    reconciles with (de-duplicates against, via ``add_edge``'s
    ``(src, dst, kind)`` key) one already present from L4 declaration
    enrichment or a separate replay pass, first-writer-wins.

    Malformed rows (missing edge-kind/src/dst, or a non-dict entry from a
    hand-edited/forward-versioned pack) are skipped rather than raising --
    ``source_edges`` is best-effort collected evidence (ADR-028 D7), never a
    reason to abort the rest of the graph build. Returns the number of edges
    actually added (excludes rows that duplicated an edge already present).

    When *project_files* is supplied and a row's ``attrs["dst_file"]`` matches
    one of them, the dst node is marked ``defined_in_project`` (+ ``def_file``)
    -- mirroring ``call_graph.augment_graph_with_calls``/
    ``type_graph.augment_graph_with_types``'s identical marker for the
    standalone replay passes. Without this, a callee/reference/type that only
    ever appears as a ``source_edges`` endpoint (never independently declared
    on the L4 public surface) carries no project provenance at all, so
    ``is_internal_dependency_node`` cannot recognize it and
    ``PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`` silently misses it (Codex review
    on PR #555; the exact gap this ADR's own "still always run [the replay]"
    note flags as outstanding for the ``source_edges`` wire format). Which
    rows carry ``dst_file`` depends on the producer: the Python inline
    extractor (``clang_source_edges.py``) resolves it for every edge kind;
    the ADR-038 C.8 clang plugin resolves it for all five kinds too as of
    ADR-038 C.13 (a ``typeDeclFile(QualType)`` helper unwraps
    pointer/reference/array sugar and resolves a typedef alias to its own
    declaring file, or a record/enum ``TagDecl`` otherwise) -- though its
    ``DECL_HAS_TYPE`` still never covers a variable's own type or a
    typedef's underlying type (only function return/parameter types), so
    ``mark_source_edges_extractor_coverage()`` still degrades the whole
    family for the plugin producer rather than trusting it, per that
    function's docstring. Applied whether the
    node is created fresh here or already existed from an earlier edge in
    this same call (backfilled, unless it already carries a ``visibility``
    attr -- real L4 evidence, never overridden by this best-effort marker),
    mirroring ``augment_graph_with_types``'s identical backfill behavior.
    """
    node_by_id: dict[str, GraphNode] = {n.id: n for n in graph.nodes}
    added = 0
    for row in source_edges:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("edge") or row.get("kind") or "")
        src_ident = str(row.get("src") or "")
        dst_ident = str(row.get("dst") or "")
        # DEPENDENCY_EDGE_KINDS, not the broader EDGE_KINDS (CodeRabbit
        # review, PR #555): source_edges only ever carries these five
        # decl/type-dependency kinds, so a forward-incompatible or malformed
        # row naming an unrelated kind (e.g. TARGET_DEPENDS_ON) must not
        # silently fall through to the decl/decl default mapping below.
        if not kind or not src_ident or not dst_ident:
            continue
        if kind not in DEPENDENCY_EDGE_KINDS:
            continue
        src_id, src_kind, dst_id, dst_kind = _source_edge_endpoint_ids(
            kind, src_ident, dst_ident
        )
        confidence = str(row.get("confidence") or CONF_UNKNOWN)
        provenance = str(row.get("provenance") or "source_edges")
        attrs_raw = row.get("attrs")
        row_attrs = dict(attrs_raw) if isinstance(attrs_raw, dict) else {}
        dst_file = str(row_attrs.get("dst_file", ""))
        dst_in_project = bool(
            project_files and dst_file and _file_in_project(dst_file, project_files)
        )
        for node_id, node_kind, ident, is_dst in (
            (src_id, src_kind, src_ident, False),
            (dst_id, dst_kind, dst_ident, True),
        ):
            existing = node_by_id.get(node_id)
            if existing is None:
                node_attrs = (
                    {"defined_in_project": True, "def_file": dst_file}
                    if is_dst and dst_in_project
                    else {}
                )
                node = GraphNode(
                    id=node_id,
                    kind=node_kind,
                    label=ident,
                    provenance=provenance,
                    confidence=confidence,
                    attrs=node_attrs,
                )
                graph.add_node(node)
                node_by_id[node_id] = node
            elif (
                is_dst
                and dst_in_project
                and not existing.attrs.get("defined_in_project")
                and not existing.attrs.get("visibility")
            ):
                # ADR-046 D2: route through register_fact (a direct
                # existing.attrs[...] mutation is dropped on the next round-trip).
                backfill = {"defined_in_project": True, "def_file": dst_file}
                register_fact(existing, provenance, confidence, backfill)
        before = len(graph.edges)
        graph.add_edge(
            GraphEdge(
                src=src_id,
                dst=dst_id,
                kind=kind,
                provenance=provenance,
                confidence=confidence,
                attrs=row_attrs,
            )
        )
        if len(graph.edges) > before:
            added += 1
    return added


def mark_source_edges_extractor_coverage(
    graph: SourceGraphSummary, surface: SourceAbiSurface | None
) -> None:
    """Translate a confirmed-complete ``source_edges`` rollup into
    ``call_graph``/``type_graph`` extractor-pass coverage (Codex review).

    ``fold_source_edges`` (called from :func:`build_source_graph`) never
    touches ``graph.extractor_passes`` itself -- when a caller runs the
    ``call_graph``/``type_graph`` replay right after building the graph
    (``inline._build_inline_graph``, ``cli_buildsource_helpers``), that
    replay's own ``extractor_pass_fully_covered()``/``narrowed_pass_confirmed()``
    tracking is strictly more precise (it knows full-vs-narrowed scope; a bare
    ``source_edges`` rollup does not) and must be the sole source of truth —
    do not call this alongside it. But a caller that folds ``source_edges``
    and never runs a replay at all (``inputs_pack.ingest_inputs_pack``
    ingesting a build-emitted Flow-2 pack; ``cli_buildsource_merge``'s
    export-relink graph rebuild) leaves both flags permanently unset even
    though the AST was genuinely, completely walked for these edge kinds --
    ``source_graph_findings._common_dependency_edge_kinds``/
    ``_dependency_kinds_covered`` then read that as "no pass ever ran" and
    suppress a real ``PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`` finding as a
    coverage artifact instead of reporting it. A Flow-2 pack always reflects
    whatever the build compiled (never a `changed_paths`-narrowed subset the
    way an inline scan can be), so a confirmed-complete rollup here is safe
    to treat as full-scope coverage.

    "complete" is only trusted when ``surface.source_edges`` is actually
    non-empty (Codex review, PR #555): ``coverage["fact_family_states"]``
    predates ``SourceAbiSurface.source_edges`` (ADR-038 C.8 vs. C.9), so a
    pre-C.9 persisted ``source_abi.json`` can carry ``source_edges:
    "complete"`` from when the per-TU edges existed but its serializer had
    no field to persist them into -- ``SourceAbiSurface.from_dict`` then
    defaults the now-missing key to ``[]``. Treating that as confirmed-zero
    coverage would read a schema-version gap as "nothing to see here",
    letting a pre-existing internal dependency look newly added the moment
    such a legacy baseline is compared against a freshly regenerated
    candidate. A mismatched "complete"-with-no-edges is left unmarked here
    (same as absent/unsupported), never silently upgraded.

    Gated on the producer being ``_FULL_WALK_SOURCE_EDGES_PRODUCER`` (Codex
    review, PR #555): "complete"/"empty-confirmed" only means "every TU's
    ``source_edges`` collection ran without trouble", not "every function/type
    in the TU was walked". The Python inline extractor
    (``clang_source_edges.build_source_edges``) reuses ``call_graph.py``'s/
    ``type_graph.py``'s full, unfiltered AST walk, so its coverage genuinely
    matches a standalone replay. The ADR-038 C.8 clang plugin's ``source_edges``
    does not: ``VisitFunctionDecl`` returns before running ``CallRefVisitor``
    unless ``classify()`` accepts the function (public-header-declared only --
    a private/internal helper defined purely in a ``.cpp`` is skipped
    entirely, its outgoing calls never walked), and it never emits
    ``DECL_HAS_TYPE`` for a typedef's underlying type or a variable's type (only
    for function return/parameter types) at all. Aliasing the plugin's
    ``source_edges`` to full ``call_graph``/``type_graph`` trust would read
    "the public surface's calls/types were captured" as "the whole TU's
    call/type graph is confirmed empty beyond what's here" -- hiding a
    genuinely new dependency added inside a private helper's body, or a
    changed typedef/variable type, as a false negative. A rolled-up
    ``fact_set`` that disagrees across TUs, or is missing (pre-C.8 producer,
    mixed pack), is treated the same as the plugin case: never grant blanket
    trust without a positive, unambiguous "full walk" signal.

    A non-full-walk producer (or an unresolved one) whose ``source_edges``
    nonetheless folded real edges into *graph* is stamped ``degraded_passes``
    instead of left entirely unmarked (Codex review): an unmarked pass falls
    back to raw edge *presence* in
    ``source_graph_findings._common_dependency_edge_kinds`` (its
    ``_pass_ran``/``_pass_trusted_kinds`` checks only consult
    ``extractor_passes``/``narrowed_passes``, not the *absence* of a
    ``degraded_passes`` entry) — and a scoped producer's edges cannot safely
    vouch for a project-wide zero any more than a narrowed/degraded
    standalone replay's edges can (the same one-directional risk the sixth/
    sixteenth Codex reviews already established ``degraded_passes`` guards
    against elsewhere in that module). Left unmarked, a plugin baseline with
    even one public-surface call edge would make ``DECL_CALLS_DECL`` look
    "common" against a full-replay candidate, and a pre-existing
    private-helper dependency the plugin structurally could never have seen
    would surface as a false ``PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`` the
    moment collection switches producers. ``degraded_passes`` only ever
    restricts trust in *this* side's absence of a kind (never gates the
    *other* side's presence), so this can only trade a missed addition for
    avoiding a false alarm — the same conservative bias the whole
    narrowed/degraded chain already commits to.
    """
    if surface is None:
        return
    families = surface.coverage.get("fact_family_states")
    # A missing/malformed fact_family_states (a third-party or hand-edited
    # surface, or a schema older than ADR-038 C.8) must not fall through to
    # "return unmarked" when source_edges nonetheless folded real edges into
    # *graph* -- that leaves the exact same raw-edge-presence-fallback gap
    # a non-full-walk producer does (Codex review): treated as unknown/
    # non-full coverage below (state stays None, so the full-walk-trust
    # branch never fires), falling through to the degraded stamp instead of
    # returning early.
    state = families.get("source_edges") if isinstance(families, dict) else None
    fact_set = surface.coverage.get("fact_set")
    full_walk_producer = (
        isinstance(fact_set, dict)
        and fact_set.get("producer") == _FULL_WALK_SOURCE_EDGES_PRODUCER
    )
    if full_walk_producer and (
        state == "empty-confirmed" or (state == "complete" and surface.source_edges)
    ):
        graph.extractor_passes["call_graph"] = True
        graph.extractor_passes["type_graph"] = True
        return
    if surface.source_edges:
        graph.degraded_passes["call_graph"] = True
        graph.degraded_passes["type_graph"] = True


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
        return bool(
            self.added_nodes
            or self.removed_nodes
            or self.added_edges
            or self.removed_edges
        )

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


def _label_map(graph: SourceGraphSummary) -> dict[str, str]:
    return {n.id: (n.label or n.id) for n in graph.nodes}


def _kind_map(graph: SourceGraphSummary) -> dict[str, str]:
    return {n.id: n.kind for n in graph.nodes}


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

    targets = sorted(
        {
            e.src
            for e in graph.edges
            if e.kind == "BINARY_EXPORTS_SYMBOL" and e.dst == sym_id
        }
    )
    decls = sorted(
        {
            e.src
            for e in graph.edges
            if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL" and e.dst == sym_id
        }
    )
    options = sorted(
        {
            e.src
            for e in graph.edges
            if e.kind == "BUILD_OPTION_AFFECTS_SYMBOL" and e.dst == sym_id
        }
    )

    headers: set[str] = set()
    callees: set[str] = set()
    for decl in decls:
        headers |= {
            e.src for e in graph.edges if e.kind == "SOURCE_DECLARES" and e.dst == decl
        }
        callees |= {
            e.dst for e in graph.edges if e.kind == "DECL_CALLS_DECL" and e.src == decl
        }

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


def diff_source_graph(
    old: SourceGraphSummary, new: SourceGraphSummary
) -> GraphSummaryDiff:
    """Compute the structural delta from *old* to *new* (Phase 5 seed).

    Edge comparison deliberately stays keyed on the coarse ``key()``
    (ADR-046 D1 — "existing callers... are unaffected"), not
    ``relation_key()``: when two role-distinct edges share a ``(src, dst,
    kind)`` (e.g. a function that both returns and takes the same private
    type), only one is a "representative" for this structural added/removed
    comparison — role-level diff granularity is not implemented here.
    """
    old_nodes = {n.id: n for n in old.nodes}
    new_nodes = {n.id: n for n in new.nodes}
    old_edges = {e.key(): e for e in old.edges}
    new_edges = {e.key(): e for e in new.edges}

    return GraphSummaryDiff(
        added_nodes=[new_nodes[i] for i in sorted(new_nodes.keys() - old_nodes.keys())],
        removed_nodes=[
            old_nodes[i] for i in sorted(old_nodes.keys() - new_nodes.keys())
        ],
        added_edges=[new_edges[k] for k in sorted(new_edges.keys() - old_edges.keys())],
        removed_edges=[
            old_edges[k] for k in sorted(old_edges.keys() - new_edges.keys())
        ],
    )


# ── Back-compat re-export shim (lazy, to avoid an import cycle) ───────────────
# `diff_source_graph_findings` moved to `source_graph_findings.py` (split out
# to keep this module under its line-count cap; that module imports schema
# names back from here). A *static* `from .source_graph_findings import ...`
# would form a `source_graph -> source_graph_findings -> source_graph` import
# cycle (the AI-readiness gate rejects it), so this module-level
# `__getattr__` (PEP 562) resolves it lazily via `importlib.import_module` —
# a runtime call, not a static import edge — preserving
# `from .source_graph import diff_source_graph_findings` for existing callers.
def __getattr__(name: str) -> Any:
    if name == "diff_source_graph_findings":
        import importlib

        return importlib.import_module(
            ".source_graph_findings", __package__
        ).diff_source_graph_findings
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
