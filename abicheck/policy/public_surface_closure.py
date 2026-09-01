# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors
"""The actual closure-walk half of ADR-063 Phase 3 D5's public-surface
traversal, plus its real entry point, :func:`resolve_public_surface` --
split out of ``policy/public_surface.py`` (which keeps the ``PublicSurface``
dataclass and the indexing/bookkeeping half) purely to keep each file under
this repo's 800-line new-file production cap (mechanical extraction, not a
redesign; mirrors the ``fact_detector_misuse.py``/``fact_detector_misuse_
aliases.py`` split ``scripts/CLAUDE.md`` documents). See that sibling
module's own docstring for the fuller accounting of the migration.

Everything here is still a **leaf module**: imports nothing from
``surface.py``/``export_surface.py``, so both of them (and
``policy/public_surface_query.py``, the ``PublicSurfaceQuery`` orchestrator
built on top of this module) can depend on it without a cycle.
``export_surface.py`` imports :func:`_walk_type_closure`/
:func:`resolve_surface_graph_nodes` directly -- its own type-closure step
reuses the former verbatim (see that module's own docstring), and both
domains resolve their graph through the latter rather than each backfilling
their own.

:func:`resolve_public_surface` is a **real traversal** over
``AbiSnapshot.surface_graph`` — the unified evidence graph
``compare/surface_graph.py`` builds unconditionally from L0-L2 facts — not a
delegation to ``surface.py``'s old, independent closure-walk implementation.
That implementation (``_seed_public_roots``/``_walk_type_closure``/
``_walk_exact_type_closure`` and their siblings) moved here, adapted to
resolve *what a declaration/type/typedef references* by reading the
graph's precomputed ``referenced_identifiers`` node attrs (populated once,
at graph-build time, from the exact same L0-L2 fields — see
``compare/surface_graph.py``'s own ``_referenced_identifiers_by_node``)
rather than independently re-parsing ``fn.return_type``/``rec.fields``/
``rec.bases``/typedef targets a second time via its own regex scan.

**A node-id collision is not automatically safe to union, and this module
does not blindly trust the graph's cached value when one occurred.** A
first version of this migration assumed unioning a colliding node's
contributors was always the conservative, safe direction (the same
over-keep principle that already governs an ambiguous bare *type* name) --
that assumption is wrong for a *declaration* collision specifically, and a
regression test catches exactly why: a public, no-argument overload and a
hidden overload taking a private-type parameter, sharing one demangled name
with no mangled name and no resolved ``entity_id`` to tell them apart,
collapse onto the same approximate node id. Unioning their referenced
identifiers would make the *public* overload appear to reference the
*hidden* overload's own private parameter type -- attributing a private
overload's reach to a public root, not merely over-approximating a single
root's own reach. ``compare/surface_graph.py``'s builder flags such a node
(``identifiers_collision``), and this module's own
``_referenced_identifiers_for_function``/``_referenced_identifiers_for_variable``/
``_referenced_identifiers_for_record`` fall back to recomputing that *one*
declaration's own identifiers directly (exactly what the pre-migration
implementation always did, unconditionally) rather than trust a value that
may be blurred across an unrelated sibling. See
:func:`_node_identifiers_or_collision`'s own docstring. This is the one
documented case where "traverse the graph" yields to "the graph flagged
this id as unsafe to trust here" -- a named, tested residual, not a silent
gap.

``export_surface.py``'s own export-table-matching *root-seeding* (the
``contract=exports`` domain) is **not** migrated in this slice — its own
proven, independently-reviewed root-seeding logic (matching declarations
against the observed ELF/PE/Mach-O export table) stays exactly as it is,
unchanged; only its final type-closure step became graph-native, by sharing
:func:`_walk_type_closure` with this module. See
``docs/contribute/plans/one-semantic-pipeline.md``'s Phase 3 section for the
full accounting of what remains open.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..compare.surface_graph import (
    build_public_surface_facts,
    fact_list,
    node_id_for_declaration,
    node_id_for_type,
    node_id_for_typedef,
)
from ..diff_cxx_rules import owner_class_of
from ..model.source_graph import SourceGraphSummary
from ..model.vocabulary import ScopeOrigin, Visibility
from .public_surface import (
    _DEMOTE_ORIGINS,
    PublicSurface,
    _index_surface_types,
    _is_real_type,
    _record_origin,
    _symbol_keys,
    _type_identifiers,
)

if TYPE_CHECKING:
    from ..model.declarations import Function, Variable
    from ..model.entities import EnumType, RecordType
    from ..model.graph_facts import GraphNode, SurfaceGraphLike
    from ..model.snapshot import AbiSnapshot

__all__ = [
    "resolve_public_surface",
    "resolve_surface_graph_nodes",
]


def _referenced_identifiers(
    graph_nodes_by_id: dict[str, GraphNode], node_id: str
) -> set[str]:
    """The precomputed union of type-identifier strings that the
    declaration/type/typedef at *node_id* references, as cached on its graph
    node by ``compare.surface_graph``'s builder — the actual "traverse the
    graph" step this module's migration is about. Returns an empty set (not
    an error) for a node id genuinely absent from the graph -- shouldn't
    happen for a graph ``build_public_surface_facts`` itself produced (every
    function/variable/record/enum/typedef gets a node), but a missing node
    is strictly the *conservative* direction here (fewer identifiers to
    expand, never a fabricated reference) so this stays a lookup, not an
    assertion.

    **Only safe when the caller does not need per-declaration precision.**
    Use this directly for a typedef alias (``snap.typedefs`` is keyed
    uniquely by the alias string itself, so its node id can never collide
    between two distinct entries). For a function/variable/record, use
    :func:`_referenced_identifiers_for_function`/
    :func:`_referenced_identifiers_for_variable`/
    :func:`_referenced_identifiers_for_record` instead — see their own
    docstrings, and ``compare.surface_graph``'s
    ``_referenced_identifiers_by_node`` docstring, for why a bare lookup by
    node id is unsound for those three kinds specifically.
    """
    node = graph_nodes_by_id.get(node_id)
    if node is None:
        return set()
    return set(node.attrs.get("referenced_identifiers", ()))


def _node_identifiers_or_collision(
    graph_nodes_by_id: dict[str, GraphNode], node_id: str
) -> set[str] | None:
    """The graph's cached ``referenced_identifiers`` for *node_id*, or
    ``None`` when that node is flagged ``identifiers_collision`` (more than
    one distinct declaration/type mapped onto this approximate id -- see
    ``compare.surface_graph``'s own docstring) or missing entirely. ``None``
    tells the caller its own per-object fallback must run instead of
    trusting a value that may be blurred across an unrelated sibling.
    """
    node = graph_nodes_by_id.get(node_id)
    if node is None or node.attrs.get("identifiers_collision", False):
        return None
    return set(node.attrs.get("referenced_identifiers", ()))


def _referenced_identifiers_for_function(
    graph_nodes_by_id: dict[str, GraphNode], node_id: str, fn: Function
) -> set[str]:
    """*fn*'s own referenced type identifiers -- the graph's cached value
    when safe, else recomputed directly from *fn*'s own return/param types.

    The fallback exists for exactly the case the graph's own
    ``identifiers_collision`` flag reports: two declarations sharing one
    approximate node id (no resolved ``entity_id`` for either, e.g. two
    overloads with no mangled name to disambiguate them) are not the same
    declaration, and a public, narrow-signature overload must not appear to
    reference whatever a same-node hidden sibling's own (possibly private)
    parameter type happens to be. Recomputing *this* declaration's own
    identifiers directly is exactly what the pre-migration implementation
    always did, unconditionally -- this is the safety net for the one case
    the graph's shared-node model cannot represent precisely, not a routine
    path.
    """
    cached = _node_identifiers_or_collision(graph_nodes_by_id, node_id)
    if cached is not None:
        return cached
    idents = _type_identifiers(fn.return_type)
    for p in fn.params:
        idents |= _type_identifiers(getattr(p, "type", None))
    return idents


def _referenced_identifiers_for_variable(
    graph_nodes_by_id: dict[str, GraphNode], node_id: str, var: Variable
) -> set[str]:
    """*var*'s own referenced type identifiers -- see
    :func:`_referenced_identifiers_for_function`'s docstring for the same
    collision-fallback rationale, applied to a variable's own type."""
    cached = _node_identifiers_or_collision(graph_nodes_by_id, node_id)
    if cached is not None:
        return cached
    return _type_identifiers(var.type)


def _referenced_identifiers_for_record(
    graph_nodes_by_id: dict[str, GraphNode], node_id: str, rec: RecordType
) -> set[str]:
    """*rec*'s own referenced type identifiers (fields, bases, virtual
    bases) -- see :func:`_referenced_identifiers_for_function`'s docstring
    for the same collision-fallback rationale, applied to a record sharing
    its approximate node id with a distinct sibling record/enum/typedef."""
    cached = _node_identifiers_or_collision(graph_nodes_by_id, node_id)
    if cached is not None:
        return cached
    idents: set[str] = set()
    for f in rec.fields:
        idents |= _type_identifiers(f.type)
    for base in (*fact_list(rec.bases_fact), *fact_list(rec.virtual_bases_fact)):
        idents |= _type_identifiers(base)
    return idents


def _seed_public_roots(
    snap: AbiSnapshot,
    surface: PublicSurface,
    graph_nodes_by_id: dict[str, GraphNode],
) -> tuple[set[str], bool]:
    """Record public symbols on *surface*; return (seed type names, has_public).

    Seeds the type-closure work-list from the return/parameter/variable types of
    every :data:`Visibility.PUBLIC` function and variable -- read from the
    evidence graph's own precomputed ``referenced_identifiers`` rather than
    re-parsing ``fn.return_type``/``p.type``/``var.type`` here a second time
    (falling back to a direct, per-declaration recomputation on a detected
    graph node-id collision -- see :func:`_referenced_identifiers_for_function`).
    """
    seed_types: set[str] = set()
    has_public = False
    for fn in snap.functions:
        keys = _symbol_keys(fn.name, fn.mangled)
        surface.all_symbols |= keys
        _record_origin(surface, keys, getattr(fn, "origin", ScopeOrigin.UNKNOWN))
        if fn.visibility == Visibility.PUBLIC:
            has_public = True
            surface.public_symbols |= keys
            if fn.params or _is_real_type(fn.return_type):
                surface.has_typed_roots = True
            seed_types |= _referenced_identifiers_for_function(
                graph_nodes_by_id, node_id_for_declaration(fn.entity_id, fn.name), fn
            )
            # A public *method* makes its enclosing class directly public even
            # when the method's own signature carries no class-typed return/
            # param (e.g. `void process();`) — the class is exported and
            # consumers can declare/allocate/inherit it by value, so its own
            # layout and base-class changes must not be scoped out as
            # "non-public-type" just because no *other* signature happens to
            # reference it. A plain string derived from mangled-name
            # demangling, not part of the declaration's own signature -- no
            # graph entry to read instead.
            owner = owner_class_of(fn)
            if owner:
                seed_types |= _type_identifiers(owner)
    for var in snap.variables:
        keys = _symbol_keys(var.name, var.mangled)
        surface.all_symbols |= keys
        _record_origin(surface, keys, getattr(var, "origin", ScopeOrigin.UNKNOWN))
        if var.visibility == Visibility.PUBLIC:
            has_public = True
            surface.public_symbols |= keys
            if _is_real_type(var.type):
                surface.has_typed_roots = True
            seed_types |= _referenced_identifiers_for_variable(
                graph_nodes_by_id, node_id_for_declaration(var.entity_id, var.name), var
            )
    return seed_types, has_public


def _walk_type_closure(
    graph_nodes_by_id: dict[str, GraphNode],
    snap: AbiSnapshot,
    surface: PublicSurface,
    record_by_name: dict[str, list[RecordType]],
    enum_by_name: dict[str, list[EnumType]],
    seed_types: set[str],
) -> None:
    """Transitive closure over the record/typedef graph; fills public_types.

    Follows typedef targets, record fields, and base classes from each seed
    type, marking every reachable known type as part of the public surface —
    read via each node's precomputed ``referenced_identifiers`` graph attr,
    not by re-parsing ``rec_node.fields``/``.bases``/``.virtual_bases``/the
    typedef target string directly. A name may resolve to *several* types
    (an ambiguous ``::`` tail shared by two namespaces); every match is
    marked public and walked -- the anti-hiding, never-lose-a-real-dependency
    direction. ``exact_type_identities`` is deliberately NOT filled here: see
    :func:`_walk_exact_type_closure`'s docstring for why an ambiguity-tolerant
    walk cannot also answer "was this reached without ever passing through an
    ambiguous fork".
    """
    queue = list(seed_types)
    seen: set[str] = set()
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        if name in surface.all_types:
            surface.public_types.add(name)
        # Follow typedef targets.
        target = snap.typedefs.get(name)
        if target:
            surface.public_typedefs.add(name)
            for ident in _referenced_identifiers(
                graph_nodes_by_id, node_id_for_typedef(name)
            ):
                if ident not in seen:
                    queue.append(ident)
        # A short/qualified enum alias (``Mode``) reached from a public signature
        # or field resolves here to its canonical namespaced name (``ns::Mode``),
        # so a scoped enum-member finding is not hidden (mirrors the record alias
        # handling below). Enums have no fields or bases, so nothing is queued.
        # An ambiguous tail may match enums in several namespaces — mark them all.
        en_nodes = enum_by_name.get(name, ())
        rec_nodes = record_by_name.get(name, [])
        for en_node in en_nodes:
            surface.public_types.add(en_node.name)
            if en_node.qualified_name:
                surface.public_types.add(en_node.qualified_name)
        if not rec_nodes:
            continue
        # A short alias (``A``) reached inside its namespace resolves here to the
        # namespaced record (``ns::A``); record the *canonical* full name as
        # public so callers that count/scope by ``RecordType.name`` see it.
        # ``rec_node.name`` is always in ``all_types``. An ambiguous tail
        # shared by two namespaces resolves to several records — walk each.
        for rec_node in rec_nodes:
            surface.public_types.add(rec_node.name)
            if rec_node.qualified_name:
                surface.public_types.add(rec_node.qualified_name)
            rec_node_id = node_id_for_type(
                rec_node.entity_id, rec_node.qualified_name or rec_node.name
            )
            for ident in _referenced_identifiers_for_record(
                graph_nodes_by_id, rec_node_id, rec_node
            ):
                if ident not in seen:
                    queue.append(ident)


def _mark_identity_forms_if_unambiguous(
    surface: PublicSurface,
    node: RecordType | EnumType,
    record_by_name: dict[str, list[RecordType]],
    enum_by_name: dict[str, list[EnumType]],
) -> None:
    """Adds *node*'s bare ``.name`` and (if present) its ``.qualified_name``
    to ``exact_type_identities`` -- but only each form that is *itself*
    independently unambiguous (unchanged from ``surface.py``'s original --
    see its own git history for the full per-form rationale)."""
    if _combined_match_count(node.name, record_by_name, enum_by_name) == 1:
        surface.exact_type_identities.add(node.name)
    if node.qualified_name and (
        _combined_match_count(node.qualified_name, record_by_name, enum_by_name) == 1
    ):
        surface.exact_type_identities.add(node.qualified_name)


def _combined_match_count(
    key: str,
    record_by_name: dict[str, list[RecordType]],
    enum_by_name: dict[str, list[EnumType]],
) -> int:
    """Records plus enums matching *key* -- the same cross-kind combination
    ``_index_surface_types`` uses to compute ``ambiguous_type_names``."""
    return len(record_by_name.get(key, ())) + len(enum_by_name.get(key, ()))


def _walk_exact_type_closure(
    graph_nodes_by_id: dict[str, GraphNode],
    snap: AbiSnapshot,
    surface: PublicSurface,
    record_by_name: dict[str, list[RecordType]],
    enum_by_name: dict[str, list[EnumType]],
    seed_types: set[str],
) -> None:
    """Fills ``surface.exact_type_identities``: every record/enum (and
    typedef alias name) reachable from a seed via a chain where *every* step
    resolved to precisely one candidate -- never through an ambiguous
    ``::``-tail fork.

    A separate walk from :func:`_walk_type_closure`, not a flag threaded
    through it, because the two questions need opposite behavior at an
    ambiguous fork -- see ``surface.py``'s original docstring (preserved in
    this migration's git history) for the full rationale, including the bug
    an earlier, single-walk version had.

    Safe to dedupe visited spellings permanently (unlike a general fixpoint
    over a monotonically-increasing confidence lattice): a name is only ever
    enqueued here *after* its parent already resolved exactly, so by
    construction nothing reachable through an ambiguous fork is ever queued
    in the first place.
    """
    queue = list(seed_types)
    seen: set[str] = set()
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        target = snap.typedefs.get(name)
        if target:
            # A typedef alias is a 1:1 mapping -- there is no ambiguity
            # concept for it the way a bare record/enum tail can collide, so
            # reaching *this* name at all (which, by this walk's own
            # invariant, only ever happens via an already-all-exact chain)
            # makes the alias name itself exact too.
            surface.exact_type_identities.add(name)
            for ident in _referenced_identifiers(
                graph_nodes_by_id, node_id_for_typedef(name)
            ):
                if ident not in seen:
                    queue.append(ident)
        en_nodes = enum_by_name.get(name, ())
        rec_nodes = record_by_name.get(name, [])
        if len(en_nodes) + len(rec_nodes) != 1:
            # Absent, or ambiguous: stop here either way -- do not expand,
            # do not mark exact.
            continue
        if en_nodes:
            en_node = en_nodes[0]
            _mark_identity_forms_if_unambiguous(
                surface, en_node, record_by_name, enum_by_name
            )
            continue  # enums have no fields/bases to expand.
        rec_node = rec_nodes[0]
        _mark_identity_forms_if_unambiguous(
            surface, rec_node, record_by_name, enum_by_name
        )
        rec_node_id = node_id_for_type(
            rec_node.entity_id, rec_node.qualified_name or rec_node.name
        )
        for ident in _referenced_identifiers_for_record(
            graph_nodes_by_id, rec_node_id, rec_node
        ):
            if ident not in seen:
                queue.append(ident)


# ── Leaf-local duplicate of internal_leak.is_internal_type ─────────────────
# Needed for exactly one purpose below: `_record_is_confirmed_public_seed`'s
# "not an internal-namespace type" condition. Duplicated rather than
# imported from `internal_leak.py` -- that module is itself unclassified in
# `architecture/modules.yaml` (imports real `extract`-layer `buildsource.*`
# modules a strictly-enforced `policy/` package member may not depend on;
# `surface.py`'s own pre-migration call to the same function got away with
# it only because `surface.py` is a `legacy_paths` entry, exempt from this
# repo's `unclassified-import`/`dependency-direction` enforcement the way a
# real `abicheck/policy/*.py` file is not). Matches the same "leaf-safe
# duplicate" precedent already used for `_type_identifiers` in
# ``public_surface.py``.
_DEFAULT_INTERNAL_NAMESPACES: tuple[str, ...] = (
    "detail",
    "impl",
    "internal",
    "__detail",
    "_impl",
)
_TEMPLATE_ARG_RE = re.compile(r"<[^<>]*>")


def _strip_template_args(name: str) -> str:
    """Collapse balanced ``<...>`` template arg lists out of *name*."""
    prev = None
    cur = name
    while cur != prev:
        prev = cur
        cur = _TEMPLATE_ARG_RE.sub("", cur)
    return cur


def _name_segments(name: str) -> list[str]:
    """Return ``::``-separated identifier segments of *name*, with template
    arguments stripped first."""
    if not name:
        return []
    stripped = _strip_template_args(name)
    return [seg.strip() for seg in stripped.split("::") if seg.strip()]


def _is_internal_type(name: str) -> bool:
    """Return True if *name* lives in one of ``_DEFAULT_INTERNAL_NAMESPACES``
    (segment-based match, case-sensitive)."""
    return any(seg in _DEFAULT_INTERNAL_NAMESPACES for seg in _name_segments(name))


def _record_exact_identities(snap: AbiSnapshot) -> set[str]:
    """The exact qualified spelling of every record in *snap* -- never a
    tail alias (unchanged from ``surface.py``'s original -- see its own
    docstring, preserved in this migration's git history, for the full
    castxml/DWARF-convention rationale)."""
    return {rec.qualified_name or rec.name for rec in snap.types}


def _record_nested_in_known_record(qname: str, record_identities: set[str]) -> bool:
    """True when *qname*'s immediate enclosing scope is itself a known
    record (unchanged from ``surface.py``'s original -- see its own
    docstring for the full rationale)."""
    if "::" not in qname:
        return False
    owner = qname.rsplit("::", 1)[0]
    return owner in record_identities


def _record_is_confirmed_public_seed(
    rec: RecordType, record_identities: set[str]
) -> bool:
    """True when *rec* should be seeded into the public-surface closure on
    its own confirmed public-header origin alone, independent of whether any
    exported function/variable signature reaches it (unchanged from
    ``surface.py``'s original -- see its own docstring for the full
    five-condition rationale, preserved in this migration's git history)."""
    qname = rec.qualified_name
    if not qname:
        return False
    return bool(
        rec.source_header
        and rec.origin is ScopeOrigin.PUBLIC_HEADER
        and not _is_internal_type(qname)
        and not _record_nested_in_known_record(qname, record_identities)
    )


def resolve_surface_graph_nodes(snap: AbiSnapshot) -> dict[str, GraphNode]:
    """``snap.surface_graph``'s nodes, keyed by id -- always ensuring
    :func:`build_public_surface_facts` has populated the returned graph's
    ``referenced_identifiers``/``identifiers_collision`` node attrs, rather
    than trusting whatever ``snap.surface_graph`` already is.

    The one place every ADR-063 Phase 3 traversal (this module's own public-
    domain closure, and ``export_surface.py``'s export-domain closure, which
    shares :func:`_walk_type_closure` verbatim) gets its graph from, so a
    caller with an already-resolved ``snap.surface_graph`` and one without
    are handled identically rather than each reimplementing the ``None``
    check.

    This always-populate step is not optional, because ``snap.surface_graph``
    being non-``None`` does **not** mean it already carries these two attrs.
    ``service_header_graph_attach._attach_header_graph`` runs on essentially
    every real dump and unconditionally installs an L5
    ``SourceGraphSummary`` as ``snap.surface_graph`` -- but deliberately
    *without* calling :func:`build_public_surface_facts` itself (that
    builder's own docstring: "populating it is deferred to whichever later
    phase actually queries the graph" -- G31 Phase A measured a 47-96%
    header-graph-attach-cost regression from paying that walk on *every*
    dump, whether or not a surface query ever follows). This function is
    that later phase, so it is exactly where the deferred cost belongs.
    Trusting an attached-but-unpopulated graph unconditionally would read
    every node as referencing nothing (``dict.get(..., ())`` on an absent
    key) on the ordinary, default `--scope-public-headers` dump path,
    collapsing the transitive closure and potentially hiding a real ABI
    break in a type only reachable through such a node (Codex review, PR
    #979) -- not merely a stale-schema edge case, but the common case.

    :func:`build_public_surface_facts` is idempotent and evidence-preserving
    (``SourceGraphSummary.add_node``/``add_edge`` merge a second
    registration's facts rather than dropping or overwriting the first), so
    calling it against an already-attached graph *enriches* that graph's
    existing nodes in place with the two attrs above -- it does not discard
    ``_attach_header_graph``'s own L5 edges/facts, and a graph that already
    carries these attrs (e.g. a second call in the same process) re-derives
    the identical value at some redundant walk cost, never a different one.

    Falls back to building a throwaway, in-memory-only graph -- using the
    exact same approximate (qualified-name-string-keyed) node ids the
    builder already uses whenever a declaration's ``entity_id`` carrier is
    unpopulated, the identical, already-accepted collision class the
    pre-migration string-keyed traversal already had -- only when
    ``snap.surface_graph`` is ``None`` (a pure binary-only L0/L1 dump, or a
    snapshot predating the field). That throwaway graph is never persisted
    back onto *snap*, unlike the enrich-in-place case above: a binary-only
    snapshot has no real header-AST declaration surface to attach, and
    fabricating one onto ``snap.surface_graph`` would misrepresent its
    actual evidence coverage to any other reader of that field (e.g.
    serialization, ``dependency_scope``).

    Re-finalizes a :class:`~abicheck.model.source_graph.SourceGraphSummary`
    after enrichment (Codex review, PR #979): ``_attach_header_graph`` already
    called ``graph.finalize()`` on the L5-only content before installing it
    as ``snap.surface_graph``, which stamped ``graph_id`` (a content hash
    over the node/edge set) and ``coverage`` from that L5-only snapshot.
    :func:`build_public_surface_facts` can add new declaration/type/typedef/
    header/symbol nodes and edges the L5 pass never saw, so leaving the
    stale ``graph_id``/``coverage`` in place would silently disagree with
    the graph's own, now-larger node/edge set on any later ``save_snapshot``/
    ``to_dict`` -- a content-addressed hash that no longer matches its own
    content. ``finalize()`` isn't part of the narrower :data:`SurfaceGraphLike`
    protocol (only a real :class:`SourceGraphSummary` has ``graph_id``/
    ``coverage`` at all), so this narrows back to the concrete type first,
    the pattern that protocol's own docstring documents for exactly this
    situation.

    **Deliberately not memoized across calls, even though a real CI perf
    gate measured this function's cost as a regression** (see
    ``docs/contribute/known-gaps.md``'s ADR-063 Phase 3 entry for the full
    accounting). A caching attempt keyed on *snap*/*graph* identity was
    tried and reverted before landing: ``tests/test_export_surface.py::
    TestUnresolvedTypeEdges::test_a_scope_lost_alias_key_is_followed_to_
    its_target`` mutates ``snap.typedefs``/``snap.types`` in place between
    two ``compute_export_surface(snap)`` calls on the *same* object and
    correctly expects the second call to see the new content -- an
    identity-keyed cache would silently serve the first call's stale
    result instead, exactly the kind of silent false-negative this whole
    migration exists to prevent. Trading a real correctness guarantee for
    a performance win is not an acceptable trade here; the cost is paid in
    full, every call, until a genuinely safe optimization (e.g. cheaper
    ``GraphNode``/``GraphFact`` construction, not caching) is designed.
    """
    graph: SurfaceGraphLike | None = snap.surface_graph
    if graph is None:
        graph = SourceGraphSummary()
    build_public_surface_facts(snap, graph)
    if isinstance(graph, SourceGraphSummary):
        graph.finalize()
    return {n.id: n for n in graph.nodes}


def _resolve_public_surface_via_graph(snap: AbiSnapshot) -> PublicSurface:
    """The real graph traversal: computes *snap*'s public-ABI surface from
    ``resolve_surface_graph_nodes(snap)`` instead of ``surface.py``'s old,
    independent closure-walk implementation.

    Public roots are :data:`Visibility.PUBLIC` functions/variables. The
    public type set is the transitive closure over the types they
    reference (returns, params, fields, bases, typedef targets), read from
    the graph's own precomputed ``referenced_identifiers`` node attrs.
    """
    surface = PublicSurface()
    graph_nodes_by_id = resolve_surface_graph_nodes(snap)

    # Build the type universe and name -> record / enum indexes for closure walks.
    record_by_name, enum_by_name = _index_surface_types(snap, surface)

    # Seed roots from public symbols; collect the type names they touch.
    seed_types, has_public = _seed_public_roots(snap, surface, graph_nodes_by_id)

    # A named enum whose declaration textually came from a parsed header is
    # part of the public surface even when no function/variable signature
    # references the enum type by name -- see ``surface.py``'s original
    # docstring (preserved in this migration's git history) for the full
    # rationale and the case20 regression it guards.
    seed_types |= {
        en.name
        for en in snap.enums
        if en.source_header and en.origin not in _DEMOTE_ORIGINS
    }

    # A record/class/union declared directly in a public header, but named
    # by NO exported function/variable signature, is otherwise invisible to
    # this closure -- see ``_record_is_confirmed_public_seed``'s own
    # docstring for the full rationale (the oneDPL ``discard_iterator``
    # shape).
    record_identities = _record_exact_identities(snap)
    seed_types |= {
        rec.qualified_name or rec.name
        for rec in snap.types
        if _record_is_confirmed_public_seed(rec, record_identities)
    }

    # Provenance is available iff some declaration was classified to a real
    # origin (only happens when the snapshot was dumped with a public-header
    # set). Used by the classifier to emit the ``no-provenance`` ledger reason.
    surface.has_provenance = any(
        o != ScopeOrigin.UNKNOWN for o in surface.origin_by_key.values()
    )

    # Scoping only makes sense when we actually have header-derived public
    # visibility -- see ``surface.py``'s original docstring (preserved in
    # this migration's git history) for the full ELF-only-mode rationale.
    surface.resolvable = has_public and not getattr(snap, "elf_only_mode", False)
    if not surface.resolvable:
        return surface

    # Transitive closure over the record/typedef graph.
    _walk_type_closure(
        graph_nodes_by_id, snap, surface, record_by_name, enum_by_name, seed_types
    )
    # Separate, ambiguity-vetoing closure -- see its own docstring for why
    # this can't be folded into the walk above.
    _walk_exact_type_closure(
        graph_nodes_by_id, snap, surface, record_by_name, enum_by_name, seed_types
    )
    return surface


def resolve_public_surface(
    snapshot: AbiSnapshot, explicit_roots: object = None
) -> PublicSurface:
    """The one place every Phase 3 consumer resolves a snapshot's public
    surface from. *explicit_roots* is accepted for this function's own
    future graph-native signature (a caller that already knows its roots,
    e.g. ``--used-by``/``--required-symbol`` scoping) but is not yet
    consulted -- the resolution today is entirely graph/snapshot-derived,
    with no external root injection.
    """
    return _resolve_public_surface_via_graph(snapshot)


# ``type_reachability.directly_referenced_stdlib_types()`` migrating here
# (D5's own closing note: "itself a relevance decision... becomes a second,
# narrower query in policy/public_surface.py") is deliberately NOT done in
# this slice, for a reason specific to this one function rather than this
# module's general scoping note above: `type_reachability.py` itself
# imports `diff_cxx_rules.py`/`type_reachability_spelling.py`, both already
# classified `extract` layer in `architecture/modules.yaml` — reclassifying
# `type_reachability.py` as `policy` (this module's own layer) would
# introduce a genuine new `policy -> extract` dependency-direction
# violation for those two pre-existing imports, not merely thread a
# passthrough. Resolving that needs its own real investigation (does
# `diff_cxx_rules.py`/`type_reachability_spelling.py` belong in `policy`
# too, or does `type_reachability.py` itself not actually belong here),
# not a same-slice reclassification alongside everything else in this
# file. Left as a named, scoped-out follow-up.
