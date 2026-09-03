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
``export_surface.py`` imports :func:`_walk_type_closure` directly -- its own
type-closure step reuses it verbatim (see that module's own docstring).

:func:`resolve_public_surface` computes *what a declaration/type/typedef
references* via :func:`~abicheck.compare.surface_graph.
referenced_identifiers_by_node` -- a pure function of *snap*'s own current
declarations, computed fresh on every call -- rather than ``surface.py``'s
old, independent closure-walk implementation (which re-parsed
``fn.return_type``/``rec.fields``/``rec.bases``/typedef targets via its own
regex scan) or ``AbiSnapshot.surface_graph``'s own persisted node attrs.

**Deliberately not read off ``snap.surface_graph``, even though
``compare/surface_graph.py`` stamps this exact same value onto its own
``GraphNode.attrs`` too** (Codex review, PR #979, two rounds). A first
version of this migration *did* read the graph's cached
``referenced_identifiers``/``identifiers_collision`` node attrs, and two
distinct hazards surfaced in review before the design settled on the
current, graph-independent approach:

1. ``snap.surface_graph`` being non-``None`` does not mean its nodes carry
   these two attrs at all (``_attach_header_graph`` installs an L5 graph
   on essentially every real dump without ever populating them, and an
   older persisted schema predates them entirely) -- trusting an
   attrs-less node as "references nothing" silently collapsed the
   transitive closure on the *ordinary, default* dump path.
2. Fixing (1) by rebuilding/enriching the graph in place ran into a
   second, more fundamental problem: ``GraphNode.attrs`` are derived
   through ``model.graph_facts``' cross-producer evidence-merge machinery,
   which resolves a same-key disagreement between two registrations by
   confidence/producer/content precedence -- correct for genuinely
   independent producer facts, but wrong for this specific, single-source
   derived computation. A stale or *adversarial* persisted fact (a crafted
   snapshot JSON is explicitly in scope here) at a confidence this
   module's own freshly-registered fact (always the lowest rank) cannot
   outrank would silently win over the correct, current value -- the same
   collapsed-closure failure mode as (1), just reachable through the fix
   for (1) instead of around it.

Both hazards share one root cause: trusting anything cached on the shared,
evidence-mergeable graph for a value that has exactly one legitimate
source (*snap*'s own declarations, right now) and no legitimate second
producer to reconcile evidence with. The fix is not a smarter merge rule;
it is not merging at all -- :func:`referenced_identifiers_by_node` is
called directly, every time, with no ``GraphNode``/``GraphFact``
construction (and its associated evidence-merge cost) anywhere in the
path. This also happens to remove the per-dump graph-construction/
enrichment cost entirely from this walk, which a real CI perf gate had
flagged as a regression against the pre-migration regex-based re-parse --
see ``docs/contribute/known-gaps.md``'s ADR-063 Phase 3 entry for the
history of that measurement, since fixing the security concern above is
what closes it, not a dedicated performance change.

**A node-id collision is not automatically safe to union.** Two
declarations can share one *approximate* node id when neither carries a
resolved ``entity_id`` (e.g. two overloads with no mangled name to
disambiguate them). Unioning their referenced identifiers would make a
*public*, narrow-signature overload appear to reference a *hidden*
sibling's own private parameter type -- attributing a private overload's
reach to a public root, not merely over-approximating a single root's own
reach. :func:`referenced_identifiers_by_node` itself already resolves this
the safe way: it returns which node ids received contributions from more
than one distinct declaration (``ReferencedIdentifiers.collided_nodes``),
and this module's own ``_referenced_identifiers_for_function``/
``_referenced_identifiers_for_variable``/``_referenced_identifiers_for_record``
fall back to recomputing that *one* declaration's own identifiers directly
on such a collision (exactly what the pre-migration implementation always
did, unconditionally) rather than trust a value that may be blurred across
an unrelated sibling. See :func:`_node_identifiers_or_collision`'s own
docstring. This is the one documented case where "trust the fresh
computation" still yields further, to "recompute even more narrowly" --
a named, tested residual, not a silent gap.

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
    ReferencedIdentifiers,
    fact_list,
    node_id_for_declaration,
    node_id_for_type,
    node_id_for_typedef,
    referenced_identifiers_by_node,
)
from ..diff_cxx_rules import owner_class_of
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
    from ..model.snapshot import AbiSnapshot

__all__ = [
    "resolve_public_surface",
]


def _referenced_identifiers(refs: ReferencedIdentifiers, node_id: str) -> set[str]:
    """The precomputed union of type-identifier strings that the
    declaration/type/typedef at *node_id* references -- read from a
    :class:`~abicheck.compare.surface_graph.ReferencedIdentifiers` computed
    directly and freshly from *snap* (:func:`~abicheck.compare.
    surface_graph.referenced_identifiers_by_node`), never from a
    :class:`~abicheck.model.graph_facts.GraphNode`'s own ``attrs``.

    **Deliberately not read off the graph's node attrs, even though
    :func:`build_public_surface_facts` also stamps this same value there**
    (Codex review, PR #979): a node's ``attrs`` are derived through
    ``model.graph_facts``' cross-producer evidence-merge machinery, which
    resolves a same-key disagreement between two registrations by
    confidence/producer/content precedence -- appropriate for genuinely
    independent producer facts, but wrong for this specific, single-source
    derived computation. A schema-v29 (or otherwise untrusted/adversarial)
    persisted snapshot could carry a stale or crafted ``referenced_
    identifiers`` fact at a confidence this module's own freshly-registered
    fact (always ``CONF_UNKNOWN``, the lowest rank) cannot outrank, so
    trusting ``node.attrs`` here could let a poisoned or stale value silently
    win over the correct, current one -- collapsing the transitive closure
    exactly like the bug this same review round already fixed once. Calling
    :func:`referenced_identifiers_by_node` directly bypasses that merge
    system entirely: there is no precedence to lose, because nothing but
    *this* call's own fresh computation is ever consulted.

    Returns an empty set (not an error) for a node id absent from *refs* --
    shouldn't happen for a snapshot :func:`referenced_identifiers_by_node`
    itself walked (every function/variable/record/typedef contributes an
    entry when it has any identifiers at all), but a missing entry is
    strictly the *conservative* direction here (fewer identifiers to
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
    ``referenced_identifiers_by_node`` docstring, for why a bare lookup by
    node id is unsound for those three kinds specifically.
    """
    return set(refs.by_node.get(node_id, ()))


def _node_identifiers_or_collision(
    refs: ReferencedIdentifiers, node_id: str
) -> set[str] | None:
    """*node_id*'s freshly-computed ``referenced_identifiers`` from *refs*,
    or ``None`` when that id is flagged as a collision (more than one
    distinct declaration/type mapped onto this approximate id -- see
    ``compare.surface_graph``'s own docstring) or absent entirely. ``None``
    tells the caller its own per-object fallback must run instead of
    trusting a value that may be blurred across an unrelated sibling."""
    if node_id not in refs.by_node or node_id in refs.collided_nodes:
        return None
    return set(refs.by_node[node_id])


def _referenced_identifiers_for_function(
    refs: ReferencedIdentifiers, node_id: str, fn: Function
) -> set[str]:
    """*fn*'s own referenced type identifiers -- the freshly-computed value
    when safe, else recomputed directly from *fn*'s own return/param types.

    The fallback exists for exactly the case the collision flag reports:
    two declarations sharing one approximate node id (no resolved
    ``entity_id`` for either, e.g. two overloads with no mangled name to
    disambiguate them) are not the same declaration, and a public,
    narrow-signature overload must not appear to reference whatever a
    same-node hidden sibling's own (possibly private) parameter type
    happens to be. Recomputing *this* declaration's own identifiers
    directly is exactly what the pre-migration implementation always did,
    unconditionally -- this is the safety net for the one case the graph's
    shared-node model cannot represent precisely, not a routine path.
    """
    cached = _node_identifiers_or_collision(refs, node_id)
    if cached is not None:
        return cached
    idents = _type_identifiers(fn.return_type)
    for p in fn.params:
        idents |= _type_identifiers(getattr(p, "type", None))
    return idents


def _referenced_identifiers_for_variable(
    refs: ReferencedIdentifiers, node_id: str, var: Variable
) -> set[str]:
    """*var*'s own referenced type identifiers -- see
    :func:`_referenced_identifiers_for_function`'s docstring for the same
    collision-fallback rationale, applied to a variable's own type."""
    cached = _node_identifiers_or_collision(refs, node_id)
    if cached is not None:
        return cached
    return _type_identifiers(var.type)


def _referenced_identifiers_for_record(
    refs: ReferencedIdentifiers, node_id: str, rec: RecordType
) -> set[str]:
    """*rec*'s own referenced type identifiers (fields, bases, virtual
    bases) -- see :func:`_referenced_identifiers_for_function`'s docstring
    for the same collision-fallback rationale, applied to a record sharing
    its approximate node id with a distinct sibling record/enum/typedef."""
    cached = _node_identifiers_or_collision(refs, node_id)
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
    refs: ReferencedIdentifiers,
) -> tuple[set[str], bool]:
    """Record public symbols on *surface*; return (seed type names, has_public).

    Seeds the type-closure work-list from the return/parameter/variable types of
    every :data:`Visibility.PUBLIC` function and variable -- read from *refs*,
    a fresh, directly-computed :class:`~abicheck.compare.surface_graph.
    ReferencedIdentifiers`, rather than re-parsing ``fn.return_type``/
    ``p.type``/``var.type`` here a second time (falling back to a direct,
    per-declaration recomputation on a detected node-id collision -- see
    :func:`_referenced_identifiers_for_function`).
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
                refs, node_id_for_declaration(fn.entity_id, fn.name), fn
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
                refs, node_id_for_declaration(var.entity_id, var.name), var
            )
    return seed_types, has_public


def _walk_type_closure(
    refs: ReferencedIdentifiers,
    snap: AbiSnapshot,
    surface: PublicSurface,
    record_by_name: dict[str, list[RecordType]],
    enum_by_name: dict[str, list[EnumType]],
    seed_types: set[str],
) -> None:
    """Transitive closure over the record/typedef graph; fills public_types.

    Follows typedef targets, record fields, and base classes from each seed
    type, marking every reachable known type as part of the public surface —
    read via *refs*, a fresh, directly-computed ``ReferencedIdentifiers``,
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
            for ident in _referenced_identifiers(refs, node_id_for_typedef(name)):
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
                refs, rec_node_id, rec_node
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
    refs: ReferencedIdentifiers,
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
            for ident in _referenced_identifiers(refs, node_id_for_typedef(name)):
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
        for ident in _referenced_identifiers_for_record(refs, rec_node_id, rec_node):
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


def _resolve_public_surface_from_snapshot(snap: AbiSnapshot) -> PublicSurface:
    """Computes *snap*'s public-ABI surface from
    :func:`~abicheck.compare.surface_graph.referenced_identifiers_by_node`
    instead of ``surface.py``'s old, independent closure-walk implementation.
    Despite this module's own name, the resolution below never reads
    ``snap.surface_graph`` itself -- see this function's own body and
    ``referenced_identifiers_by_node``'s docstring for why (CodeRabbit
    review, PR #979: an earlier revision of this function *did* read the
    persisted graph, and was renamed off ``_via_graph`` once that design
    was superseded -- see this module's own top-of-file docstring for the
    full history).

    Public roots are :data:`Visibility.PUBLIC` functions/variables. The
    public type set is the transitive closure over the types they
    reference (returns, params, fields, bases, typedef targets), read from
    *refs* -- a fresh, directly-computed
    :class:`~abicheck.compare.surface_graph.ReferencedIdentifiers`, deliberately
    **not** ``snap.surface_graph``'s own persisted node attrs (Codex review,
    PR #979: a schema-v29 or otherwise untrusted snapshot could carry a
    stale or crafted ``referenced_identifiers`` fact that the graph's
    cross-producer evidence-merge precedence would let outrank a freshly
    recomputed one -- see :func:`_referenced_identifiers`'s own docstring
    for the full account). This also means this function no longer needs
    ``snap.surface_graph``/``compare.surface_graph.build_public_surface_facts``
    at all: :func:`referenced_identifiers_by_node` is a pure function of
    *snap*'s own current declarations, with no ``GraphNode``/``GraphFact``
    construction (and its associated evidence-merge cost) in the way.
    """
    surface = PublicSurface()
    refs = referenced_identifiers_by_node(snap)

    # Build the type universe and name -> record / enum indexes for closure walks.
    record_by_name, enum_by_name = _index_surface_types(snap, surface)

    # Seed roots from public symbols; collect the type names they touch.
    seed_types, has_public = _seed_public_roots(snap, surface, refs)

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
    _walk_type_closure(refs, snap, surface, record_by_name, enum_by_name, seed_types)
    # Separate, ambiguity-vetoing closure -- see its own docstring for why
    # this can't be folded into the walk above.
    _walk_exact_type_closure(
        refs, snap, surface, record_by_name, enum_by_name, seed_types
    )
    return surface


def resolve_public_surface(
    snapshot: AbiSnapshot, explicit_roots: object = None
) -> PublicSurface:
    """The one place every Phase 3 consumer resolves a snapshot's public
    surface from. *explicit_roots* is accepted for this function's own
    future graph-native signature (a caller that already knows its roots,
    e.g. ``--used-by``/``--required-symbol`` scoping) but is not yet
    consulted -- the resolution today is entirely snapshot-derived, with no
    external root injection.
    """
    return _resolve_public_surface_from_snapshot(snapshot)


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
