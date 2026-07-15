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

"""Source-graph-derived secondary risk findings (ADR-031 D6).

Split out of :mod:`source_graph` to keep that module under its line-count cap
(ADR-041 growth via the type/call-graph dependency work pushed it past 2000
lines). This module owns Phase 5's finding-emission logic exclusively —
:func:`diff_source_graph_findings` and its per-family helpers, which turn a
:class:`~abicheck.buildsource.source_graph.SourceGraphSummary` pair into
``ChangeKind`` findings. The graph *schema* and *construction*
(:class:`GraphNode`/:class:`GraphEdge`/:class:`SourceGraphSummary`,
:func:`~abicheck.buildsource.source_graph.build_source_graph`, the structural
:func:`~abicheck.buildsource.source_graph.diff_source_graph`) stay in
``source_graph.py``; ``diff_source_graph_findings`` is re-exported there for
callers that still import it from the original module path.

Per ADR-028 D3 / ADR-031 D6 these explain and prioritize; they never
override an artifact-proven break.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from .header_graph import (
    HEADER_CALL_GRAPH_PASS,
    HEADER_INCLUDE_GRAPH_PASS,
    HEADER_TYPE_GRAPH_PASS,
)
from .source_graph import (
    _TYPE_ENTITY_KINDS,
    EVIDENCE_TIER_L5,
    PUBLIC_VISIBILITIES,
    GraphEdge,
    SourceGraphSummary,
    _kind_map,
    _label_map,
    decl_declaring_files,
    is_internal_dependency_node,
    is_public_dependency_node,
)

if TYPE_CHECKING:
    from ..checker_types import Change


# ── Phase 5: graph-derived secondary risk findings (ADR-031 D6) ─────────────


def _decl_to_symbol(graph: SourceGraphSummary) -> dict[str, str]:
    """``source_decl`` node id → exported ``binary_symbol`` node id it maps to."""
    return {e.src: e.dst for e in graph.edges if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"}


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
        if (
            node is not None
            and str(node.attrs.get("visibility", "")) in PUBLIC_VISIBILITIES
        ):
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


def _public_entry_call_reachability(
    graph: SourceGraphSummary,
) -> dict[str, frozenset[str]]:
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
    entries = {e.src for e in graph.edges if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"}
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
        n.id
        for n in graph.nodes
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
#: Deliberately does NOT also list ``header_call_graph``/``header_type_graph``
#: (the header-only graph builder's own pass names, ADR-041 header-only-graph
#: addendum) alongside their build-integrated namesakes: the per-kind fallback
#: loop below unions "common" credit across every entry in this dict, so two
#: entries sharing the same edge-kind family are not additive-safe — a kind
#: correctly excluded under ``type_graph`` (a narrowed/degraded pass) would
#: still leak back in as "common" under a second, unmarked ``header_type_graph``
#: entry for the very same kind, since that entry's own narrowed/degraded
#: flags are independently (and here, vacuously) false. Instead,
#: ``_pass_trusted_kinds`` resolves each pass name's header-only counterpart
#: *within the same iteration*, capped to the structural kinds a header-only
#: pass genuinely has project-wide visibility of
#: (:data:`_HEADER_FULL_VISIBILITY_KINDS`) — never the whole family, so a
#: header-only confirmation is never treated as equivalent to a
#: build-integrated one for a body-dependent kind, on either side of the
#: comparison, regardless of the *other* side's shape.
_DEPENDENCY_EDGE_FAMILIES: dict[str, frozenset[str]] = {
    "call_graph": frozenset({"DECL_CALLS_DECL"}),
    "type_graph": frozenset(
        {
            "DECL_REFERENCES_DECL",
            "DECL_HAS_TYPE",
            "TYPE_HAS_FIELD_TYPE",
            "TYPE_INHERITS",
        }
    ),
}


#: Maps a build-integrated pass name to its header-only-graph counterpart
#: (``header_graph.py``, ADR-041 header-only-graph addendum) — the *same*
#: edge-kind family, produced by a different, no-build extraction path.
#: Deliberately NOT folded into :data:`_DEPENDENCY_EDGE_FAMILIES` itself (see
#: that constant's docstring): adding a second entry sharing the same kinds
#: would make the per-kind fallback loop iterate the same kind under two
#: independent "authorities", and one iteration finding it common is enough
#: to override a *different* iteration's correct exclusion — a real
#: regression the existing test suite caught. Instead, every flag lookup
#: below (`_pass_ran`/`_pass_narrowed`/`_pass_degraded`/`_pass_scope`) checks
#: *both* names for the *same* pass-name iteration, so a header-only graph's
#: own confirmed-pass/narrowed/degraded markers are honored without ever
#: double-counting a kind under two separate loop iterations (Codex review).
_HEADER_PASS_ALIAS: dict[str, str] = {
    "call_graph": HEADER_CALL_GRAPH_PASS,
    "type_graph": HEADER_TYPE_GRAPH_PASS,
    "include_graph": HEADER_INCLUDE_GRAPH_PASS,
}


def _pass_ran(graph: SourceGraphSummary, pass_name: str) -> bool:
    """Whether *pass_name* (or its header-only counterpart) ran to completion."""
    return graph.extractor_passes.get(pass_name, False) or graph.extractor_passes.get(
        _HEADER_PASS_ALIAS.get(pass_name, ""), False
    )


def _pass_narrowed(graph: SourceGraphSummary, pass_name: str) -> bool:
    """Whether *pass_name* (or its header-only counterpart) ran narrowed."""
    return graph.narrowed_passes.get(pass_name, False) or graph.narrowed_passes.get(
        _HEADER_PASS_ALIAS.get(pass_name, ""), False
    )


def _pass_degraded(graph: SourceGraphSummary, pass_name: str) -> bool:
    """Whether *pass_name* (or its header-only counterpart) hit diagnostics."""
    return graph.degraded_passes.get(pass_name, False) or graph.degraded_passes.get(
        _HEADER_PASS_ALIAS.get(pass_name, ""), False
    )


def _pass_scope(graph: SourceGraphSummary, pass_name: str) -> frozenset[str]:
    """The narrowed scope *pass_name* (or its header-only counterpart) used.

    A graph only ever populates one of the two — a header-only pass is never
    narrowed by construction (it always parses the whole header aggregate in
    one shot) — so preferring the build-integrated name when both happen to
    be non-empty is an arbitrary, safe tie-break, not a real ambiguity.
    """
    return graph.narrowed_scope.get(pass_name, frozenset()) or graph.narrowed_scope.get(
        _HEADER_PASS_ALIAS.get(pass_name, ""), frozenset()
    )


#: Edge kinds a *header-only* pass (``header_call_graph``/``header_type_graph``,
#: ADR-041 header-only-graph addendum) has genuine, project-wide visibility of
#: — declaration-level facts, no function body needed. ``DECL_CALLS_DECL`` and
#: ``DECL_REFERENCES_DECL`` are deliberately excluded: a header-only pass only
#: sees a call/reference inside a body that happens to be written *in the
#: header* (inline/template functions), so its "zero" for either kind is not
#: evidence of a project-wide zero — only of "this build's out-of-line bodies
#: are invisible to a header-only scan." A tenth Codex review on the shipped
#: PR caught the consequence of treating a header-only confirmation as
#: equivalent to a build-integrated one for these two kinds: comparing a
#: header-only baseline against a build-integrated candidate could then report
#: ``PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`` for a dependency that already
#: existed via an out-of-line call the header-only baseline structurally could
#: never have seen — a real false positive the moment collection "improves"
#: from header-only to build-integrated. The three structural kinds have no
#: such gap: a base class, a field type, and a parameter/return type are
#: fully visible in headers regardless of where the function body lives.
_HEADER_FULL_VISIBILITY_KINDS: frozenset[str] = frozenset(
    {"DECL_HAS_TYPE", "TYPE_HAS_FIELD_TYPE", "TYPE_INHERITS"}
)


def _pass_trusted_kinds(
    graph: SourceGraphSummary, pass_name: str, family: frozenset[str]
) -> frozenset[str]:
    """Which kinds in *family* a confirmed *pass_name* genuinely vouches for.

    A build-integrated confirmation (``graph.extractor_passes[pass_name]``)
    vouches for the *whole* family — a real per-TU AST replay sees function
    bodies too, so its "zero" is authoritative for every kind in the family,
    exactly as before this addendum. A header-only confirmation
    (``graph.extractor_passes[header_name]``) only vouches for the structural
    subset it has true project-wide visibility of
    (:data:`_HEADER_FULL_VISIBILITY_KINDS`) — regardless of what the *other*
    side of the comparison is (build-integrated, another header-only graph, or
    unmarked): a header-only pass's blindness to out-of-line bodies is a
    property of *that side alone*, not something the other side's shape can
    make trustworthy. This deliberately loses a little recall for a
    header-only-vs-header-only comparison's body-dependent kinds (which *are*
    symmetric, and so arguably safe to widen too) in exchange for never
    needing to track which specific shape the *other* side is — the simpler,
    strictly-safe rule a Codex review asked for.
    """
    if graph.extractor_passes.get(pass_name, False):
        return family
    header_name = _HEADER_PASS_ALIAS.get(pass_name, "")
    if header_name and graph.extractor_passes.get(header_name, False):
        return family & _HEADER_FULL_VISIBILITY_KINDS
    return frozenset()


def _dependency_kinds_covered(
    graph: SourceGraphSummary, edge_kinds: frozenset[str]
) -> bool:
    """Whether *graph* has evidence for any kind in *edge_kinds*: an edge, or its
    extractor pass recorded as having run (:data:`_DEPENDENCY_EDGE_FAMILIES`).

    A pass can run to completion and still emit zero edges of its family (e.g.
    no public struct anywhere had a private field yet), which reads identically
    to "the pass never ran" if edge presence is the only signal (third Codex
    review). ``SourceGraphSummary.extractor_passes`` (set by
    ``inline._fold_call_graph``/``_fold_type_graph`` right after a successful
    extraction, regardless of edge count) breaks that tie; absent that record
    (a hand-built or pre-slice-2 graph) this falls back to edge presence alone.

    ``narrowed_passes`` also counts as "a pass ran" here (fifteenth Codex
    review) — this is a coarse, single-graph "is there *any* reason to trust
    this graph enough to attempt a closure" gate, not the fine-grained
    per-kind trust decision (that's :func:`_common_dependency_edge_kinds`,
    which already knows whether a narrowed graph's zero-edge family is safe to
    compare — matched scope against an identically-narrowed other side, or
    excluded otherwise). A narrowed pass unambiguously is *not* "no semantic
    pass at all"; relaxing this coarse gate to admit it is safe because
    ``common_kinds`` remains the sole per-kind trust source downstream — a
    kind excluded there restricts the closure to zero edges of that kind
    regardless of whether this gate passed.
    """
    if any(e.kind in edge_kinds for e in graph.edges):
        return True
    return any(
        (_pass_ran(graph, pass_name) or _pass_narrowed(graph, pass_name))
        and (family & edge_kinds)
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
    not count as coverage for that kind unless ``new`` is narrowed to the exact
    *same* scope (eleventh/twelfth/fourteenth Codex review): a baseline scoped
    to a few changed TUs having one ``TYPE_HAS_FIELD_TYPE`` edge from that
    subset says nothing about dependencies elsewhere in the project — whether
    the other side is a confirmed *full* pass that saw the rest of the project
    (eleventh review), simply carries no pass marker at all, e.g. a
    pre-slice-2/externally-ingested pack whose true scope is unknown (twelfth
    review), or is *itself* narrowed but to a different, disjoint subset —
    ``narrowed_passes`` is only a boolean, so "both narrowed" does not mean
    "narrowed to the same TUs": an old run scoped to ``src/a.cpp`` and a new
    run scoped to ``src/b.cpp`` are each narrow but examine disjoint code
    (fourteenth review). ``narrowed_scope`` (the actual scope identifier —
    ``changed_paths``, or the examined compile units' source paths for an
    unseeded ``scoped_units`` run) settles this: only an *identical, non-empty*
    scope on both sides — the common PR-diff workflow, comparing two runs
    narrowed to the same changed TUs — is trusted to leave the pre-existing
    per-kind comparison unaffected.

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

    An *identical*, non-empty ``narrowed_scope`` on both sides is trusted
    enough to widen to the whole family too (fifteenth Codex review), the same
    way a confirmed full pass on both sides already does: two sides narrowed
    to the same compile units ran the *same* single AST walk, just restricted
    to that shared region, so one kind's absence there is a real, verified
    zero *within that scope* — not merely "found an edge of this exact kind
    somewhere" (the per-kind fallback). Without this, a same-scope PR scan
    whose narrowed baseline genuinely found zero edges of a family couldn't
    credit that as coverage, and a first-ever edge the candidate finds in that
    exact shared TU would be silently dropped instead of reported.

    A pass that ran *unnarrowed* but recorded per-TU diagnostics
    (``degraded_passes``, e.g. a clang crash/timeout on some subset) still
    folds edges from the TUs that parsed cleanly — those edges get exactly
    the same narrowed-side treatment as ``old_present``'s guard above
    (sixteenth Codex review): a partial pass's edge cannot vouch for "this
    kind was examined project-wide" any more than a narrowed pass's can,
    since the failed TUs are an unknown, untracked gap (unlike
    ``narrowed_scope``, which knows exactly which TUs a *deliberately*
    scoped run examined).
    """
    common: set[str] = set()
    for pass_name, family in _DEPENDENCY_EDGE_FAMILIES.items():
        # ``_pass_trusted_kinds`` resolves *pass_name*'s header-only-graph
        # counterpart (``header_call_graph``/``header_type_graph``, ADR-041
        # header-only-graph addendum) too, but caps what a header-only
        # confirmation vouches for to its true structural visibility —
        # never the whole family, regardless of the *other* side's shape
        # (Codex review: an earlier version of this fix let a header-only
        # confirmation grant full-family trust exactly like a build-
        # integrated one, which could manufacture a false
        # ``PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`` for a pre-existing,
        # invisible-to-headers call/reference the moment a baseline switched
        # from header-only to build-integrated collection).
        old_trusted = _pass_trusted_kinds(old, pass_name, family)
        new_trusted = _pass_trusted_kinds(new, pass_name, family)
        old_narrowed = _pass_narrowed(old, pass_name)
        new_narrowed = _pass_narrowed(new, pass_name)
        old_scope = _pass_scope(old, pass_name)
        new_scope = _pass_scope(new, pass_name)
        # A narrowed old side is only trusted against a new side narrowed to
        # the *identical*, non-empty scope — "both narrowed" alone does not
        # establish they examined the same code (fourteenth Codex review).
        scope_matches = bool(old_scope) and old_scope == new_scope
        # Two sides narrowed to that identical scope examined the *same*
        # single AST walk, restricted — exactly the confirmed-full-pass
        # rationale below, just scoped to the shared region instead of the
        # whole project (fifteenth Codex review): a kind's absence there is a
        # real, verified zero *within that scope*, safe to widen to the whole
        # family, not merely a per-kind fallback.
        narrowed_confirmed = old_narrowed and new_narrowed and scope_matches
        # Whole-family widening requires BOTH sides to fully trust the
        # family — i.e. both ran a build-integrated pass (a header-only
        # confirmation's trusted set is always a strict subset of a family
        # containing a body-dependent kind, so this never fires for a
        # header-only side there; it still fires correctly for the
        # call/type structural kinds via the per-kind loop below).
        if (old_trusted == family and new_trusted == family) or narrowed_confirmed:
            common |= family
            continue
        # A pass that ran unnarrowed but hit per-TU diagnostics still folds
        # edges from the TUs that parsed — those edges must not vouch for
        # "this kind was examined project-wide" any more than a narrowed
        # side's edges may (sixteenth Codex review): the failed TUs are an
        # unknown, untracked gap.
        old_degraded = _pass_degraded(old, pass_name)
        old_kinds = {e.kind for e in old.edges if e.kind in family}
        new_kinds = {e.kind for e in new.edges if e.kind in family}
        # A header-only-confirmed OLD side (header alias set, build-integrated
        # name not) can still fold real edges of a body-dependent kind — an
        # inline/template function calling/referencing another one, both
        # visible straight from the header. That single edge is genuine, but
        # it is not proof the kind was searched project-wide: the same scan
        # is structurally blind to any out-of-line body. Raw edge *presence*
        # must therefore not stand in for pass-confirmed trust here any more
        # than it may for the family-widening shortcut above — otherwise a
        # baseline's one incidental in-header call edge would make
        # DECL_CALLS_DECL "common" against a build-integrated candidate, and
        # a pre-existing out-of-line call the header-only baseline could
        # never have seen would surface as a false
        # ``PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`` the moment collection
        # improves (Codex review).
        old_header_only = (
            _HEADER_PASS_ALIAS.get(pass_name, "") != ""
            and old.extractor_passes.get(_HEADER_PASS_ALIAS[pass_name], False)
            and not old.extractor_passes.get(pass_name, False)
        )
        for kind in family:
            # Only OLD's negative evidence needs the narrowing/degraded guard
            # — see the docstring's one-directional-risk note (thirteenth
            # Codex review).
            old_present = (
                (kind in old_kinds)
                and not (old_header_only and kind not in _HEADER_FULL_VISIBILITY_KINDS)
                and not old_degraded
                and (not old_narrowed or (new_narrowed and scope_matches))
            )
            new_present = kind in new_kinds
            old_has = old_present or (kind in old_trusted)
            new_has = new_present or (kind in new_trusted)
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
        (e.src, e.dst) for e in graph.edges if e.kind == "BUILD_OPTION_AFFECTS_SYMBOL"
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
            if is_internal_dependency_node(
                target, node_by_id, exported_decls, decl_to_file
            ):
                out.add((entry, target))
    return out


