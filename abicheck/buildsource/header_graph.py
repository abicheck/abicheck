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
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..model import AbiSnapshot, ScopeOrigin
from ..provenance import build_public_set, classify_origin
from .call_graph import augment_graph_with_calls, parse_clang_ast_calls
from .source_graph import (
    CONF_HIGH,
    CONF_REDUCED,
    GraphEdge,
    GraphNode,
    SourceGraphSummary,
    _decl_node_id,
    _header_node_id,
    _type_node_id,
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


#: Provenance tag for nodes/edges built straight from the flat
#: :class:`~abicheck.model.AbiSnapshot` (no AST at all) — distinct from
#: :data:`_PROVENANCE` (the clang-AST-derived path) so a reader can tell
#: which resolution tier produced a given node/edge.
_FLAT_PROVENANCE = "header_flat_l2"


def _seed_flat_type_node(
    graph: SourceGraphSummary,
    header_node: Callable[[str], str],
    name: str,
    kind: str,
    origin: ScopeOrigin,
    source_header: str | None,
) -> None:
    """Seed one record/enum type node straight from its own snapshot entry.

    Unlike the AST path above, ``RecordType``/``EnumType`` already carry their
    own ``origin``/``source_header`` (ADR-015 provenance, populated by
    :func:`abicheck.provenance.apply_provenance` from the same
    ``--public-header``/``--public-header-dir`` inputs) — no
    ``classify_origin`` re-derivation needed here.
    """
    node_id = _type_node_id(name)
    attrs = {"visibility": origin.value} if origin != ScopeOrigin.UNKNOWN else {}
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


def _flat_type_name_counts(snapshot: AbiSnapshot) -> dict[str, int]:
    """How many declared record/enum types in *snapshot* share each bare name.

    The flat model has no namespace/scope info to disambiguate two
    same-named types declared in different scopes (``dumper_castxml.
    _CastxmlParser._type_name`` returns the bare name for a
    ``Struct``/``Class``/``Union``/``Enumeration``, same for the clang L2
    frontend) — a count is the cheapest way to tell "this name is unique in
    the snapshot" (safe to trust) from "this name is ambiguous" (must not
    guess which declaration a reference to it means).
    """
    counts: dict[str, int] = {}
    for rt in snapshot.types:
        counts[rt.name] = counts.get(rt.name, 0) + 1
    for en in snapshot.enums:
        counts[en.name] = counts.get(en.name, 0) + 1
    return counts


def _resolve_flat_type_name(raw: str, counts: dict[str, int]) -> tuple[str, str]:
    """Best-effort bare-name resolution with no AST/scope index available.

    Mirrors ``type_graph._resolve_type_name``'s (raw, resolution) contract,
    but ``RESOLUTION_SCOPE`` is never reachable here: the flat
    :class:`~abicheck.model.AbiSnapshot` model records only a bare,
    unqualified type name, with no enclosing-namespace/scope information at
    all, so there is no scope to walk. A name matching exactly one declared
    record/enum anywhere in the snapshot is trusted as that type
    (:data:`~abicheck.buildsource.type_graph.RESOLUTION_UNIQUE_CANDIDATE`); a
    name matching zero or more than one declaration is left unresolved
    (:data:`~abicheck.buildsource.type_graph.RESOLUTION_UNRESOLVED`) rather
    than guessed — the same "never guess an ambiguous bare name" rule the AST
    path already follows.
    """
    base = _base_type_name(raw)
    if not base or _is_excluded_type(base):
        return "", RESOLUTION_UNRESOLVED
    if counts.get(base, 0) == 1:
        return base, RESOLUTION_UNIQUE_CANDIDATE
    return base, RESOLUTION_UNRESOLVED


def _flat_structural_type_edges(snapshot: AbiSnapshot) -> list[TypeEdge]:
    """Derive ``TYPE_INHERITS``/``TYPE_HAS_FIELD_TYPE``/``DECL_HAS_TYPE`` edges
    straight from the already-parsed flat :class:`~abicheck.model.AbiSnapshot`
    — no clang AST needed at all. Every L2 backend (castxml, the default, or
    clang) populates ``RecordType.bases``/``.fields``, ``Function.
    return_type``/``.params``, and ``Variable.type`` identically, so this
    works uniformly regardless of which frontend parsed the headers, at zero
    extra compiler-invocation cost. Confidence is always
    :data:`~abicheck.buildsource.source_graph.CONF_REDUCED` — even a
    :data:`~abicheck.buildsource.type_graph.RESOLUTION_UNIQUE_CANDIDATE` match
    here is a weaker guess than the AST path's scope-walk resolution.
    """
    counts = _flat_type_name_counts(snapshot)
    edges: list[TypeEdge] = []

    def emit(src: str, raw: str, kind: str, role: str) -> None:
        if not src or not raw:
            return
        # A field/parameter typed e.g. ``std::vector<Private>`` must not stop
        # at the whole template spelling — ``_resolve_nested_type_names``
        # (the same pure, AST-independent string walk the clang path already
        # uses) also surfaces the private template argument itself, the
        # actual dependency a public-to-internal-dependency check cares about
        # (Codex review: an earlier version only resolved the outer name,
        # creating an unresolved edge to the literal "std::vector<Private>"
        # string and missing the real "Private" edge entirely).
        seen: set[str] = set()
        for candidate in _resolve_nested_type_names(raw):
            name, resolution = _resolve_flat_type_name(candidate, counts)
            if not name or name in seen:
                continue
            seen.add(name)
            edges.append(TypeEdge(src, name, kind, CONF_REDUCED, role, "", resolution))

    for rt in snapshot.types:
        for base in rt.bases:
            emit(rt.name, base, EDGE_TYPE_INHERITS, "base")
        for fld in rt.fields:
            emit(rt.name, fld.type, EDGE_TYPE_HAS_FIELD_TYPE, "field")
    for fn in snapshot.functions:
        identity = _decl_identity(fn)
        emit(identity, fn.return_type, EDGE_DECL_HAS_TYPE, "return")
        for p in fn.params:
            emit(identity, p.type, EDGE_DECL_HAS_TYPE, "param")
    for var in snapshot.variables:
        emit(_decl_identity(var), var.type, EDGE_DECL_HAS_TYPE, "var")
    return edges


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

        type_edges = parse_clang_ast_types(ast_root)
        call_edges = parse_clang_ast_calls(ast_root)
        # Annotate any AST-only decl target `augment_graph_with_types`/
        # `augment_graph_with_calls` would otherwise create with no
        # provenance at all — a private declaration that isn't a function or
        # (namespace-scope) variable, e.g. an `EnumConstantDecl` referenced
        # by `inline int f() { return Color::RED; }`, is never seeded by the
        # `snapshot.functions`/`snapshot.variables` loop above, since the
        # flat AbiSnapshot model has no equivalent per-enumerator entity to
        # iterate. The build-integrated path backfills this via
        # `augment_graph_with_types`'s `project_files` parameter (matched
        # against `BuildEvidence`'s compile-unit sources); a header-only
        # world has no such set, but each edge already carries its own
        # target's declaring file (`dst_file`/`callee_file`/`caller_file`),
        # which is exactly what `classify_origin` needs (Codex review).
        for identity, file in (
            *(
                (e.dst, e.dst_file)
                for e in type_edges
                if e.kind == "DECL_REFERENCES_DECL"
            ),
            *((e.caller, e.caller_file) for e in call_edges),
            *((e.callee, e.callee_file) for e in call_edges),
        ):
            if not identity or not file:
                continue
            node_id = _decl_node_id(identity)
            if graph.has_node(node_id):
                continue
            origin = classify_origin(
                file, header_segs, dir_segs, have_public_set=have_public_set
            )
            attrs = (
                {"visibility": origin.value} if origin != ScopeOrigin.UNKNOWN else {}
            )
            graph.add_node(
                GraphNode(
                    id=node_id,
                    kind="source_decl",
                    label=identity,
                    provenance=_PROVENANCE,
                    confidence=CONF_HIGH,
                    attrs=attrs,
                )
            )

        augment_graph_with_types(graph, type_edges)
        augment_graph_with_calls(graph, call_edges)
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
    else:
        # No clang AST available (clang missing/unselected — the default L2
        # backend is castxml) — still recover the three structural edge
        # kinds directly from the flat snapshot already parsed, rather than
        # leaving the graph at declaration-visibility nodes only. No second
        # compiler invocation needed: every L2 backend populates
        # ``RecordType.bases``/``.fields``/``Function.return_type``/``.params``/
        # ``Variable.type`` identically (see :func:`_flat_structural_type_edges`).
        for rt in snapshot.types:
            _seed_flat_type_node(
                graph, header_node, rt.name, "record_type", rt.origin, rt.source_header
            )
        for en in snapshot.enums:
            _seed_flat_type_node(
                graph, header_node, en.name, "enum_type", en.origin, en.source_header
            )
        augment_graph_with_types(graph, _flat_structural_type_edges(snapshot))
        # Only the structural pass ran — no bodies were ever visible to the
        # flat model, in any circumstance, so ``HEADER_CALL_GRAPH_PASS`` must
        # never be stamped here (that would falsely vouch for a project-wide
        # zero on ``DECL_CALLS_DECL``/``DECL_REFERENCES_DECL``).
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
        import shlex

        from .build_evidence import BuildEvidence, CompileUnit
        from .include_graph import ClangIncludeExtractor

        if not self.available():
            return {}, [f"{self.clang_bin} not found in PATH"]
        extra_tokens = (
            shlex.split(gcc_options, posix=os.name != "nt") if gcc_options else []
        )
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
