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

"""ADR-049 Phase 3's observed provider ledger (plan Section 4.1), and the raw
policy-independent type graph Phase 4's ``contract_evidence`` block persists.

:mod:`abicheck.contract_evidence` landed the *shapes* (``EvidenceSearchRecord``
/ ``ProviderEvidenceEntry`` / ``ContractEvidenceBlock`` / ``TypeGraphSnapshot``)
with no producer. This module is that producer: it turns the two evidence
providers this build actually has -- :mod:`abicheck.surface`'s header-derived
public surface and :mod:`abicheck.export_surface`'s export-table surface --
plus the run's own explicit overlays into one
:class:`~abicheck.contract_evidence.ContractEvidenceBlock` per comparison.

Three properties this module exists to hold, each one a line of Phase 3's or
Phase 4's own gate:

1. **Policy-independent.** Nothing recorded here depends on which
   :class:`~abicheck.contract_relevance_types.ContractMode` was selected. Both
   providers are collected for every run regardless of mode, and the raw type
   graph is the snapshot's whole record/enum/typedef graph, not a
   mode-dependent closure of it (plan Section 5.1: "``contract_evidence``
   stores raw policy-independent type nodes/edges. The mode/root-dependent
   closure is computed by the evaluator and stored in the decision receipt").
   That is what makes the block valid input to a later re-evaluation under a
   *different* configuration (:mod:`abicheck.contract_replay`).
2. **Scoped failure.** Each provider carries its own status/completeness, so
   an unavailable header surface does not poison a completed export-table
   search, and vice versa (plan Section 4.1: "Provider failures are scoped to
   the affected domain/entity class").
3. **Order-independent.** Every collection this module produces is a *set* of
   observations; :mod:`abicheck.contract_evidence`'s own frozen dataclasses
   sort and deduplicate them, so two runs observing the same facts in a
   different traversal order produce an equal, identically-serialized block
   (Phase 4's byte/order-independent round-trip gate).

Node encoding for :class:`~abicheck.contract_evidence.TypeGraphSnapshot`
-----------------------------------------------------------------------

``TypeGraphSnapshot`` deliberately treats nodes as opaque strings, so this
module owns the encoding. Every node is ``"<kind>:<identity>"``:

===========  ============================================================
``decl:``    one function/variable declaration, keyed by its canonical
             linker identity (``mangled`` when recorded, else ``name``) --
             refined with a signature discriminator for the one case where
             that fallback names more than one declaration (see
             :func:`_function_node_keys`)
``record:``  one :class:`~abicheck.model.RecordType`, keyed by
             ``qualified_name`` when the producer recorded one, else
             ``name`` (see :func:`_type_identity`)
``enum:``    one :class:`~abicheck.model.EnumType`, keyed the same way
``typedef:`` one ``snapshot.typedefs`` alias, keyed by the alias
``alias:``   a *lookup* spelling (demangled name, bare ``::`` tail, bare
             leaf of a qualified type) that resolves to a canonical node
             above -- one alias may resolve to *several*, which is what
             makes it an alias rather than an identity
===========  ============================================================

and every edge is a resolved reference: ``decl: -> record:/enum:/typedef:``
(signature types), ``record: -> ...`` (fields, bases, virtual bases),
``typedef: -> ...`` (alias target), ``alias: -> ...`` (the canonical node a
spelling resolves to). References are resolved *at collection time*, through
the same name/bare-tail indexes :func:`~abicheck.surface._index_surface_types`
builds for the live closure walk -- which is exactly why the block carries an
``identity_algorithm_version``: a future matcher resolving the same raw
spellings differently produces a different graph from identical inputs, and
D6 requires that to be tellable apart on replay rather than silently
reinterpreted.

A spelling that resolves to nothing is *not* recorded as an edge to a
placeholder node -- there is no node to point at, and inventing one would
make a replayed closure claim to have walked something it never did. The
export provider's own :attr:`~abicheck.export_surface.ExportSurface.unresolved_type_edges`
already tracks that incompleteness for the domain where it decides an
exclusion; here it surfaces as the header provider's ``completeness``.

Cost, stated rather than silently bounded
-----------------------------------------

The graph is *whole-snapshot*: its size grows with the library's declaration
and type count, and it is embedded in the JSON report once per side. For a
large C++ library that is a real number of megabytes. Collection is gated on
the caller opting into ``compare(..., contract_evaluation=True)``, which is
advisory and off by default, so no ordinary run pays it. It is deliberately
*not* truncated to a "top N" or to the mode's own closure: a partial graph
would let a replayed closure miss an edge and report a reachable type
``PROVEN_OUT_OF_CONTRACT``, and a mode-scoped one would stop being
policy-independent -- the two properties this block exists to have. If a
size bound is ever needed, it has to be an explicit, disclosed
incompleteness (a provider ``completeness`` of ``PARTIAL`` with its own
reason code), never a silent cap.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping

from .contract_evidence import (
    ContractEvidenceBlock,
    EvidenceSearchRecord,
    InputIdentity,
    ProviderEvidenceEntry,
    TypeGraphSnapshot,
)
from .contract_relevance_types import (
    ContractMode,
    EvidenceCompleteness,
    EvidenceProviderStatus,
    coerce_contract_mode,
)
from .diff_cxx_rules import owner_class_of
from .export_surface import ExportSurface, observed_exports_by_platform
from .model import AbiSnapshot, EnumType, Function, RecordType, Visibility
from .policy.public_surface import (
    PublicSurface,
    _index_surface_types,
    _symbol_keys,
    _type_identifiers,
)

# --------------------------------------------------------------------------
# Provider vocabulary.
# --------------------------------------------------------------------------

#: The header-derived public-surface provider (ADR-049 D2's ``public`` domain).
PROVIDER_PUBLIC_HEADER = "public_header"
#: The observed-export-table provider (ADR-049 D2's ``exports`` domain).
PROVIDER_EXPORT_TABLE = "export_table"
#: ``--post-manifest``: an exact, committed-export manifest (Section 4.3's
#: item-1 evidence tier).
PROVIDER_POST_MANIFEST = "post_manifest"
#: ``--public-symbol``/``--public-symbols``: the user's own widening overlay
#: (ADR-024 D6), an assertion rather than an observation -- recorded so a
#: decision that rests on it says so, never as if it were observed evidence.
PROVIDER_FORCED_PUBLIC = "forced_public_symbols"

#: Which provider supplies the *roots* of each contract domain. ``ALL`` has no
#: root provider at all -- that is the whole content of ADR-049 D2's ``all``
#: row ("every normalized entity finding is in scope, with no root/closure
#: evidence required"), so a decision under it references no root provider.
DOMAIN_ROOT_PROVIDER: Mapping[ContractMode, str | None] = {
    ContractMode.PUBLIC: PROVIDER_PUBLIC_HEADER,
    ContractMode.EXPORTS: PROVIDER_EXPORT_TABLE,
    ContractMode.ALL: None,
}

#: Run-level evidence references that name a fact the *run* carries rather
#: than a per-side provider entry in a :class:`ContractEvidenceBlock`. A
#: finding stamped by ``--used-by``/``--required-symbol`` scoping
#: (``contract_evaluation.stamp_explicit_scope_contract_evaluation``) is
#: decided after ``compare()`` already returned, by a caller that holds no
#: evidence block at all, so its decision cites this constant instead of a
#: block record id. :func:`validate_decision_evidence` accepts it for exactly
#: that reason.
EXPLICIT_SCOPE_EVIDENCE_REF = "run:explicit_consumer_or_required_symbol"

RUN_LEVEL_EVIDENCE_REFS: frozenset[str] = frozenset({EXPLICIT_SCOPE_EVIDENCE_REF})

_SIDES = ("old", "new")


def evidence_record_id(provider: str, side: str) -> str:
    """The stable id of one provider's record for one side.

    ``(side, provider, id)`` is what
    :class:`~abicheck.contract_evidence.ContractEvidenceBlock` requires to
    identify exactly one search, so the id needs only to be unique *within*
    that pair; spelling it ``provider:side`` anyway keeps a reference
    self-describing in a report where it appears without its record.
    """
    return f"{provider}:{side}"


# --------------------------------------------------------------------------
# Raw, policy-independent type graph.
# --------------------------------------------------------------------------


def _record_node(name: str) -> str:
    return f"record:{name}"


def _enum_node(name: str) -> str:
    return f"enum:{name}"


def _typedef_node(alias: str) -> str:
    return f"typedef:{alias}"


def _decl_node(key: str) -> str:
    return f"decl:{key}"


def _alias_node(key: str) -> str:
    return f"alias:{key}"


def _canonical_decl_key(name: str, mangled: str) -> str:
    """A declaration's *linker identity*.

    ``mangled`` first: it is the linker identity, unique where a demangled
    display name is not (two overloads share a display name, never a mangled
    one). Falls back to ``name`` for a producer that recorded no mangled
    symbol at all (a header-only declaration, or an ``extern "C"`` entry a
    backend spells identically either way).

    This is what ``export_surface._linker_identity`` computes and what
    :attr:`~abicheck.export_surface.ExportSurface.root_identities` is keyed
    by, so it stays the right question for "is this declaration an export
    root". It is *not* always the graph's node key -- see
    :func:`_function_node_keys` for the one case where the fallback is
    ambiguous and the node key refines it.
    """
    return mangled or name


def _declaration_signature(fn: Function) -> str:
    """The part of a function's identity a display name does not carry."""
    params = ",".join((getattr(p, "type", None) or "?") for p in fn.params)
    return f"({params})->{fn.return_type or '?'}"