def _target_dependency_edges(graph: SourceGraphSummary) -> set[tuple[str, str]]:
    """``(target, dependency_target)`` pairs from ``TARGET_DEPENDS_ON``."""
    return {(e.src, e.dst) for e in graph.edges if e.kind == "TARGET_DEPENDS_ON"}


#: Node kinds that represent a declaring file (the graph builder emits
#: ``SOURCE_DECLARES`` from a ``header`` node — labelled with the declaration's
#: ``source_location`` path, whose ``origin`` attr says whether it is a header
#: or a source file — so accepting only ``source`` nodes would leave the owner
#: map empty on every real graph (Codex review).
_DECLARING_FILE_KINDS: frozenset[str] = frozenset(
    {"source", "header", "generated_file"}
)


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
            findings.append(
                Change(
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
                )
            )
    return findings


def _public_reachability_findings(
    old: SourceGraphSummary,
    new: SourceGraphSummary,
    old_labels: dict[str, str],
    new_labels: dict[str, str],
    boundary: str,
) -> list[Change]:
    """Public-reachability closure changes for a *persisting* declaration.

    Only fires for a decl id present in both graphs' full node sets — a decl
    that only exists on one side is a brand-new/removed declaration, not a
    persisting one whose reachability state changed. "Entering the closure"
    is a trivial, expected consequence of being newly added (nothing risky
    about a symbol being public from birth); that event is already reported
    at the correct severity by the ordinary addition/removal findings. The
    genuinely risk-worthy signal here is an *existing* declaration crossing
    the public/private boundary unexpectedly.
    """
    from ..checker_policy import ChangeKind
    from ..checker_types import Change

    findings: list[Change] = []
    # Only when both sides have a closure — an empty baseline would otherwise
    # flag every declaration.
    old_pub, new_pub = _public_decls(old), _public_decls(new)
    if old_pub and new_pub:
        old_node_ids = {n.id for n in old.nodes}
        new_node_ids = {n.id for n in new.nodes}
        for decl in sorted(new_pub - old_pub):
            if decl not in old_node_ids:
                continue
            label = new_labels.get(decl, decl)
            findings.append(
                Change(
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
                )
            )
        for decl in sorted(old_pub - new_pub):
            if decl not in new_node_ids:
                continue
            label = old_labels.get(decl, decl)
            findings.append(
                Change(
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
                )
            )
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
    newly_generated = _generated_in_public_closure(new) - _generated_in_public_closure(
        old
    )
    for gen in sorted(newly_generated):
        label = new_labels.get(gen, gen)
        findings.append(
            Change(
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
            )
        )
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
            findings.append(
                Change(
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
                )
            )
    return findings


