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
snapshot), ``declares_linker_name`` (symbol → declaration, projected from
the declaration's own mangled name -- evidence class ``derived``, *not* an
observed export-table join; ``EDGE_EVIDENCE_CLASS`` records each kind's
class), and the two Phase 2 ``resolved_join`` kinds (evidence-entity-model
plan): ``exports`` (``binary_symbol`` -> declaration, from
``compare/export_join.py``'s observed export-table join) and
``debug_type_of`` (``debug_type`` -> header type, from
``compare/debug_type_join.py``). Every observed export/debug occurrence is a
node carrying its join state, so an orphan on either side stays visible.
**Not populated**: ``includes`` (header → header) — every
function/variable's ``Visibility.PUBLIC`` is already resolved
per-declaration at parse time (ADR-016), so this phase's own relevance
query does not need a transitive header-inclusion walk to seed roots (see
``policy/public_surface.py``'s own scoping note for the query side of this
same decision); ``instantiates`` (template-specific, ADR-053/057
territory); ``owned_by_target``.

Declaration/type node ids come from ``model.graph_entity_identity``'s
:func:`~abicheck.model.graph_entity_identity.snapshot_identities` table --
the one identity function every graph producer shares (evidence-entity-model
invariant I1). ``buildsource.header_graph.build_header_only_graph`` reads the
same table, so when the two builders write into one shared
``SourceGraphSummary`` (``service_header_graph_attach.py``'s assembly step)
a declaration both see is one node, not a ``declaration``/``source_decl``
pair: this builder emits the header graph's own node kinds
(``source_decl``/``record_type``/``enum_type``/``typedef``) and adds its
attrs to the existing node. An entity with no resolvable identity is an
explicit ``unresolved`` node (``attrs["identity"] == "unresolved"``), never
an approximate name-string id. The earlier ``declaration::``/``type::``/
``typedef::`` fallback scheme and the ``canonical_key(occurrence_id)`` ids
(which no other producer could compute) are retired.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, NamedTuple

from ..model.graph_entity_identity import (
    IDENTITY_STATE_ATTR,
    GraphEntityIdentity,
    SnapshotIdentities,
    register_identity_alias,
    snapshot_identities,
)
from ..model.graph_evidence_class import (
    PUBLIC_SURFACE_FACTS_PRODUCER,
    EdgeEvidenceClass,
)
from ..model.graph_facts import GraphEdge, GraphNode
from ..model.graph_join import (
    EDGE_KIND_DEBUG_TYPE_OF,
    EDGE_KIND_EXPORTS,
    CrossLayerJoin,
)
from .debug_type_join import join_debug_types
from .export_join import join_exports
from .ownership_relations import (
    EDGE_KIND_IN_CONTRACT,
    EDGE_KIND_OWNED_BY,
    OwnershipRelations,
    ownership_relations,
)

if TYPE_CHECKING:
    from ..model.fact import Fact
    from ..model.graph_facts import SurfaceGraphLike
    from ..model.snapshot import AbiSnapshot

__all__ = [
    "EDGE_EVIDENCE_CLASS",
    "ReferencedIdentifiers",
    "TypeSpellingIndex",
    "build_public_surface_facts",
    "fact_list",
    "referenced_identifiers_by_node",
    "type_spelling_index",
]

NODE_KIND_HEADER = "header"
#: The header graph's own node kinds (I1: one node per entity, so one kind
#: vocabulary for it too).
NODE_KIND_DECLARATION = "source_decl"
NODE_KIND_RECORD_TYPE = "record_type"
NODE_KIND_ENUM_TYPE = "enum_type"
NODE_KIND_TYPEDEF = "typedef"
NODE_KIND_SYMBOL = "symbol"
#: An observed export-table entry / debug-info type occurrence -- the other
#: side of a Phase 2 join (``model/graph_join.py``).
NODE_KIND_BINARY_SYMBOL = "binary_symbol"
NODE_KIND_DEBUG_TYPE = "debug_type"
#: ADR-075 D5: the targets of ``owned_by``/``in_contract``.
NODE_KIND_OWNER = "owner"
NODE_KIND_CONTRACT = "contract"
#: The node/edge attr a join's per-subject state is stamped under.
JOIN_STATE_ATTR = "join_state"
#: The declaration-node attr carrying its ``exports`` join state, so an
#: orphan declaration (a public inline function) is visible in the graph.
EXPORT_JOIN_STATE_ATTR = "export_join_state"

EDGE_KIND_DECLARES = "declares"
EDGE_KIND_REFERENCES = "references"
#: A declaration's *own* mangled linker name, projected from the record
#: itself -- never matched against the observed export table. The observed
#: join is ``exports`` (``model.graph_join.EDGE_KIND_EXPORTS``, Phase 2).
EDGE_KIND_DECLARES_LINKER_NAME = "declares_linker_name"

#: The evidence class of every edge kind this builder can emit. A new edge
#: kind must be added here too (``tests/test_compare_surface_graph.py``
#: checks exhaustiveness against what the builder actually emits).
EDGE_EVIDENCE_CLASS: Mapping[str, EdgeEvidenceClass] = MappingProxyType(
    {
        EDGE_KIND_DECLARES: EdgeEvidenceClass.OBSERVED,
        EDGE_KIND_REFERENCES: EdgeEvidenceClass.RESOLVED_JOIN,
        EDGE_KIND_DECLARES_LINKER_NAME: EdgeEvidenceClass.DERIVED,
        EDGE_KIND_EXPORTS: EdgeEvidenceClass.RESOLVED_JOIN,
        EDGE_KIND_DEBUG_TYPE_OF: EdgeEvidenceClass.RESOLVED_JOIN,
        # ADR-075 D5 (evidence-entity-model Phase 3): projections of each
        # entity's persisted ownership_fact.
        EDGE_KIND_OWNED_BY: EdgeEvidenceClass.DERIVED,
        EDGE_KIND_IN_CONTRACT: EdgeEvidenceClass.DERIVED,
    }
)

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


def _identity_attrs(ident: GraphEntityIdentity) -> dict[str, object]:
    return {} if ident.resolved else {IDENTITY_STATE_ATTR: ident.state.value}


def _header_node_id(header: str) -> str:
    return f"header://{header}"


def _add_header_declares(
    graph: SurfaceGraphLike, source_header: str | None, decl_node_id: str
) -> None:
    if not source_header:
        return
    header_id = _header_node_id(source_header)
    graph.add_node(
        GraphNode(
            provenance=PUBLIC_SURFACE_FACTS_PRODUCER,
            id=header_id,
            kind=NODE_KIND_HEADER,
            label=source_header,
        )
    )
    graph.add_edge(
        GraphEdge(
            provenance=PUBLIC_SURFACE_FACTS_PRODUCER,
            src=header_id,
            dst=decl_node_id,
            kind=EDGE_KIND_DECLARES,
        )
    )


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
                    GraphEdge(
                        provenance=PUBLIC_SURFACE_FACTS_PRODUCER,
                        src=src_node_id,
                        dst=dst,
                        kind=EDGE_KIND_REFERENCES,
                    )
                )


class ReferencedIdentifiers(NamedTuple):
    by_node: dict[str, list[str]]
    collided_nodes: frozenset[str]
    #: The snapshot's identity table these node ids were computed from --
    #: readers look an entity's node id up here (:meth:`node_id`) rather than
    #: recomputing it, so a lookup can never disagree with the producer.
    ids: SnapshotIdentities
    #: ``id(entity object) -> node id`` for every function/variable/record/
    #: enum of the snapshot this was computed from.
    node_ids: dict[int, str]

    def node_id(self, entity: object) -> str:
        """The node id of *entity* -- one of the snapshot's own
        function/variable/record/enum objects."""
        return self.node_ids[id(entity)]

    def typedef_node_id(self, alias: str) -> str:
        return self.ids.typedefs[alias].node_id


def referenced_identifiers_by_node(snap: AbiSnapshot) -> ReferencedIdentifiers:
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
    ids = snapshot_identities(snap)
    node_ids: dict[int, str] = {}
    acc: dict[str, set[str]] = {}
    contributor_counts: dict[str, int] = {}

    def _add(entity: object, node_id: str, *type_strs: str | None) -> None:
        if entity is not None:
            node_ids[id(entity)] = node_id
        idents: set[str] = set()
        for s in type_strs:
            idents |= _type_identifiers(s)
        if idents:
            acc.setdefault(node_id, set()).update(idents)
        contributor_counts[node_id] = contributor_counts.get(node_id, 0) + 1

    for fn, ident in zip(snap.functions, ids.functions):
        _add(fn, ident.node_id, fn.return_type, *(p.type for p in fn.params))
    for var, ident in zip(snap.variables, ids.variables):
        _add(var, ident.node_id, var.type)
    for rec, ident in zip(snap.types, ids.records):
        _add(
            rec,
            ident.node_id,
            *(f.type for f in rec.fields),
            *fact_list(rec.bases_fact),
            *fact_list(rec.virtual_bases_fact),
        )
    for en, ident in zip(snap.enums, ids.enums):
        node_ids[id(en)] = ident.node_id
    for alias, target in snap.typedefs.items():
        _add(None, ids.typedefs[alias].node_id, target)
    by_node = {node_id: sorted(idents) for node_id, idents in acc.items()}
    collided = frozenset(
        node_id for node_id, count in contributor_counts.items() if count > 1
    )
    return ReferencedIdentifiers(
        by_node=by_node, collided_nodes=collided, ids=ids, node_ids=node_ids
    )


def _node_attrs(refs: ReferencedIdentifiers, node_id: str) -> dict[str, object]:
    """The ``referenced_identifiers``/``identifiers_collision`` attrs pair
    every declaration/type/typedef node carries -- see
    :func:`referenced_identifiers_by_node`'s own docstring for what
    ``identifiers_collision`` means and why a caller needing per-declaration
    precision must check it before trusting the unioned list.

    **Informational graph content only, as of ADR-063 Phase 3 D5's third
    review round (Codex, PR #979) -- no consumer inside this codebase
    trusts these two attrs for a correctness-sensitive decision.**
    ``policy.public_surface_closure``'s closure walk (the one consumer that
    originally did) now calls :func:`referenced_identifiers_by_node`
    directly instead: a node's ``attrs`` are derived through
    ``model.graph_facts``' cross-producer evidence-merge machinery, which
    resolves a same-key disagreement between two registrations by
    confidence/producer/content precedence -- appropriate for genuinely
    independent producer facts, but wrong for this specific, single-source
    derived computation, since a stale or adversarial persisted fact could
    silently outrank a freshly recomputed correct one. These attrs are kept
    on the node purely because they are legitimate, general graph content
    (per ADR-063's "one graph, all evidence" governing principle) that some
    future consumer may still find useful to inspect -- not because
    anything here should be trusted over a direct call to
    :func:`referenced_identifiers_by_node` for a real decision."""
    return {
        "referenced_identifiers": refs.by_node.get(node_id, []),
        "identifiers_collision": node_id in refs.collided_nodes,
    }


class TypeSpellingIndex(NamedTuple):
    """Type spelling -> node id, for every spelling naming exactly one
    record/enum/typedef; ``ambiguous`` maps each spelling naming several to
    all of them, since a reference through it resolves to none."""

    by_spelling: dict[str, str]
    ambiguous: dict[str, frozenset[str]]


def type_spelling_index(
    snap: AbiSnapshot, ids: SnapshotIdentities
) -> TypeSpellingIndex:
    """Both the bare leaf and the qualified spelling of every declared
    record/enum/typedef, mirroring ``surface.py``'s own alias-index
    convention -- bare names included, but never a silent first-wins pick: a
    bare name shared by more than one type (``ns1::Foo``/``ns2::Foo``) is
    ambiguous, exactly what ``surface.py``'s own ``ambiguous_type_names``
    tracks and every one of its consumers checks before trusting a bare
    match, so the index drops that key entirely rather than resolving it
    arbitrarily; a qualified spelling shared by two declarations is dropped
    the same way. The one owner of this rule: :func:`_add_references`
    resolves against it and ``compare/edge_query.py`` reads its ambiguous
    keys to tell "references nothing" from "reference never resolved"."""
    index: dict[str, str] = {}
    named: dict[str, set[str]] = {}
    entries: list[tuple[str, str, str]] = [
        *(
            (rec.qualified_name or rec.name, rec.name, ident.node_id)
            for rec, ident in zip(snap.types, ids.records)
        ),
        *(
            (en.qualified_name or en.name, en.name, ident.node_id)
            for en, ident in zip(snap.enums, ids.enums)
        ),
        *(
            (alias, alias.rsplit("::", 1)[-1], ids.typedefs[alias].node_id)
            for alias in snap.typedefs
        ),
    ]
    for qname, bare, node_id in entries:
        for key in {qname, bare}:
            if key:
                named.setdefault(key, set()).add(node_id)
                index.setdefault(key, node_id)
    ambiguous = {k: frozenset(v) for k, v in named.items() if len(v) > 1}
    for key in ambiguous:
        index.pop(key, None)
    return TypeSpellingIndex(index, ambiguous)


def _build_type_index(
    graph: SurfaceGraphLike,
    snap: AbiSnapshot,
    refs: ReferencedIdentifiers,
) -> dict[str, str]:
    """Register every declared record/enum/typedef as a type node, returning
    :func:`type_spelling_index`'s name -> node-id index for
    :func:`_add_references` to resolve a signature/field type string
    against."""
    ids = refs.ids

    def _register(qname: str, ident: GraphEntityIdentity, kind: str) -> None:
        node_id = ident.node_id
        graph.add_node(
            GraphNode(
                provenance=PUBLIC_SURFACE_FACTS_PRODUCER,
                id=node_id,
                kind=kind,
                label=qname or node_id,
                attrs={**_node_attrs(refs, node_id), **_identity_attrs(ident)},
            )
        )

    for rec, ident in zip(snap.types, ids.records):
        _register(rec.qualified_name or rec.name, ident, NODE_KIND_RECORD_TYPE)
    for en, ident in zip(snap.enums, ids.enums):
        _register(en.qualified_name or en.name, ident, NODE_KIND_ENUM_TYPE)
    for alias in snap.typedefs:
        _register(alias, ids.typedefs[alias], NODE_KIND_TYPEDEF)
    return type_spelling_index(snap, ids).by_spelling


def _add_linker_name_edges(
    graph: SurfaceGraphLike, decl_node_ids: dict[str, str]
) -> None:
    """``declares_linker_name`` edges (evidence class ``derived``) from a
    ``symbol`` node to its declaration, for every declaration this builder
    resolved a mangled linker name for. Deliberately not export-table-matched
    (that is `export_surface.py`'s own root-seeding logic): the edge says
    "this declaration's own linker identity is this symbol", never that the
    symbol was observed exported."""
    for mangled, decl_node_id in decl_node_ids.items():
        symbol_id = f"symbol://{mangled}"
        graph.add_node(
            GraphNode(
                provenance=PUBLIC_SURFACE_FACTS_PRODUCER,
                id=symbol_id,
                kind=NODE_KIND_SYMBOL,
                label=mangled,
            )
        )
        graph.add_edge(
            GraphEdge(
                provenance=PUBLIC_SURFACE_FACTS_PRODUCER,
                src=symbol_id,
                dst=decl_node_id,
                kind=EDGE_KIND_DECLARES_LINKER_NAME,
            )
        )


def _add_join_edges(
    graph: SurfaceGraphLike,
    join: CrossLayerJoin,
    node_kind: str,
    labels: Mapping[str, str],
) -> None:
    """One node per observed subject of *join* (its state in
    :data:`JOIN_STATE_ATTR`, orphans included) and one edge, of the join's own
    ``resolved_join`` kind, per candidate relation."""
    for subject, rec in join.right.items():
        graph.add_node(
            GraphNode(
                provenance=PUBLIC_SURFACE_FACTS_PRODUCER,
                id=subject,
                kind=node_kind,
                label=labels[subject],
                attrs={JOIN_STATE_ATTR: rec.state.value, "join_reason": rec.reason},
            )
        )
    for src, dst in join.edges():
        graph.add_edge(
            GraphEdge(
                provenance=PUBLIC_SURFACE_FACTS_PRODUCER,
                src=src,
                dst=dst,
                kind=join.spec.edge_kind,
                attrs={JOIN_STATE_ATTR: join.right[src].state.value},
            )
        )


def build_public_surface_facts(snap: AbiSnapshot, graph: SurfaceGraphLike) -> None:
    """Populate *graph* with declaration/type/header/symbol nodes and
    declares/references/declares_linker_name edges for *snap*, from L0-L2 facts alone.
    Idempotent — ``add_node``/``add_edge`` already dedup by id/relation
    key, so calling this twice on the same graph (or on a graph another
    builder already wrote into) is safe.
    """
    refs = referenced_identifiers_by_node(snap)
    ids = refs.ids
    type_index = _build_type_index(graph, snap, refs)
    decl_node_ids: dict[str, str] = {}
    exports = join_exports(snap, ids)

    def _declaration(
        ident: GraphEntityIdentity, label: str, source_header: str | None
    ) -> str:
        node_id = ident.node_id
        for alias in ident.aliases:
            register_identity_alias(graph, alias, node_id)
        graph.add_node(
            GraphNode(
                provenance=PUBLIC_SURFACE_FACTS_PRODUCER,
                id=node_id,
                kind=NODE_KIND_DECLARATION,
                label=label,
                attrs={
                    **_node_attrs(refs, node_id),
                    **_identity_attrs(ident),
                    EXPORT_JOIN_STATE_ATTR: exports.declaration(node_id).state.value,
                },
            )
        )
        _add_header_declares(graph, source_header, node_id)
        return node_id

    for fn, ident in zip(snap.functions, ids.functions):
        node_id = _declaration(ident, fn.name, fn.source_header)
        _add_references(
            graph, node_id, type_index, fn.return_type, *(p.type for p in fn.params)
        )
        if fn.mangled and ident.resolved:
            decl_node_ids[fn.mangled] = node_id

    for var, ident in zip(snap.variables, ids.variables):
        node_id = _declaration(ident, var.name, var.source_header)
        _add_references(graph, node_id, type_index, var.type)
        if var.mangled and ident.resolved:
            decl_node_ids[var.mangled] = node_id

    for rec, ident in zip(snap.types, ids.records):
        node_id = ident.node_id
        _add_header_declares(graph, rec.source_header, node_id)
        _add_references(
            graph,
            node_id,
            type_index,
            *(f.type for f in rec.fields),
            *fact_list(rec.bases_fact),
            *fact_list(rec.virtual_bases_fact),
        )

    for en, ident in zip(snap.enums, ids.enums):
        _add_header_declares(graph, en.source_header, ident.node_id)

    for alias, target in snap.typedefs.items():
        _add_references(graph, ids.typedefs[alias].node_id, type_index, target)

    _add_linker_name_edges(graph, decl_node_ids)
    _add_join_edges(
        graph,
        exports.join,
        NODE_KIND_BINARY_SYMBOL,
        {eid: e.spelling for eid, e in exports.entries.items()},
    )
    debug = join_debug_types(snap, ids)
    _add_join_edges(
        graph,
        debug.join,
        NODE_KIND_DEBUG_TYPE,
        {oid: o.name for oid, o in debug.occurrences.items()},
    )
    _add_ownership_edges(graph, ownership_relations(snap, ids))


def _add_ownership_edges(
    graph: SurfaceGraphLike, relations: OwnershipRelations
) -> None:
    """``owned_by``/``in_contract`` edges (``derived``, ADR-075 D5) from each
    classified entity to one ``owner``/``contract`` node per distinct value.
    An unclassified entity gets no edge: the graph then says "unknown", not
    "unresolved"."""

    for src, dst, kind in relations.edges():
        node_kind = (
            NODE_KIND_OWNER if kind == EDGE_KIND_OWNED_BY else NODE_KIND_CONTRACT
        )
        graph.add_node(
            GraphNode(
                provenance=PUBLIC_SURFACE_FACTS_PRODUCER,
                id=dst,
                kind=node_kind,
                label=dst.split("://", 1)[1],
            )
        )
        graph.add_edge(
            GraphEdge(
                provenance=PUBLIC_SURFACE_FACTS_PRODUCER, src=src, dst=dst, kind=kind
            )
        )
