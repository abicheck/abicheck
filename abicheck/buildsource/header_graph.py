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

"""Header-only (L2) semantic graph — no build integration required (ADR-041
header-only-graph addendum).

ADR-041 P0 built the semantic impact graph (``type_graph.py``/``call_graph.py``
folded into ``source_graph.py``) as an *L4/L5* feature: it needs a real build
(a ``compile_commands.json`` and a per-translation-unit ``clang -ast-dump=json``
replay of full bodies) via ``inline.collect_inline_pack``/``inline_graph_fold``.

That build requirement is not fundamental to the "no call at all" risk the ADR
opens with — a public struct with a private field type, or a public class
inheriting an internal base, is visible in the **declarations alone**, with no
body needed. This module builds a smaller, strictly-weaker-recall graph
straight from an ordinary L2 header scan:

- :func:`build_header_only_graph` seeds ``source_decl`` nodes for every
  function/variable in the already-parsed :class:`~abicheck.model.AbiSnapshot`
  (visibility from ``Function.origin``/``Variable.origin`` — the same
  ``ScopeOrigin`` classification :func:`abicheck.provenance.apply_provenance`
  already computes when a public-header set is given),
  then folds ``type_graph.parse_clang_ast_types()``/
  ``call_graph.parse_clang_ast_calls()`` over the *same* header-aggregate
  ``clang -ast-dump=json`` tree the L2 clang frontend (``dumper_clang.py``)
  already produces when ``--ast-frontend clang`` is selected.

Both parsers are pure functions over a bare AST dict (ADR-041 P0's own
docstring: "unit-tested without a compiler") — nothing about them assumes a
real, build-integrated translation unit. Reusing them here needs zero changes.

**What is structurally available vs. not, from headers alone:**

- ``TYPE_INHERITS`` / ``TYPE_HAS_FIELD_TYPE`` / ``DECL_HAS_TYPE`` /
  ``SOURCE_DECLARES`` — fully available. A base class, a field type, and a
  parameter/return type are declaration-level facts; no function body is
  needed. This is also exactly the ADR's own motivating example.
- ``DECL_CALLS_DECL`` / ``DECL_REFERENCES_DECL`` — only for declarations whose
  *body* is actually written in a header (inline/template/constexpr
  functions). An ordinary out-of-line function has a prototype but no body in
  a header, so it contributes no call/reference edges here — a real, honestly
  bounded subset of the L4/L5 graph's recall, not a false claim of parity.
- Anything from ADR-031's *build*-level schema (``target``/``compile_unit``/
  ``build_option`` nodes, ``TARGET_HAS_SOURCE``, …) — not available at all;
  there is no ``BuildEvidence`` in a header-only world, so this module never
  calls :func:`~abicheck.buildsource.source_graph_build.build_source_graph`.

**Coverage honesty (ADR-031 D9):** every node/edge this module creates itself
carries ``provenance="header_ast_l2"`` and the graph's ``extractor_passes`` use
the module's own pass names (:data:`HEADER_CALL_GRAPH_PASS` /
:data:`HEADER_TYPE_GRAPH_PASS`), distinct from the build-integrated
``call_graph``/``type_graph`` pass names — so a header-only graph is never
mistaken for (and never grants the same build-integrated "confirmed full
pass" trust to a comparison against) a real L4/L5 graph. A header-only
confirmation only ever grants trust for the structural kinds it has genuine
project-wide visibility of (``model.source_graph_coverage.HEADER_FULL_VISIBILITY_KINDS``)
— never the two body-dependent kinds, regardless of the other side's shape.

**Header include graph** (:class:`ClangHeaderIncludeExtractor`): an optional,
separate ``clang -M`` pass per top-level header — reusing
``include_graph.ClangIncludeExtractor``'s vetted depfile-replay logic via a
throwaway per-header ``BuildEvidence``/``CompileUnit`` rather than
duplicating its argv-sanitization/timeout/diagnostics handling — adds
``COMPILE_UNIT_INCLUDES_FILE`` edges from each public entry header to every
file it (transitively) includes. This is advisory structure, not a
classification override: a "private" header transitively reached from a
public entry header is still labelled by its own declaring-file origin
(ADR-031 D9 coverage honesty — inclusion reachability and declaration
provenance are different facts), but the edge lets `graph explain`/future
triage show *how* a public entry reaches it.

Same authority boundary as the rest of ADR-028/041: this can only explain,
localize, or add a RISK/API_BREAK finding — never a shipped-ABI verdict.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from ..fact_provenance import func_fact_key, var_fact_key
from ..model import AbiSnapshot, ScopeOrigin, resolved_fact_value
from ..model.graph_entity_identity import (
    IDENTITY_STATE_ATTR,
    GraphEntityIdentity,
    SnapshotIdentities,
    endpoint_key,
    register_identity_alias,
    snapshot_identities,
)
from ..model.graph_facts import (
    CONF_HIGH,
    CONF_REDUCED,
    GraphEdge,
    GraphNode,
)
from ..model.source_graph import SourceGraphSummary, _header_node_id
from ..model.source_graph_coverage import (
    HEADER_INCLUDE_GRAPH_PASS as _HEADER_INCLUDE_GRAPH_PASS,
)
from ..provenance import (
    build_public_set,
    classify_origin,
    split_include_roots,
)
from .header_graph_ast_projection import (
    _PROVENANCE,
    HEADER_TYPE_GRAPH_PASS,
    HeaderGraphAstProjection,
    project_header_graph_ast,
    seed_ast_graph,
)
from .type_graph import (
    EDGE_DECL_HAS_TYPE,
    EDGE_TYPE_HAS_FIELD_TYPE,
    EDGE_TYPE_INHERITS,
    RESOLUTION_UNIQUE_CANDIDATE,
    RESOLUTION_UNRESOLVED,
    TypeEdge,
    _base_type_name,
    _is_excluded_type,
    _resolve_nested_type_names,
    augment_graph_with_types,
)

if TYPE_CHECKING:
    from ..model import Function, Variable

#: The one pass name this module still owns. ``HEADER_CALL_GRAPH_PASS``/
#: ``HEADER_TYPE_GRAPH_PASS`` moved to ``header_graph_ast_projection`` with
#: the code that stamps them -- import them from there, not from here
#: (ADR-061: no delegation-only re-export).
HEADER_INCLUDE_GRAPH_PASS = _HEADER_INCLUDE_GRAPH_PASS


def _identity_attrs(ident: GraphEntityIdentity) -> dict[str, Any]:
    """``{"identity": "unresolved"}`` for an explicit unresolved node, so a
    reader need not parse the id; nothing for a resolved one."""
    return {} if ident.resolved else {IDENTITY_STATE_ATTR: ident.state.value}


#: Provenance tag for nodes/edges built straight from the flat
#: :class:`~abicheck.model.AbiSnapshot` (no AST at all) — distinct from
#: :data:`_PROVENANCE` (the clang-AST-derived path) so a reader can tell
#: which resolution tier produced a given node/edge.
_FLAT_PROVENANCE = "header_flat_l2"


def _seed_flat_type_node(
    graph: SourceGraphSummary,
    header_node: Callable[[str], str],
    ident: GraphEntityIdentity,
    name: str,
    kind: str,
    origin: ScopeOrigin,
    source_header: str | None,
) -> None:
    """Seed one record/enum type node straight from its own snapshot entry.

    Unlike the AST path above, ``RecordType``/``EnumType`` already carry their
    own ``origin``/``source_header`` (ADR-015 provenance, populated by
    :func:`abicheck.provenance.apply_provenance` from the same
    public-header inputs) — no
    ``classify_origin`` re-derivation needed here.
    """
    node_id = ident.node_id
    attrs: dict[str, Any] = (
        {"visibility": origin.value} if origin != ScopeOrigin.UNKNOWN else {}
    )
    attrs.update(_identity_attrs(ident))
    graph.add_node(
        GraphNode(
            id=node_id,
            kind=kind,
            label=name,
            provenance=_FLAT_PROVENANCE,
            confidence=CONF_HIGH,
            attrs=attrs,
        )
    )
    if source_header:
        hid = header_node(source_header)
        graph.add_edge(
            GraphEdge(
                src=hid,
                dst=node_id,
                kind="SOURCE_DECLARES",
                provenance=_FLAT_PROVENANCE,
                confidence=CONF_HIGH,
            )
        )


class _FlatTypeIndex:
    """The snapshot's declared records/enums, by qualified spelling and by
    leaf -- what a flat (AST-less) type reference is resolved against.

    Each entry maps to the type's own graph endpoint key (from the shared
    :func:`~abicheck.model.graph_entity_identity.snapshot_identities`
    table), so an edge lands on exactly the node the type was seeded under,
    and two same-leaf types in different scopes (``ns::W``/``other::W``)
    stay two nodes."""

    def __init__(self, snapshot: AbiSnapshot, ids: SnapshotIdentities) -> None:
        self.by_leaf: dict[str, list[tuple[str, str]]] = {}
        entries: list[tuple[str, GraphEntityIdentity]] = [
            *(
                (r.qualified_name or r.name, i)
                for r, i in zip(snapshot.types, ids.records)
            ),
            *(
                (e.qualified_name or e.name, i)
                for e, i in zip(snapshot.enums, ids.enums)
            ),
        ]
        for qname, ident in entries:
            if qname:
                leaf = qname.rsplit("::", 1)[-1]
                self.by_leaf.setdefault(leaf, []).append((qname, endpoint_key(ident)))

    def resolve(self, raw: str) -> tuple[str, str]:
        """``(endpoint key, resolution)`` for a type spelling -- mirroring
        ``type_graph._resolve_type_name``'s contract, without a scope walk.

        A declared type whose qualified name equals the spelling wins;
        otherwise one whose name is consistent with it up to a missing scope
        prefix on either side (clang's ``qualType`` is "as written", so a
        sibling-namespace reference prints ``detail::Impl`` for
        ``ns::detail::Impl``; a scope-less legacy record is just ``Impl``).
        Exactly one such entity resolves. None: an external name, kept as
        spelled (unresolved). Several: ambiguous, ``""`` -- the reference
        must not join any of them."""
        base = _base_type_name(raw)
        if not base or _is_excluded_type(base):
            return "", RESOLUTION_UNRESOLVED
        candidates = self.by_leaf.get(base.rsplit("::", 1)[-1], [])
        exact = {k for q, k in candidates if q == base}
        keys = exact or {
            k
            for q, k in candidates
            if q.endswith("::" + base) or base.endswith("::" + q)
        }
        if len(keys) == 1:
            return next(iter(keys)), RESOLUTION_UNIQUE_CANDIDATE
        if keys:
            return "", RESOLUTION_UNRESOLVED
        return base, RESOLUTION_UNRESOLVED


def _flat_structural_type_edges(
    snapshot: AbiSnapshot, ids: SnapshotIdentities
) -> list[TypeEdge]:
    """Derive ``TYPE_INHERITS``/``TYPE_HAS_FIELD_TYPE``/``DECL_HAS_TYPE`` edges
    straight from the already-parsed flat :class:`~abicheck.model.AbiSnapshot`
    — no clang AST needed at all. Every L2 backend (castxml, the default, or
    clang) populates ``RecordType.bases``/``.fields``, ``Function.
    return_type``/``.params``, and ``Variable.type`` identically, so this
    works uniformly regardless of which frontend parsed the headers, at zero
    extra compiler-invocation cost. Confidence is always
    :data:`~abicheck.model.graph_facts.CONF_REDUCED` — even a
    :data:`~abicheck.buildsource.type_graph.RESOLUTION_UNIQUE_CANDIDATE` match
    here is a weaker guess than the AST path's scope-walk resolution.

    Every endpoint is keyed through the shared identity table (I1): a record
    by its qualified name, a declaration by its linker name (or its explicit
    ``unresolved`` id), so these edges meet the nodes the header graph and the
    public-surface builder seed for the same entities.
    """
    index = _FlatTypeIndex(snapshot, ids)
    edges: list[TypeEdge] = []

    def emit(src: str, raw: str, kind: str, role: str) -> None:
        if not src or not raw:
            return
        # A field/parameter typed e.g. ``std::vector<Private>`` must not stop
        # at the whole template spelling — ``_resolve_nested_type_names``
        # (the same pure, AST-independent string walk the clang path already
        # uses) also surfaces the private template argument itself, the
        # actual dependency a public-to-internal-dependency check cares about
        # (Codex review).
        seen: set[str] = set()
        for candidate in _resolve_nested_type_names(raw):
            name, resolution = index.resolve(candidate)
            if not name or name in seen:
                continue
            seen.add(name)
            edges.append(TypeEdge(src, name, kind, CONF_REDUCED, role, "", resolution))

    for rt, ident in zip(snapshot.types, ids.records):
        src = endpoint_key(ident)
        # Fact[T]-bridged read (ADR-063 Phase 0): value-preserving, see
        # `model.resolved_fact_value`'s own docstring. ADR-063 Phase 5B audit
        # note: stays on the plain collapse deliberately — a single-snapshot
        # graph (never an old/new pair), and this package's governing rule
        # caps everything it feeds at API_BREAK_KINDS/RISK_KINDS, so a gap
        # here can only omit an edge, never fabricate a BREAKING finding.
        for base in resolved_fact_value(rt.bases_fact, []):
            emit(src, base, EDGE_TYPE_INHERITS, "base")
        for fld in rt.fields:
            emit(src, fld.type, EDGE_TYPE_HAS_FIELD_TYPE, "field")
    for fn, ident in zip(snapshot.functions, ids.functions):
        src = endpoint_key(ident)
        emit(src, fn.return_type, EDGE_DECL_HAS_TYPE, "return")
        for p in fn.params:
            emit(src, p.type, EDGE_DECL_HAS_TYPE, "param")
    for var, ident in zip(snapshot.variables, ids.variables):
        emit(endpoint_key(ident), var.type, EDGE_DECL_HAS_TYPE, "var")
    return edges


def _seed_flat_graph(
    graph: SourceGraphSummary,
    snapshot: AbiSnapshot,
    header_node: Callable[[str], str],
    ids: SnapshotIdentities,
) -> None:
    """Recover structural edges from the flat snapshot when no clang AST exists.

    clang missing/unselected (the default L2 backend is castxml) — still
    recover the three structural edge kinds directly from the flat snapshot
    already parsed, rather than leaving the graph at declaration-visibility
    nodes only. No second compiler invocation needed: every L2 backend
    populates ``RecordType.bases``/``.fields``/``Function.return_type``/
    ``.params``/``Variable.type`` identically (see
    :func:`_flat_structural_type_edges`).
    """
    for rt, ident in zip(snapshot.types, ids.records):
        _seed_flat_type_node(
            graph,
            header_node,
            ident,
            rt.qualified_name or rt.name,
            "record_type",
            rt.origin,
            rt.source_header,
        )
    for en, ident in zip(snapshot.enums, ids.enums):
        _seed_flat_type_node(
            graph,
            header_node,
            ident,
            en.qualified_name or en.name,
            "enum_type",
            en.origin,
            en.source_header,
        )
    augment_graph_with_types(graph, _flat_structural_type_edges(snapshot, ids))
    # Only the structural pass ran — no bodies were ever visible to the flat
    # model, in any circumstance, so ``HEADER_CALL_GRAPH_PASS`` must never be
    # stamped here (that would falsely vouch for a project-wide zero on
    # ``DECL_CALLS_DECL``/``DECL_REFERENCES_DECL``). Nor may
    # ``HEADER_TYPE_GRAPH_PASS`` be stamped when the snapshot itself recorded a
    # PE/Mach-O header-scope fallback (``scope_fallback`` — mangling mismatch or
    # an unavailable header backend): that state's ``functions``/``types`` are
    # placeholder export-table entries or a PDB-recovered approximation, not a
    # genuine header parse, so this was never a real structural scan of the
    # declared types at all (Codex review) — stamping it would let a later real
    # header dump's first structural edge misread as newly added.
    if not snapshot.scope_fallback:
        graph.extractor_passes[HEADER_TYPE_GRAPH_PASS] = True


def build_header_only_graph(
    snapshot: AbiSnapshot,
    ast_root: dict[str, Any] | None = None,
    *,
    ast_projection: HeaderGraphAstProjection | None = None,
    public_header_paths: list[str] | None = None,
    public_dir_paths: list[str] | None = None,
    header_paths: list[str] | None = None,
    fact_provenance: dict[str, str] | None = None,
    include_search_dirs: list[str] | None = None,
) -> SourceGraphSummary:
    """Build a header-only semantic graph from an L2 :class:`AbiSnapshot`.

    *ast_root* is a parsed ``clang -ast-dump=json`` tree over the same header
    aggregate the L2 clang frontend parses (``dumper._clang_header_dump``) —
    ``None`` when clang was unavailable/not selected, in which case the graph
    still carries ``source_decl``/``header`` nodes (declaration-level
    visibility from the snapshot alone) but no type/call edges.

    *ast_projection* is that same evidence already reduced to
    :class:`HeaderGraphAstProjection`, so the caller can drop the parsed
    tree before this builder allocates anything (see that module's
    docstring for the measurement). Observably equivalent to passing the
    tree it came from, ``None`` included. Passing both raises.

    *public_header_paths*/*public_dir_paths* are the same public-header
    inputs already threaded through
    :func:`abicheck.provenance.apply_provenance` — required for anything to
    classify as ``public_header``/``private_header`` rather than ``unknown``
    (provenance stays opt-in, matching the rest of the L2 pipeline).

    *header_paths* are the top-level header files the caller parsed (the
    ``-H``/``--header`` inputs, already expanded from any directory) —
    pre-seeded as ``header`` nodes even when they declare nothing themselves
    (a pure ``#include``-only umbrella header is still a real public entry
    point). Without this, such a header would get no node at all, leaving a
    later :func:`ClangHeaderIncludeExtractor` include edge with no valid
    source endpoint to attach to.

    *fact_provenance* is *snapshot*'s own ``AbiSnapshot.fact_provenance`` map
    (empty/``None`` for a single-backend snapshot, real for a
    ``--ast-frontend hybrid`` merge) — G31 Phase C's hybrid-graph
    provenance-tagging. A hybrid snapshot's ``source_decl`` node ``attrs``
    already carries ``visibility`` (the entity's own ``ScopeOrigin``, unified
    identically for a castxml-primary or clang-only-appended declaration by
    ``provenance.apply_provenance()`` after the merge — so the value itself
    never differs by backend); what it doesn't carry is WHICH backend that
    entity's declaration record came from in the first place, which is the
    one thing a hybrid merge can't unify away (a clang-only-appended function
    exists in the graph at all only because clang, not castxml, saw it).
    When *fact_provenance* names a node's declaration (via
    ``fact_provenance.func_fact_key``/``var_fact_key(mangled, "visibility")``
    — see ``dumper_hybrid.merge_snapshots``'s own "Declaration-existence
    provenance" doc bullet for what writes this), the node's ``attrs`` also
    gets a ``visibility_provenance`` entry (``"castxml"``/``"clang"``) — pure
    enrichment, additive only, never changing an existing attr's meaning or
    absent for a non-hybrid/unrecorded declaration. No current L5 detector
    reads it; it exists so a future one can discount/flag a RISK finding
    resting on a clang-backfilled-only declaration specifically, the same
    class of "this finding's evidence, verify it if that matters"
    annotation ``LAYOUT_UNVERIFIABLE`` already provides for layout facts
    (see AGENTS.md's "Findings emitted from absent evidence" entry).

    *include_search_dirs* mirrors ``apply_provenance``'s own parameter of
    the same name -- a caller's explicit ``-I``/``--include`` roots, folded
    into the public-directory set the same way, once a real public-header
    set already opted classification in. Without it, ``header_node()``
    below (which classifies a header-level graph node fresh from
    ``public_header_paths``/``public_dir_paths`` alone) could disagree with
    the per-declaration ``entity.origin`` this snapshot's ``apply_provenance``
    call already widened -- a transitively-included header reached only
    under an explicit ``-I`` root would classify ``public_header`` for its
    own declarations but still ``private_header`` for its own header node
    (Codex review, fresh evidence).
    """
    if ast_root is not None and ast_projection is not None:
        raise ValueError(
            "build_header_only_graph takes ast_root or ast_projection, not both"
        )
    graph = SourceGraphSummary()
    header_segs, dir_segs, have_public_set = build_public_set(
        public_header_paths, public_dir_paths
    )
    dir_segs, compile_only_dir_segs = split_include_roots(
        header_segs, dir_segs, include_search_dirs
    )
    # Bound once, here, because every consumer below must answer a header's
    # origin identically -- `extract.public_root_ownership`'s own contract.
    # Threading the four context values instead let one call site drop
    # `compile_only_dir_segs` and quietly answer `PRIVATE_HEADER` where
    # `apply_provenance` answered `UNKNOWN` (CodeRabbit review).
    classify_path = partial(
        classify_origin,
        public_header_segs=header_segs,
        public_dir_segs=dir_segs,
        have_public_set=have_public_set,
        compile_only_dir_segs=compile_only_dir_segs,
    )
    # A header's origin is a pure function of its path under this one bound
    # context, and a real graph asks for the same few hundred headers once
    # per declaration they declare -- so each path is classified once.
    origins: dict[str, ScopeOrigin] = {}

    def classify(path: str) -> ScopeOrigin:
        origin = origins.get(path)
        if origin is None:
            origin = origins[path] = classify_path(path)
        return origin

    #: Header nodes already added. Re-adding one merges an identical fact --
    #: a no-op beyond the re-resolve it costs -- so a repeat returns early.
    header_ids: dict[str, str] = {}

    def header_node(path: str) -> str:
        node_id = header_ids.get(path)
        if node_id is not None:
            return node_id
        node_id = _header_node_id(path)
        origin = classify(path)
        attrs = {"visibility": origin.value} if origin != ScopeOrigin.UNKNOWN else {}
        graph.add_node(
            GraphNode(
                id=node_id,
                kind="header",
                label=path,
                provenance=_PROVENANCE,
                confidence=CONF_HIGH,
                attrs=attrs,
            )
        )
        header_ids[path] = node_id
        return node_id

    for h in header_paths or ():
        header_node(h)

    ids = snapshot_identities(snapshot)

    def seed_decl(entity: Function | Variable, ident: GraphEntityIdentity) -> None:
        node_id = ident.node_id
        for alias in ident.aliases:
            register_identity_alias(graph, alias, node_id)
        attrs: dict[str, Any] = (
            {"visibility": entity.origin.value}
            if entity.origin != ScopeOrigin.UNKNOWN
            else {}
        )
        attrs.update(_identity_attrs(ident))
        if fact_provenance:
            mangled = getattr(entity, "mangled", "")
            if mangled:
                # A mangled symbol name is either a function's or a
                # variable's, never genuinely both (Itanium/MSVC mangling
                # encodes the signature), so trying the function key first
                # and falling back to the variable key is safe -- no runtime
                # isinstance/import needed for a TYPE_CHECKING-only pair.
                origin_producer = fact_provenance.get(
                    func_fact_key(mangled, "visibility")
                ) or fact_provenance.get(var_fact_key(mangled, "visibility"))
                if origin_producer:
                    attrs["visibility_provenance"] = origin_producer
        graph.add_node(
            GraphNode(
                id=node_id,
                kind="source_decl",
                label=entity.name or node_id,
                provenance=_PROVENANCE,
                confidence=CONF_HIGH,
                attrs=attrs,
            )
        )
        if entity.source_header:
            hid = header_node(entity.source_header)
            graph.add_edge(
                GraphEdge(
                    src=hid,
                    dst=node_id,
                    kind="SOURCE_DECLARES",
                    provenance=_PROVENANCE,
                    confidence=CONF_HIGH,
                )
            )

    for fn, ident in zip(snapshot.functions, ids.functions):
        seed_decl(fn, ident)
    for var, ident in zip(snapshot.variables, ids.variables):
        seed_decl(var, ident)

    if ast_projection is not None:
        seed_ast_graph(graph, ast_projection, header_node, classify)
    elif ast_root is not None:
        seed_ast_graph(graph, project_header_graph_ast(ast_root), header_node, classify)
    else:
        _seed_flat_graph(graph, snapshot, header_node, ids)

    return graph.finalize()


@dataclass
class ClangHeaderIncludeExtractor:
    """Per-header include-closure extractor via ``clang -M`` (integration-only).

    A header-only world has no real compile units — only the top-level
    header paths a caller parses. Rather than duplicating
    ``include_graph.ClangIncludeExtractor``'s vetted depfile-replay logic
    (argv sanitization, timeouts, per-unit diagnostics), :meth:`extract`
    drives it through a throwaway :class:`~abicheck.buildsource.build_evidence.BuildEvidence`
    with one synthetic :class:`~abicheck.buildsource.build_evidence.CompileUnit`
    per header — its ``id`` set to that header's graph node id
    (:func:`abicheck.model.source_graph._header_node_id`), so
    :func:`abicheck.buildsource.include_graph.augment_graph_with_includes`
    can fold the result straight onto the already-built
    :class:`~abicheck.model.source_graph.SourceGraphSummary` without any
    extra id translation. A missing ``clang`` (or any per-header failure)
    degrades to an empty/partial map — never aborts the dump (ADR-028 D3).
    """

    clang_bin: str = "clang++"

    def available(self) -> bool:
        import shutil

        return shutil.which(self.clang_bin) is not None

    def extract(
        self,
        headers: list[str],
        includes: list[str],
        *,
        language: str = "CXX",
        sysroot: str | None = None,
        nostdinc: bool = False,
        gcc_options: str | None = None,
        gcc_option_tokens: tuple[str, ...] = (),
    ) -> tuple[dict[str, list[str]], list[str]]:
        """Return ``({header_node_id: [included path, ...]}, diagnostics)``.

        *gcc_options* is the same free-form ``--gcc-options`` string
        (e.g. ``"-I build/generated -DFOO=1"``) the AST pass
        (``dumper._clang_header_dump``) also receives — tokenized the same
        way (``shlex.split``) so a define/include gated by it doesn't leave
        this include pass silently missing edges the AST pass could resolve
        (Codex review: an earlier version of this method only forwarded
        *gcc_option_tokens*, the deferred-``-isystem`` roots, not this).
        *sysroot*/*nostdinc* are the same cross/hermetic-toolchain flags the
        AST pass receives (``--sysroot=<path>``/``-nostdinc``) — without
        them a cross-compiled or ``--nostdinc`` header context resolves this
        include pass against the *host*'s system headers instead (or fails
        outright under ``-nostdinc``), producing missing or wrong
        ``COMPILE_UNIT_INCLUDES_FILE`` edges for the same headers the AST
        pass parsed correctly (Codex review).
        """
        from .._compiler_options import split_gcc_options
        from .build_evidence import BuildEvidence, CompileUnit
        from .include_graph import ClangIncludeExtractor

        if not self.available():
            return {}, [f"{self.clang_bin} not found in PATH"]
        extra_tokens = split_gcc_options(gcc_options) if gcc_options else []
        toolchain_tokens: list[str] = []
        if sysroot:
            toolchain_tokens.append(f"--sysroot={sysroot}")
        if nostdinc:
            toolchain_tokens.append("-nostdinc")
        compile_units = [
            CompileUnit(
                id=_header_node_id(h),
                source=h,
                argv=[
                    *(f"-I{i}" for i in includes),
                    *toolchain_tokens,
                    *extra_tokens,
                    *gcc_option_tokens,
                    h,
                ],
                language=language,
            )
            for h in headers
        ]
        extractor = ClangIncludeExtractor(clang_bin=self.clang_bin)
        include_map = extractor.extract_from_build(
            BuildEvidence(compile_units=compile_units)
        )
        # `clang -M`'s depfile lists the source itself as the first
        # prerequisite (`foo.o: foo.h bar.h ...`) — here the "source" is the
        # header itself, which would otherwise create a `header X includes
        # header X` self-loop once folded (the header's own node id doubles
        # as both the synthetic compile unit id and its own include target).
        # Path-resolve both sides so a relative vs. absolute spelling
        # mismatch still filters correctly.
        from pathlib import Path

        filtered: dict[str, list[str]] = {}
        for h in headers:
            paths = include_map.get(_header_node_id(h), [])
            if not paths:
                continue
            try:
                self_resolved = Path(h).resolve()
            except OSError:
                self_resolved = Path(h)
            kept = []
            for p in paths:
                try:
                    if Path(p).resolve() == self_resolved:
                        continue
                except OSError:
                    pass
                kept.append(p)
            if kept:
                filtered[_header_node_id(h)] = kept
        return filtered, list(extractor.diagnostics)
