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

"""ADR-061 Phase 5 item 2: shared graph node/edge-classification predicates.

Split out of ``source_graph.py``: these read-only predicates classify an
already-built :class:`~abicheck.model.source_graph.SourceGraphSummary`'s
nodes as public/internal/consumer-compiled — they neither construct a graph
(``source_graph_build.py``) nor compare two of them (``source_graph_compare.
py``), and are shared well beyond either half: ``crosscheck.py``'s intra-
version checks, ``graph_reconcile.py``, ``internal_leak.py``,
``impact/use_cases.py``/``impact/consumer_graph.py``, ``surface.py``, and
``post_processing_reachability.py`` all import from here (via the
``source_graph.py`` facade). Classified ``compare`` in ``architecture/
modules.yaml``: these predicates classify structure on an already-built
graph rather than deciding relevance/suppression/severity, which fits
``compare``'s task-routing role better than ``policy``'s — see that ADR's
Phase 5 item 2 status notes for the full reasoning, including why the two
``policy``-classified callers above (``surface.py``,
``post_processing_reachability.py``) aren't blocked (``policy -> compare``
is an allowed edge).
"""

from __future__ import annotations

from ..model.graph_facts import GraphNode
from ..model.source_graph import SourceGraphSummary

#: Graph node kinds a type entity (as opposed to a function/variable
#: ``source_decl``) can carry. Mirrors ``crosscheck._DECL_NODE_KINDS`` minus ``source_decl``.
_TYPE_ENTITY_KINDS: frozenset[str] = frozenset({"record_type", "enum_type", "typedef"})

#: Graph node kinds that carry a declaration/type visibility we can classify as
#: public or internal. Shared with ``crosscheck.py``'s intra-version
#: ``public_to_internal_dependency`` check (ADR-041 P0 slice 2, fourth Codex
#: review) so the two never classify a node differently.
DECL_NODE_KINDS: frozenset[str] = frozenset({"source_decl"}) | _TYPE_ENTITY_KINDS

#: Node visibilities that put an entity *on* the public source surface. Mirrors
#: ``source_link._is_public`` (which the L5 graph's ``visibility`` attr is
#: derived from): ``generated`` means a generated header **under the public
#: roots** — a public, consumer-visible entity — so it is NOT an internal dependency.
PUBLIC_VISIBILITIES: frozenset[str] = frozenset({"public_header", "generated"})

#: Node visibilities that make an entity *internal* (not public surface): a
#: project-private header or an implementation ("source") file. System headers
#: are third-party (excluded), and ``generated`` is public (above).
INTERNAL_VISIBILITIES: frozenset[str] = frozenset({"private_header", "source"})

#: Visibilities that carry no provenance. The built-in call/type-graph
#: extractors create dependency-target nodes with **no** ``visibility`` attr
#: when the target isn't part of the linked L4 surface. Such a node is
#: internal *only when the project also declares it* (``decl_to_file``) or the
#: extractor marked it ``defined_in_project`` — caller/reference presence
#: alone is unsound (a third-party header-inline symbol whose body is reached
#: also appears as a dependency target), so a bare node with no project
#: provenance is treated as a third-party/system target and not flagged.
UNANNOTATED_VISIBILITIES: frozenset[str] = frozenset({"", "unknown"})

#: Mangled-name prefixes / substrings that mark a standard-library or
#: compiler-internal decl. The call/type graphs resolve targets into ``std::``/
#: ``__gnu_cxx``/cxxabi helpers, which carry no visibility either; without this
#: an unannotated stdlib target would be mis-read as a project-internal
#: dependency and a public API merely using ``std::`` would light up. Mirrors
#: the stdlib/compiler filtering the dumper already applies to exported
#: symbols.
_SYSTEM_NAME_PREFIXES = (
    "_ZSt",
    "_ZNSt",
    "_ZNKSt",
    "_ZNSa",
    "_ZN9__gnu_cxx",
    "_ZNK9__gnu_cxx",
    "_ZN6__cxxabiv",
    "_Znw",
    "_Zna",
    "_Zdl",
    "_Zda",
    "__",
)
_SYSTEM_NAME_SUBSTRINGS = ("std::", "__gnu_cxx::", "__cxxabiv")


