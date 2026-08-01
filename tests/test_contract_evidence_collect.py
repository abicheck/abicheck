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

"""ADR-049 Phase 3: the observed provider ledger and its raw type graph."""

from __future__ import annotations

import pytest

from abicheck.contract_evidence import TypeGraphSnapshot
from abicheck.contract_evidence_collect import (
    EXPLICIT_SCOPE_EVIDENCE_REF,
    PROVIDER_EXPORT_TABLE,
    PROVIDER_FORCED_PUBLIC,
    PROVIDER_POST_MANIFEST,
    PROVIDER_PUBLIC_HEADER,
    build_type_graph,
    closure_from_graph,
    collect_contract_evidence,
    evidence_record_id,
    evidence_refs_for_reason,
    graph_node_index,
    resolve_graph_node,
    validate_decision_evidence,
)
from abicheck.contract_relevance_types import (
    ContractMode,
    EvidenceCompleteness,
    EvidenceProviderStatus,
)
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.export_surface import compute_export_surface
from abicheck.model import (
    AbiSnapshot,
    EnumType,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.surface import compute_public_surface


def _snap(
    version: str = "1",
    *,
    functions: list[Function] | None = None,
    variables: list[Variable] | None = None,
    types: list[RecordType] | None = None,
    enums: list[EnumType] | None = None,
    typedefs: dict[str, str] | None = None,
    elf: ElfMetadata | None = None,
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libdemo.so.1",
        version=version,
        functions=functions or [],
        variables=variables or [],
        types=types or [],
        enums=enums or [],
        typedefs=typedefs or {},
        elf=elf,
    )


def _public_fn(
    name: str, *, ret: str = "void", params: list[str] | None = None
) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params or [])],
        visibility=Visibility.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )


