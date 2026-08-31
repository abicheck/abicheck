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

"""Virtual-dispatch graph augmentation (G29 Phase 5 item 3, G29.6's third
open graph family: :data:`~abicheck.buildsource.graph_facts.
VIRTUAL_DISPATCH_NODE_KINDS`/:data:`~abicheck.buildsource.graph_facts.
VIRTUAL_DISPATCH_EDGE_KINDS`).

Unlike every sibling module in this family (``override_graph.py``,
``template_graph.py``, ``macro_graph.py``), this module runs **no new clang
invocation at all** — both parts below are pure graph-transformation
functions over a :class:`~abicheck.buildsource.source_graph.
SourceGraphSummary` that ``call_graph.py``/``type_graph.py``/
``override_graph.py`` already folded. They read already-collected facts and
write more graph; nothing here shells out.

**Part A — :func:`augment_graph_with_virtual_call_targets`
(``VIRTUAL_CALL_MAY_DISPATCH_TO``).** A virtual call's ``DECL_CALLS_DECL``
edge (``call_graph.py``) already carries ``attrs["call_kind"] == "virtual"``
and points at the statically-resolved, slot-owning base method — the one
declaration a compiler sees at the call site, not any particular override.
``override_graph.py``'s ``METHOD_POSSIBLE_OVERRIDE`` edges already map that
same base method to every override candidate found in the codebase. This
function materializes the join: for every such virtual call edge (caller ->
base method) and every ``METHOD_POSSIBLE_OVERRIDE`` edge whose ``dst`` is
that same base method (``src`` = an override candidate), it emits
``VIRTUAL_CALL_MAY_DISPATCH_TO`` from the *original calling* declaration to
the override candidate — enriching the call graph's already-known,
statically-resolved target with the genuine runtime alternatives a real
dispatch could reach. ``resolution`` is always ``"overapprox"`` (never
``"exact"`` — mirroring ``call_graph.RESOLUTION_OVERAPPROX``, and the
explicit instruction in this family's own design brief: a possible-target-set
change must never read as a confirmed break) and confidence is
``CONF_REDUCED``, matching the epistemic status the underlying virtual call
already carries: this is a static approximation of a runtime decision, not a
new, stronger claim about it.

The base method's own slot is deliberately **not** re-emitted as a
``VIRTUAL_CALL_MAY_DISPATCH_TO`` target: it is already the ``DECL_CALLS_DECL``
edge's own ``dst``, so restating it here would be a redundant self-fact, not
new information — this edge exists purely to *add* the override candidates a
plain call-graph reader could not already see. A virtual call to a base
method with **no** override candidates at all (a leaf virtual method) emits
**no** ``VIRTUAL_CALL_MAY_DISPATCH_TO`` edge, not a spurious self-edge to the
base method: there is no dispatch ambiguity to represent when the call graph
already names the only possible target directly.

**Part B — :func:`augment_graph_with_vtable_presence`
(``TYPE_HAS_VTABLE``).** Per the Itanium C++ ABI, a class is polymorphic
(has a vtable) iff it declares or inherits at least one virtual function.
This function derives that fact from three already-collected sources: the
class hierarchy (``TYPE_INHERITS`` edges, from ``type_graph.py``), *which*
classes own a method appearing in a ``METHOD_POSSIBLE_OVERRIDE`` edge
(either endpoint — an overriding method or an overridden base method is, by
definition, virtual), and — a second, independent seed (Codex review, fresh
evidence) — *which* classes own a method ``override_graph.py`` stamped
``is_virtual: True`` directly onto its own ``source_decl`` node
(``augment_graph_with_overrides``'s ``virtual_methods`` parameter). The
second seed exists because the first one alone is blind to arguably the
single most common polymorphic-class shape: a virtual method with no
override anywhere in the *scanned* codebase (a base class's own leaf virtual
method, or ANY method on a class with no base at all) never appears as
either endpoint of a ``METHOD_POSSIBLE_OVERRIDE`` edge, so it was previously
never seeded as polymorphic at all — nor was anything inheriting from it.
Both seeds need an owning-class join ``override_graph.py``'s own folded
edges/facts don't carry directly (they name methods, not owner types) —
solved by decoding the method's own Itanium/MSVC mangled identity via
:func:`~abicheck.model.mangled_name.itanium_scope_components`/
:func:`~abicheck.model.mangled_name.msvc_scope_components` (the same
structural, no-external-demangler decoders ``diff_cxx_rules.owner_class_of``
already uses elsewhere in this codebase for the identical "which class owns
this mangled symbol" question) and dropping the method's own leaf component. This is
still a pure string transform over an already-mangled identity, not a new
compiler pass.

**A deliberately conservative join, not a best-effort one.** The decoded
owner is only trusted when it matches a ``record_type`` node's identity
**exactly** — never as a fuzzy/bare-suffix match. This differs from
``type_reachability.py``'s own namespace-suffix matching (which accepts a
partially-qualified spelling) because the failure mode here is asymmetric:
for a class-template specialization, ``itanium_scope_components`` keeps the
*raw* Itanium template-argument encoding (documented in its own docstring),
not the spelled form ``type_graph.py``'s record identities use — so an exact
match never fires for a templated owner, a silent, conservative false
negative (a template-instantiated polymorphic class may go undetected here),
never a wrong ``TYPE_HAS_VTABLE`` claim on an unrelated same-named type. For
every *non*-template class (the common case) the raw Itanium/MSVC encoding
and the spelled qualified name coincide exactly, since a plain identifier
mangles to itself length-prefixed — so this exact-match join is fully
precise there. A real fix (resolving a template specialization's raw
encoding back to its spelled record identity) needs the same demangling
investment ``type_reachability.py``'s own "fifth finding" already flagged
and deliberately deferred for the identical reason; not reattempted here.

Once a class is known to directly own a virtual slot, ``TYPE_HAS_VTABLE`` is
closed transitively over ``TYPE_INHERITS``: any class inheriting (directly or
transitively) from a polymorphic base is itself polymorphic, per the same
ABI rule, even if it declares no virtual method of its own. Both directly-
and transitively-polymorphic classes get exactly one ``vtable`` node
(``vtable://<record-type-identity>``) and one ``TYPE_HAS_VTABLE`` edge from
their own ``record_type`` node — this is the one new **node** kind this
family introduces (items 1/2/6 all reused pre-existing node kinds): a
``vtable`` node is minted at most once per genuinely polymorphic class, so it
is bounded by the number of polymorphic classes in the codebase, nothing
like the thousands-of-symbols scale ``archive_graph.py``'s join-only
discipline guards against. Confidence is ``CONF_HIGH``: once a class is
known to own or inherit a virtual slot, "this class has a vtable" is a
structural, provable fact, not a guess.

**``DECL_OVERRIDES_DECL`` and ``VTABLE_SLOT_MAPS_TO_DECL`` are deliberately
not populated here** — see :data:`~abicheck.buildsource.graph_facts.
VIRTUAL_DISPATCH_EDGE_KINDS`'s own docstring for why: the former is already
satisfied by ``override_graph.py``'s existing ``METHOD_POSSIBLE_OVERRIDE``
(``resolution == "override_confirmed"``) edges, and the latter needs a real,
verified per-slot Itanium vtable layout model this slice deliberately does
not attempt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..model.mangled_name import itanium_scope_components, msvc_scope_components
from .graph_facts import CONF_HIGH, CONF_REDUCED, GraphEdge, GraphNode

if TYPE_CHECKING:
    from .source_graph import SourceGraphSummary

EDGE_VIRTUAL_CALL_MAY_DISPATCH_TO = "VIRTUAL_CALL_MAY_DISPATCH_TO"
EDGE_TYPE_HAS_VTABLE = "TYPE_HAS_VTABLE"
NODE_VTABLE = "vtable"

RESOLUTION_OVERAPPROX = "overapprox"


# ── Part A: VIRTUAL_CALL_MAY_DISPATCH_TO ────────────────────────────────────


@dataclass
class VirtualDispatchResult:
    """What one :func:`augment_graph_with_virtual_call_targets` pass did, for
    the caller's own extractor-row/coverage bookkeeping."""

    virtual_call_edges_seen: int = 0
    dispatch_edges_added: int = 0