def looks_like_system_name(name: str) -> bool:
    """Whether *name* is a standard-library / compiler-internal decl spelling."""
    if name.startswith(_SYSTEM_NAME_PREFIXES):
        return True
    return any(sub in name for sub in _SYSTEM_NAME_SUBSTRINGS)


def decl_declaring_files(graph: SourceGraphSummary) -> dict[str, str]:
    """Map each decl/type id to its declaring file via ``SOURCE_DECLARES`` edges."""
    node_by_id = {n.id: n for n in graph.nodes}
    decl_to_file: dict[str, str] = {}
    for e in graph.edges:
        if e.kind != "SOURCE_DECLARES":
            continue
        header = node_by_id.get(e.src)
        if header is not None and header.label:
            decl_to_file.setdefault(e.dst, header.label)
    return decl_to_file


def is_public_dependency_node(
    node_id: str, node_by_id: dict[str, GraphNode], exported_decls: set[str]
) -> bool:
    """Whether *node_id* is public: exported-symbol-mapped or public-header visible.

    Shared with ``crosscheck.py``'s ``_is_public_decl`` (ADR-041 P0 slice 2).
    Deliberately does not consider whether the node's own body is compiled
    into consumer code (see :func:`is_consumer_compiled_public_entry`) — an
    exported-or-header-visible declaration is exactly the "public API
    surface" question ``crosscheck.py``'s advisory
    ``public_to_internal_dependency`` check (RISK-only, never gates
    suppression) wants to ask, regardless of where the declaration's body
    lives.
    """
    if node_id in exported_decls:
        return True
    node = node_by_id.get(node_id)
    if node is None or node.kind not in DECL_NODE_KINDS:
        return False
    return str(node.attrs.get("visibility", "")) in PUBLIC_VISIBILITIES


def is_consumer_compiled_public_entry(
    node_id: str, node_by_id: dict[str, GraphNode], exported_decls: set[str]
) -> bool:
    """Whether *node_id* is a public entry whose own body is compiled into
    consumer binaries — the correct "entry" set for a *call-graph*
    reachability walk (Codex review, fresh evidence).

    :func:`is_public_dependency_node` alone over-reaches here: an ordinary,
    out-of-line exported function (e.g. ``api()`` defined in a ``.cpp``
    file) is public, but its *body* — and therefore its own internal calls,
    e.g. to ``ns::detail::helper()`` — is compiled into the **library's**
    binary only, never into any consumer's. A consumer links against
    ``api()``'s exported symbol alone; it never sees, references, or
    embeds ``helper()``. So walking the call graph from *every* exported
    function (as :func:`is_public_dependency_node` does) treats an ordinary
    internal implementation-detail call as if it were public-reachable,
    which either manufactures a spurious "still reachable" narrative on a
    genuinely safe-to-suppress internal change, or (via
    ``post_processing.MarkReachability``) blocks a broad internal-namespace
    suppression rule from ever applying to the common case — most
    functions in most libraries are ordinary, out-of-line, non-template.

    The real criterion is whether the entry's own body is emitted into
    every including translation unit — true for inline functions/methods
    and templates, false for an ordinary out-of-line definition — captured
    by ``GraphNode.attrs["consumer_compiled_body"]``
    (:func:`build_source_graph`). A node without that attr at all defaults
    permissively to ``True`` — matching the header-graph/type-node/generic
    case, where no signal either way is available — **except** a node whose
    ``provenance`` is one of :data:`_NO_CONSUMER_COMPILED_SIGNAL_PROVENANCES`
    (Codex review, fresh evidence): ``augment_graph_with_calls``
    (``call_graph.py``) stamps its own fallback tag on a node it creates for
    a caller/callee identity with no other declaration node backing it — a
    real, build-integrated project function reached only through the call
    graph itself, whose out-of-line body is not necessarily
    consumer-compiled. An inline public ``wrap()`` calling an ordinary
    out-of-line project function ``helper_a()`` (this fallback shape) which
    itself calls an internal ``ns::detail::helper()`` must stop expanding
    *at* ``helper_a()`` — treating "no signal" as "safe" for this one node
    shape would silently reintroduce the exact over-reach this predicate
    exists to reject. ``graph_backends.py``'s Kythe/CodeQL ingestion
    (``ingest_kythe_entries``/``ingest_codeql_call_results``) creates the
    identical shape for an external-indexer edge: a bare ``source_decl``
    node stamped with provenance ``"kythe"``/``"codeql"`` and no
    ``consumer_compiled_body`` attr at all, since neither export format says
    whether the referenced declaration's body is inline/template — an
    imported Kythe/CodeQL call chain through an ordinary out-of-line
    intermediate helper must stop there too, for the same reason.
    """
    if not is_public_dependency_node(node_id, node_by_id, exported_decls):
        return False
    return is_consumer_compiled_node(node_id, node_by_id)


