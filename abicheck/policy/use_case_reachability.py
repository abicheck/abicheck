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

"""Use-case reachability: which public symbols each use case's entrypoints reach.

The call-graph half of ``compare --use-cases`` attribution. It resolves a
manifest's entrypoints through :mod:`.use_cases` and walks the library graph
with the compare-owned :mod:`abicheck.compare.call_graph_walk`.
:func:`abicheck.policy.use_case_attribution.build_use_case_impact` calls it
directly.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..compare.call_graph_walk import (
    CALL_GRAPH_TRAVERSAL_POLICY,
    consumer_compiled_reachability,
)
from .use_cases import UseCaseDefinition, _public_entry_index

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary


def _strip_binary_symbol(node_id: str) -> str:
    """*node_id* without its ``binary_symbol://`` scheme; any other id unchanged."""
    scheme, sep, rest = node_id.partition("://")
    return rest if sep and scheme == "binary_symbol" else node_id


def explain_use_case_impact(
    definitions: list[UseCaseDefinition],
    library_graph: SourceGraphSummary,
    symbols: Iterable[str],
) -> dict[str, tuple[str, ...]]:
    """For each of *symbols* (an ordinary two-snapshot diff's own
    ``Change.symbol`` values), which declared use case(s)' own resolved
    entrypoints reach it via *library_graph*'s call/reference edges (G29
    Phase 4, the use-case counterpart of
    :func:`abicheck.impact.consumer_graph.explain_required_symbols`).

    The key difference from that function: its root set is *every*
    consumer-compiled public entry in the library, because a real
    consumer's requirement is proven by the export table alone, independent
    of which declared use case (if any) covers it. Here the roots are
    restricted to exactly the entrypoints *this manifest itself declares* —
    "every public entry reaches it" says nothing about whether a
    *specific* use case's own surface does, and attributing a change to
    every use case whenever *any* public entry reaches it would make the
    field meaningless (every use case would show every public-reachable
    change). Entrypoint resolution reads only :func:`_public_entry_index`
    against the plain library graph, the identical resolution
    :func:`resolve_use_case_entrypoints` performs.

    Returns ``{symbol: (use_case_name, ...)}`` for exactly the symbols this
    manifest's own entrypoints can be shown to reach — a symbol absent from
    the mapping is not evidence no use case touches it, only that this
    function found no proof, mirroring
    :func:`~abicheck.impact.consumer_graph.explain_required_symbols`'s own
    "absent, never speculative" contract. Two structural limitations, both
    inherited from that same sibling function rather than introduced here:
    a *type*-shaped change (no ``SOURCE_DECL_MAPS_TO_SYMBOL`` edge — no
    declaration ever backs a `record_type`/`enum_type` node) can never be
    named, only a function/variable-shaped one; and only a
    *consumer-compiled* entrypoint (:func:`~abicheck.buildsource.
    source_graph.is_consumer_compiled_public_entry` — inline/template
    bodies, not an ordinary out-of-line exported function's own internal
    calls) can walk past its own declaration, so a use case naming only
    ordinary exported functions as entrypoints still gets direct-hit
    attribution (the symbol's own declaration is one of the resolved
    entrypoints) but never transitive attribution through it.
    """
    wanted = set(symbols)
    if not wanted or not definitions:
        return {}

    from abicheck.model.source_graph_query import is_consumer_compiled_public_entry

    entry_index = _public_entry_index(library_graph)
    # Merge (not overwrite) across repeated `use_case` entries (Codex
    # review, fresh evidence): a manifest is not required to give each
    # use case exactly one list entry -- `parse_use_case_manifest` never
    # rejects a repeated `use_case` name (the duplicate-key check is per
    # *mapping*, and each entry is a separate list item, not a mapping
    # key), so every entry sharing a name is one use case whose entrypoints
    # are the union of its entries'. A plain
    # `use_case_entries[name] = ids` assignment here would let the later
    # entry's entrypoint set silently replace the earlier one's, so a
    # change reachable only
    # through an earlier entry's entrypoints would never be attributed.
    use_case_entries: dict[str, set[str]] = {}
    for definition in definitions:
        ids = {entry_index[e] for e in definition.entrypoints if e in entry_index}
        if ids:
            use_case_entries.setdefault(definition.use_case, set()).update(ids)
    if not use_case_entries:
        return {}

    # decl node id -> every *wanted* symbol name it maps to (plural -- Codex
    # review, fresh evidence: a single declaration can map to more than one
    # exported symbol, e.g. two versioned exports foo@V1/foo@V2 of the same
    # definition; a plain dict[str, str] here silently kept only the last
    # one seen, so a change to foo@V2 could be mis-attributed through
    # decl://foo to a use case that named foo@V1 specifically, or a genuine
    # foo@V1 change could vanish if foo@V2's edge happened to be visited
    # later), and the reverse symbol id -> decl id(s) -- the latter to
    # resolve a coalesced ``binary_symbol://`` entry (`_public_entry_index`
    # prefers the mapped symbol id over the declaring decl id, see its own
    # docstring) back to the decl node(s) the call-graph's own edges are
    # keyed on: a ``DECL_CALLS_DECL``/``DECL_REFERENCES_DECL`` edge's
    # src/dst is always a decl (or type) node id, never a bare
    # exported-symbol one, so walking from the coalesced binary_symbol id
    # directly would find no outgoing edges at all and silently explain
    # nothing.
    decl_to_symbols: dict[str, set[str]] = {}
    # Every decl that maps onto a given symbol, not just one (Codex review,
    # fresh evidence): the same export can legitimately be reached by more
    # than one SOURCE_DECL_MAPS_TO_SYMBOL edge -- e.g. an inline/weak
    # definition captured once per TU, all mangling to the identical symbol
    # name. A plain dict[str, str] here kept only the first decl seen
    # (edge-iteration-order dependent), and a manifest entry naming that
    # exact binary_symbol could then walk from a declaration with no
    # captured call edges (a forward declaration, an extern with no body in
    # *this* TU's evidence) while the sibling decl carrying the real body --
    # and its transitive calls -- was silently never walked at all.
    symbol_to_decls: dict[str, set[str]] = {}
    for edge in library_graph.edges:
        if edge.kind != "SOURCE_DECL_MAPS_TO_SYMBOL":
            continue
        symbol_to_decls.setdefault(edge.dst, set()).add(edge.src)
        name = _strip_binary_symbol(edge.dst)
        if name in wanted:
            decl_to_symbols.setdefault(edge.src, set()).add(name)

    def walk_roots_for(entry: str) -> set[str]:
        return symbol_to_decls.get(entry, {entry})

    node_by_id = {n.id: n for n in library_graph.nodes}
    exported_decls = {
        e.src for e in library_graph.edges if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"
    }
    all_entries = sorted({eid for ids in use_case_entries.values() for eid in ids})
    walk_roots = sorted(
        {
            root
            for eid in all_entries
            for root in walk_roots_for(eid)
            if is_consumer_compiled_public_entry(root, node_by_id, exported_decls)
        }
    )
    reachability = (
        consumer_compiled_reachability(
            library_graph, CALL_GRAPH_TRAVERSAL_POLICY, walk_roots, node_by_id
        )
        if walk_roots
        else {}
    )

    result: dict[str, set[str]] = {}
    for use_case, entry_ids in use_case_entries.items():
        for entry in entry_ids:
            # An entry that names one *exact* binary_symbol id directly
            # (the manifest wrote the versioned symbol itself, or
            # _public_entry_index left it uncoalesced because the owning
            # decl maps to more than one symbol -- see that function's own
            # ambiguity rule) is attributed to exactly that symbol alone,
            # never the decl's whole symbol set (Codex review, fresh
            # evidence): a use case that named foo@V1 specifically must not
            # also pick up a foo@V2-only change just because they share one
            # declaration.
            scheme, sep, exact_symbol = entry.partition("://")
            if sep and scheme == "binary_symbol":
                if exact_symbol in wanted:
                    result.setdefault(exact_symbol, set()).add(use_case)
            else:
                # A decl-shaped entry (or a label that coalesced onto one)
                # didn't disambiguate a specific version at all, so every
                # wanted symbol that decl maps to counts.
                for direct_symbol in decl_to_symbols.get(entry, ()):
                    result.setdefault(direct_symbol, set()).add(use_case)
            for root in walk_roots_for(entry):
                seen, _came_from, _degraded = reachability.get(
                    root, (frozenset(), {}, frozenset())
                )
                for decl in seen:
                    for sym in decl_to_symbols.get(decl, ()):
                        result.setdefault(sym, set()).add(use_case)

    return {symbol: tuple(sorted(names)) for symbol, names in result.items()}
