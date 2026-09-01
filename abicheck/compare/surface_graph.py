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
from typing import TYPE_CHECKING, NamedTuple

from ..model.graph_facts import GraphEdge, GraphNode
from ..model.occurrence import OccurrenceId, canonical_key

if TYPE_CHECKING:
    from ..model.declarations import Function, Variable
    from ..model.entities import EnumType, RecordType
    from ..model.fact import Fact
    from ..model.graph_facts import SurfaceGraphLike
    from ..model.identity import EntityId
    from ..model.snapshot import AbiSnapshot

__all__ = [
    "build_public_surface_facts",
    "fact_list",
    "node_id_for_declaration",
    "node_id_for_type",
    "node_id_for_typedef",
]

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


def fact_list(fact: Fact[list[str]] | None) -> list[str]:
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


def node_id_for_declaration(entity_id: EntityId | None, name: str) -> str:
    """Public wrapper over :func:`_node_id_for` for a function/variable
    declaration -- the same id :func:`build_public_surface_facts` gives its
    own declaration nodes, exposed so ``policy/public_surface.py`` (the
    ADR-063 Phase 3 D5 query side) can look one up by the same key without
    reaching into this module's private helpers."""
    return _node_id_for(entity_id, name, kind="declaration")


def node_id_for_type(entity_id: EntityId | None, qualified_name: str) -> str:
    """Public wrapper over :func:`_node_id_for` for a record/enum type node
    -- see :func:`node_id_for_declaration`."""
    return _node_id_for(entity_id, qualified_name, kind="type")


def node_id_for_typedef(alias: str) -> str:
    """Public wrapper over :func:`_approximate_node_id` for a typedef alias
    node -- see :func:`node_id_for_declaration`. A typedef has no
    ``entity_id`` carrier at all (``snap.typedefs`` is a bare ``dict[str,
    str]``), so this is always the approximate string form."""
    return _approximate_node_id(alias, kind="typedef")


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


class _ReferencedIdentifiers(NamedTuple):
    by_node: dict[str, list[str]]
    collided_nodes: frozenset[str]


def _referenced_identifiers_by_node(snap: AbiSnapshot) -> _ReferencedIdentifiers:
    """First pass, computed before any node is emitted: node id -> the
    sorted union of every type-identifier string that *any* declaration/
    record/typedef mapping to that id references in its own signature/
    fields/bases/target (ADR-063 Phase 3 D5's own follow-up: the actual
    ``policy.public_surface`` traversal migration reads this attrs entry
    instead of re-parsing ``fn.return_type``/``rec.fields``/etc. a second,
    third, and fourth time the way ``surface.py``'s own closure walk used
    to) -- plus which node ids that union is *not* safe to trust for a
    caller needing per-declaration precision.

    **Why a union, and why precomputed rather than attached inline as each
    node is built**: two declarations can share one *approximate* node id
    (no real ``entity_id`` resolved for either -- see this module's own
    docstring) without being the same declaration at all (two overloads
    sharing one demangled name, e.g.). Attaching each one's own identifier
    set independently, in two separate ``add_node`` calls for the same id,
    would hand the second call's list to the generic cross-producer
    ``GraphFact`` merge machinery (``model.graph_facts.merge_graph_facts``),
    which resolves a same-key disagreement by confidence/producer/content
    precedence, not by union -- silently dropping whichever side loses that
    tie-break. That is exactly the anti-hiding violation this whole module
    exists to avoid: a real reference from the losing declaration would
    vanish from the graph's evidence with no trace. Precomputing the union
    up front means every ``add_node`` call for a given id carries the
    *same*, already-complete value, so the merge machinery only ever sees
    identical repeated registrations (a no-op), never a real value
    conflict.

    **Why a union is not always the right answer either, and why the
    collision needs to be reported, not just resolved.** Unioning is safe
    for a caller that only ever asks "is *anything* reachable from here"
    (over-keeping is this whole area's own established safe direction).
    It is not safe for a caller that needs to know what *one specific*
    declaration references, independent of a same-node sibling it happens
    to share an approximate id with -- e.g. a public, no-argument overload
    sharing one id with a hidden, private-type-taking overload (no
    ``entity_id`` resolved for either, no mangled name to fall back to)
    must not appear to reference the hidden overload's own private
    parameter type merely because both collapsed onto one node. Returning
    the set of node ids where more than one distinct declaration/type/
    typedef entry contributed lets such a caller detect exactly that case
    and fall back to computing that *one* entry's own identifiers directly,
    rather than either silently trusting a blurred union or (worse) an
    arbitrary single contributor's value.
    """
    acc: dict[str, set[str]] = {}
    contributor_counts: dict[str, int] = {}

    def _add(node_id: str, *type_strs: str | None) -> None:
        idents: set[str] = set()
        for s in type_strs:
            idents |= _type_identifiers(s)
        if idents:
            acc.setdefault(node_id, set()).update(idents)
        contributor_counts[node_id] = contributor_counts.get(node_id, 0) + 1

    for fn in snap.functions:
        node_id = node_id_for_declaration(_declaration_entity_id(fn), fn.name)
        _add(node_id, fn.return_type, *(p.type for p in fn.params))
    for var in snap.variables:
        node_id = node_id_for_declaration(_declaration_entity_id(var), var.name)
        _add(node_id, var.type)
    for rec in snap.types:
        qname = rec.qualified_name or rec.name
        node_id = node_id_for_type(rec.entity_id, qname)
        _add(
            node_id,
            *(f.type for f in rec.fields),
            *fact_list(rec.bases_fact),
            *fact_list(rec.virtual_bases_fact),
        )
    for alias, target in snap.typedefs.items():
        _add(node_id_for_typedef(alias), target)
    by_node = {node_id: sorted(idents) for node_id, idents in acc.items()}
    collided = frozenset(
        node_id for node_id, count in contributor_counts.items() if count > 1
    )
    return _ReferencedIdentifiers(by_node=by_node, collided_nodes=collided)


