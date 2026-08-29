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

"""L5 source-graph *values* (ADR-061 Phase 5 item 2's "values" third of
"separate source-graph values, construction, and comparison").

Split out of ``buildsource/source_graph.py``, which re-exports every name
here so existing imports keep working. Owns the compact
:class:`SourceGraphSummary` container (ADR-031 D7), its structural-diff
result shape :class:`GraphSummaryDiff`, the node-id constructors, and the
schema vocabulary (``NODE_KINDS``/``EDGE_KINDS``/``SOURCE_GRAPH_VERSION``).
Construction (folding :class:`~abicheck.buildsource.build_evidence.
BuildEvidence`/``SourceAbiSurface`` into a graph — ``build_source_graph`` and
its `_fold_*`/`_augment_*` helpers) and comparison (``diff_source_graph``,
``localize_symbol``) remain in ``buildsource/source_graph.py`` — a separate,
not-yet-attempted follow-up slice of the same item.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from .entity_resolver import EntityResolver
from .graph_facts import (
    CALLBACK_EDGE_KINDS,
    CONSUMER_EDGE_KINDS,
    CONSUMER_NODE_KINDS,
    LINK_PROVENANCE_EDGE_KINDS,
    LINK_PROVENANCE_NODE_KINDS,
    MACRO_DEP_EDGE_KINDS,
    TEMPLATE_EDGE_KINDS,
    TEMPLATE_NODE_KINDS,
    USE_CASE_EDGE_KINDS,
    USE_CASE_NODE_KINDS,
    VIRTUAL_DISPATCH_EDGE_KINDS,
    VIRTUAL_DISPATCH_NODE_KINDS,
    GraphEdge,
    GraphNode,
    _normalize_graph_identity,
    _normalize_if_decl_or_type,
    ensure_facts_and_resolve,
    merge_entity_facts,
)

#: Evidence-boundary label stamped on every source-graph finding (ADR-031 D9),
#: mirroring ``DataLayer.L5_SOURCE_GRAPH``. It keeps a graph-derived risk
#: visibly distinct from an artifact-proven shipped-ABI break (ADR-028 D3).
EVIDENCE_TIER_L5 = "L5_SOURCE_GRAPH"

#: Source-graph schema version, independent of the pack/build/source/snapshot
#: versions (ADR-028 D8 versioning). Bump on any breaking change to
#: :class:`SourceGraphSummary`, :class:`~abicheck.model.graph_facts.GraphNode`,
#: or :class:`~abicheck.model.graph_facts.GraphEdge`.
#:
#: 2 — ADR-046 D4 (scoped implementation): :class:`EntityResolver` is
#:     available on :class:`SourceGraphSummary`. This is a *signal* bump, not
#:     a breaking schema change — nothing reads/branches on ``schema_version``
#:     today, ``entity_resolver`` is an additive optional field
#:     (``from_dict`` defaults it to an empty, unresolved
#:     :class:`EntityResolver`), and ``GraphNode.id`` generation itself is
#:     unchanged, so a v1 pack (``schema_version: 1``, no ``entity_resolver``
#:     key) still loads and compares correctly with no forced re-collection —
#:     see ADR-046's "D4 implementation" section.
SOURCE_GRAPH_VERSION: int = 2

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
    }
    | CONSUMER_NODE_KINDS
    | USE_CASE_NODE_KINDS
    | TEMPLATE_NODE_KINDS
    | LINK_PROVENANCE_NODE_KINDS
    | VIRTUAL_DISPATCH_NODE_KINDS
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
        "METHOD_POSSIBLE_OVERRIDE",  # ADR-041 P2 item 1 (override_graph.py)
        "BINARY_EXPORTS_SYMBOL",
        "SOURCE_DECL_MAPS_TO_SYMBOL",
        "SOURCE_TYPE_MAPS_TO_DEBUG_TYPE",
        "BUILD_OPTION_AFFECTS_DECL",
        "BUILD_OPTION_AFFECTS_SYMBOL",
        "FINDING_LOCALIZES_TO_DECL",
        "FINDING_CAUSED_BY_OPTION",
    }
    | CONSUMER_EDGE_KINDS
    | USE_CASE_EDGE_KINDS
    | TEMPLATE_EDGE_KINDS
    | LINK_PROVENANCE_EDGE_KINDS
    | MACRO_DEP_EDGE_KINDS
    | VIRTUAL_DISPATCH_EDGE_KINDS
    | CALLBACK_EDGE_KINDS
)

#: L5 edge kinds that express a decl/type dependency (ADR-041 P0): a call, a
#: non-call reference to a global/constant, a parameter/field type, or a base
#: class. ``crosscheck.py``'s intra-version ``public_to_internal_dependency``
#: check and ``buildsource/source_graph.py``'s version-over-version internal-
#: dependency diff both read exactly this set, so the two stay in lockstep on
#: what "a public entity reaches an internal one" means — a struct's private
#: field type or base class is exactly the "not a call at all" risk ADR-041
#: opens with.
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
#: review, PR #555): the Python inline extractor (``source_extractors/clang.py``)
#: reuses ``call_graph.py``'s/``type_graph.py``'s pure AST walk with no
#: public/private filtering. The ADR-038 C.8 clang plugin's own producer id
#: (``"abicheck-clang-plugin"``) is deliberately NOT this constant: it only
#: walks call/reference bodies for functions ``classify()`` accepts
#: (public-header-declared), and never emits ``DECL_HAS_TYPE`` for a
#: typedef's underlying type or a variable's type.
_FULL_WALK_SOURCE_EDGES_PRODUCER = "abicheck-cc-clang-extractor"


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
    #: Which named extractor passes ran to completion (``"call_graph"`` / ``"type_graph"``),
    #: independent of how many edges they produced (ADR-041 P0 slice 2 follow-up, second Codex
    #: review). Edge *presence* alone cannot distinguish "the pass ran and found nothing" from
    #: "the pass never ran" — a project where no public struct happens to have a private field
    #: would look identical to one whose type-graph pass never executed, even though only the
    #: second is actually missing evidence. Set by ``inline._fold_call_graph``/
    #: ``_fold_type_graph`` right after a successful extraction (regardless of edge count);
    #: absent/``False`` means "unknown whether it ran" (e.g. a hand-built or pre-slice-2
    #: graph), so readers fall back to edge-presence inference for those.
    extractor_passes: dict[str, bool] = field(default_factory=dict)
    #: Which named extractor passes ran, but only over a *narrowed* scope
    #: (``changed_paths``/``scoped_units`` restricting ``_fold_call_graph``/``_fold_type_graph``
    #: to a subset of compile units — eleventh Codex review). A narrowed pass never sets
    #: ``extractor_passes`` for that name (it did not examine the whole project), but it still
    #: serializes whatever edges it *did* collect from the subset it saw. Those edges must not
    #: be treated as full-family coverage when compared against a side that ran a confirmed
    #: *full* pass — a baseline scoped to a few changed TUs having one ``TYPE_HAS_FIELD_TYPE``
    #: edge says nothing about whether the rest of the project's dependencies were ever
    #: inspected, so comparing it as if that kind were fully covered lets unrelated, never-
    #: examined dependencies read as "newly added". Set alongside (in place of)
    #: ``extractor_passes`` by ``inline._fold_call_graph``/``_fold_type_graph`` when the local
    #: ``narrowed`` flag is ``True``.
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
    #: Which named extractor passes hit per-TU diagnostics — a clang crash/timeout/degenerate
    #: AST on some subset (sixteenth Codex review). Such a run (narrowed or not) still folds
    #: edges from the TUs that *did* parse, but those edges must not vouch for "this kind was
    #: examined" over whatever scope the pass claims: the failed TUs are an unknown, untracked
    #: gap (unlike ``narrowed_scope``, which knows exactly which TUs a deliberately-scoped run
    #: examined). Set by ``inline._fold_call_graph``/
    #: ``_fold_type_graph``/``cli_buildsource_helpers._collect_call_graph``
    #: whenever the pass examined units but ``extractor.diagnostics`` was
    #: non-empty (mutually exclusive with ``extractor_passes``/``narrowed_passes``,
    #: which both require zero diagnostics — so a narrowed run with
    #: diagnostics lands here too, on top of never confirming
    #: ``narrowed_passes``, since it is even less trustworthy than either).
    degraded_passes: dict[str, bool] = field(default_factory=dict)
    #: USR-based canonical identity aliasing (ADR-046 D4, scoped
    #: implementation) — ``v1_id -> canonical_id`` for every node
    #: :meth:`resolve_entities` has resolved, plus any cross-producer
    #: identity conflicts it found. Empty/unresolved until a caller
    #: explicitly calls :meth:`resolve_entities` (opt-in, same "no cost until
    #: asked for" discipline as D1's ``occurrence_id`` and D5's
    #: ``effect_transitions``) — nothing in the default graph-build path
    #: computes it automatically.
    entity_resolver: EntityResolver = field(default_factory=EntityResolver)

    def __post_init__(self) -> None:
        # Re-register through add_node()/add_edge() rather than building the de-dup indexes
        # directly (Codex review): a caller building SourceGraphSummary(nodes=..., edges=...)
        # directly never routes through _decl_node_id/_type_node_id, so a hand-built id/
        # endpoint can carry a checkout-dependent marker -- add_node()/add_edge() normalize it
        # themselves now (their own docstrings), which can make two originally-distinct
        # constructor-seeded ids collide; a plain set/dict comprehension would then disagree
        # with len(self.nodes)/len(self.edges), the same "index vs list" desync from_dict()
        # already fixed the identical way. A no-op for the common case: on a fresh,
        # already-normalized id this just resolves+appends, identical to a direct index build.
        seeded_nodes, seeded_edges = self.nodes, self.edges
        self.nodes, self.edges = [], []
        self._node_ids: set[str] = set()
        self._edge_keys: set[tuple[str, str, str, str]] = set()
        self._node_by_id: dict[str, GraphNode] = {}
        self._edge_by_key: dict[tuple[str, str, str, str], GraphEdge] = {}
        for n in seeded_nodes:
            self.add_node(n)
        for e in seeded_edges:
            self.add_edge(e)
        # A caller-supplied entity_resolver (Codex review) is still keyed by pre-normalization
        # ids here -- a plain remap alone was already insufficient for the identical
        # from_dict() case (a stale value can survive node coalescing), so this rebuilds it
        # the same way, from the now-registered nodes. Gated on non-empty so a caller passing
        # none doesn't pay for it by default.
        if self.entity_resolver.aliases:
            self.resolve_entities()

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

        Normalizes ``node.id`` first (Codex review): the one true choke point every insertion
        path funnels through, catching a hand-built id even if its own producer forgot
        ``_decl_node_id``/``_type_node_id``. A no-op for every other id.
        """
        node.id = _normalize_if_decl_or_type(node.id)
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

        Normalizes ``edge.src``/``edge.dst`` first, same reasoning as :meth:`add_node`.
        """
        edge.src = _normalize_if_decl_or_type(edge.src)
        edge.dst = _normalize_if_decl_or_type(edge.dst)
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

    def resolve_entities(self) -> SourceGraphSummary:
        """(Re)populate :attr:`entity_resolver` from the current node set
        (ADR-046 D4, scoped implementation).

        Opt-in: not called automatically by :meth:`add_node`/
        :meth:`__post_init__`/:meth:`finalize` — computing a USR-preferring
        canonical identity for every node is extra work no current graph
        consumer needs by default (same discipline as D1's ``occurrence_id``
        staying a no-op until a producer populates its opt-in attrs).

        Always starts from a fresh :class:`EntityResolver` rather than
        reusing the existing one (CodeRabbit review): :meth:`add_node` can
        merge a *stronger* identity fact into an already-registered node
        (e.g. a later registration contributes a USR that an earlier one
        lacked) — :meth:`EntityResolver.resolve` is idempotent per node id,
        so reusing a resolver that already resolved that node from its
        earlier, weaker attrs would silently keep serving the stale
        canonical id and never see the improved evidence. Recomputing from
        scratch is the only way to guarantee ``entity_resolver`` reflects
        each node's *current* merged attrs; safe to call repeatedly (e.g.
        after further ``add_node`` calls) at the cost of re-resolving every
        node each time, not just the new ones.
        """
        self.entity_resolver = EntityResolver()
        for n in self.nodes:
            self.entity_resolver.resolve(n)
        return self

    def finalize(self) -> SourceGraphSummary:
        """Fill ``graph_id``/``coverage``; merges onto (never replaces) the latter, at every nesting level, so a persisted unrecognized field survives; return self."""

        def _section(key: str, **new: Any) -> dict[str, Any]:
            old = self.coverage.get(key)
            return {**(old if isinstance(old, dict) else {}), **new}

        self.graph_id = self.compute_graph_id()
        kinds: dict[str, int] = {}
        for n in self.nodes:
            kinds[n.kind] = kinds.get(n.kind, 0) + 1
        edge_kinds: dict[str, int] = {}
        for e in self.edges:
            edge_kinds[e.kind] = edge_kinds.get(e.kind, 0) + 1
        # A pass that ran but found zero edges is still "collected" (ADR-041 P0 slice 2
        # follow-up): edge presence alone reads identically to "the pass never ran", which
        # is the exact coverage-honesty gap ``extractor_passes`` closes. Fall back to edge
        # presence alone when the flag is absent (a hand-built or pre-slice-2 graph).
        # ``header_call_graph``/``header_type_graph`` are the header-only graph builder's
        # own pass names (ADR-041 header-only-graph addendum) — a distinct AST-walk shape
        # (one synthetic header-aggregate TU, no build integration) from the build-integrated
        # ``call_graph``/``type_graph`` passes. Only ``header_type_graph`` grants "ran, zero
        # found still collected" credit here, and only for the *structural* kinds
        # (TYPE_INHERITS/TYPE_HAS_FIELD_TYPE/DECL_HAS_TYPE): a header-only pass has true
        # project-wide visibility of those (declaration-level facts, no body needed).
        # ``DECL_CALLS_DECL``/``DECL_REFERENCES_DECL`` need a function body a header-only
        # pass only sees when it happens to be written *in the header* — its "ran" is not
        # evidence of project-wide call/reference coverage the way a build-integrated pass's
        # is, so neither ``call_edges.collected`` nor ``reference_edges.collected`` may be
        # granted from ``header_call_graph``/``header_type_graph`` alone (Codex review;
        # mirrors ``source_graph_findings._pass_trusted_kinds``'s structural-vs-body split).
        call_pass_ran = self.extractor_passes.get("call_graph", False)
        type_pass_ran = self.extractor_passes.get("type_graph", False)
        header_type_pass_ran = self.extractor_passes.get("header_type_graph", False)
        # ``include_graph``/``header_include_graph`` (build-integrated and header-only-graph
        # builder respectively) are pure file-inclusion facts with no body-dependent gap the
        # way calls/references have — a confirmed pass with zero edges (a leaf header with no
        # #includes of its own) is a genuine zero, not "never collected" (Codex review: this
        # mirrors ``has_calls``/``has_type_edges`` below, which already credit a
        # confirmed-but-empty pass; ``has_includes`` previously looked at edge presence alone).
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
        #: ADR-041 P0: TYPE_INHERITS/TYPE_HAS_FIELD_TYPE/DECL_HAS_TYPE describe type-level
        #: dependencies; DECL_REFERENCES_DECL a non-call decl reference. Both come from
        #: ``type_graph.py`` (folded alongside the call graph) or an external backend
        #: (``graph_backends.py``), so "collected" is tracked separately — a graph can have
        #: calls but no type edges (e.g. an older pack), and coverage must say so honestly.
        type_edge_kinds = ("TYPE_INHERITS", "TYPE_HAS_FIELD_TYPE", "DECL_HAS_TYPE")
        has_type_edges = (type_pass_ran or header_type_pass_ran) or any(
            e.kind in type_edge_kinds for e in self.edges
        )
        has_reference_edges = type_pass_ran or any(
            e.kind == "DECL_REFERENCES_DECL" for e in self.edges
        )
        self.coverage = {
            **self.coverage,  # forward-compat: keep any unrecognized field
            "targets": kinds.get("target", 0),
            "compile_units": kinds.get("compile_unit", 0),
            "source_decls": kinds.get("source_decl", 0),
            "binary_symbol_mappings": edge_kinds.get("SOURCE_DECL_MAPS_TO_SYMBOL", 0),
            "include_edges": _section(
                "include_edges",
                collected=has_includes,
                count=edge_kinds.get("COMPILE_UNIT_INCLUDES_FILE", 0),
            ),
            "call_edges": _section(
                "call_edges",
                collected=has_calls,
                count=edge_kinds.get("DECL_CALLS_DECL", 0),
            ),
            "type_edges": _section(
                "type_edges",
                collected=has_type_edges,
                count=sum(edge_kinds.get(k, 0) for k in type_edge_kinds),
            ),
            "reference_edges": _section(
                "reference_edges",
                collected=has_reference_edges,
                count=edge_kinds.get("DECL_REFERENCES_DECL", 0),
            ),
            "node_kinds": dict(sorted(kinds.items())),
            "edge_kinds": dict(sorted(edge_kinds.items())),
        }
        return self

    # -- (de)serialization --------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
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
        # Sparse: omitted entirely (never an empty {"aliases": {}, ...})
        # unless resolve_entities() was actually called (ADR-046 D4) — same
        # opt-in-cost convention as occurrence_id/effect_transitions.
        if self.entity_resolver.aliases or self.entity_resolver.conflicts:
            d["entity_resolver"] = self.entity_resolver.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SourceGraphSummary:
        # Defensive ``.get`` parsing so a newer/hand-edited summary never aborts
        # a load (evidence/CLAUDE.md forward-compat rule); ``indexes`` are
        # derived and intentionally not read back. ``extractor_passes``
        # defaults to {} for a pre-slice-2 pack (ADR-041 P0 slice 1).
        raw_entity_resolver = d.get("entity_resolver")
        _raw_entity_resolver: dict[str, Any] = (
            raw_entity_resolver if isinstance(raw_entity_resolver, dict) else {}
        )
        obj = cls(
            schema_version=int(d.get("schema_version", SOURCE_GRAPH_VERSION)),
            graph_id=str(d.get("graph_id", "")),
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
            entity_resolver=EntityResolver.from_dict(_raw_entity_resolver),
        )
        # add_node/add_edge coalesce migration-colliding ids (Codex review).
        for raw_node in d.get("nodes", []):
            obj.add_node(GraphNode.from_dict(raw_node))
        for raw_edge in d.get("edges", []):
            obj.add_edge(GraphEdge.from_dict(raw_edge))
        if _raw_entity_resolver:
            obj.resolve_entities()  # rebuild from coalesced facts (Codex review)
        obj.finalize()  # recomputes graph_id + coverage post-migration
        return obj


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


def _vtable_node_id(identity: str) -> str:
    # A vtable's identity is its owning record's, which for a polymorphic anonymous struct
    # can embed the checkout marker (Codex review) -- no-op for a fresh build's own
    # already-normalized identity, matters for any other caller and for the migration gate.
    return f"vtable://{_normalize_graph_identity(identity)}"


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


#: SourceEntity.kind → graph type-node kind. Records/classes/unions all map to
#: ``record_type``; enums and typedefs get their own node kind so reachability
#: queries can distinguish them (ADR-031 D2).
_TYPE_NODE_KINDS: dict[str, str] = {"enum": "enum_type", "typedef": "typedef"}


def _type_node_kind(decl_kind: str) -> str:
    return _TYPE_NODE_KINDS.get(decl_kind, "record_type")


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