class TestTypeGraph:
    def test_declaration_reaches_its_signature_types(self) -> None:
        snap = _snap(
            functions=[_public_fn("api", params=["Widget *"])],
            types=[RecordType(name="Widget", kind="struct", fields=[])],
        )
        graph = build_type_graph(snap)
        assert "decl:api" in graph.nodes
        assert "record:Widget" in graph.nodes
        assert ("decl:api", "record:Widget") in graph.edges

    def test_field_and_base_edges_are_followed_transitively(self) -> None:
        snap = _snap(
            functions=[_public_fn("api", ret="Outer *")],
            types=[
                RecordType(
                    name="Outer",
                    kind="struct",
                    fields=[TypeField(name="inner", type="Inner")],
                ),
                RecordType(
                    name="Inner",
                    kind="struct",
                    bases=["Root"],
                    fields=[],
                ),
                RecordType(name="Root", kind="struct", fields=[]),
            ],
        )
        closure = closure_from_graph(build_type_graph(snap), ["decl:api"])
        assert {"record:Outer", "record:Inner", "record:Root"} <= closure

    def test_typedef_target_is_an_edge(self) -> None:
        snap = _snap(
            functions=[_public_fn("api", ret="Alias")],
            types=[RecordType(name="Real", kind="struct", fields=[])],
            typedefs={"Alias": "Real"},
        )
        closure = closure_from_graph(build_type_graph(snap), ["decl:api"])
        assert "typedef:Alias" in closure
        assert "record:Real" in closure

    def test_unreferenced_type_is_outside_the_closure(self) -> None:
        snap = _snap(
            functions=[_public_fn("api", ret="Kept *")],
            types=[
                RecordType(name="Kept", kind="struct", fields=[]),
                RecordType(name="Orphan", kind="struct", fields=[]),
            ],
        )
        graph = build_type_graph(snap)
        closure = closure_from_graph(graph, ["decl:api"])
        assert "record:Orphan" in graph.nodes  # observed...
        assert "record:Orphan" not in closure  # ...but not reachable

    def test_graph_is_policy_independent(self) -> None:
        """A private declaration is still observed -- the graph is raw facts.

        This is what makes one collected block valid input to a later
        re-evaluation under a different contract mode (plan Section 5.1).
        """
        snap = _snap(
            functions=[
                Function(
                    name="internal",
                    mangled="internal",
                    return_type="Secret *",
                    visibility=Visibility.HIDDEN,
                    origin=ScopeOrigin.PRIVATE_HEADER,
                )
            ],
            types=[RecordType(name="Secret", kind="struct", fields=[])],
        )
        graph = build_type_graph(snap)
        assert ("decl:internal", "record:Secret") in graph.edges

    def test_graph_is_order_independent(self) -> None:
        types = [
            RecordType(name="A", kind="struct", fields=[TypeField(name="b", type="B")]),
            RecordType(name="B", kind="struct", fields=[]),
        ]
        forward = build_type_graph(_snap(types=types))
        reversed_ = build_type_graph(_snap(types=list(reversed(types))))
        assert forward == reversed_

    def test_alias_spellings_resolve_to_the_canonical_node(self) -> None:
        snap = _snap(functions=[_public_fn("ns::api")])
        graph = build_type_graph(snap)
        assert resolve_graph_node(graph, "ns::api") == {"decl:ns::api"}
        # The bare tail is an alias of the same declaration.
        assert resolve_graph_node(graph, "api") == {"decl:ns::api"}

    def test_unknown_spelling_resolves_to_nothing(self) -> None:
        graph = build_type_graph(_snap(functions=[_public_fn("api")]))
        assert resolve_graph_node(graph, "nope") == set()

    def test_method_reaches_its_owner_class(self) -> None:
        """A method's enclosing class is reachable even with no class-typed
        signature -- the same seed both live surfaces make.

        Without this edge a replay walking the persisted graph would find the
        owner unreachable and could turn the live ``IN_CONTRACT`` decision
        into ``PROVEN_OUT_OF_CONTRACT`` -- a strengthened decision, which
        ``compare_decisions`` treats as unsound.
        """
        snap = _snap(
            functions=[_public_fn("Foo::bar")],
            types=[
                RecordType(
                    name="Foo",
                    kind="struct",
                    fields=[TypeField(name="member", type="Payload")],
                ),
                RecordType(name="Payload", kind="struct", fields=[]),
            ],
        )
        graph = build_type_graph(snap)
        assert ("decl:Foo::bar", "record:Foo") in graph.edges
        closure = closure_from_graph(graph, ["decl:Foo::bar"])
        # ...and the owner's own field closure comes with it, matching what
        # `_walk_type_closure` reaches from the same seed.
        assert {"record:Foo", "record:Payload"} <= closure

    def test_owner_edges_require_an_exact_record_identity(self) -> None:
        """A namespace function does not link to a same-tailed record.

        ``owner_class_of`` returns ``"api"`` for a namespace function
        ``api::run()`` -- namespace noise, not a class. Resolving that
        permissively matched an unrelated ``other::api`` record by its bare
        tail and pulled it into the *export* closure, turning a live
        ``PROVEN_OUT_OF_CONTRACT`` into a replayed ``IN_CONTRACT`` (Codex
        review, fresh evidence). ``export_surface`` seeds owners by exact
        identity for exactly this reason; the graph mirrors it.
        """
        snap = _snap(
            functions=[_public_fn("api::run")],
            types=[RecordType(name="other::api", kind="struct", fields=[])],
        )
        graph = build_type_graph(snap)
        assert ("decl:api::run", "record:other::api") not in graph.edges
        assert "record:other::api" not in closure_from_graph(graph, ["decl:api::run"])

    def test_unmangled_overloads_do_not_share_one_node(self) -> None:
        """A display name is not a declaration identity.

        With no mangled symbol recorded, a public ``over()`` and a private
        ``over(Secret *)`` fell onto one ``decl:`` node, so the public root
        inherited the private overload's edge to ``Secret`` and a
        re-evaluation answered ``IN_CONTRACT`` where the live evaluator --
        which walks each declaration object separately -- proved it out of
        contract (Codex review, fresh evidence).
        """
        snap = _snap(
            functions=[
                Function(
                    name="over",
                    mangled="",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
                Function(
                    name="over",
                    mangled="",
                    return_type="int",
                    params=[Param(name="s", type="Secret *")],
                    visibility=Visibility.HIDDEN,
                    origin=ScopeOrigin.PRIVATE_HEADER,
                ),
            ],
            types=[RecordType(name="Secret", kind="struct", fields=[])],
        )
        graph = build_type_graph(snap)
        public_root, private_root = (
            "decl:over()->int",
            "decl:over(Secret *)->int",
        )
        assert {public_root, private_root} <= set(graph.nodes)
        assert "record:Secret" not in closure_from_graph(graph, [public_root])
        assert "record:Secret" in closure_from_graph(graph, [private_root])
        # The shared display name still resolves -- to both, since with no
        # linker identity recorded it genuinely names either one.
        assert resolve_graph_node(graph, "over") == {public_root, private_root}

    def test_a_fallback_key_yields_to_a_recorded_linker_identity(self) -> None:
        """The two identity tiers can collide with each other, not just within.

        A private header-only ``foo(Secret *)`` beside a public declaration
        whose recorded ``mangled`` is literally ``foo`` both fell on
        ``decl:foo``, so the public root inherited the private overload's
        edge to ``Secret`` -- the same merge as the all-unmangled case, one
        tier over (Codex review, fresh evidence).
        """
        snap = _snap(
            functions=[
                Function(
                    name="foo",
                    mangled="foo",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
                Function(
                    name="foo",
                    mangled="",
                    return_type="int",
                    params=[Param(name="s", type="Secret *")],
                    visibility=Visibility.HIDDEN,
                    origin=ScopeOrigin.PRIVATE_HEADER,
                ),
            ],
            types=[RecordType(name="Secret", kind="struct", fields=[])],
        )
        graph = build_type_graph(snap)
        assert "decl:foo" in graph.nodes  # the one with a linker identity
        assert "record:Secret" not in closure_from_graph(graph, ["decl:foo"])
        assert "decl:foo(Secret *)->int" in graph.nodes

    def test_a_lone_unmangled_declaration_keeps_its_plain_name(self) -> None:
        """The discriminator is a tie-break, not a new encoding.

        ``contract_replay._without_shared_export_aliases`` exempts a spelling
        that is a root's *own* node identity from its alias pruning, so
        refining an unambiguous name would prune a genuine export root --
        the same strengthening, one corner over.
        """
        snap = _snap(
            functions=[
                Function(
                    name="solo",
                    mangled="",
                    return_type="int",
                    params=[Param(name="s", type="Secret *")],
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            types=[RecordType(name="Secret", kind="struct", fields=[])],
        )
        graph = build_type_graph(snap)
        assert "decl:solo" in graph.nodes
        assert resolve_graph_node(graph, "solo") == {"decl:solo"}

    def test_identical_unmangled_declarations_still_share_a_node(self) -> None:
        """Merging is only wrong when it merges *different* declarations.

        Two entries agreeing on name, parameters and return type reach the
        same types and the same owner class, so one node loses nothing.
        """

        def _decl() -> Function:
            return Function(
                name="dup",
                mangled="",
                return_type="int",
                params=[Param(name="s", type="Secret *")],
                visibility=Visibility.PUBLIC,
                origin=ScopeOrigin.PUBLIC_HEADER,
            )

        graph = build_type_graph(
            _snap(
                functions=[_decl(), _decl()],
                types=[RecordType(name="Secret", kind="struct", fields=[])],
            )
        )
        assert "decl:dup" in graph.nodes
        assert [n for n in graph.nodes if n.startswith("decl:")] == ["decl:dup"]

    def test_a_qualified_record_identity_seeds_its_owner_edge(self) -> None:
        """``owner_class_of`` answers with the *qualified* identity.

        A castxml/clang producer stores a class as ``name="Widget"`` with
        ``qualified_name="ns::Widget"``, so a method ``ns::Widget::draw``
        yields the owner ``"ns::Widget"`` -- which an identity set keyed on
        ``name`` alone never contained, dropping the edge and letting a
        replay prove a live ``IN_CONTRACT`` owner out of contract (Codex
        review, fresh evidence). ``export_surface``'s own owner map registers
        the qualified spelling; the graph mirrors it.
        """
        snap = _snap(
            functions=[_public_fn("ns::Widget::draw")],
            types=[
                RecordType(
                    name="Widget",
                    qualified_name="ns::Widget",
                    kind="struct",
                    fields=[TypeField(name="member", type="Payload")],
                ),
                RecordType(name="Payload", kind="struct", fields=[]),
            ],
        )
        graph = build_type_graph(snap)
        assert ("decl:ns::Widget::draw", "record:ns::Widget") in graph.edges
        # The record is keyed by its qualified identity; the bare leaf two
        # namespaces could share is an alias of it, not its identity.
        assert ("alias:Widget", "record:ns::Widget") in graph.edges
        closure = closure_from_graph(graph, ["decl:ns::Widget::draw"])
        assert {"record:ns::Widget", "record:Payload"} <= closure

    def test_a_qualified_typedef_resolves_only_by_its_exact_key(self) -> None:
        """A typedef has no bare-tail spelling, unlike a record or an enum.

        ``_walk_type_closure`` follows one with ``snap.typedefs.get(name)`` --
        a plain dict lookup, no tail fallback -- so registering the tail let
        a signature spelling bare ``Alias`` reach a qualified
        ``ns::Alias -> Secret``, and a private ``Secret`` change the live
        evaluator proved out of contract replayed as ``IN_CONTRACT`` (Codex
        review, fresh evidence).
        """
        snap = _snap(
            functions=[_public_fn("api", ret="Alias")],
            typedefs={"ns::Alias": "Secret"},
            types=[RecordType(name="Secret", kind="struct", fields=[])],
        )
        graph = build_type_graph(snap)
        assert "record:Secret" not in closure_from_graph(graph, ["decl:api"])
        # The exact key still resolves, which is what live matches on.
        exact = _snap(
            functions=[_public_fn("api", ret="ns::Alias")],
            typedefs={"ns::Alias": "Secret"},
            types=[RecordType(name="Secret", kind="struct", fields=[])],
        )
        assert "record:Secret" in closure_from_graph(
            build_type_graph(exact), ["decl:api"]
        )

    def test_an_enum_is_keyed_and_aliased_like_a_record(self) -> None:
        """``enum:`` is a first-class node kind, not a record afterthought.

        It carries the same bare-vs-qualified split (``EnumType`` has its own
        ``qualified_name``), is keyed by the same :func:`_type_identity`, and
        participates in the same ambiguity rule -- but nothing in this file
        constructed one, so every enum path shipped untested (self-review).
        """
        snap = _snap(
            functions=[_public_fn("api", ret="ns::Mode")],
            enums=[
                EnumType(
                    name="Mode",
                    qualified_name="ns::Mode",
                    members={"A": 0, "B": 1},
                )
            ],
        )
        graph = build_type_graph(snap)
        assert "enum:ns::Mode" in graph.nodes
        # Keyed by the qualified identity, with the bare leaf as an alias --
        # exactly the record rule.
        assert ("alias:Mode", "enum:ns::Mode") in graph.edges
        assert resolve_graph_node(graph, "Mode") == {"enum:ns::Mode"}
        assert "enum:ns::Mode" in closure_from_graph(graph, ["decl:api"])

    def test_a_record_and_an_enum_sharing_a_leaf_both_resolve(self) -> None:
        """Ambiguity spans the two kinds, which is why they share a set.

        ``_index_surface_types`` tallies record and enum entries into one
        count, so ``ns1::Mode`` (a record) and ``ns2::Mode`` (an enum)
        collide on the bare leaf exactly as two records would.
        """
        snap = _snap(
            types=[RecordType(name="Mode", qualified_name="ns1::Mode", kind="struct")],
            enums=[EnumType(name="Mode", qualified_name="ns2::Mode", members={"A": 0})],
        )
        graph = build_type_graph(snap)
        assert resolve_graph_node(graph, "Mode") == {
            "record:ns1::Mode",
            "enum:ns2::Mode",
        }
        surf = compute_public_surface(snap)
        assert "Mode" in surf.ambiguous_type_names
        # ...and the collision reaches the persisted record, which is what a
        # replay consults instead of re-deriving it.
        block = collect_contract_evidence(snap, snap, surf, surf)
        header = next(
            e for e in block.providers if e.record.provider == PROVIDER_PUBLIC_HEADER
        )
        assert "Mode" in header.record.ambiguous_identities

    def test_two_namespaces_sharing_a_leaf_are_two_nodes(self) -> None:
        """``ns1::Foo`` and ``ns2::Foo`` are not one type.

        A castxml/clang producer records both as the bare ``name="Foo"``, so
        keying the node on that merged two unrelated records into one and
        unioned their field edges -- an export rooted in ``ns1::Foo`` then
        reached ``ns2::Foo``'s own field (Codex review, confirmed with a
        minimal snapshot).
        """
        snap = _snap(
            types=[
                RecordType(
                    name="Foo",
                    qualified_name="ns1::Foo",
                    kind="struct",
                    fields=[TypeField(name="a", type="Public")],
                ),
                RecordType(
                    name="Foo",
                    qualified_name="ns2::Foo",
                    kind="struct",
                    fields=[TypeField(name="b", type="Secret")],
                ),
                RecordType(name="Public", kind="struct", fields=[]),
                RecordType(name="Secret", kind="struct", fields=[]),
            ],
        )
        graph = build_type_graph(snap)
        assert {"record:ns1::Foo", "record:ns2::Foo"} <= set(graph.nodes)
        one = closure_from_graph(graph, ["record:ns1::Foo"])
        assert "record:Public" in one
        assert "record:Secret" not in one
        # The shared leaf is an alias of *both* -- which is what makes a
        # finding that names it ambiguous rather than resolvable.
        assert resolve_graph_node(graph, "Foo") == {
            "record:ns1::Foo",
            "record:ns2::Foo",
        }

    def test_a_leaf_only_bare_name_still_seeds_no_owner(self) -> None:
        """Registering the qualified spelling must not re-admit the leaf.

        The bare ``name`` of a record carrying a differing ``qualified_name``
        is the leaf alone, so registering it lets an ``api::run()`` namespace
        fragment match ``other::api`` "exactly" -- the collision the exact
        rule exists to close, from the record side.
        """
        snap = _snap(
            functions=[_public_fn("api::run")],
            types=[
                RecordType(
                    name="api",
                    qualified_name="other::api",
                    kind="struct",
                    fields=[TypeField(name="secret", type="Secret")],
                ),
                RecordType(name="Secret", kind="struct", fields=[]),
            ],
        )
        closure = closure_from_graph(build_type_graph(snap), ["decl:api::run"])
        assert "record:api" not in closure
        assert "record:Secret" not in closure

    def test_free_function_seeds_no_owner(self) -> None:
        snap = _snap(
            functions=[_public_fn("api")],
            types=[RecordType(name="Unrelated", kind="struct", fields=[])],
        )
        closure = closure_from_graph(build_type_graph(snap), ["decl:api"])
        assert "record:Unrelated" not in closure


class TestGraphNodeIndex:
    def test_index_agrees_with_the_one_shot_resolver(self) -> None:
        snap = _snap(
            functions=[_public_fn("ns::api", ret="Widget *")],
            types=[RecordType(name="Widget", kind="struct", fields=[])],
        )
        graph = build_type_graph(snap)
        index = graph_node_index(graph)
        for spelling in ("ns::api", "api", "Widget"):
            assert index.get(spelling, set()) == resolve_graph_node(graph, spelling)

    def test_alias_edge_without_its_node_is_not_followed(self) -> None:
        """An edge whose ``alias:`` source node is absent resolves nowhere.

        ``resolve_graph_node`` requires the alias node itself to be present,
        so the index must too -- a truncated or hand-authored graph is
        exactly where the two would otherwise disagree, and they are
        documented to agree by construction (CodeRabbit review).
        """
        graph = TypeGraphSnapshot(
            nodes=("decl:ns::api",),
            edges=(("alias:api", "decl:ns::api"),),
        )
        assert resolve_graph_node(graph, "api") == set()
        assert graph_node_index(graph).get("api") is None


class TestProviderLedger:
    def test_header_provider_records_public_roots(self) -> None:
        snap = _snap(functions=[_public_fn("api")])
        block = collect_contract_evidence(
            snap, snap, compute_public_surface(snap), compute_public_surface(snap)
        )
        entry = next(
            e
            for e in block.providers
            if e.record.provider == PROVIDER_PUBLIC_HEADER and e.record.side == "old"
        )
        assert entry.declarations == ("decl:api",)
        assert entry.record.status is EvidenceProviderStatus.AVAILABLE
        assert entry.record.id == evidence_record_id(PROVIDER_PUBLIC_HEADER, "old")

    def test_unresolvable_header_surface_is_unavailable_and_not_started(self) -> None:
        """A snapshot with no public declarations resolves no surface.

        ``searched_scope`` must then differ from ``requested_scope`` -- plan
        Section 4.2's "requested scope equals searched scope" clause is only
        checkable if an incomplete search actually records a smaller one.
        """
        snap = _snap()
        block = collect_contract_evidence(
            snap, snap, compute_public_surface(snap), compute_public_surface(snap)
        )
        entry = next(e for e in block.providers if e.record.side == "old")
        assert entry.record.status is EvidenceProviderStatus.UNAVAILABLE
        assert entry.record.completeness is EvidenceCompleteness.NOT_STARTED
        assert entry.record.reason_code == "header_surface_unresolvable"
        assert entry.record.searched_scope == ()
        assert entry.record.requested_scope != entry.record.searched_scope

    def test_export_provider_absent_when_not_consulted(self) -> None:
        """ "Not consulted" is a different fact from "consulted and failed"."""
        snap = _snap(functions=[_public_fn("api")])
        block = collect_contract_evidence(
            snap, snap, compute_public_surface(snap), compute_public_surface(snap)
        )
        assert not [
            e for e in block.providers if e.record.provider == PROVIDER_EXPORT_TABLE
        ]

    def test_export_provider_records_observed_roots(self) -> None:
        elf = ElfMetadata(symbols=[ElfSymbol(name="api")])
        snap = _snap(functions=[_public_fn("api", params=["int"])], elf=elf)
        exports = compute_export_surface(snap)
        block = collect_contract_evidence(
            snap,
            snap,
            compute_public_surface(snap),
            compute_public_surface(snap),
            exports_old=exports,
            exports_new=exports,
        )
        entry = next(
            e
            for e in block.providers
            if e.record.provider == PROVIDER_EXPORT_TABLE and e.record.side == "new"
        )
        assert entry.declarations == ("decl:api",)
        assert entry.manifests == ("elf",)
        assert entry.record.domain_kind == "exports"

    def test_export_roots_are_matched_by_linker_identity(self) -> None:
        """An unexported ``ns::foo`` is not rooted by an exported C ``foo``.

        ``_symbol_keys`` gives the C++ declaration the bare tail ``"foo"``,
        which the exported C symbol also owns, so intersecting the
        alias-expanded ``export_symbols`` persisted both as export roots --
        and a re-evaluation then placed the unexported one inside a contract
        it is provably out of (Codex review, fresh evidence).
        """
        elf = ElfMetadata(symbols=[ElfSymbol(name="foo")])
        cxx = Function(
            name="ns::foo",
            mangled="_ZN2ns3fooEv",
            return_type="int",
            visibility=Visibility.PUBLIC,
            origin=ScopeOrigin.PUBLIC_HEADER,
        )
        snap = _snap(
            functions=[_public_fn("foo", ret="int"), cxx],
            elf=elf,
        )
        exports = compute_export_surface(snap)
        block = collect_contract_evidence(
            snap,
            snap,
            compute_public_surface(snap),
            compute_public_surface(snap),
            exports_old=exports,
            exports_new=exports,
        )
        entry = next(
            e
            for e in block.providers
            if e.record.provider == PROVIDER_EXPORT_TABLE and e.record.side == "old"
        )
        assert entry.declarations == ("decl:foo",)

    def test_export_provider_reports_which_guard_failed(self) -> None:
        """An export no declaration accounts for is reported by name.

        ``ExportSurface.exclusion_is_provable`` folds five conditions into one
        boolean; the ledger exists to say which of them was not met.
        """
        elf = ElfMetadata(
            symbols=[
                ElfSymbol(name="api"),
                ElfSymbol(name="mystery"),
            ]
        )
        snap = _snap(functions=[_public_fn("api", params=["int"])], elf=elf)
        exports = compute_export_surface(snap)
        block = collect_contract_evidence(
            snap,
            snap,
            compute_public_surface(snap),
            compute_public_surface(snap),
            exports_old=exports,
            exports_new=exports,
        )
        entry = next(
            e for e in block.providers if e.record.provider == PROVIDER_EXPORT_TABLE
        )
        assert entry.record.completeness is EvidenceCompleteness.PARTIAL
        assert entry.record.reason_code == "unmatched_exports"

    def test_overlays_are_recorded_for_both_sides(self) -> None:
        snap = _snap(functions=[_public_fn("api")])
        block = collect_contract_evidence(
            snap,
            snap,
            compute_public_surface(snap),
            compute_public_surface(snap),
            public_surface_allowlist=["api"],
            force_public_symbols=["other"],
        )
        for provider in (PROVIDER_POST_MANIFEST, PROVIDER_FORCED_PUBLIC):
            sides = {
                e.record.side for e in block.providers if e.record.provider == provider
            }
            assert sides == {"old", "new"}

    def test_collection_is_order_independent(self) -> None:
        snap_a = _snap(functions=[_public_fn("a"), _public_fn("b")])
        snap_b = _snap(functions=[_public_fn("b"), _public_fn("a")])
        block_a = collect_contract_evidence(
            snap_a,
            snap_a,
            compute_public_surface(snap_a),
            compute_public_surface(snap_a),
        )
        block_b = collect_contract_evidence(
            snap_b,
            snap_b,
            compute_public_surface(snap_b),
            compute_public_surface(snap_b),
        )
        assert block_a == block_b

    def test_input_identity_changes_with_the_observation(self) -> None:
        one = _snap(functions=[_public_fn("api")])
        two = _snap(functions=[_public_fn("api"), _public_fn("extra")])
        block_one = collect_contract_evidence(
            one, one, compute_public_surface(one), compute_public_surface(one)
        )
        block_two = collect_contract_evidence(
            two, two, compute_public_surface(two), compute_public_surface(two)
        )
        digests = [
            next(e for e in b.providers if e.record.side == "old").record.input_identity
            for b in (block_one, block_two)
        ]
        assert digests[0] is not None and digests[1] is not None
        assert digests[0].sha256 != digests[1].sha256


class TestEvidenceReferences:
    def _block(self, *, exports: bool = False):
        snap = _snap(
            functions=[_public_fn("api")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="api")]),
        )
        surf = compute_public_surface(snap)
        export_surface = compute_export_surface(snap) if exports else None
        return collect_contract_evidence(
            snap,
            snap,
            surf,
            surf,
            exports_old=export_surface,
            exports_new=export_surface,
        )

    def test_non_entity_finding_cites_no_provider(self) -> None:
        refs = evidence_refs_for_reason(
            "non_entity_finding",
            mode=ContractMode.PUBLIC,
            block=self._block(),
            authoritative_side="old",
        )
        assert refs == ()

    def test_public_membership_cites_both_sides(self) -> None:
        """The one genuinely two-sided decision: ``classify_change_surface``
        runs against ``surface_unions(surf_old, surf_new)``."""
        refs = evidence_refs_for_reason(
            "public_root_membership",
            mode=ContractMode.PUBLIC,
            block=self._block(),
            authoritative_side="old",
        )
        assert refs == ("public_header:old", "public_header:new")

    def test_one_sided_reason_cites_only_the_authoritative_side(self) -> None:
        refs = evidence_refs_for_reason(
            "required_evidence_incomplete",
            mode=ContractMode.PUBLIC,
            block=self._block(),
            authoritative_side="new",
        )
        assert refs == ("public_header:new",)

    def test_exports_domain_cites_only_the_authoritative_side(self) -> None:
        """``_exports_mode_decision`` reads one surface, so it cites one.

        Citing the other side would claim the decision rests on evidence it
        never read -- actively misleading when that side's provider is
        unavailable (an exported removal is conclusive from the old table
        even if the new one failed to parse; Codex review, fresh evidence).
        """
        refs = evidence_refs_for_reason(
            "export_root_membership",
            mode=ContractMode.EXPORTS,
            block=self._block(exports=True),
            authoritative_side="old",
        )
        assert refs == ("export_table:old",)

    def test_reference_is_dropped_when_the_record_is_absent(self) -> None:
        """A reference must never dangle -- see ``validate_decision_evidence``."""
        refs = evidence_refs_for_reason(
            "export_root_membership",
            mode=ContractMode.EXPORTS,
            block=self._block(exports=False),
            authoritative_side="old",
        )
        assert refs == ()

    def test_explicit_scope_cites_the_run_level_reference(self) -> None:
        refs = evidence_refs_for_reason(
            "explicit_consumer_or_required_symbol_evidence",
            mode=ContractMode.PUBLIC,
            block=self._block(),
            authoritative_side="old",
        )
        assert refs == (EXPLICIT_SCOPE_EVIDENCE_REF,)

    def test_validate_accepts_run_level_and_known_records(self) -> None:
        block = self._block()
        validate_decision_evidence(
            [EXPLICIT_SCOPE_EVIDENCE_REF, "public_header:old"], block
        )

    def test_validate_rejects_a_dangling_reference(self) -> None:
        with pytest.raises(ValueError, match="no known provider record"):
            validate_decision_evidence(["export_table:old"], self._block())


class TestOverlayAttribution:
    """A decision an explicit overlay produced must cite *that* overlay.

    ``--public-symbol`` and ``--post-manifest`` short-circuit the evaluator
    before it ever looks at the header surface, but both carry the same
    reason code a genuine header-derived root membership does -- so a
    reason-code-only mapping would cite the header provider for evidence
    that never decided anything here. ``evidence_refs_for_change`` is the
    wrapper that distinguishes them (it lives in ``contract_evaluation``
    so the matching mirrors that module's own overlay rules).
    """

    def _pair(self) -> tuple[AbiSnapshot, AbiSnapshot]:
        old = _snap(
            functions=[
                _public_fn("pub"),
                Function(
                    name="priv",
                    mangled="priv",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PRIVATE_HEADER,
                ),
            ]
        )
        new = _snap("2")
        return old, new

    def _refs(self, **compare_kwargs) -> dict[str, tuple[str, ...]]:
        from abicheck.checker import compare

        old, new = self._pair()
        result = compare(old, new, contract_evaluation=True, **compare_kwargs)
        return {
            c.symbol: tuple(c.contract_evidence_refs or ())
            for c in result.changes + result.out_of_surface_changes
        }

    def test_forced_public_symbol_cites_its_own_overlay(self) -> None:
        refs = self._refs(force_public_symbols={"priv"})
        assert refs["priv"] == ("forced_public_symbols:old",)
        # An ordinary header-derived membership is unaffected.
        assert refs["pub"] == ("public_header:old", "public_header:new")

    def test_post_manifest_cites_the_manifest_in_both_directions(self) -> None:
        """A commitment *and* an exclusion both rest on the manifest."""
        refs = self._refs(public_surface_allowlist={"pub"})
        assert refs["pub"] == ("post_manifest:old",)
        assert refs["priv"] == ("post_manifest:old",)

    def test_an_empty_manifest_still_owns_its_exclusions(self) -> None:
        """Committing to zero exports is a selection, not an absence.

        A truthiness check on the allowlist skipped the attribution for it,
        so an exclusion the empty manifest alone produced cited
        ``public_header`` instead (Codex review, fresh evidence).
        """
        refs = self._refs(public_surface_allowlist=set())
        assert refs["pub"] == ("post_manifest:old",)
        assert refs["priv"] == ("post_manifest:old",)

    def test_private_header_declaration_is_still_a_persisted_root(self) -> None:
        """Root selection is by *visibility*, and the receipt says so.

        ``_public_header_declarations`` seeds from ``Visibility.PUBLIC``
        exactly as ``surface._seed_public_roots`` does, so a public-visibility
        declaration whose header origin is private is recorded as a root here
        even though header-origin scoping would demote a finding about it.
        That is the intended policy-independence of the evidence block, not
        an oversight -- pinning it means a later widening (or narrowing) of
        the root rule fails this test instead of silently changing what a
        replay computes (CodeRabbit review).
        """
        from abicheck.contract_context import build_persisted_context
        from abicheck.contract_relevance_types import ContractMode

        old, _new = self._pair()
        surf = compute_public_surface(old)
        receipt = build_persisted_context(
            collect_contract_evidence(old, old, surf, surf),
            mode=ContractMode.PUBLIC,
        ).decision_receipt
        assert set(receipt.evaluated_contract_roots) == {"decl:pub", "decl:priv"}

    def test_no_overlay_configured_cites_the_domain_provider(self) -> None:
        refs = self._refs()
        assert all(
            r and all(ref.startswith("public_header:") for ref in r)
            for r in refs.values()
        )
