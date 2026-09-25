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

"""The node encoding of the persisted contract type graph, and its readers.

:class:`~abicheck.contract_evidence.TypeGraphSnapshot` deliberately treats
nodes as opaque strings; this module owns what they mean. The producer is
:func:`abicheck.contract_evidence_collect.build_type_graph`; the consumers
are :mod:`abicheck.contract_context` (receipt closure, overlay roots) and
:mod:`abicheck.contract_replay` (re-evaluation). Keeping the readers here,
beside the encoding, is what lets both encodings a persisted report can
carry be answered in one place.

``TypeGraphSnapshot`` deliberately treats nodes as opaque strings, so this
module owns the encoding. Since ``contract_evidence`` schema 2 every
*canonical* node -- one per declaration or type -- is that entity's Phase 1
node id (:func:`~abicheck.model.graph_entity_identity.snapshot_identities`,
invariant I1 "one ID per entity"): ``decl://<linker name>``,
``type://<qualified name>`` (``type://<name>#typedef`` for a C typedef
beside an unrelated same-named tag), or an explicit
``unresolved://decl/...``/``unresolved://type/...`` id for an entity with no
resolvable identity (an unmangled callable, an ODR-duplicate record). The
replay graph and the L2 header/surface graphs therefore name one entity by
one id, and anything I1 keeps apart is never merged here. Two *spelling*
tiers point at those nodes:

===========  ============================================================
``name:``    the entity's own exact spelling -- the linker identity
             (``mangled`` when recorded, else ``name``, refined with a
             signature for an ambiguous unmangled overload, see
             :func:`~abicheck.contract_evidence_collect._function_node_keys`), a type's ``qualified_name`` or
             ``name``, a typedef's alias. Several entities may share one
             (an ODR pair, two identical overloads); the spelling then
             resolves to every one of them, exactly as the live index does
``alias:``   a *lookup* spelling (demangled name, bare ``::`` tail, bare
             leaf of a qualified type) that resolves to a canonical node --
             the tier an exact-matching caller does not follow
             (:func:`graph_node_index`'s ``follow_aliases``)
===========  ============================================================

and every other edge is a resolved reference between canonical nodes:
declaration -> type (signature types), record -> type (fields, bases,
virtual bases), typedef -> type (alias target), resolved at collection
time by :mod:`abicheck.contract_evidence_collect`.

Schema 1 (the encoding before this one) keyed canonical nodes
``decl:``/``record:``/``enum:``/``typedef:<spelling>`` and had no ``name:``
tier. A persisted schema-1 graph is still *read* as written -- every reader
here (:func:`graph_node_index`, :func:`graph_node_category`) recognizes both
encodings node by node, which is unambiguous because no schema-1 spelling
can begin with ``//`` -- and never *mapped* onto I1 ids: a schema-1
``decl:over`` stands for every unmangled ``over`` at once, and nothing in
the graph says which I1 ``unresolved://`` id each of them would get, so a
mapping could only guess. Re-evaluating an old graph under its own encoding
joins it with nothing, so it cannot mis-join either.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..contract_evidence import TypeGraphSnapshot


def name_node(spelling: str) -> str:
    """The exact-spelling node of *spelling* (schema 2)."""
    return f"name:{spelling}"


def alias_node(key: str) -> str:
    """The lookup-alias node of *key* (both schemas)."""
    return f"alias:{key}"


#: Canonical-node prefixes of the current (schema 2) encoding: the Phase 1
#: node-id schemes. A schema-1 canonical node never starts with one of these
#: -- its spelling follows ``decl:``/``record:``/... directly, and no linker
#: or type spelling begins with ``//``.
_I1_DECL_PREFIXES = ("decl://", "unresolved://decl/")
_I1_TYPE_PREFIXES = ("type://", "unresolved://type/")
_SCHEMA1_DECL_PREFIXES = ("decl:",)
_SCHEMA1_TYPE_PREFIXES = ("record:", "enum:", "typedef:")

#: :func:`graph_node_category`'s two answers.
NODE_CATEGORY_DECL = "decl"
NODE_CATEGORY_TYPE = "type"


def is_schema1_canonical(node: str) -> bool:
    """Whether *node* is a canonical node of the schema-1 encoding."""
    return node.startswith(
        _SCHEMA1_DECL_PREFIXES + _SCHEMA1_TYPE_PREFIXES
    ) and not node.startswith(_I1_DECL_PREFIXES + _I1_TYPE_PREFIXES)


def is_decl_node(node: str) -> bool:
    return graph_node_category(node) == NODE_CATEGORY_DECL


def graph_node_category(node: str) -> str | None:
    """``"decl"`` or ``"type"`` for a canonical node of either encoding,
    ``None`` for a spelling node (``name:``/``alias:``) or anything else.

    The one place a reader asks "is this node a declaration or a type", so
    :mod:`abicheck.contract_replay` never spells either encoding's prefixes
    itself."""
    if node.startswith(_I1_DECL_PREFIXES):
        return NODE_CATEGORY_DECL
    if node.startswith(_I1_TYPE_PREFIXES):
        return NODE_CATEGORY_TYPE
    if node.startswith(_SCHEMA1_DECL_PREFIXES):
        return NODE_CATEGORY_DECL
    if node.startswith(_SCHEMA1_TYPE_PREFIXES):
        return NODE_CATEGORY_TYPE
    return None


def resolve_graph_node(graph: TypeGraphSnapshot, spelling: str) -> set[str]:
    """Canonical nodes a *spelling* names in an already-persisted graph.

    A one-off lookup through :func:`graph_node_index` -- the entity's own
    exact spelling (``name:``, or a schema-1 canonical node's own key) and
    its lookup aliases (``alias:``) alike. A spelling naming nothing returns
    an empty set, never a guess: the caller (:mod:`abicheck.contract_replay`)
    reports that as unresolved rather than proving an entity out of a
    contract whose graph never knew it.
    """
    return set(graph_node_index(graph).get(spelling, ()))


def graph_node_index(
    graph: TypeGraphSnapshot, *, follow_aliases: bool = True
) -> dict[str, set[str]]:
    """Spelling -> canonical nodes, built once for a whole persisted graph.

    A caller resolving many spellings against one graph (a re-evaluation
    asks once per spelling per finding over a graph this module documents as
    whole-snapshot) builds this index once instead of searching per lookup.

    The exact tier is always included: a schema-2 ``name:`` node's edges,
    or -- for a schema-1 graph, which has no ``name:`` tier -- a canonical
    node's own spelling after its ``decl:``/``record:``/... prefix. Either
    encoding is recognized node by node (see this module's "Node
    encoding"); an I1 canonical node's id is never parsed for a spelling,
    since the id is normalized and may carry a discriminator the spelling
    does not.

    *follow_aliases* is what a caller whose own live matching is **exact**
    turns off. ``--post-manifest``'s committed-export allowlist is matched
    against ``Change.symbol`` verbatim by the live evaluator, so resolving
    its entries through the alias tier here would root a *different*
    declaration than the run did -- an unexported ``ns::foo`` carries the
    bare alias ``foo`` that an exported C ``foo`` also owns (Codex review,
    fresh evidence). Alias-following stays on for every other caller,
    including a finding's own spelling, where recognizing fewer encodings
    than the live lookup would silently lose roots.

    An edge is followed only when its spelling node is itself present, so a
    hand-authored or truncated graph carrying an edge whose source node was
    dropped resolves nothing through it (CodeRabbit review).
    """
    index: dict[str, set[str]] = {}
    known = set(graph.nodes)
    alias_edges: dict[str, set[str]] = {}
    for src, dst in graph.edges:
        if src not in known:
            continue
        if src.startswith("name:"):
            index.setdefault(src[len("name:") :], set()).add(dst)
        elif src.startswith("alias:"):
            alias_edges.setdefault(src[len("alias:") :], set()).add(dst)
    for node in graph.nodes:
        if is_schema1_canonical(node):
            identity = node.partition(":")[2]
            if identity:
                index.setdefault(identity, set()).add(node)
    if follow_aliases:
        for spelling, targets in alias_edges.items():
            index.setdefault(spelling, set()).update(targets)
    return index


def closure_from_graph(
    graph: TypeGraphSnapshot, roots: Iterable[str]
) -> frozenset[str]:
    """Every node reachable from *roots* over *graph*'s edges, roots included.

    The mode/root-dependent closure Section 5.1 keeps in the decision receipt
    rather than in the observed evidence -- computed here from the persisted
    graph alone, so a replay reproduces it without re-reading a binary or a
    header (Section 5.1: "no silent live-file re-probe changes a replayed
    verdict"). ``alias:`` edges are followed like any other, so a root named
    by an alias still reaches its canonical node's own closure.
    """
    adjacency: dict[str, set[str]] = {}
    for src, dst in graph.edges:
        adjacency.setdefault(src, set()).add(dst)
    known = set(graph.nodes)
    seen: set[str] = set()
    stack = [r for r in roots if r in known]
    empty: set[str] = set()
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(adjacency.get(node, empty) - seen)
    return frozenset(seen)
