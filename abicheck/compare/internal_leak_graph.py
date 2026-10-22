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

"""Call-graph leak overlay: internal declarations a public entry reaches.

``compute_call_graph_leak_paths`` walks the optional L5 source graph from
every consumer-compiled public entry (ADR-044 P1). ``detect_call_graph_leaks``
composes those proof paths with artifact-proven breaking changes on internal
declarations. It is a symbol-availability signal, distinct from the layout
walk in ``internal_leak_paths``.
"""

from __future__ import annotations

import collections
from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..change_registry import unanimous_entity_for
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.evidence_status import ReachabilityState
from .call_graph_walk import (
    CALL_GRAPH_TRAVERSAL_POLICY,
    consumer_compiled_reachability,
    reconstruct_path,
)
from .internal_leak_names import (
    INTERNAL_TYPE_LEAK,
    _strip_signature_params,
)
from .internal_namespaces import DEFAULT_INTERNAL_NAMESPACES

if TYPE_CHECKING:
    from ..model import AbiSnapshot


def compute_call_graph_leak_paths(
    snap: AbiSnapshot,
    internal_namespaces: Iterable[str] = DEFAULT_INTERNAL_NAMESPACES,
) -> dict[str, list[str]]:
    """Call-graph analogue of :func:`compute_leak_paths` (ADR-044 P1 item 1).

    Walks the optional L5 source graph's ``DECL_CALLS_DECL``/
    ``DECL_REFERENCES_DECL`` edges from every public entry whose own body is
    actually compiled into consumer code (exported-symbol decl or
    public-header-visible decl/type, restricted to an inline/template
    rendition where the graph can tell the difference — Codex review; see
    :func:`~abicheck.model.source_graph_query.is_consumer_compiled_public_entry`
    for why an ordinary out-of-line exported function does not qualify),
    returning a mapping ``lookup_key -> list of formatted proof-path
    strings`` (one per public entry that reaches it, edge-kind-annotated via
    :func:`~abicheck.model.source_graph_query.format_dependency_path`,
    e.g. ``"pub() --[DECL_CALLS_DECL]--> detail::helper()"``). A path that
    only exists because it crossed a virtual/function-pointer call
    (``CALL_GRAPH_TRAVERSAL_POLICY.effect_transitions``, ADR-046 D5) is
    prefixed ``"overapprox: "`` — it proves *a* possible dispatch target
    reaches the internal symbol, not that this exact chain does.

    Each reachable internal target is recorded under **two** keys when both
    are available (Codex review, fresh evidence): the graph node's own
    ``label`` (a demangled qualified name, e.g. ``ns::detail::helper`` — the
    format ``compute_leak_paths``'s type-layout walk already keys by, and
    what a hand-authored/synthetic ``Change`` uses), and, when the node has
    its own ``SOURCE_DECL_MAPS_TO_SYMBOL`` edge, the exported **mangled**
    symbol name that edge maps to (the same ``binary_symbol://`` identity
    :func:`~abicheck.buildsource.source_graph_compare.localize_symbol` already
    resolves for the reverse direction). The latter matters because
    ``diff_symbols.py`` builds a real ``FUNC_REMOVED``/similar ``Change`` with
    ``symbol=`` the **mangled** linker name, not the demangled qualified name
    — a call-graph-only node's ``label`` can also be the mangled name or a
    hash-suffixed qualified name depending on provenance (see
    :mod:`abicheck.buildsource.call_graph`), so keying by ``label`` alone
    would silently never match a real, compiled C++ removal. For exactly this
    mangled-label shape, the internal-namespace *classification* check below
    also demangles first (only for that check — the stored key stays the
    original mangled ``label``, which already equals a real ``Change.symbol``
    directly), since a bare mangled name has no ``::`` segments for
    :func:`is_internal_type` to recognize at all.

    This is a *symbol-availability* signal, distinct from
    :func:`compute_leak_paths`'s layout/type-graph walk: a public inline
    function's body calling into a removed/changed internal template
    specialization has no field/base/signature evidence at all (nothing a
    layout walk can see) but is real to a linker — the exact oneDAL
    dispatcher gap this ADR's P0 slice explicitly left open (see the ADR's
    "What this ADR does not fix" section).

    Requires an embedded L5 graph (``--sources``/``--build-info``, or the now-always-on
    L2 header-only graph) with at least one relevant edge; returns ``{}`` otherwise —
    never an error, mirroring :func:`~abicheck.buildsource.poi.resolve_changed_paths_public_impact`'s
    degrade contract, so a project with no build-source evidence sees no behavior change at all.
    """
    from ..evidence_depth import resolve_l5_source_graph

    graph = resolve_l5_source_graph(snap, getattr(snap, "build_source", None))
    if graph is None or not graph.nodes:
        return {}

    from abicheck.model.source_graph_query import (
        format_dependency_path,
        is_consumer_compiled_public_entry,
    )

    if not any(
        e.kind in CALL_GRAPH_TRAVERSAL_POLICY.allowed_edges for e in graph.edges
    ):
        return {}

    node_by_id = {n.id: n for n in graph.nodes}
    decl_to_symbol: dict[str, str] = {}
    symbol_prefix = "binary_symbol://"
    for e in graph.edges:
        if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL" and e.dst.startswith(symbol_prefix):
            decl_to_symbol[e.src] = e.dst[len(symbol_prefix) :]
    exported_decls = set(decl_to_symbol)
    # is_consumer_compiled_public_entry (not the broader is_public_dependency_node
    # cross_source_checks.py's advisory RISK-only check uses) -- an ordinary out-of-line
    # exported function's own internal calls never reach consumer-compiled code
    # at all, so seeding the walk from it would treat a purely internal
    # implementation-detail dependency as public-reachable (Codex review).
    entries = [
        n.id
        for n in graph.nodes
        if is_consumer_compiled_public_entry(n.id, node_by_id, exported_decls)
    ]
    if not entries:
        return {}

    internal_set = set(internal_namespaces)
    reachability = consumer_compiled_reachability(
        graph, CALL_GRAPH_TRAVERSAL_POLICY, entries, node_by_id
    )
    result: dict[str, list[str]] = collections.defaultdict(list)
    for entry in entries:
        targets, came_from, degraded = reachability.get(
            entry, (frozenset(), {}, frozenset())
        )
        for target in targets:
            node = node_by_id.get(target)
            if node is None:
                continue
            name = node.label or target
            # Codex review (fresh evidence): a call-graph-only fallback node
            # (augment_graph_with_calls, added when a callee has no other
            # SOURCE_DECLARES-backed node) gets label=ident straight from
            # function_decl_identity, which returns the *mangled* name for
            # any ordinary (non-extern-"C") C++ function -- with no "::" at
            # all, is_internal_type would reject it here before this
            # function's own dual-key logic below ever runs. Demangle only
            # for this classification check, not for the stored key: the
            # mangled name here is the same canonical Itanium symbol
            # diff_symbols.py puts on a real FUNC_REMOVED's Change.symbol, so
            # the existing direct mangled-to-mangled match already works
            # once classification correctly recognizes it as internal.
            lookup_name = name
            if name.startswith("_Z"):
                from ..demangle import demangle

                # demangle() returns the full signature (e.g.
                # "ns::api::foo(ns::detail::T*)"), not just the qualified
                # name -- is_internal_type's segment scan would otherwise
                # find "detail" inside the *parameter* type and misclassify
                # an ordinary public function as an internal leak target
                # merely because it takes/returns an internal type (Codex
                # review, fresh evidence). Strip the signature's own
                # parameter list before classifying.
                lookup_name = _strip_signature_params(demangle(name) or name)
            path_edges = reconstruct_path(came_from, entry, target)
            if path_edges is None or not INTERNAL_TYPE_LEAK.confirmed(
                lookup_name, path_edges, internal_namespaces=internal_set
            ):
                continue
            formatted = format_dependency_path(graph, path_edges)
            if target in degraded:
                # ADR-046 D5 effect_transitions: this proof crossed a
                # virtual/function-pointer call, so it is an
                # over-approximation of the real dispatch target, not an
                # exact proof -- flagged in the displayed path rather than
                # silently shown identically to a direct-call-only chain.
                formatted = f"overapprox: {formatted}"
            result[name].append(formatted)
            mangled = decl_to_symbol.get(target)
            if mangled and mangled != name:
                result[mangled].append(formatted)
            # Codex review (fresh evidence): function_decl_identity's third
            # shape -- a declaration with no distinct mangled name (e.g.
            # extern "C") gets label="{qualified_name}#sha256:{digest}" (see
            # source_graph.py's function_decl_identity), not the bare
            # qualified name a real Change.symbol/qualified_name would ever
            # carry. Index the hash-stripped qualified name too so this
            # shape matches the same way the label-only case already does.
            hash_stripped = name.split("#sha256:", 1)[0]
            if hash_stripped != name:
                result[hash_stripped].append(formatted)
    return dict(result)