def _include_graph_covered(graph: SourceGraphSummary) -> bool:
    """Whether *graph* actually collected include-graph data at all.

    True when its include-graph pass is confirmed — either the build-
    integrated ``"include_graph"`` name or its header-only-graph counterpart
    (:data:`~abicheck.buildsource.header_graph.HEADER_INCLUDE_GRAPH_PASS`,
    via :func:`_pass_ran`) — (``extractor_passes``, ADR-041 P0 slice 2
    coverage-honesty convention: a pass can run and find zero edges, e.g. a
    leaf public header with no ``#include``s of its own) or it carries any
    ``COMPILE_UNIT_INCLUDES_FILE`` edge at all (an unmarked/legacy graph with
    real recorded data). False only when the graph has neither — i.e.
    include-graph folding never ran, whether because the caller never
    requested it (an older snapshot dumped before the fold became automatic)
    or clang was unavailable.
    """
    return _pass_ran(graph, "include_graph") or any(
        e.kind == "COMPILE_UNIT_INCLUDES_FILE" for e in graph.edges
    )


def _include_graph_drift_findings(
    old: SourceGraphSummary,
    new: SourceGraphSummary,
    old_labels: dict[str, str],
    new_labels: dict[str, str],
    boundary: str,
) -> list[Change]:
    """Public headers entering/leaving the compiled include graph.

    Trusts a side's *absence* of a header from the include graph only when
    that side actually collected include-graph data at all
    (:func:`_include_graph_covered`) — mirroring the same "an absent/never-
    run pass is not evidence of absence" principle
    :func:`_common_dependency_edge_kinds` already applies to the dependency-
    edge families. Without this, comparing a snapshot with no include-graph
    data (dumped before the fold existed/became automatic, or where clang
    was unavailable) against one that has it would read *every* header in
    the covered side as newly "entered"/"left" — a coverage artifact, not a
    real change (Codex review: this became a much more likely everyday
    scenario once include-graph folding stopped being an explicit opt-in
    flag both sides had to remember to pass identically).
    """
    from ..checker_policy import ChangeKind
    from ..checker_types import Change

    findings: list[Change] = []
    # Needs COMPILE_UNIT_INCLUDES_FILE edges from a depfile/-M include extractor.
    old_inc, new_inc = (
        _public_headers_in_include_graph(old),
        _public_headers_in_include_graph(new),
    )
    old_covered, new_covered = _include_graph_covered(old), _include_graph_covered(new)
    entered = sorted(new_inc - old_inc) if old_covered else []
    left = sorted(old_inc - new_inc) if new_covered else []
    for hdr in entered + left:
        is_entered = hdr in new_inc
        label = (new_labels if is_entered else old_labels).get(hdr, hdr)
        findings.append(
            Change(
                kind=ChangeKind.INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT,
                symbol=label,
                description=(
                    f"Public header {label!r} {'entered' if is_entered else 'left'} "
                    "the compiled include graph. Consumers may pull in different "
                    "declarations/macros through it. Source-graph evidence to review."
                ),
                old_value="in include graph" if not is_entered else "not included",
                new_value="in include graph" if is_entered else "not included",
                source_location=boundary,
            )
        )
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
        findings.append(
            Change(
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
            )
        )
    return findings