def _function_node_keys(snap: AbiSnapshot) -> list[str]:
    """One node key per entry of ``snap.functions``, in order.

    :func:`_canonical_decl_key`'s display-name fallback is unique for a
    linker identity but *not* for a declaration: a header-only producer that
    recorded no mangled symbol gives every overload of one name the same
    fallback, so a public ``over()`` and a private ``over(Secret *)`` merge
    into one ``decl:`` node and the public root inherits the private
    overload's edge to ``Secret``. A re-evaluation then answers
    ``IN_CONTRACT`` for a private ``Secret`` layout change the live evaluator
    -- which walks each declaration object separately -- answered
    ``PROVEN_OUT_OF_CONTRACT`` for (Codex review, fresh evidence, confirmed
    with a minimal snapshot).

    The fix is the same "most specific available identity, ambiguity-safe"
    tiering ``finding_identity.py`` and ``diff_helpers.TypeMap`` already use:
    the plain display name is kept whenever it names exactly one unmangled
    declaration, and only a name shared by two unmangled declarations with
    *differing* signatures is refined with :func:`_declaration_signature`.
    Two unmangled declarations agreeing on name, parameters and return type
    still share a node, which is lossless -- their signature edges and their
    ``owner_class_of`` result are derived from exactly those fields, so
    nothing a closure walk could reach differs between them.

    Deliberately not applied unconditionally: the ``exports`` domain exempts
    a spelling that is a root's *own* node identity from
    ``contract_replay._without_shared_export_aliases``'s alias pruning, so
    refining an unambiguous ``over`` to ``over()->int`` for every unmangled
    declaration would lose that exemption and prune a genuine root -- the
    same strengthening, one corner over.
    """
    signatures: dict[str, set[str]] = {}
    for fn in snap.functions:
        if not fn.mangled:
            signatures.setdefault(fn.name, set()).add(_declaration_signature(fn))
    # A fallback collides across the two identity *tiers* too, not only within
    # its own: a private header-only `foo(Secret *)` beside a public
    # declaration whose recorded linker identity is literally `foo` merged the
    # two, and the public root inherited the private overload's edge to
    # `Secret` (Codex review, fresh evidence). Every recorded linker identity
    # is therefore reserved -- a name is only unambiguous if nothing else
    # already answers to it, whichever tier that something came from.
    reserved = {fn.mangled for fn in snap.functions if fn.mangled}
    reserved |= {var.mangled for var in snap.variables if var.mangled}
    reserved |= {var.name for var in snap.variables if not var.mangled}
    keys: list[str] = []
    for fn in snap.functions:
        if fn.mangled:
            keys.append(fn.mangled)
        elif len(signatures.get(fn.name, ())) > 1 or fn.name in reserved:
            keys.append(fn.name + _declaration_signature(fn))
        else:
            keys.append(fn.name)
    return keys