def _all_reachable_overrides(
    base_id: str, overrides_of: dict[str, list[tuple[str, str]]]
) -> list[tuple[str, str]]:
    """Every override candidate transitively reachable from *base_id* over
    ``overrides_of`` (a one-hop ``base -> [(override, resolution), ...]``
    map, ``override_graph.py``'s own chain-not-flattened edge shape) — not
    just the direct, one-hop candidates (Codex review, fresh evidence: see
    the caller's own comment for why a multi-level override chain needs
    this). A plain BFS/DFS over the chain, deduplicated and cycle-safe via
    *visited* (a real class hierarchy is a DAG, but this makes no such
    assumption); each candidate keeps its own edge's resolution label.
    """
    reached: list[tuple[str, str]] = []
    visited: set[str] = {base_id}
    stack = [base_id]
    while stack:
        current = stack.pop()
        for candidate_id, resolution in overrides_of.get(current, []):
            if candidate_id in visited:
                continue
            visited.add(candidate_id)
            reached.append((candidate_id, resolution))
            stack.append(candidate_id)
    return reached


def augment_graph_with_virtual_call_targets(
    graph: SourceGraphSummary,
) -> VirtualDispatchResult:
    """Fold ``VIRTUAL_CALL_MAY_DISPATCH_TO`` edges into *graph* (Part A — see
    the module docstring for the full join and its "no self-edge for a leaf
    virtual method" rule). Pure graph transformation: reads
    ``DECL_CALLS_DECL``/``METHOD_POSSIBLE_OVERRIDE`` edges *graph already
    carries*, mints no new node (both endpoints are always pre-existing
    ``source_decl`` nodes call/override graph folding already created).
    """
    result = VirtualDispatchResult()

    # base method decl id -> list of (override candidate decl id, override
    # edge's own resolution label) -- built once, reused per virtual call.
    overrides_of: dict[str, list[tuple[str, str]]] = {}
    for e in graph.edges:
        if e.kind == "METHOD_POSSIBLE_OVERRIDE":
            overrides_of.setdefault(e.dst, []).append(
                (e.src, str(e.resolved.get("resolution", "")))
            )

    # Snapshot before folding (Codex review, fresh evidence): this loop
    # calls graph.add_edge() below, and every edge it adds is itself kind
    # VIRTUAL_CALL_MAY_DISPATCH_TO -- always rejected by the
    # "!= DECL_CALLS_DECL" guard above, so iterating the live list is safe
    # today, but a future DECL_CALLS_DECL-kind emitter elsewhere would feed
    # this loop its own output.
    for e in list(graph.edges):
        if e.kind != "DECL_CALLS_DECL":
            continue
        if e.resolved.get("call_kind") != "virtual":
            continue
        result.virtual_call_edges_seen += 1
        # Transitive closure over METHOD_POSSIBLE_OVERRIDE, not just the
        # one-hop candidates of the call's own static target (Codex review,
        # fresh evidence): `override_graph.py` records each override
        # against its *nearest* declaring ancestor only (chains, never
        # flattened -- its own `OverrideEdge` docstring), so for a
        # multi-level chain `Base::f <- Mid::f <- Derived::f`, a virtual
        # call statically resolved to `Base::f` previously reached only
        # `Mid::f` -- `Derived::f` is an equally real runtime target (a
        # `Base*`/`Base&` can be bound to a `Derived` object) but was never
        # surfaced. `_all_reachable_overrides` walks every level, each
        # candidate keeping its OWN edge's resolution label (not the base
        # call's), and a `visited` set both dedupes a diamond-shaped
        # hierarchy and guarantees termination.
        candidates = _all_reachable_overrides(e.dst, overrides_of)
        if not candidates:
            continue  # leaf virtual method: no ambiguity to represent
        for candidate_id, override_resolution in candidates:
            before = len(graph.edges)
            graph.add_edge(
                GraphEdge(
                    src=e.src,
                    dst=candidate_id,
                    kind=EDGE_VIRTUAL_CALL_MAY_DISPATCH_TO,
                    provenance="virtual_dispatch_graph",
                    confidence=CONF_REDUCED,
                    attrs={
                        "resolution": RESOLUTION_OVERAPPROX,
                        "base_method": e.dst,
                        "override_resolution": override_resolution,
                    },
                )
            )
            result.dispatch_edges_added += len(graph.edges) - before

    return result


