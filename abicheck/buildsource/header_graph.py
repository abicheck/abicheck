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
  already computes when ``--public-header``/``--public-header-dir`` is given),
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
  calls :func:`~abicheck.buildsource.source_graph.build_source_graph`.

**Coverage honesty (ADR-031 D9):** every node/edge this module creates itself
carries ``provenance="header_ast_l2"`` and the graph's ``extractor_passes`` use
the module's own pass names (:data:`HEADER_CALL_GRAPH_PASS` /
:data:`HEADER_TYPE_GRAPH_PASS`), distinct from the build-integrated
``call_graph``/``type_graph`` pass names — so a header-only graph is never
mistaken for (and never grants the same build-integrated "confirmed full
pass" trust to a comparison against) a real L4/L5 graph, while still getting
symmetric family-widening credit against another header-only graph
(``source_graph_findings._DEPENDENCY_EDGE_FAMILIES``).

Same authority boundary as the rest of ADR-028/041: this can only explain,
localize, or add a RISK/API_BREAK finding — never a shipped-ABI verdict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..model import AbiSnapshot, ScopeOrigin
from ..provenance import build_public_set, classify_origin
from .call_graph import augment_graph_with_calls, parse_clang_ast_calls
from .source_graph import (
    CONF_HIGH,
    GraphEdge,
    GraphNode,
    SourceGraphSummary,
    _decl_node_id,
    _header_node_id,
    _type_node_id,
)
from .type_graph import (
    augment_graph_with_types,
    index_declared_type_files,
    parse_clang_ast_types,
)

if TYPE_CHECKING:
    from ..model import Function, Variable

#: Extractor-pass names this module stamps onto ``SourceGraphSummary.
#: extractor_passes`` (ADR-031 D9 coverage honesty), distinct from
#: ``inline_graph_fold``'s build-integrated ``"call_graph"``/``"type_graph"``
#: so a reader (and ``source_graph_findings._common_dependency_edge_kinds``)
#: never conflates a header-only pass with a full build-integrated one.
HEADER_CALL_GRAPH_PASS = "header_call_graph"
HEADER_TYPE_GRAPH_PASS = "header_type_graph"

_PROVENANCE = "header_ast_l2"


def _decl_identity(fn_or_var: Function | Variable) -> str:
    """Mirror ``type_graph._decl_identity``/``call_graph._identity``: mangled
    name when present, else the bare name — the same fallback both AST-side
    parsers use, so a pre-seeded node id matches an edge the AST parsers
    create for the identical declaration."""
    return str(getattr(fn_or_var, "mangled", "") or getattr(fn_or_var, "name", ""))


def build_header_only_graph(
    snapshot: AbiSnapshot,
    ast_root: dict[str, Any] | None = None,
    *,
    public_header_paths: list[str] | None = None,
    public_dir_paths: list[str] | None = None,
) -> SourceGraphSummary:
    """Build a header-only semantic graph from an L2 :class:`AbiSnapshot`.

    *ast_root* is a parsed ``clang -ast-dump=json`` tree over the same header
    aggregate the L2 clang frontend parses (``dumper._clang_header_dump``) —
    ``None`` when clang was unavailable/not selected, in which case the graph
    still carries ``source_decl``/``header`` nodes (declaration-level
    visibility from the snapshot alone) but no type/call edges.

    *public_header_paths*/*public_dir_paths* are the same ``--public-header``/
    ``--public-header-dir`` inputs already threaded through
    :func:`abicheck.provenance.apply_provenance` — required for anything to
    classify as ``public_header``/``private_header`` rather than ``unknown``
    (provenance stays opt-in, matching the rest of the L2 pipeline).
    """
    graph = SourceGraphSummary()
    header_segs, dir_segs, have_public_set = build_public_set(
        public_header_paths, public_dir_paths
    )

    def header_node(path: str) -> str:
        node_id = _header_node_id(path)
        graph.add_node(
            GraphNode(
                id=node_id,
                kind="header",
                label=path,
                provenance=_PROVENANCE,
                confidence=CONF_HIGH,
            )
        )
        return node_id

    def seed_decl(entity: Function | Variable) -> None:
        identity = _decl_identity(entity)
        if not identity:
            return
        node_id = _decl_node_id(identity)
        attrs = (
            {"visibility": entity.origin.value}
            if entity.origin != ScopeOrigin.UNKNOWN
            else {}
        )
        graph.add_node(
            GraphNode(
                id=node_id,
                kind="source_decl",
                label=entity.name or identity,
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

    for fn in snapshot.functions:
        seed_decl(fn)
    for var in snapshot.variables:
        seed_decl(var)

    if ast_root is not None:
        # Type nodes are seeded straight from the AST's own qualified-name
        # index, not from ``snapshot.types``/``snapshot.enums``: the flat
        # snapshot model records a *bare*, unqualified type name (see
        # ``dumper_clang._ClangAstParser._build_record``), while the type
        # graph's node ids are the AST's *resolved qualified* name
        # (``ns::Widget``) — two representations that would silently fail to
        # join on any namespaced type. Deriving both the file (hence origin)
        # and the node id from the same AST index sidesteps that mismatch
        # entirely, and covers the ADR's own headline case: a public struct
        # rarely has its own exported binary symbol, so it needs its
        # ``visibility`` set directly on the type node to act as a valid
        # graph "entry" (``is_public_dependency_node``).
        for qname, file in index_declared_type_files(ast_root).items():
            origin = classify_origin(
                file, header_segs, dir_segs, have_public_set=have_public_set
            )
            if origin == ScopeOrigin.UNKNOWN:
                continue
            node_id = _type_node_id(qname)
            # ``augment_graph_with_types`` defaults every AST-only type node to
            # "record_type" uniformly (it cannot distinguish record/enum/
            # typedef without an L4 surface) — matching that convention here
            # keeps first-writer-wins joins consistent either way.
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
            hid = header_node(file)
            graph.add_edge(
                GraphEdge(
                    src=hid,
                    dst=node_id,
                    kind="SOURCE_DECLARES",
                    provenance=_PROVENANCE,
                    confidence=CONF_HIGH,
                )
            )

        augment_graph_with_types(graph, parse_clang_ast_types(ast_root))
        augment_graph_with_calls(graph, parse_clang_ast_calls(ast_root))
        # A header-only pass is a single parse over the whole header
        # aggregate — never narrowed/scoped like a per-compile-unit
        # build-integrated pass, and ``_clang_header_dump`` raises on a
        # failed/empty parse rather than returning a degraded partial result
        # (ADR-028 D3 "never abort collection" lives one layer up, in the
        # caller's try/except around the clang invocation) — so reaching
        # this line means the whole pass ran cleanly. Stamp unconditionally,
        # regardless of edge count (ADR-041 P0 slice 2 coverage-honesty
        # convention: "ran, zero output" must be distinguishable from
        # "never ran").
        graph.extractor_passes[HEADER_CALL_GRAPH_PASS] = True
        graph.extractor_passes[HEADER_TYPE_GRAPH_PASS] = True

    return graph.finalize()
