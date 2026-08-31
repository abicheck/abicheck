# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors
"""Public-surface evidence graph builder (ADR-063 Phase 3 D5).

Registers this phase's own node/edge *kind* vocabulary and populates graph
facts from L0-L2 data already on a live ``AbiSnapshot`` — reusing
``model.graph_facts``'s ``GraphNode``/``GraphEdge`` primitive directly,
never a second dataclass hierarchy (the Governing Invariant this phase
exists to defend). ``compare/`` may depend only on ``model``, so this
module never imports ``buildsource``, ``surface.py``, or
``export_surface.py`` — it reads the same L0-L2 declaration fields those
modules independently reconstruct today (``source_header``, ``origin``,
``qualified_name``, field/base/signature type strings), not their code.

**Node kinds populated this slice**: ``declaration`` (function/variable),
``type`` (record/enum/typedef), ``header``, ``symbol``. **Not populated**:
``translation_unit``, ``target`` — no current consumer needs a
target-attribution edge/node (ADR-057/053's consumer graph and TU→DSO
attribution stay their own, later, separately-justified migration per this
plan's "don't attempt a change with no real caller" discipline).

**Edge kinds populated this slice**: ``declares`` (header → declaration/
type), ``references`` (declaration/type → type, from field/base/signature
type references resolvable to another declared type in this same
snapshot), ``exports`` (symbol → declaration, from the observed export
table). **Not populated**: ``includes`` (header → header) — every
function/variable's ``Visibility.PUBLIC`` is already resolved
per-declaration at parse time (ADR-016), so this phase's own relevance
query does not need a transitive header-inclusion walk to seed roots (see
``policy/public_surface.py``'s own scoping note for the query side of this
same decision); ``instantiates`` (template-specific, ADR-053/057
territory); ``owned_by_target``.

Declaration/type node ids are ``canonical_key(occurrence_id)`` with an
empty disambiguator — this phase's own L0-L2 builder carries no TU-context
signal to populate one with (see ``model.occurrence``'s own module
docstring), which is exactly what makes that key reduce to plain
``entity_id.key``. When a declaration's parse-time ``entity_id`` carrier
is unpopulated (a pre-ADR-063-Phase-2 snapshot, or a kind the header-AST
backends don't resolve one for yet — see AGENTS.md's own "exhaustive
``entity_id`` population" open item), this builder falls back to a plain
string node id derived from the flattened qualified-name string, namespaced
by entity kind (``declaration::``/``type::``/``typedef::``) so a function
and an unrelated record/enum/typedef sharing one bare spelling never
collide onto one node — **not** a synthesized ``EntityId``: ``entity_id_for_*`` may only be called
by a header-AST producer, the only place a real, typed ``ScopePath``
exists to build one from (``tests/test_entity_id_carrier.py::
TestResolverIsOnlyCalledByAProducer`` enforces this repo-wide), and a
post-parse module recomputing one from a bare string could only ever
approximate it, which that invariant exists specifically to rule out.

**Known gap, deliberately not closed this slice**: ``buildsource.
header_graph.build_header_only_graph`` (the pre-existing L2/L5 builder this
module's own facts now share one ``SourceGraphSummary`` instance with —
see ``service_header_graph_attach.py``'s assembly step) mints its own
``source_decl``/``record_type``/``enum_type`` nodes under a *different* id
scheme entirely: ``decl://<normalized identity>``/``type://<normalized
identity>`` (``model.graph_facts._decl_node_id``/``_type_node_id``, a
mangled-or-qualified-name string), never this module's
``canonical_key(occurrence_id)``/``declaration::``/``type::``/``typedef::``
ids. Sharing one graph instance is real (both builders' nodes/edges coexist
in it, verified by
``tests/test_service_header_graph_attach_surface_graph.py``), but the two
id namespaces do not currently collide or dedup onto one node for a
declaration both builders happen to see — reconciling them is a real,
separate, deeper migration (either this module adopts ``header_graph.py``'s
``decl://``/``type://`` scheme, or that already-multi-round-hardened,
mangled-identity-based module adopts this one), left for a later phase
rather than attempted reactively here.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..model.graph_facts import GraphEdge, GraphNode
from ..model.occurrence import OccurrenceId, canonical_key

if TYPE_CHECKING:
    from ..model.declarations import Function, Variable
    from ..model.entities import EnumType, RecordType
    from ..model.fact import Fact
    from ..model.graph_facts import SurfaceGraphLike
    from ..model.identity import EntityId
    from ..model.snapshot import AbiSnapshot

__all__ = ["build_public_surface_facts"]

NODE_KIND_HEADER = "header"
NODE_KIND_DECLARATION = "declaration"
NODE_KIND_TYPE = "type"
NODE_KIND_SYMBOL = "symbol"

EDGE_KIND_DECLARES = "declares"
EDGE_KIND_REFERENCES = "references"
EDGE_KIND_EXPORTS = "exports"

_TYPE_NOISE: frozenset[str] = frozenset(
    {
        "const",
        "volatile",
        "unsigned",
        "signed",
        "struct",
        "class",
        "union",
        "enum",
        "typename",
        "mutable",
        "restrict",
        "register",
        "void",
        "bool",
        "char",
        "short",
        "int",
        "long",
        "float",
        "double",
        "wchar_t",
        "char8_t",
        "char16_t",
        "char32_t",
    }
)
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_:]*")


def _type_identifiers(type_str: str | None) -> set[str]:
    """Candidate type names referenced by *type_str* — a local, leaf-safe
    duplicate of ``surface._type_identifiers`` (``compare/`` may not import
    a `policy`-layer module; both sides independently reading the same
    small, pure regex scan is the established leaf-duplication pattern this
    codebase already uses for e.g. ``storage``/``model``'s two ``_packed``
    implementations)."""
    if not type_str:
        return set()
    out: set[str] = set()
    for tok in _IDENT_RE.findall(type_str):
        if tok in _TYPE_NOISE:
            continue
        out.add(tok)
        if "::" in tok:
            out.add(tok.rsplit("::", 1)[1])
    return out


def _approximate_node_id(qualified_name: str, *, kind: str) -> str:
    """Plain string node id for a declaration/type with no parse-time
    ``entity_id`` -- the flattened qualified-name string itself, never a
    synthesized ``EntityId`` (see this module's own docstring for why).
    *kind* (``"declaration"``/``"type"``/``"typedef"``) namespaces the
    fallback id space per entity kind, so a function and an unrelated
    record/enum/typedef sharing one bare spelling (legal C: ``struct stat``
    alongside a function named ``stat``, or the ``typedef struct Foo Foo;``
    idiom) never collide onto the same node id when neither side has a
    resolved ``entity_id`` -- confirmed to fail without this discriminator
    (Codex review, PR #962)."""
    return f"{kind}::{qualified_name}"


def _declaration_entity_id(decl: Function | Variable) -> EntityId | None:
    return decl.entity_id


def _fact_list(fact: Fact[list[str]] | None) -> list[str]:
    """``rec.bases``/``.virtual_bases``' ``Fact[T]`` sibling, unwrapped —
    never the legacy field directly (ADR-063 Phase 0's `fact-field-readers`
    gate): ``NOT_COLLECTED`` reads as no bases to reference, the same as a
    confirmed-empty list, since this builder only ever adds edges that
    genuinely resolve against another declared type in this snapshot —
    a missing base contributes nothing either way, not a wrong edge."""
    if fact is None or not fact.is_present or fact.value is None:
        return []
    return fact.value


def _node_id(entity_id: EntityId) -> str:
    return canonical_key(OccurrenceId(entity_id))


def _node_id_for(entity_id: EntityId | None, qualified_name: str, *, kind: str) -> str:
    """A declaration/type's node id: its real, parse-time ``entity_id`` when
    populated, else the plain-string approximate fallback (never a
    synthesized ``EntityId`` -- see this module's own docstring). *kind* is
    forwarded to :func:`_approximate_node_id` and ignored when a real
    ``entity_id`` is present (that id is already kind-disambiguated)."""
    return (
        _node_id(entity_id)
        if entity_id is not None
        else _approximate_node_id(qualified_name, kind=kind)
    )


def _header_node_id(header: str) -> str:
    return f"header://{header}"


def _add_header_declares(
    graph: SurfaceGraphLike, source_header: str | None, decl_node_id: str
) -> None:
    if not source_header:
        return
    header_id = _header_node_id(source_header)
    graph.add_node(GraphNode(id=header_id, kind=NODE_KIND_HEADER, label=source_header))
    graph.add_edge(GraphEdge(src=header_id, dst=decl_node_id, kind=EDGE_KIND_DECLARES))


def _add_references(
    graph: SurfaceGraphLike,
    src_node_id: str,
    type_index: dict[str, str],
    *type_strs: str | None,
) -> None:
    seen: set[str] = set()
    for type_str in type_strs:
        for ident in _type_identifiers(type_str):
            if ident in seen:
                continue
            seen.add(ident)
            dst = type_index.get(ident)
            if dst is not None and dst != src_node_id:
                graph.add_edge(
                    GraphEdge(src=src_node_id, dst=dst, kind=EDGE_KIND_REFERENCES)
                )


def _build_type_index(
    graph: SurfaceGraphLike,
    types: list[RecordType],
    enums: list[EnumType],
    typedefs: dict[str, str],
) -> dict[str, str]:
    """Register every declared record/enum/typedef as a ``type`` node,
    returning a name → node-id index (both the bare leaf and the qualified
    spelling, mirroring ``surface.py``'s own alias-index convention -- bare
    names included, but never a silent first-wins pick: a bare name shared
    by more than one type (``ns1::Foo``/``ns2::Foo``) is ambiguous, exactly
    what ``surface.py``'s own ``ambiguous_type_names`` tracks and every one
    of its consumers checks before trusting a bare match, so this index
    drops that bare key entirely rather than resolving it arbitrarily) for
    :func:`_add_references` to resolve a signature/field type string
    against."""
    index: dict[str, str] = {}
    ambiguous_bare: set[str] = set()

    def _register(qname: str, bare: str, node_id: str, label: str) -> None:
        graph.add_node(GraphNode(id=node_id, kind=NODE_KIND_TYPE, label=label))
        index.setdefault(qname, node_id)
        if bare in index and index[bare] != node_id:
            ambiguous_bare.add(bare)
        else:
            index.setdefault(bare, node_id)

    for rec in types:
        qname = rec.qualified_name or rec.name
        node_id = _node_id_for(rec.entity_id, qname, kind="type")
        _register(qname, rec.name, node_id, qname)
    for en in enums:
        qname = en.qualified_name or en.name
        node_id = _node_id_for(en.entity_id, qname, kind="type")
        _register(qname, en.name, node_id, qname)
    for alias in typedefs:
        node_id = _approximate_node_id(alias, kind="typedef")
        _register(alias, alias.rsplit("::", 1)[-1], node_id, alias)
    for bare in ambiguous_bare:
        index.pop(bare, None)
    return index


def _add_export_edges(graph: SurfaceGraphLike, decl_node_ids: dict[str, str]) -> None:
    """``exports`` edges from a ``symbol`` node to its declaration, for
    every declaration this builder resolved a mangled linker name for.
    Deliberately not export-table-matched (that is `export_surface.py`'s
    own, more precise root-seeding logic) — this is a straightforward
    "this declaration's own linker identity is a symbol" edge, useful graph
    data independent of whether it was actually observed exported."""
    for mangled, decl_node_id in decl_node_ids.items():
        symbol_id = f"symbol://{mangled}"
        graph.add_node(GraphNode(id=symbol_id, kind=NODE_KIND_SYMBOL, label=mangled))
        graph.add_edge(
            GraphEdge(src=symbol_id, dst=decl_node_id, kind=EDGE_KIND_EXPORTS)
        )


def build_public_surface_facts(snap: AbiSnapshot, graph: SurfaceGraphLike) -> None:
    """Populate *graph* with declaration/type/header/symbol nodes and
    declares/references/exports edges for *snap*, from L0-L2 facts alone.
    Idempotent — ``add_node``/``add_edge`` already dedup by id/relation
    key, so calling this twice on the same graph (or on a graph another
    builder already wrote into) is safe.
    """
    type_index = _build_type_index(graph, snap.types, snap.enums, snap.typedefs)
    decl_node_ids: dict[str, str] = {}

    for fn in snap.functions:
        node_id = _node_id_for(_declaration_entity_id(fn), fn.name, kind="declaration")
        graph.add_node(GraphNode(id=node_id, kind=NODE_KIND_DECLARATION, label=fn.name))
        _add_header_declares(graph, fn.source_header, node_id)
        _add_references(
            graph, node_id, type_index, fn.return_type, *(p.type for p in fn.params)
        )
        if fn.mangled:
            decl_node_ids[fn.mangled] = node_id

    for var in snap.variables:
        node_id = _node_id_for(
            _declaration_entity_id(var), var.name, kind="declaration"
        )
        graph.add_node(
            GraphNode(id=node_id, kind=NODE_KIND_DECLARATION, label=var.name)
        )
        _add_header_declares(graph, var.source_header, node_id)
        _add_references(graph, node_id, type_index, var.type)
        if var.mangled:
            decl_node_ids[var.mangled] = node_id

    for rec in snap.types:
        qname = rec.qualified_name or rec.name
        node_id = _node_id_for(rec.entity_id, qname, kind="type")
        _add_header_declares(graph, rec.source_header, node_id)
        _add_references(
            graph,
            node_id,
            type_index,
            *(f.type for f in rec.fields),
            *_fact_list(rec.bases_fact),
            *_fact_list(rec.virtual_bases_fact),
        )

    for en in snap.enums:
        qname = en.qualified_name or en.name
        node_id = _node_id_for(en.entity_id, qname, kind="type")
        _add_header_declares(graph, en.source_header, node_id)

    for alias, target in snap.typedefs.items():
        node_id = _approximate_node_id(alias, kind="typedef")
        _add_references(graph, node_id, type_index, target)

    _add_export_edges(graph, decl_node_ids)