def _type_identity(type_obj: RecordType | EnumType) -> str:
    """The one identity a ``record:``/``enum:`` node is keyed by.

    ``qualified_name`` first, for the same reason ``mangled`` comes first for
    a declaration: it is the identity that is unique where a display name is
    not. On the castxml/clang path ``name`` is the bare leaf, so ``ns1::Foo``
    and ``ns2::Foo`` are *both* recorded as ``"Foo"`` -- keying the node on
    that merged two unrelated records into one, unioning their field edges,
    and an export rooted in ``ns1::Foo`` then placed a layout finding on
    ``ns2::Foo`` ``IN_CONTRACT`` where the live evaluator answered
    ``UNKNOWN_UNRESOLVED`` on exactly that ambiguity (Codex review, confirmed
    with a minimal snapshot).

    The bare name does not disappear -- it becomes an ``alias:`` spelling
    (:func:`_link_type_aliases`), which is what it honestly is once two
    records answer to it. A producer that records no qualified name (DWARF
    bakes the whole path into ``name``) is unaffected: the identity is the
    name, exactly as before.
    """
    return getattr(type_obj, "qualified_name", None) or type_obj.name


class _TypeIndex:
    """Name -> canonical node resolution, mirroring the live closure walk.

    Built from :func:`~abicheck.surface._index_surface_types`'s own record/enum
    indexes (full name *and* bare ``::`` tail) plus ``snapshot.typedefs``, so a
    spelling resolves here exactly when
    :func:`~abicheck.surface._walk_type_closure` would resolve it there. An
    ambiguous spelling (several records/enums answering to one bare tail)
    resolves to *every* candidate, the same over-keeping
    ``_index_surface_types`` documents: never hide a real reference behind
    snapshot order.
    """

    def __init__(self, snap: AbiSnapshot) -> None:
        scratch = PublicSurface()
        record_by_name, enum_by_name = _index_surface_types(snap, scratch)
        self._nodes_by_spelling: dict[str, set[str]] = {}
        for name, records in record_by_name.items():
            self._nodes_by_spelling.setdefault(name, set()).update(
                _record_node(_type_identity(r)) for r in records
            )
        for name, enums in enum_by_name.items():
            self._nodes_by_spelling.setdefault(name, set()).update(
                _enum_node(_type_identity(e)) for e in enums
            )
        # `_index_surface_types` keys on `name` and its `::` tail only, so a
        # qualified identity is not a lookup key there. It has to be one here,
        # since it is now the node's own identity -- the same local
        # augmentation `compute_export_surface` makes to its own copy of that
        # index, for the same reason. Nothing widens: a *reference* spelled
        # `ns::Foo` already resolved through the tail, which
        # `_type_identifiers` derives on both sides.
        for rec in snap.types:
            self._nodes_by_spelling.setdefault(_type_identity(rec), set()).add(
                _record_node(_type_identity(rec))
            )
        for en in snap.enums:
            self._nodes_by_spelling.setdefault(_type_identity(en), set()).add(
                _enum_node(_type_identity(en))
            )
        # A typedef resolves by its **exact** key and nothing else, because
        # that is all `_walk_type_closure` does with one: `snap.typedefs.get(
        # name)`, a plain dict lookup with no tail fallback. Records and enums
        # get a bare tail above only because `_index_surface_types` gives them
        # one. Registering a tail here too let a signature spelling the bare
        # `Alias` reach a qualified `ns::Alias -> Secret`, so a private
        # `Secret` layout change the live evaluator proved out of contract
        # re-evaluated as `IN_CONTRACT` (Codex review, fresh evidence).
        for alias in snap.typedefs:
            self._nodes_by_spelling.setdefault(alias, set()).add(_typedef_node(alias))
        self.ambiguous_type_names = set(scratch.ambiguous_type_names)
        self.all_types = set(scratch.all_types)
        self._owner_seed_nodes = self._build_owner_seed_index(
            snap, set(record_by_name) | set(enum_by_name)
        )

    def _build_owner_seed_index(
        self, snap: AbiSnapshot, type_keys: set[str]
    ) -> dict[str, set[str]]:
        """Which *exact* owner identities seed which record nodes.

        A mirror of ``export_surface.compute_export_surface``'s own
        ``owner_seed_by_identity`` map, field for field, because that map is
        what the live exports closure seeds a method root's owner through --
        and a graph recognizing fewer identities than it does drops the edge
        and lets a replay prove a live ``IN_CONTRACT`` owner out of contract
        (see :func:`_link_owner_class`).

        The rule mirrored, not re-invented: a record's ``qualified_name`` is
        always an exact identity (it names exactly one record; two records
        sharing one are the same type), while its bare ``name`` counts only
        when the producer did not separately record a differing qualified
        spelling -- on the castxml/clang path ``name`` is the leaf alone, so
        registering it lets an ``api::run()`` namespace fragment match an
        unrelated ``other::api`` "exactly" after all -- and only when that
        name is unambiguous.

        Keying the identity set on ``name`` alone was the gap: a producer
        storing ``name="Widget", qualified_name="ns::Widget"`` makes
        ``owner_class_of`` return the qualified identity, which the bare-only
        set never contained, so the method-to-owner edge was dropped and a
        live ``IN_CONTRACT`` replayed as ``PROVEN_OUT_OF_CONTRACT`` (Codex
        review, fresh evidence, confirmed with a minimal snapshot).
        """
        # `compute_export_surface` folds typedef-alias collisions into its
        # ambiguity set *before* building the owner map, since the closure
        # walk resolves one spelling through both indexes; mirrored here so
        # the same bare name is refused on both sides.
        keys = type_keys | {r.qualified_name for r in snap.types if r.qualified_name}
        keys |= {e.qualified_name for e in snap.enums if e.qualified_name}
        ambiguous = self.ambiguous_type_names | {
            alias for alias in snap.typedefs if alias in keys
        }
        out: dict[str, set[str]] = {}
        for rec in snap.types:
            node = _record_node(_type_identity(rec))
            leaf_only = bool(rec.qualified_name) and rec.qualified_name != rec.name
            if not leaf_only and rec.name not in ambiguous:
                out.setdefault(rec.name, set()).update(self.resolve(rec.name) or {node})
            if rec.qualified_name:
                # The qualified spelling is not in `_index_surface_types`'s
                # own index (it keys on `name` and its `::` tail), which is
                # exactly the augmentation `compute_export_surface` makes
                # locally for the same reason -- so the record's own node is
                # added directly rather than looked up.
                out.setdefault(rec.qualified_name, set()).update(
                    self.resolve(rec.qualified_name) | {node}
                )
        return out

    def resolve(self, spelling: str) -> set[str]:
        return set(self._nodes_by_spelling.get(spelling, ()))

    def resolve_owner_class(self, identity: str) -> set[str]:
        """The record node(s) an owner-class *identity* exactly names.

        Deliberately not :meth:`resolve`: that one answers a bare ``::``
        tail too, which is right for a genuine type *reference* (a backend
        may legitimately spell a reachable type unqualified) and wrong for
        an owner-class name, which `owner_class_of` always returns as a
        complete scope chain -- or as namespace noise when the function is
        not a method at all. See :func:`_link_owner_class`.
        """
        return set(self._owner_seed_nodes.get(identity, ()))

    def resolve_type_string(self, type_str: str | None) -> set[str]:
        """Every canonical node a type *string* references."""
        out: set[str] = set()
        for ident in _type_identifiers(type_str):
            out |= self.resolve(ident)
        return out


