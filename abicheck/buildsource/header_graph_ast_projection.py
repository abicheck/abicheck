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

"""Everything ``build_header_only_graph`` reads out of a clang AST, projected.

Why this module exists
----------------------
``service_header_graph_attach._attach_header_graph`` parses a whole
``clang -ast-dump=json`` document into Python dicts and then builds the
header-only (L5) semantic graph **while that tree is still resident**. On a
real library that overlap is the member's peak: measured on oneDAL's
``libonedal_core.so.3`` the AST alone is ~1.4-1.5 GiB of dicts and the graph
adds ~271 MiB on top of it (``docs/contribute/measurements/
header-graph-attach-memory.md``).

The graph builder never needs the tree itself. It reads exactly four pure,
compact projections of it:

* :func:`~abicheck.buildsource.type_graph_index.index_declared_type_files` --
  qualified type name -> declaring file;
* :func:`~abicheck.buildsource.type_graph_walk.parse_clang_ast_types` --
  ``TypeEdge`` list;
* :func:`~abicheck.buildsource.call_graph.parse_clang_ast_calls` --
  ``CallEdge`` list;
* :func:`~abicheck.buildsource.type_graph_index.index_declared_entity_files` --
  declared identity -> declaring file, consulted only to resolve the
  *source* side of a ``DECL_REFERENCES_DECL`` edge.

It also carries, from the same parse, the override, template and callback
facts (``semantic_facts``, :mod:`~abicheck.buildsource.semantic_fact_folds`
-- the owner the L5 pass folds those families through too), so a
headers-only graph has the facts its AST holds rather than none of them.

Taking all of it **before** the graph exists lets the caller drop its
reference to the AST first, so the two never occupy memory at the same time
and the graph's own long-lived objects land in arenas the AST parse has
already released rather than pinning them.

What that is and is not worth, measured on that same library (the
2026-09-19 follow-up in the doc above, three runs per side) rather than
argued: the graph build's own residency cost goes from **+147 MiB to
-27 MiB** against the AST-parse level, and the projection costs 23.6 MiB
against a 1044 MiB tree. It does **not** lower the member's peak
(2216.3 -> 2215.3 MiB), and steady-state retention once the attach returns
is ~8-12 MiB *higher* -- the peak is inside ``json.load``, where the
document is held as one ``str`` while the tree is built from it, so it is
``document + tree`` and never ``tree + graph``. Don't cite this module as
having fixed that; it did not. The open lever is pruning the tree, and this
projection is the statement of what such a prune would have to preserve.

This is a *memory-ordering* change only. The same four functions run, over
the same tree, in the same order, and their results are handed to the same
consumer unchanged -- no finding, verdict, exit code or report section can
move, ``DECL_CALLS_DECL`` edges included.

``entity_files`` preserves the caller's own laziness exactly:
:func:`_unseeded_decl_endpoints` computed it only when at least one
``DECL_REFERENCES_DECL`` edge existed, so this module does the same rather
than paying an unconditional extra AST walk on every dump.

The seeding half (:func:`seed_ast_graph` and its two helpers) lives here
too, with the projection it reads: together they are the AST-sourced half
of :func:`~abicheck.buildsource.header_graph.build_header_only_graph`,
whose own file sits at its ``architecture/debt.yaml`` line cap. The
snapshot-sourced half (``_seed_flat_graph``, the clang-unavailable
fallback) stays there -- it touches no AST at all.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Hashable, Iterator, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from ..errors import SnapshotError
from ..extract.headers.clang.ast_inner import (
    MalformedClangAstError as MalformedClangAstError,
)
from ..model import ScopeOrigin
from ..model.execution_cache import request_key
from ..model.execution_cache_scoped import ScopedCache
from ..model.graph_facts import (
    CONF_HIGH,
    GraphEdge,
    GraphNode,
    _decl_node_id,
    _type_node_id,
)
from ..model.source_graph_coverage import (
    HEADER_BODY_ATTR,
    HEADER_CALL_GRAPH_PASS as _HEADER_CALL_GRAPH_PASS,
    HEADER_DECLARATIONS_PASS,
    HEADER_TYPE_GRAPH_PASS as _HEADER_TYPE_GRAPH_PASS,
)
from .ast_special_members import collect_special_member_names
from .call_graph import (
    augment_graph_with_calls,
    collect_function_bodies,
    parse_clang_ast_calls,
)
from .inline_graph_fold import _mark_role_coverage
from .semantic_fact_folds import (
    SemanticFacts,
    fold_header_semantic_facts,
    merge_semantic_facts,
    parse_semantic_facts,
)
from .type_graph import augment_graph_with_types
from .type_graph_index import (
    ast_derived_scope,
    index_declared_entity_files,
    index_declared_type_files,
)
from .type_graph_walk import parse_clang_ast_types

_H = TypeVar("_H")

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary
    from .call_graph import CallEdge
    from .type_graph import TypeEdge


#: Extractor-pass names the AST-sourced seeding below stamps onto
#: ``SourceGraphSummary.extractor_passes`` (ADR-031 D9 coverage honesty),
#: distinct from ``inline_graph_fold``'s build-integrated ``"call_graph"``/
#: ``"type_graph"`` so a reader (and ``source_graph_findings.
#: _common_dependency_edge_kinds``) never conflates a header-only pass with
#: a full build-integrated one. Owned here rather than in ``header_graph``
#: because the code that stamps them lives here and that module imports
#: this one, never the reverse; ``header_graph`` re-exports both names. The
#: strings themselves are owned by ``model.source_graph_coverage``, which
#: states what each pass vouches for (evidence-entity-model I4).
HEADER_CALL_GRAPH_PASS = _HEADER_CALL_GRAPH_PASS
HEADER_TYPE_GRAPH_PASS = _HEADER_TYPE_GRAPH_PASS

#: Provenance tag for nodes/edges built from the clang AST.
_PROVENANCE = "header_ast_l2"


@dataclass(frozen=True)
class HeaderGraphAstProjection:
    """The whole of a clang AST that the header-only graph builder reads.

    Deliberately holds no reference to the AST it was projected from: the
    point of the projection is that the tree can be released the moment this
    object exists.
    """

    #: Qualified record/enum/typedef name -> its declaring file.
    type_files: dict[str, str] = field(default_factory=dict)
    #: ``DECL_HAS_TYPE``/``DECL_REFERENCES_DECL``-style edges.
    type_edges: list[TypeEdge] = field(default_factory=list)
    #: ``DECL_CALLS_DECL`` edges.
    call_edges: list[CallEdge] = field(default_factory=list)
    #: Every declared identity -> declaring file. Empty when no
    #: ``DECL_REFERENCES_DECL`` edge needed it, matching the lazy
    #: computation this replaced -- never a claim that the AST declares
    #: nothing.
    entity_files: dict[str, str] = field(default_factory=dict)
    #: Every constructor/destructor linker name the AST declares
    #: (``ast_special_members``) -- binary-independent identity evidence for
    #: castxml's unmangled ctor/dtor placeholders.
    special_member_names: frozenset[str] = frozenset()
    #: The override, template and callback facts of the same parse
    #: (``semantic_fact_folds``). ``None`` when they were not projected --
    #: a hand-built projection -- so the header passes for those families
    #: stay unstamped (unknown), never "ran and found nothing".
    semantic_facts: SemanticFacts | None = None
    #: Identities of every function whose body the parse saw in full
    #: (``call_graph.collect_function_bodies``) -- the per-declaration body
    #: coverage :func:`seed_ast_graph` marks on the graph.
    function_bodies: frozenset[str] = frozenset()


# MalformedClangAstError: one type with the clang header readers' own
# ``ast_inner`` check, so every refusal path catches both (imported above).


def validate_ast_shape(ast_root: object) -> None:
    """Reject *ast_root* unless every node is an object with a list ``inner``.

    A node may omit ``inner`` (a leaf), but if the key is present it must be a
    list, and every element of that list must itself be an object. Walks with
    an explicit stack rather than recursion, so a deeply nested tree cannot
    exhaust the interpreter's stack, and does it in a single pass.
    """
    if not isinstance(ast_root, dict):
        raise MalformedClangAstError(
            f"clang AST root is {type(ast_root).__name__}, expected an object"
        )
    stack: list[dict[str, Any]] = [ast_root]
    while stack:
        node = stack.pop()
        if "inner" not in node:
            continue
        children = node["inner"]
        if not isinstance(children, list):
            raise MalformedClangAstError(
                f"clang AST node {node.get('kind')!r} has 'inner' of type "
                f"{type(children).__name__}, expected a list"
            )
        for child in children:
            if not isinstance(child, dict):
                raise MalformedClangAstError(
                    f"clang AST node {node.get('kind')!r} has a child of type "
                    f"{type(child).__name__}, expected an object"
                )
            stack.append(child)


def project_header_graph_ast(ast_root: dict[str, Any]) -> HeaderGraphAstProjection:
    """Read *ast_root* once into the compact form the graph builder needs.

    Raises :class:`MalformedClangAstError` on a tree whose shape the readers
    cannot trust (see :func:`validate_ast_shape`).
    """
    validate_ast_shape(ast_root)
    # The type-file index, the type edges and the entity-file index all come
    # from one whole-TU index walk (type_graph.ast_derived_scope).
    with ast_derived_scope():
        type_files = index_declared_type_files(ast_root)
        type_edges = parse_clang_ast_types(ast_root)
        needs_entity_files = any(e.kind == "DECL_REFERENCES_DECL" for e in type_edges)
        entity_files = (
            index_declared_entity_files(ast_root) if needs_entity_files else {}
        )
        # Inside the same scope, and handed the type edges just parsed: the
        # override facts read the inheritance edges, never a second walk.
        semantic_facts = parse_semantic_facts(ast_root, type_edges)
    call_edges = parse_clang_ast_calls(ast_root)
    special: set[str] = set()
    collect_special_member_names(ast_root, special)
    return HeaderGraphAstProjection(
        type_files=type_files,
        type_edges=type_edges,
        call_edges=call_edges,
        entity_files=entity_files,
        special_member_names=frozenset(special),
        semantic_facts=semantic_facts,
        function_bodies=frozenset(collect_function_bodies(ast_root)),
    )


def merge_header_graph_ast_projections(
    parts: Sequence[HeaderGraphAstProjection],
) -> HeaderGraphAstProjection:
    """Union the projections of several header subsets' separate parses.

    Used when a whole-tree parse fails and the headers are re-parsed in
    smaller groups: each group's projection is complete *for that group*, so
    their union is the graph a single parse of every group that succeeded
    would have produced. Index maps keep the first declaring file (header
    order), and edges are de-duplicated preserving first occurrence, so the
    result is deterministic and independent of how often a shared header was
    re-read across groups.
    """
    type_files: dict[str, str] = {}
    entity_files: dict[str, str] = {}
    type_edges: dict[TypeEdge, None] = {}
    call_edges: dict[CallEdge, None] = {}
    special: set[str] = set()
    bodies: set[str] = set()
    for part in parts:
        for name, path in part.type_files.items():
            type_files.setdefault(name, path)
        for name, path in part.entity_files.items():
            entity_files.setdefault(name, path)
        type_edges.update(dict.fromkeys(part.type_edges))
        call_edges.update(dict.fromkeys(part.call_edges))
        special.update(part.special_member_names)
        bodies.update(part.function_bodies)
    facts = [p.semantic_facts for p in parts]
    return HeaderGraphAstProjection(
        type_files=type_files,
        type_edges=list(type_edges),
        call_edges=list(call_edges),
        entity_files=entity_files,
        special_member_names=frozenset(special),
        semantic_facts=(
            merge_semantic_facts(f for f in facts if f is not None)
            if facts and all(f is not None for f in facts)
            else None
        ),
        function_bodies=frozenset(bodies),
    )


def parse_header_groups_bisecting(
    headers: Sequence[_H],
    parse: Callable[[list[_H]], HeaderGraphAstProjection],
    errors: tuple[type[BaseException], ...],
    *,
    known_failed: Sequence[_H] = (),
) -> tuple[list[HeaderGraphAstProjection], list[_H]]:
    """Parse *headers* in groups, halving any group whose parse raises.

    Returns ``(projections of every group that parsed, headers that failed
    alone)``. The whole list is assumed to have failed already, so it is
    split before the first attempt. Cost is ``O(f * log n)`` parses for
    ``f`` failing headers, never one parse per header unless every header
    fails.

    *known_failed* are headers the failed batch's own diagnostics already
    blame. They are dropped first and the rest parsed as one group: one
    parse instead of ~2 * f * log2(n), each of which re-reads the whole
    shared include closure (18 parses, 3.75 GB of AST JSON, on oneDNN's
    SYCL headers). Only when that group fails too -- the diagnostics missed
    a header -- does the bisect run, over the rest.
    """
    parts: list[HeaderGraphAstProjection] = []
    failed: list[_H] = []
    pending: list[list[_H]] = []
    items = list(headers)
    if known_failed:
        blamed = set(known_failed)
        rest = [h for h in items if h not in blamed]
        failed = [h for h in items if h in blamed]
        if not rest:
            return parts, failed
        try:
            return [parse(rest)], failed
        except errors:
            items = rest
    if len(items) <= 1:
        return parts, failed + items
    mid = len(items) // 2
    pending = [items[:mid], items[mid:]]
    while pending:
        group = pending.pop(0)
        try:
            parts.append(parse(group))
        except errors:
            if len(group) == 1:
                failed.append(group[0])
            else:
                half = len(group) // 2
                pending[:0] = [group[:half], group[half:]]
    return parts, failed


def _seed_ast_type_nodes(
    graph: SourceGraphSummary,
    type_files: dict[str, str],
    header_node: Callable[[str], str],
    classify: Callable[[str], ScopeOrigin],
) -> None:
    """Seed ``record_type`` nodes from the AST's own qualified-name index.

    Deliberately not from ``snapshot.types``/``snapshot.enums``: the flat
    snapshot model records a *bare*, unqualified type name (see
    ``dumper_clang._ClangAstParser._build_record``), while the type graph's node
    ids are the AST's *resolved qualified* name (``ns::Widget``) — two
    representations that would silently fail to join on any namespaced type.
    Deriving both the file (hence origin) and the node id from the same AST
    index sidesteps that mismatch, and covers the ADR's headline case: a public
    struct rarely has its own exported binary symbol, so it needs ``visibility``
    set directly on the type node to act as a valid graph "entry"
    (``is_public_dependency_node``).
    """
    for qname, file in type_files.items():
        origin = classify(file)
        if origin == ScopeOrigin.UNKNOWN:
            continue
        node_id = _type_node_id(qname)
        # ``augment_graph_with_types`` defaults every AST-only type node to
        # "record_type" uniformly (it cannot distinguish record/enum/typedef
        # without an L4 surface) — matching that convention here keeps
        # first-writer-wins joins consistent either way.
        graph.add_node(
            GraphNode(
                id=node_id,
                kind="record_type",
                label=qname,
                provenance=_PROVENANCE,
                confidence=CONF_HIGH,
                attrs={"visibility": origin.value},
            )
        )
        graph.add_edge(
            GraphEdge(
                src=header_node(file),
                dst=node_id,
                kind="SOURCE_DECLARES",
                provenance=_PROVENANCE,
                confidence=CONF_HIGH,
            )
        )


def _unseeded_decl_endpoints(
    entity_files: dict[str, str], type_edges: list[Any], call_edges: list[Any]
) -> tuple[tuple[str, str], ...]:
    """``(identity, file)`` for every edge endpoint the snapshot never seeded.

    Annotates any AST-only decl target ``augment_graph_with_types``/
    ``augment_graph_with_calls`` would otherwise create with no provenance at
    all — a private declaration that is not a function or (namespace-scope)
    variable, e.g. an ``EnumConstantDecl`` referenced by ``inline int f() {
    return Color::RED; }``, is never seeded from
    ``snapshot.functions``/``snapshot.variables``, since the flat AbiSnapshot
    model has no equivalent per-enumerator entity to iterate. The
    build-integrated path backfills this via ``augment_graph_with_types``'s
    ``project_files`` parameter (matched against ``BuildEvidence``'s
    compile-unit sources); a header-only world has no such set, but each edge
    already carries its own target's declaring file
    (``dst_file``/``callee_file``/``caller_file``), which is exactly what
    ``classify_origin`` needs (Codex review).

    A ``DECL_REFERENCES_DECL`` edge's *source* can be unseeded too — a field's
    default member initializer (``struct Widget { int x = detail::k; };``)
    makes ``Widget::x`` the edge's ``src``, and a field is never in
    ``snapshot.functions``/``snapshot.variables`` either (Codex review). Unlike
    the targets, ``TypeEdge`` carries no ``src_file``, so this falls back to
    ``index_declared_entity_files`` (the unfiltered declaring-file index,
    including fields) — computed once, lazily, only if there is at least one
    such source to look up.
    """
    # First-occurrence order, not a `set`'s: this order becomes node order,
    # and a set of strings iterates in per-process hash order.
    ref_srcs = dict.fromkeys(
        e.src for e in type_edges if e.kind == "DECL_REFERENCES_DECL"
    )
    return (
        *((e.dst, e.dst_file) for e in type_edges if e.kind == "DECL_REFERENCES_DECL"),
        *((src, entity_files.get(src, "")) for src in ref_srcs),
        *((e.caller, e.caller_file) for e in call_edges),
        *((e.callee, e.callee_file) for e in call_edges),
    )


def seed_ast_graph(
    graph: SourceGraphSummary,
    projection: HeaderGraphAstProjection,
    header_node: Callable[[str], str],
    classify: Callable[[str], ScopeOrigin],
) -> None:
    """Seed type nodes and fold the clang type/call edges into *graph*."""
    _seed_ast_type_nodes(graph, projection.type_files, header_node, classify)
    type_edges = projection.type_edges
    call_edges = projection.call_edges
    for identity, file in _unseeded_decl_endpoints(
        projection.entity_files, type_edges, call_edges
    ):
        if not identity or not file:
            continue
        node_id = _decl_node_id(identity)
        if graph.has_node(node_id):
            # Already seeded as a real function/variable.
            continue
        origin = classify(file)
        graph.add_node(
            GraphNode(
                id=node_id,
                kind="source_decl",
                label=identity,
                provenance=_PROVENANCE,
                confidence=CONF_HIGH,
                attrs=(
                    {"visibility": origin.value}
                    if origin != ScopeOrigin.UNKNOWN
                    else {}
                ),
            )
        )
    augment_graph_with_types(graph, type_edges)
    augment_graph_with_calls(graph, call_edges)
    _mark_seen_bodies(graph, projection.function_bodies)
    if projection.semantic_facts is not None:
        # After the call and type edges, which the virtual-dispatch and
        # callback joins read -- the same order the L5 folds run in.
        fold_header_semantic_facts(graph, projection.semantic_facts)
    # A header-only pass is a single parse over the whole header aggregate —
    # never narrowed/scoped like a per-compile-unit build-integrated pass, and
    # ``clang_header_dump`` raises on a failed/empty parse rather than
    # returning a degraded partial result (ADR-028 D3 "never abort collection"
    # lives one layer up, in the caller's try/except around the clang
    # invocation) — so reaching this line means the whole pass ran cleanly.
    # Stamped unconditionally, regardless of edge count (ADR-041 P0 slice 2
    # coverage-honesty convention: "ran, zero output" must be distinguishable
    # from "never ran").
    graph.extractor_passes[HEADER_CALL_GRAPH_PASS] = True
    graph.extractor_passes[HEADER_TYPE_GRAPH_PASS] = True
    graph.extractor_passes[HEADER_DECLARATIONS_PASS] = True
    # ADR-046 D3 role coverage (Codex review, fresh evidence): this call above
    # is the *same* ``type_graph.parse_clang_ast_types()`` walker the
    # build-integrated ``fold_type_graph`` drives — reused unmodified, per
    # this module's own docstring — so it has the identical per-role fidelity
    # (no known gap for any ``ROLE_COVERAGE_MATRIX`` role, same as the
    # build-integrated pass). Without this, `source_graph_findings.
    # _disagreeing_roles()` would see *no* role key on either side of a
    # header-only-vs-header-only comparison, forever (this pass has never
    # stamped one, in any abicheck version) — reading as vacuous agreement
    # rather than "role coverage unknown," and letting a genuinely new
    # `template_param`/`default_template_arg`/`enum_underlying` edge slip
    # through as a false `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` the same way
    # the build-integrated pass's own pre-D3-consumer gap did.
    _mark_role_coverage(graph.extractor_passes, HEADER_TYPE_GRAPH_PASS)


def degrade_header_type_pass(graph: SourceGraphSummary) -> None:
    """Withdraw the header type pass's collected stamp after its AST failed.

    The flat fallback stamps ``header_type_graph`` as collected from the
    snapshot's own declarations, and a partial parse stamps it with its role
    keys. Neither read the AST-only type edges (parameter and variable
    ``DECL_HAS_TYPE``, ``DECL_REFERENCES_DECL``), so the family must read as
    not collected, with its role coverage unstamped and the pass degraded.
    """
    from ..model.source_graph_coverage import ROLE_COVERAGE_MATRIX
    from .inline_graph_fold import _role_coverage_key

    graph.extractor_passes.pop(HEADER_TYPE_GRAPH_PASS, None)
    for edge_kind, roles in ROLE_COVERAGE_MATRIX.items():
        for role in roles:
            graph.extractor_passes.pop(
                _role_coverage_key(HEADER_TYPE_GRAPH_PASS, edge_kind, role), None
            )
    graph.degraded_passes[HEADER_TYPE_GRAPH_PASS] = True


def _mark_seen_bodies(graph: SourceGraphSummary, bodies: frozenset[str]) -> None:
    """Record per-declaration body coverage (ADR-077 D5) on each already
    seeded ``decl://`` node whose body this header parse saw in full. A
    declaration with no node has no public role and no edge, so it can carry
    no body-dependent finding; it is not minted just to hold the mark."""
    for identity in sorted(bodies):
        node_id = _decl_node_id(identity)
        if graph.has_node(node_id):
            graph.add_node(
                GraphNode(
                    id=node_id,
                    kind="source_decl",
                    label=identity,
                    provenance=_PROVENANCE,
                    confidence=CONF_HIGH,
                    attrs={HEADER_BODY_ATTR: True},
                )
            )


class MemoizedParseFailure(SnapshotError):
    """A whole-tree header-graph parse already known (memoized) to fail;
    ``hit`` is the memoized ``(failed_headers, failure_line)``."""

    def __init__(self, hit: tuple[tuple[Path, ...], str]) -> None:
        super().__init__(hit[1])
        self.hit = hit


class HeaderGraphBisectionMemo:
    """One request's memo of which headers a header-graph clang parse loses.

    The header-graph parse runs once per library per side, and libraries of
    one package usually share one header tree and one compile context. When
    that whole-tree parse fails, the per-group bisect is the expensive part
    (~455 s per library on oneDAL), and its answer depends only on the
    headers, their content and the parse flags -- so every library sharing
    the key reuses the first one's answer instead of failing the whole-tree
    parse and bisecting again. :meth:`slot` serializes concurrent callers on
    one key, so two prefetches of the same tree bisect once, not twice.

    The memo holds the outcome (the headers that failed alone, plus the
    first line of the original failure for the degraded-graph record), never
    a projection: a hit still parses the surviving headers, so the graph is
    built from the same parse a first run makes.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key_locks: dict[Hashable, threading.Lock] = {}
        self._outcomes: dict[Hashable, tuple[tuple[Path, ...], str]] = {}
        self._projections: dict[Hashable, HeaderGraphAstProjection] = {}

    @contextmanager
    def slot(self, key: Hashable) -> Iterator[None]:
        with self._lock:
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        with key_lock:
            yield

    def load_projection(
        self,
        key: Hashable,
        load: Callable[[], HeaderGraphAstProjection | None],
    ) -> HeaderGraphAstProjection | None:
        """The projection stored under *key*, loading it at most once per scope.

        Only a *successful* load is kept: a ``None`` (a miss) is returned to
        the caller and never memoized, so a later sibling can still see an
        entry that another library stored in the meantime. The per-key slot
        makes concurrent callers wait for the first load instead of reading
        the same sidecar in parallel. The returned object is shared between
        callers, so it is treated as read-only: every consumer reads its
        fields and none rebinds or mutates them.
        """
        with self.slot(("projection", key)):
            with self._lock:
                hit = self._projections.get(key)
            if hit is not None:
                return hit
            loaded = load()
            if loaded is not None:
                with self._lock:
                    self._projections[key] = loaded
            return loaded

    def get(self, key: Hashable) -> tuple[tuple[Path, ...], str] | None:
        with self._lock:
            return self._outcomes.get(key)

    def enter(self, scope: ExitStack, key: Hashable) -> None:
        """Hold *key*'s slot for the rest of *scope*; raise
        :class:`MemoizedParseFailure` when a sibling already learned this
        whole-tree parse fails."""
        scope.enter_context(self.slot(key))
        hit = self.get(key)
        if hit is not None:
            raise MemoizedParseFailure(hit)

    def put(self, key: Hashable, failed: Sequence[Path], failure: str) -> None:
        with self._lock:
            self._outcomes[key] = (tuple(failed), failure)


