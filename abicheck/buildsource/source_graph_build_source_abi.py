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

"""ADR-061 Phase 5 item 2: SourceGraphSummary construction, Phases 3-4.

Split out of ``source_graph.py`` (construction half — ADR-031 D2 Phases 3-4,
plus the ADR-038 C.9 ``source_edges`` fold): enriches the Phase-2 graph
:func:`~abicheck.buildsource.source_graph_build.build_source_graph` built
from a plain :class:`~abicheck.buildsource.build_evidence.BuildEvidence`
with an optional ADR-030 :class:`~abicheck.buildsource.source_abi.
SourceAbiSurface` — public-reachability declarations/types/macros, their
source-to-binary mappings, and the raw ``source_edges`` dependency rows a
Clang-plugin/replay collection captured. Split into its own module purely
to keep ``source_graph_build.py`` under the new-file line-count cap; called
from the end of :func:`build_source_graph` there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..model.graph_facts import (
    CONF_HIGH,
    CONF_REDUCED,
    CONF_UNKNOWN,
    GraphEdge,
    GraphNode,
    _decl_node_id,
    _type_node_id,
    register_fact,
)
from ..model.source_graph import (
    _FULL_WALK_SOURCE_EDGES_PRODUCER,
    DEPENDENCY_EDGE_KINDS,
    SourceGraphSummary,
    _debug_type_node_id,
    _header_node_id,
    _macro_node_id,
    _symbol_node_id,
    _type_node_kind,
)

if TYPE_CHECKING:
    from .source_abi import SourceAbiSurface, SourceEntity


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
    # source_link.py accounts for a real export under several other mappings
    # too (each keyed *by* the symbol, unlike decl_to_sym): template-
    # instantiation/synthesized/allocator-interposer/undocumented exports.
    # Omitting these left no binary_symbol node for a downstream pass's
    # join-only-onto-an-existing-node rule to find (Codex review).
    for mapping_name in (
        "template_instantiation_symbol_to_decl",
        "synthesized_symbol_to_owner",
        "allocator_interposer_symbol_to_owner",
        "non_public_symbol_to_reason",
    ):
        for symbol in surface.mappings.get(mapping_name, {}):
            if symbol:
                export_symbol(symbol, CONF_REDUCED)

    declarations = (
        *surface.reachable_declarations,
        *surface.reachable_templates,
        *surface.reachable_inline_bodies,
    )
    # An entity routed to reachable_templates/reachable_inline_bodies shares its identity()
    # (mangled name, or qualified_name+signature_hash) with the plain "function"-kind
    # declaration entity clang.py *also* emits for the same function -- both land on the same
    # node id via _decl_node_id below, and add_node keeps only the first writer's attrs.
    # reachable_declarations is iterated first in `declarations` above, so for any function
    # that also has an inline/template rendition, the winning node's own
    # attrs["decl_kind"] is always "function"/"method", never "inline"/"template" -- silently
    # losing the one signal that distinguishes "body compiled into every consumer TU that
    # includes this header" from "ordinary out-of-line body, compiled into this library's
    # binary only" (Codex review). Compute the identity set up front so every entity sharing
    # it gets the *same* attrs["consumer_compiled_body"] value regardless of which one wins.
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