def build_type_graph(snap: AbiSnapshot) -> TypeGraphSnapshot:
    """The raw record/enum/typedef/declaration graph of *snap*.

    Policy-independent by construction: every declaration is walked, whatever
    its visibility or header origin, and no contract mode is consulted. What
    a *domain* then treats as roots is the per-provider ``declarations`` list,
    not this graph -- which is why one graph serves both domains and a later
    re-evaluation under a third.
    """
    index = _TypeIndex(snap)
    nodes: set[str] = set()
    edges: set[tuple[str, str]] = set()

    def link(src: str, targets: Iterable[str]) -> None:
        for target in targets:
            nodes.add(target)
            edges.add((src, target))

    for rec in snap.types:
        node = _record_node(_type_identity(rec))
        nodes.add(node)
        for fld in rec.fields:
            link(node, index.resolve_type_string(fld.type))
        for base in list(rec.bases) + list(rec.virtual_bases):
            link(node, index.resolve_type_string(base))
        _link_type_aliases(rec, node, nodes, edges)

    for en in snap.enums:
        node = _enum_node(_type_identity(en))
        nodes.add(node)
        _link_type_aliases(en, node, nodes, edges)

    for alias, target in snap.typedefs.items():
        node = _typedef_node(alias)
        nodes.add(node)
        link(node, index.resolve_type_string(target))

    for fn, key in zip(snap.functions, _function_node_keys(snap), strict=True):
        node = _decl_node(key)
        nodes.add(node)
        link(node, index.resolve_type_string(fn.return_type))
        for param in fn.params:
            link(node, index.resolve_type_string(getattr(param, "type", None)))
        _link_owner_class(fn, node, index, link)
        _link_decl_aliases(fn.name, fn.mangled, node, nodes, edges)

    for var in snap.variables:
        node = _decl_node(_canonical_decl_key(var.name, var.mangled))
        nodes.add(node)
        link(node, index.resolve_type_string(var.type))
        _link_decl_aliases(var.name, var.mangled, node, nodes, edges)

    return TypeGraphSnapshot(nodes=tuple(nodes), edges=tuple(edges))


def _link_owner_class(
    fn: Function,
    node: str,
    index: _TypeIndex,
    link: Callable[[str, Iterable[str]], None],
) -> None:
    """Link a method declaration to its own enclosing class.

    Both live surfaces seed a method root's owner class into the closure --
    ``surface._seed_public_roots`` via ``_type_identifiers(owner)``, and
    ``export_surface._seed_export_roots`` via its exact-identity owner map --
    because a consumer holding an exported method can declare, allocate, and
    inherit that class even when no *other* signature names it. Without the
    same edge here, a replay walking only return/parameter edges would find
    the owner unreachable and could turn the live ``IN_CONTRACT`` decision
    into ``PROVEN_OUT_OF_CONTRACT`` -- a *strengthened* decision, which is the
    one direction :func:`~abicheck.contract_replay.compare_decisions` treats
    as unsound (Codex review, fresh evidence).

    Matched by **exact** record identity, mirroring
    ``export_surface._seed_export_roots``'s own ``owner_seed_by_identity``
    map rather than ``surface.py``'s permissive spelling resolution. An
    earlier version of this edge used the permissive one on the reasoning
    that over-linking only ever *weakens* a decision -- which is wrong,
    because one graph serves both domains: an exported namespace function
    ``api::run()`` yields the owner string ``"api"``, which permissive
    resolution happily matches against an unrelated record whose bare tail
    is ``api`` (``other::api``), pulling it into the *export* closure and
    turning a live ``PROVEN_OUT_OF_CONTRACT`` into a replayed
    ``IN_CONTRACT`` (Codex review, fresh evidence). Exact matching loses
    nothing real: ``owner_class_of`` reconstructs a complete scope chain
    from a qualified name or an Itanium/MSVC mangling, never a partially
    elided one, so when the owner is a real class its identity matches
    exactly -- and when the function is not a method, what it returns is
    namespace noise that *should* match nothing. This is the same rule, for
    the same reason, that ``type_reachability.py``'s owner seeding settled
    on.

    *Which* spellings count as an exact identity is
    :meth:`_TypeIndex._build_owner_seed_index`'s subject -- the qualified
    name included, since a producer recording ``name="Widget"`` beside
    ``qualified_name="ns::Widget"`` makes ``owner_class_of`` answer with the
    qualified one.
    """
    owner = owner_class_of(fn)
    if owner:
        link(node, index.resolve_owner_class(owner))