def _node_attrs(refs: _ReferencedIdentifiers, node_id: str) -> dict[str, object]:
    """The ``referenced_identifiers``/``identifiers_collision`` attrs pair
    every declaration/type/typedef node carries -- see
    :func:`_referenced_identifiers_by_node`'s own docstring for what
    ``identifiers_collision`` means and why a caller needing per-declaration
    precision must check it before trusting the unioned list."""
    return {
        "referenced_identifiers": refs.by_node.get(node_id, []),
        "identifiers_collision": node_id in refs.collided_nodes,
    }


def _build_type_index(
    graph: SurfaceGraphLike,
    types: list[RecordType],
    enums: list[EnumType],
    typedefs: dict[str, str],
    referenced_by_node: _ReferencedIdentifiers,
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
        graph.add_node(
            GraphNode(
                id=node_id,
                kind=NODE_KIND_TYPE,
                label=label,
                attrs=_node_attrs(referenced_by_node, node_id),
            )
        )
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
    referenced_by_node = _referenced_identifiers_by_node(snap)
    type_index = _build_type_index(
        graph, snap.types, snap.enums, snap.typedefs, referenced_by_node
    )
    decl_node_ids: dict[str, str] = {}

    for fn in snap.functions:
        node_id = _node_id_for(_declaration_entity_id(fn), fn.name, kind="declaration")
        graph.add_node(
            GraphNode(
                id=node_id,
                kind=NODE_KIND_DECLARATION,
                label=fn.name,
                attrs=_node_attrs(referenced_by_node, node_id),
            )
        )
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
            GraphNode(
                id=node_id,
                kind=NODE_KIND_DECLARATION,
                label=var.name,
                attrs=_node_attrs(referenced_by_node, node_id),
            )
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
            *fact_list(rec.bases_fact),
            *fact_list(rec.virtual_bases_fact),
        )

    for en in snap.enums:
        qname = en.qualified_name or en.name
        node_id = _node_id_for(en.entity_id, qname, kind="type")
        _add_header_declares(graph, en.source_header, node_id)

    for alias, target in snap.typedefs.items():
        node_id = _approximate_node_id(alias, kind="typedef")
        _add_references(graph, node_id, type_index, target)

    _add_export_edges(graph, decl_node_ids)