def _has_internal_reach_coverage(
    g: SourceGraphSummary, edge_kinds: frozenset[str]
) -> bool:
    """Whether a graph carries evidence for *edge_kinds* (:func:`_dependency_kinds_covered`)
    and a public closure."""
    return _dependency_kinds_covered(g, edge_kinds) and bool(
        _public_decls(g) or _public_types(g)
    )


#: source_diff.py findings whose old/new value is literally a body_hash or
#: type_hash (ADR-041 P0 roadmap item 2) — the narrow subset of the nine
#: source-replay findings that prove a *public* decl's own implementation
#: changed, as opposed to e.g. a default-argument or macro-value change.
_BODY_OR_TYPE_HASH_CHANGE_KINDS = frozenset(
    {
        "inline_body_changed",
        "template_body_changed",
        "public_typedef_target_changed",
    }
)


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
        findings.append(
            Change(
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
            )
        )
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
        findings.append(
            Change(
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
            )
        )
    return findings


def _path_segments(path: str) -> tuple[str, ...]:
    """Path components in posix order, dropping anchors/'.' parts.

    Backslashes are normalized to forward slashes so Windows-style build
    paths segment the same way as posix ones (mirrors provenance._segments).
    """
    from pathlib import PurePosixPath  # noqa: PLC0415

    posix = path.replace("\\", "/")
    return tuple(p for p in PurePosixPath(posix).parts if p not in ("/", ".", ""))


