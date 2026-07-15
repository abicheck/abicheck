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
pass" trust to a comparison against) a real L4/L5 graph. A header-only
confirmation only ever grants trust for the structural kinds it has genuine
project-wide visibility of (``source_graph_findings._HEADER_FULL_VISIBILITY_KINDS``)
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

import os
from dataclasses import dataclass
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
    header_paths: list[str] | None = None,
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

    *header_paths* are the top-level header files the caller parsed (the
    ``-H``/``--header`` inputs, already expanded from any directory) —
    pre-seeded as ``header`` nodes even when they declare nothing themselves
    (a pure ``#include``-only umbrella header is still a real public entry
    point). Without this, such a header would get no node at all, leaving a
    later :func:`ClangHeaderIncludeExtractor` include edge with no valid
    source endpoint to attach to.
    """
    graph = SourceGraphSummary()
    header_segs, dir_segs, have_public_set = build_public_set(
        public_header_paths, public_dir_paths
    )

    def header_node(path: str) -> str:
        node_id = _header_node_id(path)
        origin = classify_origin(
            path, header_segs, dir_segs, have_public_set=have_public_set
        )
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
        return node_id

    for h in header_paths or ():
        header_node(h)

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
    (:func:`abicheck.buildsource.source_graph._header_node_id`), so
    :func:`abicheck.buildsource.include_graph.augment_graph_with_includes`
    can fold the result straight onto the already-built
    :class:`~abicheck.buildsource.source_graph.SourceGraphSummary` without any
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
        """
        import shlex

        from .build_evidence import BuildEvidence, CompileUnit
        from .include_graph import ClangIncludeExtractor

        if not self.available():
            return {}, [f"{self.clang_bin} not found in PATH"]
        extra_tokens = (
            shlex.split(gcc_options, posix=os.name != "nt") if gcc_options else []
        )
        compile_units = [
            CompileUnit(
                id=_header_node_id(h),
                source=h,
                argv=[
                    *(f"-I{i}" for i in includes),
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