# ── Part B: TYPE_HAS_VTABLE ─────────────────────────────────────────────────


@dataclass
class VtablePresenceResult:
    """What one :func:`augment_graph_with_vtable_presence` pass did."""

    polymorphic_types: int = 0
    vtable_edges_added: int = 0


def _record_identity(node_id: str) -> str | None:
    """The bare identity a ``record_type`` node id encodes, or ``None`` for
    any other node kind's id shape (``_type_node_id``'s inverse)."""
    prefix = "type://"
    return node_id[len(prefix) :] if node_id.startswith(prefix) else None


def _owner_identity(method_identity: str) -> str | None:
    """The scope-qualified owner class of a mangled method identity, via the
    same structural Itanium/MSVC decoders ``diff_cxx_rules.owner_class_of``
    already uses (see the module docstring's "deliberately conservative
    join" section for why an exact match, not a fuzzy one, is required of
    the caller). Returns ``None`` for a free function (no owning scope) or an
    identity this module cannot decode at all (e.g. the
    ``qualified_name#sha256:...`` fallback shape ``function_decl_identity``
    produces when a method carries no *real* Itanium/MSVC mangling -- rare
    for a genuine C++ virtual method, whose mangled name is essentially
    always distinct from its bare name).
    """
    comps = itanium_scope_components(method_identity) or msvc_scope_components(
        method_identity
    )
    if not comps or len(comps) < 2:
        return None
    return "::".join(comps[:-1])