def _common_prefix_len(node_ids: list[str]) -> int:
    """Length (in path segments) of the longest common leading prefix shared
    by every node id's path (after its ``scheme://`` prefix).

    Two independent checkouts of the *same* source tree (e.g. old/new
    directories in a benchmark harness, or two CI job workspaces) share no
    absolute root, so comparing raw absolute paths would treat every file as
    "moved" even when nothing changed relative to its own tree. Stripping
    each side's own common root before comparing (see
    :func:`_root_relative_key`) lets an unmoved file be recognised as
    unmoved regardless of where its tree happened to be checked out.
    """
    seg_lists = [
        _path_segments(nid.split("://", 1)[1]) for nid in node_ids if "://" in nid
    ]
    if not seg_lists:
        return 0
    if len(seg_lists) == 1:
        # A side with exactly one declaring file has no sibling to compare
        # directory structure against — there is no way to tell "checkout
        # root" apart from "real subdirectory" with a sample of one, so fall
        # back to basename-only identity (reserve just the filename). This is
        # the same "unmoved" outcome multi-file sides reach structurally
        # (below), just via the only signal a single sample can offer.
        return max(0, len(seg_lists[0]) - 1)
    # Cap below the shortest path's final segment (the filename) so an
    # all-symbols-in-one-file side never strips down to an empty key — that
    # would hide a same-side rename and, asymmetrically, false-positive a
    # multi-file side's untouched files as "moved" relative to it.
    shortest = max(0, min(len(s) for s in seg_lists) - 1)
    n = 0
    for i in range(shortest):
        if len({s[i] for s in seg_lists}) == 1:
            n += 1
        else:
            break
    return n