def _link_decl_aliases(
    name: str,
    mangled: str,
    node: str,
    nodes: set[str],
    edges: set[tuple[str, str]],
) -> None:
    """Record every spelling a finding may name this declaration by.

    :func:`~abicheck.surface._symbol_keys` is the repo's own answer to "what
    can a ``Change.symbol`` look like for this declaration" (demangled name,
    mangled name, bare ``::`` tail), so the alias set is taken from it rather
    than re-derived -- a replayed root lookup that recognized fewer spellings
    than the live one would silently lose roots and report findings out of a
    contract they are actually in.
    """
    canonical = node.partition(":")[2]
    for key in _symbol_keys(name, mangled):
        if key == canonical:
            continue
        alias = _alias_node(key)
        nodes.add(alias)
        edges.add((alias, node))


def _link_type_aliases(
    type_obj: RecordType | EnumType,
    node: str,
    nodes: set[str],
    edges: set[tuple[str, str]],
) -> None:
    """Record a type's *other* spelling as an alias of its canonical node.

    A record/enum is keyed by :func:`_type_identity` -- ``qualified_name``
    when the producer recorded one, else ``name`` -- so the spelling that
    needs an alias is the bare leaf, the one two records in different
    namespaces can share. Both spellings reach a node here so a finding
    naming either one resolves; only the *canonical* one identifies it.
    """
    identity = _type_identity(type_obj)
    spellings = {type_obj.name}
    if "::" in identity:
        # The bare ``::`` tail is a lookup key in
        # ``_index_surface_types``/``_type_identifiers`` on both sides, so it
        # has to be one here too -- and not only so a finding naming it
        # resolves: a tail shared by two records is precisely what makes a
        # *qualified* candidate unconfirmable live (``_confirmed_type_matches``
        # rejects "a qualified name whose own trailing tail is ambiguous"),
        # and the replay can only see that collision if the shared tail is a
        # spelling in the graph at all. With a DWARF producer, which bakes the
        # whole path into ``name``, nothing else records it (Codex review,
        # fresh evidence).
        spellings.add(identity.rsplit("::", 1)[1])
    for spelling in spellings:
        if not spelling or spelling == identity:
            continue
        alias = _alias_node(spelling)
        nodes.add(alias)
        edges.add((alias, node))


# --------------------------------------------------------------------------
# Provider records.
# --------------------------------------------------------------------------


def content_digest(payload: object) -> str:
    """A stable content digest of an already-canonicalized observation.

    Public so a consumer assembling a *configuration* receipt from this
    ledger (:mod:`abicheck.contract_context`) digests overlay content the
    same way the ledger's own provider records do, instead of introducing a
    second, silently-divergent convention.
    """
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _domain_identity(snap: AbiSnapshot) -> str:
    return f"{snap.library}@{snap.version}"


def _public_header_declarations(snap: AbiSnapshot, surf: PublicSurface) -> list[str]:
    """Canonical node ids of the header provider's observed roots.

    Roots are the ``Visibility.PUBLIC`` declarations
    :func:`~abicheck.surface._seed_public_roots` seeds the live closure from,
    recorded as ``decl:`` node ids so a replay can start the same walk from
    the persisted graph. Public *types* are deliberately not listed: they are
    the closure's *result*, not its roots, and Section 5.1 keeps the
    mode-dependent closure in the decision receipt.
    """
    out: list[str] = []
    for fn, key in zip(snap.functions, _function_node_keys(snap), strict=True):
        if fn.visibility == Visibility.PUBLIC:
            out.append(_decl_node(key))
    for var in snap.variables:
        if var.visibility == Visibility.PUBLIC:
            out.append(_decl_node(_canonical_decl_key(var.name, var.mangled)))
    return out


def _export_table_declarations(snap: AbiSnapshot, exports: ExportSurface) -> list[str]:
    """Canonical node ids of the declarations the export table rooted.

    Derived by asking which declarations
    :func:`~abicheck.export_surface.compute_export_surface` actually matched --
    via its own ``root_identities`` set, the one place that answer lives --
    rather than re-implementing the platform-specific underscore/mangling
    normalization that produced it.

    Keyed by *linker identity*, never by intersecting the alias-expanded
    ``export_symbols``: a binary exporting the C symbol ``foo`` alongside an
    unexported ``ns::foo`` puts the bare tail ``"foo"`` in both declarations'
    alias sets, so an intersection persists the unexported C++ declaration as
    an export root and a re-evaluation then reports it in a contract it is
    provably out of (Codex review, fresh evidence -- the identical trap
    ``export_surface._unresolved_type_edges`` already documents avoiding).
    """
    out: list[str] = []
    for fn, key in zip(snap.functions, _function_node_keys(snap), strict=True):
        # Rootness is decided by *linker* identity (what the export table
        # matched), while the node recorded is the graph's own key for that
        # declaration -- the two differ only for an unmangled overload, where
        # every member of the group shares one linker identity and so is
        # rooted or not as a group, exactly as live roots them.
        identity = _canonical_decl_key(fn.name, fn.mangled)
        if identity and identity in exports.root_identities:
            out.append(_decl_node(key))
    for var in snap.variables:
        identity = _canonical_decl_key(var.name, var.mangled)
        if identity and identity in exports.root_identities:
            out.append(_decl_node(identity))
    return out


def _public_header_status(
    surf: PublicSurface,
) -> tuple[EvidenceProviderStatus, EvidenceCompleteness, str | None]:
    if not surf.resolvable:
        return (
            EvidenceProviderStatus.UNAVAILABLE,
            EvidenceCompleteness.NOT_STARTED,
            "header_surface_unresolvable",
        )
    if not surf.has_provenance:
        return (
            EvidenceProviderStatus.AVAILABLE,
            EvidenceCompleteness.PARTIAL,
            "no_declaration_provenance",
        )
    if not surf.has_typed_roots:
        return (
            EvidenceProviderStatus.AVAILABLE,
            EvidenceCompleteness.PARTIAL,
            "no_typed_public_roots",
        )
    return (EvidenceProviderStatus.AVAILABLE, EvidenceCompleteness.COMPLETE, None)