def header_tree_signature(headers: Sequence[Path]) -> tuple[tuple[str, int, int], ...]:
    """``(path, size, mtime_ns)`` per header, so an edited header misses."""
    sig: list[tuple[str, int, int]] = []
    for h in headers:
        try:
            st = h.stat()
            sig.append((str(h), st.st_size, st.st_mtime_ns))
        except OSError:
            sig.append((str(h), -1, -1))
    return tuple(sig)


def header_graph_memo_key(
    headers: Sequence[Path],
    includes: Sequence[Path],
    tokens: Sequence[str],
    hash_dirs: Sequence[Path],
    lang: str | None,
    compile_context: object,
    resolved_lang_mode: str | None = None,
) -> tuple[Any, ...]:
    """Everything a header-graph parse's outcome depends on. That includes
    *resolved_lang_mode*, the mode the primary pass resolved: under no
    *lang* it turns a C parse of the same headers into a C++ one."""
    return (
        header_tree_signature(headers),
        tuple(str(p) for p in includes),
        tuple(tokens),
        tuple(str(p) for p in hash_dirs),
        (lang or "").lower(),
        repr(compile_context),
        resolved_lang_mode or "",
    )


#: The memo lives only inside :func:`header_graph_bisection_scope` (one
#: compare/release request). The key fingerprints the top-level headers, not
#: every transitively included one, so it is valid only while those inputs
#: cannot change -- one request -- and a process-wide memo would replay a
#: stale failure after an included header is repaired, and keep a lock and an
#: outcome per key forever. Worker threads share it by inheriting the
#: request's context (``copy_context``), as the release fan-out and the
#: header-graph prefetch both do.
_BISECTION_SCOPE = ScopedCache("abicheck.buildsource.header_graph_bisection")


def header_graph_bisection_scope() -> AbstractContextManager[None]:
    """Share header-graph bisection outcomes across one request's dumps."""
    return _BISECTION_SCOPE.scope()


def current_bisection_memo() -> HeaderGraphBisectionMemo:
    """This request's memo; a throwaway one outside a scope (or in reference
    mode), so a bare dump simply memoizes nothing."""
    return _BISECTION_SCOPE.get_or_compute(
        request_key(memo="header_graph_bisection"), HeaderGraphBisectionMemo
    )