def _root_relative_key(node_id: str, prefix_len: int) -> str:
    """Strip a node id's scheme and the first *prefix_len* path segments."""
    if "://" not in node_id or prefix_len <= 0:
        return node_id
    scheme, path = node_id.split("://", 1)
    segs = _path_segments(path)
    return f"{scheme}://{'/'.join(segs[prefix_len:])}"


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
    # Compare each side's declaring-file path relative to its own common
    # root, not the raw absolute path — two independently-rooted checkouts
    # of the same tree must not look like every file moved (see
    # _common_prefix_len).
    old_prefix_len = _common_prefix_len(list(old_owner.values()))
    new_prefix_len = _common_prefix_len(list(new_owner.values()))
    for symbol in sorted(set(old_owner) & set(new_owner)):
        old_key = _root_relative_key(old_owner[symbol], old_prefix_len)
        new_key = _root_relative_key(new_owner[symbol], new_prefix_len)
        if old_key != new_key:
            label = new_labels.get(symbol, symbol)
            findings.append(
                Change(
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
                )
            )
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
    findings += _public_reachability_findings(
        old, new, old_labels, new_labels, boundary
    )
    findings += _generated_public_closure_findings(old, new, new_labels, boundary)
    findings += _call_reachability_findings(old, new, new_labels, boundary)
    findings += _include_graph_drift_findings(
        old, new, old_labels, new_labels, boundary
    )
    findings += _build_option_reach_findings(old, new, new_labels, boundary)
    findings += _internal_dependency_findings(
        old, new, new_labels, boundary, source_diff_changes
    )
    findings += _target_dependency_findings(old, new, new_labels, boundary)
    findings += _symbol_owner_findings(old, new, old_labels, new_labels, boundary)
    return findings