#: ``provenance`` tag ``augment_graph_with_calls`` (``call_graph.py``) stamps on a fallback
#: node it creates for a caller/callee identity with no other declaration node backing it —
#: the one node shape known to lack a ``consumer_compiled_body`` attr while still representing
#: a genuine, build-integrated (out-of-line, not-necessarily-consumer-compiled) project
#: declaration, as opposed to "no signal available" (header-graph/type nodes, synthetic test
#: fixtures, …) which stays permissive by default. Mirrored as a literal string rather than
#: imported from ``call_graph.py`` to avoid coupling this module to that one's own constant.
_CALL_GRAPH_FALLBACK_PROVENANCE = "call_graph"

#: Same shape as :data:`_CALL_GRAPH_FALLBACK_PROVENANCE`, for external
#: indexer backends (Codex review, fresh evidence): ``graph_backends.py``'s
#: ``ingest_kythe_entries``/``ingest_codeql_call_results``/
#: ``ingest_codeql_extends_results`` stamp exactly these two provenance
#: strings on a bare ``source_decl``/``record_type`` node with no
#: ``consumer_compiled_body`` attr — Kythe entries and CodeQL query results
#: carry cross-reference edges only, never whether the referenced
#: declaration's body is inline/template, so an attr-less node reached only
#: through one of these backends is exactly as unproven as the call-graph
#: fallback shape and must not be treated as a safe stopping point by
#: default either.
_NO_CONSUMER_COMPILED_SIGNAL_PROVENANCES = frozenset(
    {
        _CALL_GRAPH_FALLBACK_PROVENANCE,
        "kythe",
        "codeql",
    }
)


def is_consumer_compiled_node(node_id: str, node_by_id: dict[str, GraphNode]) -> bool:
    """Whether *node_id*'s own body is compiled into consumer code, independent
    of whether it also qualifies as a *public* entry (see
    :func:`is_consumer_compiled_public_entry` for that combined check) — the
    predicate a call-graph *traversal* needs at every intermediate node, not
    just at the entries it starts from (Codex review, fresh evidence: see
    :func:`is_consumer_compiled_public_entry`'s docstring for the fallback-node
    shapes this conservative exception protects against).
    """
    node = node_by_id.get(node_id)
    if node is None:
        return True
    if "consumer_compiled_body" in node.attrs:
        return bool(node.attrs["consumer_compiled_body"])
    return node.provenance not in _NO_CONSUMER_COMPILED_SIGNAL_PROVENANCES


def is_internal_dependency_node(
    node_id: str,
    node_by_id: dict[str, GraphNode],
    exported_decls: set[str],
    decl_to_file: dict[str, str],
) -> bool:
    """Whether *node_id* is a project-internal decl/type consumers cannot see.

    "Not declared by a public header" alone is not internal — a third-party or
    standard-library type used as a field/parameter type is *also* not
    declared by any project header, and must not be conflated with a genuinely
    private project entity (ADR-041 P0 slice 2, fourth Codex review). Requires
    positive evidence instead: an explicit ``private_header``/``source``
    visibility, or — for an unannotated node — project-file provenance
    (``decl_to_file``/``defined_in_project``) plus a non-system-looking name.
    Shared with ``crosscheck.py``'s ``_is_internal_decl`` (same algorithm, same
    source of truth) so the intra-version and inter-version checks classify a
    node identically.
    """
    node = node_by_id.get(node_id)
    if node is None or node.kind not in DECL_NODE_KINDS:
        return False
    if node_id in exported_decls:
        return False
    vis = str(node.attrs.get("visibility", ""))
    if vis in INTERNAL_VISIBILITIES:
        return True
    if vis in UNANNOTATED_VISIBILITIES:
        has_provenance = node_id in decl_to_file or bool(
            node.attrs.get("defined_in_project")
        )
        if not has_provenance:
            return False
        return not looks_like_system_name(node.label or "")
    return False