def _build_call_graph_leak_change(
    dname: str,
    triggers: list[Change],
    proof_paths: list[str],
) -> Change:
    """Build a single ``INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API`` Change entry.

    The call-graph analogue of :func:`_build_leak_change`: composes an
    already artifact-proven ``BREAKING`` finding on an internal decl with a
    ``DECL_CALLS_DECL``/``DECL_REFERENCES_DECL`` proof path from a public
    entry, instead of a layout/type-graph reachability path.
    """
    kinds_seen = sorted({c.kind.value for c in triggers})
    # reachability_proof_path is the *representative* path for this Change,
    # so it must prefer an exact route over an "overapprox: "-prefixed one
    # when both exist among *proof_paths* -- the caller (_diff_call_graph_
    # leaks) concatenates every public entry's own independently-discovered
    # path in discovery order, not exactness order (Codex review, fresh
    # evidence: one entry reaching dname through a virtual/function-pointer
    # call can precede a different entry's exact route to the same dname,
    # which would otherwise make an exact-reachable symbol's Change read
    # back as approximate -- unlike the *within one entry's own BFS*
    # exact-beats-overapprox preference _consumer_compiled_reachability
    # already guarantees, nothing dedupes preference *across* entries).
    # Searches the full list, not just the first three kept for display,
    # since the strongest path can be truncated out of path_strs.
    proof_path = next(
        (p for p in proof_paths if not p.startswith("overapprox:")),
        proof_paths[0] if proof_paths else None,
    )
    # path_strs is what the description text actually displays. It must
    # lead with the same path just selected as the representative one, not
    # an independently-taken "first three in discovery order" slice --
    # otherwise the human-readable description could describe nothing but
    # "overapprox: "-prefixed paths while reachability_proof_path/the
    # correlator's evidence level both report an exact proof, an internally
    # contradictory finding (Codex review, fresh evidence).
    if proof_path is None:
        path_strs: list[str] = []
    else:
        # .remove() drops only the one selected occurrence -- a filtered
        # comprehension (``p != proof_path``) would drop every occurrence,
        # silently losing a genuinely distinct duplicate path if
        # proof_paths happens to contain proof_path more than once
        # (CodeRabbit review).
        rest = list(proof_paths)
        rest.remove(proof_path)
        path_strs = [proof_path, *rest[:2]]
    more = "" if len(proof_paths) <= 3 else f" (+{len(proof_paths) - 3} more paths)"
    change = Change(
        kind=ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
        symbol=dname,
        entity_discriminator=unanimous_entity_for(kinds_seen),
        description=(
            f"Internal symbol '{dname}' changed ({', '.join(kinds_seen)}) and "
            "is called/referenced from the public ABI surface — an "
            "application built against the old public entry point can fail "
            f"to resolve this symbol at load time. Call/reference paths: "
            f"{'; '.join(path_strs)}{more}."
        ),
        caused_by_type=dname,
        # ADR-044 D1/D2: same reasoning as _build_leak_change — this finding
        # exists *because* dname is reachable from the public surface via the
        # call graph, so a broad suppression rule's reachability gate must
        # not treat it as unreachable either.
        public_reachable=True,
        reachability_state=ReachabilityState.PROVEN_REACHABLE,
        reachability_kind="symbol_availability",
        reachability_proof_path=proof_path,
    )
    return change