def _export_table_status(
    exports: ExportSurface,
) -> tuple[EvidenceProviderStatus, EvidenceCompleteness, str | None]:
    """Map the export provider's own completeness guards onto the ledger.

    Reads the same five conditions
    :attr:`~abicheck.export_surface.ExportSurface.exclusion_is_provable` folds
    together, but reports *which* one failed -- the ledger's whole purpose is
    to say why a domain was not closed, which that single boolean cannot.
    """
    if not exports.resolvable:
        return (
            EvidenceProviderStatus.UNAVAILABLE,
            EvidenceCompleteness.NOT_STARTED,
            "export_table_absent",
        )
    if not exports.has_roots:
        return (
            EvidenceProviderStatus.AVAILABLE,
            EvidenceCompleteness.PARTIAL,
            "no_export_roots_resolved",
        )
    if not exports.all_roots_typed:
        return (
            EvidenceProviderStatus.AVAILABLE,
            EvidenceCompleteness.PARTIAL,
            "untyped_export_roots",
        )
    if exports.unmatched_exports:
        return (
            EvidenceProviderStatus.AVAILABLE,
            EvidenceCompleteness.PARTIAL,
            "unmatched_exports",
        )
    if exports.unresolved_type_edges:
        return (
            EvidenceProviderStatus.AVAILABLE,
            EvidenceCompleteness.PARTIAL,
            "unresolved_type_edges",
        )
    return (EvidenceProviderStatus.AVAILABLE, EvidenceCompleteness.COMPLETE, None)


def _identity_coverage(ambiguous: set[str]) -> EvidenceCompleteness:
    """Section 4.2's ``identity_coverage`` facet.

    "Ambiguous mangled/demangled/type identity is partial for affected
    entities" -- an ambiguous bare tail is exactly that, so a surface carrying
    any is ``PARTIAL`` rather than ``COMPLETE``.
    """
    return EvidenceCompleteness.PARTIAL if ambiguous else EvidenceCompleteness.COMPLETE


def public_header_evidence(
    snap: AbiSnapshot, surf: PublicSurface, side: str
) -> ProviderEvidenceEntry:
    """One side's ``public_header`` provider entry, graph included.

    The raw type graph rides on this provider rather than being duplicated
    onto every entry: it is a *declaration-parse* observation (records,
    fields, bases, typedefs and the signatures that reference them), which the
    export table -- a list of linker symbols and nothing else -- does not
    independently observe. :func:`~abicheck.contract_replay.reevaluate_from_evidence`
    reads it from here for every domain, which is correct precisely because
    the graph is policy-independent.
    """
    declarations = _public_header_declarations(snap, surf)
    status, completeness, reason = _public_header_status(surf)
    graph = build_type_graph(snap)
    record = EvidenceSearchRecord(
        id=evidence_record_id(PROVIDER_PUBLIC_HEADER, side),
        provider=PROVIDER_PUBLIC_HEADER,
        side=side,
        domain_kind="public_headers",
        entity_class="declaration",
        domain_identity=_domain_identity(snap),
        status=status,
        completeness=completeness,
        requested_scope=("declarations", "types"),
        # Section 4.2's "requested scope equals searched scope" clause, made
        # checkable rather than asserted: an unresolvable header surface
        # searched neither, so the two tuples differ and no consumer can read
        # the record as a completed search.
        searched_scope=(("declarations", "types") if surf.resolvable else ()),
        identity_coverage=_identity_coverage(surf.ambiguous_type_names),
        ambiguous_identities=tuple(sorted(surf.ambiguous_type_names)),
        # No variant source is declared anywhere in this build (no
        # ``--variant``/generated-header variant set exists yet), so the
        # configuration-coverage facet Section 4.2's closed-world rule
        # requires has genuinely not started. This is the ledger-level
        # counterpart of ``contract_evaluation.py``'s own documented
        # under-claim: with this facet never ``COMPLETE``, the closed-world
        # rule cannot be satisfied, which is exactly why ``UNKNOWN_UNPROVEN``
        # is never emitted. Recording it honestly is what lets a future
        # variant provider flip that on evidence rather than by assertion.
        configuration_coverage=EvidenceCompleteness.NOT_STARTED,
        reason_code=reason,
        input_identity=InputIdentity(
            sha256=content_digest(
                {
                    "declarations": sorted(set(declarations)),
                    "nodes": list(graph.nodes),
                    "edges": [list(e) for e in graph.edges],
                }
            ),
            path=snap.library or None,
        ),
    )
    return ProviderEvidenceEntry(
        record=record, declarations=tuple(declarations), type_graph=graph
    )


def export_table_evidence(
    snap: AbiSnapshot, exports: ExportSurface, side: str
) -> ProviderEvidenceEntry:
    """One side's ``export_table`` provider entry.

    Carries no type graph of its own -- see :func:`public_header_evidence`.
    ``manifests`` lists the observed export-table names (``elf``/``pe``/
    ``macho``), the closest thing this provider has to an enumerable manifest
    of what it searched.
    """
    tables = observed_exports_by_platform(snap) or {}
    declarations = _export_table_declarations(snap, exports)
    status, completeness, reason = _export_table_status(exports)
    record = EvidenceSearchRecord(
        id=evidence_record_id(PROVIDER_EXPORT_TABLE, side),
        provider=PROVIDER_EXPORT_TABLE,
        side=side,
        domain_kind="exports",
        entity_class="declaration",
        domain_identity=_domain_identity(snap),
        status=status,
        completeness=completeness,
        requested_scope=("export_table",),
        searched_scope=("export_table",) if exports.resolvable else (),
        identity_coverage=_identity_coverage(exports.ambiguous_type_names),
        ambiguous_identities=tuple(sorted(exports.ambiguous_type_names)),
        configuration_coverage=EvidenceCompleteness.NOT_STARTED,
        reason_code=reason,
        input_identity=InputIdentity(
            sha256=content_digest(
                {
                    "tables": {k: sorted(v) for k, v in sorted(tables.items())},
                    "declarations": sorted(set(declarations)),
                }
            ),
            path=snap.library or None,
        ),
    )
    return ProviderEvidenceEntry(
        record=record,
        declarations=tuple(declarations),
        manifests=tuple(sorted(tables)),
    )


