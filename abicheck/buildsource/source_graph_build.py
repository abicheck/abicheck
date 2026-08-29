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

"""ADR-061 Phase 5 item 2: SourceGraphSummary construction, Phase 2.

Split out of ``source_graph.py`` (construction half — ADR-031 D2 Phase 2):
:func:`build_source_graph` folds an ADR-029 :class:`~abicheck.buildsource.
build_evidence.BuildEvidence` into a target/source/header/compile-unit/
build-option/link graph. The Phase 3-4 enrichment from an optional ADR-030
:class:`~abicheck.buildsource.source_abi.SourceAbiSurface` (public
reachability + source-binary mapping) lives in the sibling
``source_graph_build_source_abi.py`` — split into its own module purely to
stay under the new-file line-count cap, called from the end of
:func:`build_source_graph` below.

Graph values (the container, node-id constructors, schema vocabulary) live
in ``abicheck.model.source_graph``; structural comparison
(:func:`~abicheck.buildsource.source_graph_compare.diff_source_graph`,
:func:`~abicheck.buildsource.source_graph_compare.localize_symbol`) lives
in ``source_graph_compare.py``. ``source_graph.py`` itself is now a thin
backward-compatibility facade re-exporting every public/historically-
imported name from all of these plus the shared node/edge-classification
predicates in ``source_graph_query.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model.graph_facts import (
    CONF_HIGH,
    CONF_REDUCED,
    CONF_UNKNOWN,
    GraphEdge,
    GraphNode,
)
from ..model.source_graph import (
    SourceGraphSummary,
    _header_node_id,
    _object_node_id,
    _option_node_id,
    _source_node_id,
    _static_library_node_id,
    _version_script_node_id,
)
from .build_evidence import BuildEvidence, Confidence
from .source_graph_build_source_abi import _augment_with_source_abi

if TYPE_CHECKING:
    from .source_abi import SourceAbiSurface


def _conf_from_build(conf: Confidence) -> str:
    """Map an ADR-029 build-evidence confidence onto a graph confidence label."""
    if conf == Confidence.HIGH:
        return CONF_HIGH
    if conf == Confidence.REDUCED:
        return CONF_REDUCED
    return CONF_UNKNOWN


#: Suffixes identifying a static-library archive among a LinkUnit's inputs
#: (ADR-041 P1 #2). Lowercase only — compared case-insensitively below (Codex
#: review): Windows evidence can spell this uppercase (``FOO.LIB``), hidden from ``archive_graph.py`` otherwise, same as ``adapters/make.py``.
_STATIC_LIBRARY_SUFFIXES = (".a", ".lib")


# ── Phase 2: build the graph from ADR-029 BuildEvidence ─────────────────────


def project_source_files(build: BuildEvidence) -> frozenset[str]:
    """Project-internal source files for ``defined_in_project`` provenance.

    Compile-unit sources **plus the targets' private headers** — a function whose
    body is in a project ``.cc`` *or* a project private header is internal
    implementation. Public headers are deliberately excluded: an inline function
    in a public header is consumer-visible public surface, so marking it
    ``defined_in_project`` (→ internal) would false-positive
    ``public_to_internal_dependency``. Third-party/system headers (Boost, libc++)
    are never in either list, so they stay external (Codex review).
    """
    files: set[str] = {cu.source for cu in build.compile_units if cu.source}
    for tgt in build.targets:
        files.update(h for h in tgt.private_headers if h)
    return frozenset(files)


def build_source_graph(
    build: BuildEvidence, source_abi: SourceAbiSurface | None = None
) -> SourceGraphSummary:
    """Fold ADR-029 build evidence (+ optional L4 source surface) into a graph.

    **Phase 2** emits the build-level slice from *build*:

    - ``target`` nodes, with ``TARGET_HAS_SOURCE`` / ``TARGET_HAS_PUBLIC_HEADER``
      / ``TARGET_DEPENDS_ON`` edges;
    - ``compile_unit`` nodes, with ``COMPILE_UNIT_BUILDS_SOURCE`` edges and
      ``COMPILE_UNIT_USES_OPTION`` edges to the ABI-relevant flags they carry;
    - ``source`` / ``header`` / ``generated_file`` nodes (a source listed in
      ``build.generated_files`` is typed ``generated_file``).

    **Phases 3-4** — when an ADR-030 ``source_abi`` surface is supplied — add the
    public-reachability and source↔binary slices: ``source_decl`` / type / macro
    nodes declared by public headers (``SOURCE_DECLARES``), their
    ``SOURCE_DECL_MAPS_TO_SYMBOL`` / ``SOURCE_TYPE_MAPS_TO_DEBUG_TYPE`` mappings,
    and ``BINARY_EXPORTS_SYMBOL`` edges from the owning target. Together they
    yield the target → public-header → decl → exported-symbol closure that
    reachability triage needs.

    Deeper call edges and external backends (Phases 6-7) extend the same graph.
    """
    graph = SourceGraphSummary()
    generated = set(build.generated_files)

    def file_node(path: str, *, header: bool = False) -> str:
        if not path:
            return ""
        if path in generated:
            node_id = _source_node_id(path)
            graph.add_node(
                GraphNode(
                    id=node_id,
                    kind="generated_file",
                    label=path,
                    provenance="build_evidence",
                    confidence=CONF_REDUCED,
                    attrs={"generated": True},
                )
            )
            return node_id
        if header:
            node_id = _header_node_id(path)
            graph.add_node(
                GraphNode(
                    id=node_id,
                    kind="header",
                    label=path,
                    provenance="build_evidence",
                    confidence=CONF_HIGH,
                )
            )
            return node_id
        node_id = _source_node_id(path)
        graph.add_node(
            GraphNode(
                id=node_id,
                kind="source",
                label=path,
                provenance="build_evidence",
                confidence=CONF_HIGH,
            )
        )
        return node_id

    known_targets = {t.id for t in build.targets}
    for tgt in build.targets:
        conf = _conf_from_build(tgt.confidence)
        graph.add_node(
            GraphNode(
                id=tgt.id,
                kind="target",
                label=tgt.name or tgt.id,
                provenance="build_evidence",
                confidence=conf,
                attrs={
                    "kind": tgt.kind.value,
                    "visibility": tgt.visibility,
                    "build_system": tgt.build_system,
                },
            )
        )
        for src in tgt.source_files:
            sid = file_node(src)
            graph.add_edge(
                GraphEdge(
                    src=tgt.id,
                    dst=sid,
                    kind="TARGET_HAS_SOURCE",
                    provenance="build_evidence",
                    confidence=conf,
                )
            )
        for hdr in tgt.public_headers:
            hid = file_node(hdr, header=True)
            graph.add_edge(
                GraphEdge(
                    src=tgt.id,
                    dst=hid,
                    kind="TARGET_HAS_PUBLIC_HEADER",
                    provenance="build_evidence",
                    confidence=conf,
                )
            )
        for dep in tgt.dependencies:
            # Reference an external dependency explicitly when it is not one of
            # our own targets, so the graph distinguishes intra-project edges
            # from third-party ones (informative for reachability triage).
            if dep not in known_targets:
                graph.add_node(
                    GraphNode(
                        id=dep,
                        kind="external_dependency",
                        label=dep,
                        provenance="build_evidence",
                        confidence=CONF_REDUCED,
                    )
                )
            graph.add_edge(
                GraphEdge(
                    src=tgt.id,
                    dst=dep,
                    kind="TARGET_DEPENDS_ON",
                    provenance="build_evidence",
                    confidence=conf,
                )
            )

    for cu in build.compile_units:
        graph.add_node(
            GraphNode(
                id=cu.id,
                kind="compile_unit",
                label=cu.output or cu.source or cu.id,
                provenance="build_evidence",
                confidence=CONF_HIGH,
                attrs={
                    "language": cu.language,
                    "standard": cu.standard,
                    "target_id": cu.target_id,
                },
            )
        )
        if cu.source:
            sid = file_node(cu.source)
            graph.add_edge(
                GraphEdge(
                    src=cu.id,
                    dst=sid,
                    kind="COMPILE_UNIT_BUILDS_SOURCE",
                    provenance="build_evidence",
                    confidence=CONF_HIGH,
                )
            )
        for flag in cu.abi_relevant_flags:
            oid = _option_node_id(flag)
            graph.add_node(
                GraphNode(
                    id=oid,
                    kind="build_option",
                    label=flag,
                    provenance="build_evidence",
                    confidence=CONF_HIGH,
                    attrs={"abi_relevant": True},
                )
            )
            graph.add_edge(
                GraphEdge(
                    src=cu.id,
                    dst=oid,
                    kind="COMPILE_UNIT_USES_OPTION",
                    provenance="build_evidence",
                    confidence=CONF_HIGH,
                )
            )

    _fold_link_provenance(graph, build)

    if source_abi is not None:
        _augment_with_source_abi(graph, source_abi, project_source_files(build))
        _link_options_to_symbols(graph)

    return graph.finalize()


def _link_options_to_symbols(graph: SourceGraphSummary) -> None:
    """Add ``BUILD_OPTION_AFFECTS_SYMBOL`` edges (ADR-031 D2, build→symbol flow).

    Connects each ABI-relevant build option to the exported symbols it can
    affect, via the path *option ← compile_unit (target) → exported symbol*.
    Only meaningful once the L4 surface has contributed ``BINARY_EXPORTS_SYMBOL``
    edges, so it is a no-op for a build-only graph.
    """
    target_syms: dict[str, list[str]] = {}
    for e in graph.edges:
        if e.kind == "BINARY_EXPORTS_SYMBOL":
            target_syms.setdefault(e.src, []).append(e.dst)
    if not target_syms:
        return
    cu_target = {
        n.id: str(n.attrs.get("target_id", ""))
        for n in graph.nodes
        if n.kind == "compile_unit"
    }
    for e in list(graph.edges):
        if e.kind != "COMPILE_UNIT_USES_OPTION":
            continue
        target = cu_target.get(e.src, "")
        for sym in target_syms.get(target, []):
            graph.add_edge(
                GraphEdge(
                    src=e.dst,
                    dst=sym,
                    kind="BUILD_OPTION_AFFECTS_SYMBOL",
                    provenance="build_evidence+source_abi",
                    confidence=CONF_REDUCED,
                )
            )


def _fold_link_provenance(graph: SourceGraphSummary, build: BuildEvidence) -> None:
    """Fold object/link provenance from *build* into *graph* (ADR-041 P1 #2).

    Lets a symbol change be attributed to "which object/archive member/link
    step", not only "which target" — the gap the roadmap named:
    ``TARGET_DEPENDENCY_ADDED``/``EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED``
    currently cannot explain an accidental export from a static archive, a
    COMDAT/weak-symbol resolution change, or a transitive ``DT_NEEDED`` traced
    to a specific object.

    - Every ``compile_unit`` with a known ``output`` gets an ``object_file``
      node and a ``COMPILE_UNIT_EMITS_OBJECT`` edge — "this TU produced this
      object."
    - Every :class:`~abicheck.buildsource.build_evidence.LinkUnit` becomes a
      ``link_unit`` node (``NODE_KINDS`` reserved this kind since ADR-031 D2
      but nothing populated it before this), linked to its owning ``target``
      (``TARGET_HAS_LINK_UNIT``) when the target is known. Each input path is
      classified by suffix into an ``object_file`` or ``static_library`` node
      (best-effort textual classification, no archive introspection) and
      connected via ``LINK_UNIT_HAS_INPUT`` — an object a compile unit already
      emitted (same path) lands on the *same* node instead of a disconnected
      duplicate, so a change traced to one object correlates across both
      slices. A non-empty ``version_script`` gets its own node
      (``LINK_UNIT_USES_VERSION_SCRIPT``).
    - ``linker_script``/``export_map``/``comdat_group`` stay reserved
      (schema-only) — no normalized data source for those three yet.
      ``archive_member``/``ARCHIVE_CONTAINS_OBJECT``/``OBJECT_DEFINES_SYMBOL``
      are *not* populated here — that needs a real archive introspection
      pass (:mod:`~abicheck.buildsource.archive_graph`, G29 Phase 5 item 6),
      run separately over the ``static_library`` nodes this function
      creates; this function only classifies a link input by filename
      suffix, it never opens the archive.

    ``LINK_UNIT_EXPORTS_SYMBOL`` (a link unit's own exported symbols) is added
    by :func:`_augment_with_source_abi` instead, once ``BINARY_EXPORTS_SYMBOL``
    resolves which symbols the owning target actually exports — this function
    runs first (build-evidence-only, no ``source_abi`` required) so the
    ``link_unit`` node it creates is already there for that later step to
    attach to.
    """
    for cu in build.compile_units:
        if not cu.output:
            continue
        oid = _object_node_id(cu.output)
        if not graph.has_node(oid):
            graph.add_node(
                GraphNode(
                    id=oid,
                    kind="object_file",
                    label=cu.output,
                    provenance="build_evidence",
                    confidence=CONF_HIGH,
                )
            )
        graph.add_edge(
            GraphEdge(
                src=cu.id,
                dst=oid,
                kind="COMPILE_UNIT_EMITS_OBJECT",
                provenance="build_evidence",
                confidence=CONF_HIGH,
            )
        )

    known_targets = {t.id for t in build.targets}
    for link in build.link_units:
        graph.add_node(
            GraphNode(
                id=link.id,
                kind="link_unit",
                label=link.output or link.id,
                provenance="build_evidence",
                confidence=CONF_HIGH,
                attrs={
                    "kind": link.kind,
                    "target_id": link.target_id,
                    "soname": link.soname,
                },
            )
        )
        if link.target_id and link.target_id in known_targets:
            graph.add_edge(
                GraphEdge(
                    src=link.target_id,
                    dst=link.id,
                    kind="TARGET_HAS_LINK_UNIT",
                    provenance="build_evidence",
                    confidence=CONF_HIGH,
                )
            )
        for inp in link.inputs:
            if not inp:
                continue
            is_archive = inp.lower().endswith(_STATIC_LIBRARY_SUFFIXES)
            iid = _static_library_node_id(inp) if is_archive else _object_node_id(inp)
            if not graph.has_node(iid):
                graph.add_node(
                    GraphNode(
                        id=iid,
                        kind="static_library" if is_archive else "object_file",
                        label=inp,
                        provenance="build_evidence",
                        confidence=CONF_REDUCED,
                    )
                )
            graph.add_edge(
                GraphEdge(
                    src=link.id,
                    dst=iid,
                    kind="LINK_UNIT_HAS_INPUT",
                    provenance="build_evidence",
                    confidence=CONF_HIGH,
                )
            )
        if link.version_script:
            vid = _version_script_node_id(link.version_script)
            graph.add_node(
                GraphNode(
                    id=vid,
                    kind="version_script",
                    label=link.version_script,
                    provenance="build_evidence",
                    confidence=CONF_HIGH,
                )
            )
            graph.add_edge(
                GraphEdge(
                    src=link.id,
                    dst=vid,
                    kind="LINK_UNIT_USES_VERSION_SCRIPT",
                    provenance="build_evidence",
                    confidence=CONF_HIGH,
                )
            )