def augment_graph_with_vtable_presence(
    graph: SourceGraphSummary,
) -> VtablePresenceResult:
    """Fold ``TYPE_HAS_VTABLE`` edges (and their ``vtable`` nodes) into
    *graph* (Part B — see the module docstring for the full derivation).
    Pure graph transformation over already-folded ``TYPE_INHERITS``/
    ``METHOD_POSSIBLE_OVERRIDE`` edges and ``is_virtual``-tagged
    ``source_decl`` node facts (the second, leaf-virtual-method seed —
    see the module docstring); mints at most one ``vtable`` node per
    genuinely polymorphic ``record_type`` node already in *graph*.
    """
    result = VtablePresenceResult()

    record_ids = {n.id for n in graph.nodes if n.kind == "record_type"}
    record_identities = {
        ident: node_id
        for node_id in record_ids
        for ident in [_record_identity(node_id)]
        if ident is not None
    }

    bases_of: dict[str, list[str]] = {}
    for e in graph.edges:
        if e.kind == "TYPE_INHERITS" and e.src in record_ids and e.dst in record_ids:
            src_ident, dst_ident = _record_identity(e.src), _record_identity(e.dst)
            if src_ident is not None and dst_ident is not None:
                bases_of.setdefault(src_ident, []).append(dst_ident)

    # Seed: every record identity directly owning a method that appears as
    # either endpoint of a METHOD_POSSIBLE_OVERRIDE edge -- both an
    # overriding method and the base method it overrides are, by
    # definition, virtual.
    polymorphic: set[str] = set()
    decl_prefix = "decl://"
    for e in graph.edges:
        if e.kind != "METHOD_POSSIBLE_OVERRIDE":
            continue
        for method_id in (e.src, e.dst):
            if not method_id.startswith(decl_prefix):
                continue
            owner = _owner_identity(method_id[len(decl_prefix) :])
            if owner is not None and owner in record_identities:
                polymorphic.add(owner)

    # Second seed: every source_decl node override_graph.py stamped
    # `is_virtual: True` on directly (`augment_graph_with_overrides`'s
    # `virtual_methods` parameter) -- independent of any
    # METHOD_POSSIBLE_OVERRIDE edge (Codex review, fresh evidence: the
    # override-edge-only seed above leaves a class with no override
    # anywhere in the scanned codebase non-polymorphic, even though a
    # base class with a leaf virtual method -- arguably the single most
    # common polymorphic-class shape -- is exactly this case). Reads a
    # plain node fact, mints nothing.
    for n in graph.nodes:
        if n.kind != "source_decl" or not n.id.startswith(decl_prefix):
            continue
        if not n.resolved.get("is_virtual"):
            continue
        owner = _owner_identity(n.id[len(decl_prefix) :])
        if owner is not None and owner in record_identities:
            polymorphic.add(owner)

    # Third seed: a class's own virtual destructor (Codex review, fresh
    # evidence) -- a class whose *only* virtual member is its destructor
    # (a common, real shape, e.g. a plugin/interface base class) is invisible
    # to both seeds above: the override-edge seed can't see a destructor at
    # all (override_graph.py deliberately excludes CXXDestructorDecl from
    # override-EDGE matching, its own Itanium D1/D2 dual-mangling concern —
    # see that module's docstring), and the leaf-virtual-method seed reads
    # `is_virtual`-tagged decl:// nodes, which a bare, uncalled destructor
    # declaration typically has none of. override_graph.py's own
    # parse_clang_ast_virtual_destructor_owners stamps this fact directly
    # onto the owning class's own record_type node instead (see that
    # function's docstring), which is what this seed reads. Same
    # join-only-onto-an-existing-node discipline as the other two seeds:
    # a class isolated enough that NOTHING else in the graph (no
    # TYPE_INHERITS edge, no override edge) already minted its bare
    # record_type node has nothing here to stamp the fact onto either
    # (verified empirically: type_graph.py's own CXXRecordDecl walk mints
    # only the class's own injected-class-name identity, e.g.
    # `record::Base::Base`, not the bare `Base` this seed's owner
    # identities use, unless something else independently triggers the
    # bare node's creation) -- an accepted, pre-existing limitation shared
    # by the other two seeds, not new to this one.
    for n in graph.nodes:
        if n.kind != "record_type" or n.id not in record_ids:
            continue
        if not n.resolved.get("has_virtual_destructor"):
            continue
        ident = _record_identity(n.id)
        if ident is not None:
            polymorphic.add(ident)

    # Transitive closure over TYPE_INHERITS: inheriting from a polymorphic
    # base makes the derived class polymorphic too, per the Itanium ABI rule
    # -- fixed-point iteration since bases_of may chain several levels deep.
    changed = True
    while changed:
        changed = False
        for derived, bases in bases_of.items():
            if derived in polymorphic:
                continue
            if any(b in polymorphic for b in bases):
                polymorphic.add(derived)
                changed = True

    from .source_graph import _vtable_node_id

    for ident in sorted(polymorphic):
        record_node_id = record_identities.get(ident)
        if record_node_id is None:
            continue
        result.polymorphic_types += 1
        vtable_id = _vtable_node_id(ident)
        if not graph.has_node(vtable_id):
            graph.add_node(
                GraphNode(
                    id=vtable_id,
                    kind=NODE_VTABLE,
                    label=ident,
                    provenance="virtual_dispatch_graph",
                    confidence=CONF_HIGH,
                )
            )
        before = len(graph.edges)
        graph.add_edge(
            GraphEdge(
                src=record_node_id,
                dst=vtable_id,
                kind=EDGE_TYPE_HAS_VTABLE,
                provenance="virtual_dispatch_graph",
                confidence=CONF_HIGH,
            )
        )
        result.vtable_edges_added += len(graph.edges) - before

    return result


def augment_graph_with_virtual_dispatch(
    graph: SourceGraphSummary,
) -> tuple[VirtualDispatchResult, VtablePresenceResult]:
    """Run both Part A and Part B over *graph* in one call (the shape
    :mod:`inline_graph_fold`'s ``fold_virtual_dispatch_graph`` uses)."""
    return (
        augment_graph_with_virtual_call_targets(graph),
        augment_graph_with_vtable_presence(graph),
    )