def _overlay_evidence(
    provider: str,
    domain_kind: str,
    side: str,
    entries: Iterable[str],
) -> ProviderEvidenceEntry:
    """One explicitly-configured overlay's entry (manifest / forced-public).

    Recorded on *both* sides with identical content: an overlay is a
    run-level input applied to whichever side a finding is judged on, but
    ``EvidenceSearchRecord.side`` is mandatory, and a decision on an
    old-side-authoritative finding must be able to cite a record that exists
    for its own side (ADR-049 D4: the authoritative side's evidence is what
    decides). Duplicating an exactly-equal record per side is the honest
    encoding of "this input covers both", and costs one small entry.
    """
    items = sorted(set(entries))
    record = EvidenceSearchRecord(
        id=evidence_record_id(provider, side),
        provider=provider,
        side=side,
        domain_kind=domain_kind,
        entity_class="declaration",
        status=EvidenceProviderStatus.AVAILABLE,
        # An explicit list is an exact, closed, enumerable domain: every entry
        # in it was read, so the search over *it* is complete. That says
        # nothing about the domain it overlays, which its own provider
        # reports separately (Section 4.1: failures are scoped per provider).
        completeness=EvidenceCompleteness.COMPLETE,
        requested_scope=(domain_kind,),
        searched_scope=(domain_kind,),
        identity_coverage=EvidenceCompleteness.COMPLETE,
        configuration_coverage=EvidenceCompleteness.NOT_STARTED,
        input_identity=InputIdentity(sha256=content_digest(items)),
    )
    return ProviderEvidenceEntry(record=record, manifests=tuple(items))


def collect_contract_evidence(
    old: AbiSnapshot,
    new: AbiSnapshot,
    surf_old: PublicSurface,
    surf_new: PublicSurface,
    *,
    exports_old: ExportSurface | None = None,
    exports_new: ExportSurface | None = None,
    public_surface_allowlist: Iterable[str] | None = None,
    force_public_symbols: Iterable[str] | None = None,
) -> ContractEvidenceBlock:
    """The observed provider ledger for one comparison (plan Section 4.1).

    ``exports_old``/``exports_new`` are optional only because computing them
    costs a full export-table match the caller may already know it does not
    need; when omitted, the ``export_table`` provider is simply absent from
    the block rather than recorded as failed -- "not consulted" and "consulted
    and unavailable" are different facts, and Section 4.1 forbids persisting
    "this provider was required under one policy" as if it were observed.
    """
    entries: list[ProviderEvidenceEntry] = [
        public_header_evidence(old, surf_old, "old"),
        public_header_evidence(new, surf_new, "new"),
    ]
    if exports_old is not None:
        entries.append(export_table_evidence(old, exports_old, "old"))
    if exports_new is not None:
        entries.append(export_table_evidence(new, exports_new, "new"))
    # `is not None`: a manifest committing to zero exports is a selected
    # source that scopes everything out, not an absent one -- the same
    # distinction `post_processing` and `SuppressionConfig` already draw
    # (Codex review, fresh evidence).
    if public_surface_allowlist is not None:
        for side in _SIDES:
            entries.append(
                _overlay_evidence(
                    PROVIDER_POST_MANIFEST,
                    "committed_exports",
                    side,
                    public_surface_allowlist,
                )
            )
    if force_public_symbols:
        for side in _SIDES:
            entries.append(
                _overlay_evidence(
                    PROVIDER_FORCED_PUBLIC,
                    "explicit_public_symbols",
                    side,
                    force_public_symbols,
                )
            )
    return ContractEvidenceBlock(providers=tuple(entries))


# --------------------------------------------------------------------------
# Reading the persisted graph back (Phase 4's replay/re-evaluation).
# --------------------------------------------------------------------------


def resolve_graph_node(graph: TypeGraphSnapshot, spelling: str) -> set[str]:
    """Canonical nodes a *spelling* names in an already-persisted graph.

    Tries the three encodings a finding can name an entity by -- a
    declaration key, a type name, and a lookup alias of either -- and follows
    an ``alias:`` node's own edges to the canonical node(s) it stands for. A
    spelling naming nothing returns an empty set, never a guess: the caller
    (:mod:`abicheck.contract_replay`) reports that as unresolved rather than
    proving an entity out of a contract whose graph never knew it.
    """
    out: set[str] = set()
    nodes = set(graph.nodes)
    for candidate in (
        _decl_node(spelling),
        _record_node(spelling),
        _enum_node(spelling),
        _typedef_node(spelling),
    ):
        if candidate in nodes:
            out.add(candidate)
    alias = _alias_node(spelling)
    if alias in nodes:
        out |= {dst for src, dst in graph.edges if src == alias}
    return out


def graph_node_index(
    graph: TypeGraphSnapshot, *, follow_aliases: bool = True
) -> dict[str, set[str]]:
    """Spelling -> canonical nodes, built once for a whole persisted graph.

    :func:`resolve_graph_node` answers one spelling by rescanning every node
    and edge, which is O(nodes + edges) per call -- fine for a one-off lookup,
    but a re-evaluation asks it once per spelling per finding over a graph
    this module documents as whole-snapshot (CodeRabbit review). A caller
    resolving many spellings against one graph builds this index instead; the
    two agree by construction, since this is the same resolution rule stated
    as a forward map rather than a search.

    *follow_aliases* is what a caller whose own live matching is **exact**
    turns off. ``--post-manifest``'s committed-export allowlist is matched
    against ``Change.symbol`` verbatim by the live evaluator, so resolving
    its entries through the alias tier here would root a *different*
    declaration than the run did -- an unexported ``ns::foo`` carries the
    bare alias ``foo`` that an exported C ``foo`` also owns (Codex review,
    fresh evidence). Alias-following stays on for every other caller,
    including a finding's own spelling, where recognizing fewer encodings
    than the live lookup would silently lose roots.
    """
    index: dict[str, set[str]] = {}
    alias_edges: dict[str, set[str]] = {}
    known = set(graph.nodes)
    for src, dst in graph.edges:
        # `resolve_graph_node` follows an alias edge only when the `alias:`
        # node itself is present, so this index must too: a hand-authored or
        # truncated graph carrying an edge whose source node was dropped would
        # otherwise resolve here and not there, and the two are documented to
        # agree by construction (CodeRabbit review).
        if src.startswith("alias:") and src in known:
            alias_edges.setdefault(src, set()).add(dst)
    for node in graph.nodes:
        kind, _, identity = node.partition(":")
        if kind == "alias" or not identity:
            continue
        index.setdefault(identity, set()).add(node)
    if follow_aliases:
        for alias, targets in alias_edges.items():
            index.setdefault(alias.split(":", 1)[1], set()).update(targets)
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


# --------------------------------------------------------------------------
# Decision -> evidence references (Phase 3's gate).
# --------------------------------------------------------------------------


#: Reason codes whose decision genuinely rests on *both* sides' evidence,
#: keyed by the domain that produced them. Only the ``public`` domain has
#: one: ``classify_change_surface`` runs a membership classification against
#: ``surface_unions(surf_old, surf_new)``, so both header providers really
#: were consulted.
#:
#: ``exports`` deliberately has none. ``_exports_mode_decision`` selects one
#: :class:`~abicheck.export_surface.ExportSurface` by ADR-049 D4's side rule
#: and decides from it alone -- so citing the other side would claim a
#: decision rests on evidence it never read, and would be actively
#: misleading when that side's provider is unavailable (an exported removal
#: is conclusive from the old table even if the new one failed to parse;
#: Codex review, fresh evidence). Every other reason -- an unresolvable
#: surface, an ambiguous identity, a terminal exclusion already recorded on
#: the finding -- is settled by the authoritative side alone in both domains.
_TWO_SIDED_REASONS_BY_MODE: Mapping[ContractMode, frozenset[str]] = {
    ContractMode.PUBLIC: frozenset(
        {"public_root_membership", "closed_domain_no_commitment"}
    ),
    ContractMode.EXPORTS: frozenset(),
    ContractMode.ALL: frozenset(),
}


def evidence_refs_for_reason(
    reason_code: str,
    *,
    mode: ContractMode,
    block: ContractEvidenceBlock,
    authoritative_side: str,
) -> tuple[str, ...]:
    """Which provider records a decision with *reason_code* rests on.

    Phase 3's gate is "every shadow delta has evidence and stable identity";
    this is the "has evidence" half made explicit rather than implied by the
    reason string. The mapping follows what
    :func:`~abicheck.contract_evaluation.evaluate_change_contract_relevance`
    actually consults:

    - a non-entity (``NOT_APPLICABLE``) finding consults no provider at all,
      and correctly cites none -- its classification is a property of the
      ``ChangeKind``, not of any observation;
    - a finding decided by ``--used-by``/``--required-symbol`` scoping cites
      the run-level reference, since that decision is made outside any
      comparison that holds an evidence block;
    - every other entity decision cites the root provider for the selected
      domain, on the authoritative side (ADR-049 D4) -- plus the other side
      only where the evaluator genuinely read it, which is the ``public``
      domain's membership classification (``classify_change_surface`` runs
      against the union of both surfaces). See
      :data:`_TWO_SIDED_REASONS_BY_MODE` for why ``exports`` cites one side.

    Two overlay-decided cases (``--public-symbol``, ``--post-manifest``) are
    indistinguishable by reason code alone and are attributed one level up,
    in :func:`~abicheck.contract_evaluation.evidence_refs_for_change`, which
    owns the matching rules -- callers stamping a real finding should use
    that function rather than this one.

    A reference is emitted only when the block really carries that record, so
    a reference never dangles.
    """
    mode = coerce_contract_mode(mode)
    available = {(e.record.provider, e.record.side) for e in block.providers}

    def ref(provider: str, side: str) -> tuple[str, ...]:
        return (
            (evidence_record_id(provider, side),)
            if (provider, side) in available
            else ()
        )

    if reason_code == "non_entity_finding":
        return ()
    if reason_code == "explicit_consumer_or_required_symbol_evidence":
        return (EXPLICIT_SCOPE_EVIDENCE_REF,)
    other_side = "new" if authoritative_side == "old" else "old"
    if mode is ContractMode.ALL:
        # `all` needs no root/closure evidence by definition (D2), so a
        # decision under it cites only the identity evidence that placed the
        # finding as an entity at all -- the declaration parse, i.e. the
        # header provider, on the authoritative side.
        return ref(PROVIDER_PUBLIC_HEADER, authoritative_side)
    root_provider = DOMAIN_ROOT_PROVIDER[mode] or PROVIDER_PUBLIC_HEADER
    refs = ref(root_provider, authoritative_side)
    if reason_code in _TWO_SIDED_REASONS_BY_MODE[mode]:
        refs += ref(root_provider, other_side)
    return refs


def validate_decision_evidence(
    refs: Iterable[str], block: ContractEvidenceBlock | None
) -> None:
    """Raise ``ValueError`` for a reference naming no known record.

    The executable half of Phase 3's "every shadow delta has evidence" gate:
    a dangling reference is worse than no reference, since a consumer reading
    the ledger cannot tell it apart from a record that failed to serialize.
    Run-level references (:data:`RUN_LEVEL_EVIDENCE_REFS`) are always valid --
    they name a fact of the run, not an entry in any block.
    """
    known = set(RUN_LEVEL_EVIDENCE_REFS)
    if block is not None:
        known |= {e.record.id for e in block.providers}
    dangling = sorted(set(refs) - known)
    if dangling:
        raise ValueError(
            f"contract-evidence references name no known provider record: "
            f"{dangling!r} (known: {sorted(known)!r})"
        )
