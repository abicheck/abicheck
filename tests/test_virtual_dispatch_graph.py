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

"""Tests for G29 Phase 5 item 3: the virtual-dispatch graph
(``VIRTUAL_CALL_MAY_DISPATCH_TO``/``TYPE_HAS_VTABLE``).

Both parts are pure graph transformations over an already-built
:class:`SourceGraphSummary` (no clang, no compiler-dependent fixtures) — so,
unlike the sibling ``test_override_graph.py``/``test_macro_graph.py``, every
test here runs unconditionally; there is no ``integration``-only live path to
guard."""

from __future__ import annotations

from abicheck.buildsource.graph_facts import (
    CONF_HIGH,
    CONF_REDUCED,
    GraphEdge,
    GraphNode,
)
from abicheck.buildsource.inline_graph_fold import fold_virtual_dispatch_graph
from abicheck.buildsource.source_graph import SourceGraphSummary
from abicheck.buildsource.virtual_dispatch_graph import (
    EDGE_TYPE_HAS_VTABLE,
    EDGE_VIRTUAL_CALL_MAY_DISPATCH_TO,
    NODE_VTABLE,
    VirtualDispatchResult,
    VtablePresenceResult,
    _owner_identity,
    _record_identity,
    augment_graph_with_virtual_call_targets,
    augment_graph_with_virtual_dispatch,
    augment_graph_with_vtable_presence,
)

# ── shared fixtures ──────────────────────────────────────────────────────────

BASE_RUN = "_ZN4Base3runEv"
DERIVED1_RUN = "_ZN8Derived13runEv"
DERIVED2_RUN = "_ZN8Derived23runEv"
CALLER = "_ZN6Caller8dispatchEv"
LEAF_RUN = "_ZN4Leaf3hopEv"
LEAF_CALLER = "_ZN10LeafCaller8dispatchEv"


def _decl_node(ident: str, *, confidence: str = CONF_HIGH) -> GraphNode:
    return GraphNode(
        id=f"decl://{ident}",
        kind="source_decl",
        provenance="call_graph",
        confidence=confidence,
    )


def _virtual_decl_node(ident: str) -> GraphNode:
    """A ``source_decl`` node carrying the ``is_virtual`` fact
    ``override_graph.augment_graph_with_overrides``' ``virtual_methods``
    parameter stamps — mirrors that function's own real construction
    (``provenance="override_graph"``, ``CONF_HIGH``) rather than mutating a
    plain node's ``attrs``/``resolved`` after the fact."""
    return GraphNode(
        id=f"decl://{ident}",
        kind="source_decl",
        provenance="override_graph",
        confidence=CONF_HIGH,
        attrs={"is_virtual": True},
    )


def _call_edge(caller: str, callee: str, *, virtual: bool = True) -> GraphEdge:
    attrs = (
        {"call_kind": "virtual", "resolution": "overapprox"}
        if virtual
        else {"call_kind": "direct", "resolution": "exact"}
    )
    return GraphEdge(
        src=f"decl://{caller}",
        dst=f"decl://{callee}",
        kind="DECL_CALLS_DECL",
        provenance="call_graph",
        confidence=CONF_REDUCED if virtual else CONF_HIGH,
        attrs=attrs,
    )


def _override_edge(overrider: str, base: str, *, confirmed: bool = True) -> GraphEdge:
    return GraphEdge(
        src=f"decl://{overrider}",
        dst=f"decl://{base}",
        kind="METHOD_POSSIBLE_OVERRIDE",
        provenance="override_graph",
        confidence=CONF_HIGH if confirmed else CONF_REDUCED,
        attrs={
            "resolution": "override_confirmed"
            if confirmed
            else "override_signature_match"
        },
    )


def _record_node(name: str) -> GraphNode:
    return GraphNode(
        id=f"type://{name}",
        kind="record_type",
        provenance="type_graph",
        confidence=CONF_HIGH,
    )


def _inherits_edge(derived: str, base: str) -> GraphEdge:
    return GraphEdge(
        src=f"type://{derived}",
        dst=f"type://{base}",
        kind="TYPE_INHERITS",
        provenance="type_graph",
        confidence=CONF_HIGH,
    )


def _multi_override_graph() -> SourceGraphSummary:
    """Base::run declared virtual, two derived overrides, one confirmed by
    the ``override`` keyword and one only a signature match; a caller
    reaches Base::run through a virtual DECL_CALLS_DECL edge."""
    g = SourceGraphSummary()
    for ident in (BASE_RUN, DERIVED1_RUN, DERIVED2_RUN, CALLER):
        g.add_node(_decl_node(ident))
    g.add_edge(_call_edge(CALLER, BASE_RUN))
    g.add_edge(_override_edge(DERIVED1_RUN, BASE_RUN, confirmed=True))
    g.add_edge(_override_edge(DERIVED2_RUN, BASE_RUN, confirmed=False))
    return g


# ── Part A: VIRTUAL_CALL_MAY_DISPATCH_TO ────────────────────────────────────


class TestVirtualCallMayDispatchTo:
    def test_multi_candidate_join_produces_one_edge_per_override(self) -> None:
        g = _multi_override_graph()
        result = augment_graph_with_virtual_call_targets(g)

        assert result.virtual_call_edges_seen == 1
        assert result.dispatch_edges_added == 2
        dispatch_edges = [
            e for e in g.edges if e.kind == EDGE_VIRTUAL_CALL_MAY_DISPATCH_TO
        ]
        assert {e.dst for e in dispatch_edges} == {
            f"decl://{DERIVED1_RUN}",
            f"decl://{DERIVED2_RUN}",
        }
        assert all(e.src == f"decl://{CALLER}" for e in dispatch_edges)

    def test_edge_attrs_carry_overapprox_resolution_never_exact(self) -> None:
        g = _multi_override_graph()
        augment_graph_with_virtual_call_targets(g)

        for e in g.edges:
            if e.kind == EDGE_VIRTUAL_CALL_MAY_DISPATCH_TO:
                assert e.resolved["resolution"] == "overapprox"
                assert e.resolved["resolution"] != "exact"
                assert e.resolved["base_method"] == f"decl://{BASE_RUN}"
                assert e.confidence == CONF_REDUCED

    def test_override_resolution_label_is_preserved_per_candidate(self) -> None:
        g = _multi_override_graph()
        augment_graph_with_virtual_call_targets(g)

        by_dst = {
            e.dst: e.resolved["override_resolution"]
            for e in g.edges
            if e.kind == EDGE_VIRTUAL_CALL_MAY_DISPATCH_TO
        }
        assert by_dst[f"decl://{DERIVED1_RUN}"] == "override_confirmed"
        assert by_dst[f"decl://{DERIVED2_RUN}"] == "override_signature_match"

    def test_leaf_virtual_method_with_no_overrides_emits_no_edge(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_decl_node(LEAF_RUN))
        g.add_node(_decl_node(LEAF_CALLER))
        g.add_edge(
            _call_edge(LEAF_CALLER, LEAF_RUN)
        )  # virtual call, no overrides recorded

        result = augment_graph_with_virtual_call_targets(g)

        assert result.virtual_call_edges_seen == 1
        assert result.dispatch_edges_added == 0
        assert not [e for e in g.edges if e.kind == EDGE_VIRTUAL_CALL_MAY_DISPATCH_TO]
        # No self-edge to the base method either.
        assert not any(
            e.kind == EDGE_VIRTUAL_CALL_MAY_DISPATCH_TO
            and e.dst == f"decl://{LEAF_RUN}"
            for e in g.edges
        )

    def test_multi_level_override_chain_reaches_every_level(self) -> None:
        """Codex review, fresh evidence: ``override_graph.py`` records each
        override against its *nearest* declaring ancestor only (chains, not
        flattened), so a virtual call statically resolved to `Base::f` must
        still reach `Derived::f` two levels down, not just the immediate
        `Mid::f`."""
        mid_run = "_ZN3Mid3runEv"
        g = SourceGraphSummary()
        for ident in (CALLER, BASE_RUN, mid_run, DERIVED1_RUN):
            g.add_node(_decl_node(ident))
        g.add_edge(_call_edge(CALLER, BASE_RUN))
        g.add_edge(_override_edge(mid_run, BASE_RUN))
        g.add_edge(_override_edge(DERIVED1_RUN, mid_run, confirmed=False))

        result = augment_graph_with_virtual_call_targets(g)

        assert result.dispatch_edges_added == 2
        dispatch_edges = [
            e for e in g.edges if e.kind == EDGE_VIRTUAL_CALL_MAY_DISPATCH_TO
        ]
        assert {e.dst for e in dispatch_edges} == {
            f"decl://{mid_run}",
            f"decl://{DERIVED1_RUN}",
        }
        assert all(e.src == f"decl://{CALLER}" for e in dispatch_edges)
        by_dst = {e.dst: e.resolved["override_resolution"] for e in dispatch_edges}
        assert by_dst[f"decl://{mid_run}"] == "override_confirmed"
        assert by_dst[f"decl://{DERIVED1_RUN}"] == "override_signature_match"

    def test_non_virtual_call_is_ignored(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_decl_node(CALLER))
        g.add_node(_decl_node(BASE_RUN))
        g.add_node(_decl_node(DERIVED1_RUN))
        g.add_edge(_call_edge(CALLER, BASE_RUN, virtual=False))
        g.add_edge(_override_edge(DERIVED1_RUN, BASE_RUN))

        result = augment_graph_with_virtual_call_targets(g)

        assert result.virtual_call_edges_seen == 0
        assert result.dispatch_edges_added == 0

    def test_empty_graph_is_a_no_op(self) -> None:
        g = SourceGraphSummary()
        result = augment_graph_with_virtual_call_targets(g)
        assert result == VirtualDispatchResult()
        assert g.edges == []

    def test_idempotent_second_run_adds_no_duplicate_edges(self) -> None:
        g = _multi_override_graph()
        augment_graph_with_virtual_call_targets(g)
        edge_count_after_first = len(
            [e for e in g.edges if e.kind == EDGE_VIRTUAL_CALL_MAY_DISPATCH_TO]
        )
        augment_graph_with_virtual_call_targets(g)
        edge_count_after_second = len(
            [e for e in g.edges if e.kind == EDGE_VIRTUAL_CALL_MAY_DISPATCH_TO]
        )
        assert edge_count_after_first == edge_count_after_second == 2


# ── Part B: TYPE_HAS_VTABLE ──────────────────────────────────────────────────


class TestTypeHasVtable:
    def test_class_with_virtual_method_gets_vtable(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_record_node("Base"))
        g.add_node(_decl_node(BASE_RUN))
        g.add_node(_decl_node(DERIVED1_RUN))
        g.add_edge(_override_edge(DERIVED1_RUN, BASE_RUN))

        result = augment_graph_with_vtable_presence(g)

        assert result.polymorphic_types == 1
        vtable_edges = [e for e in g.edges if e.kind == EDGE_TYPE_HAS_VTABLE]
        assert len(vtable_edges) == 1
        assert vtable_edges[0].src == "type://Base"
        assert vtable_edges[0].dst == "vtable://Base"
        assert vtable_edges[0].confidence == CONF_HIGH
        vtable_nodes = [n for n in g.nodes if n.kind == NODE_VTABLE]
        assert len(vtable_nodes) == 1
        assert vtable_nodes[0].id == "vtable://Base"

    def test_class_with_no_virtual_method_gets_no_vtable(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_record_node("PlainOldData"))

        result = augment_graph_with_vtable_presence(g)

        assert result.polymorphic_types == 0
        assert not [e for e in g.edges if e.kind == EDGE_TYPE_HAS_VTABLE]
        assert not [n for n in g.nodes if n.kind == NODE_VTABLE]

    def test_leaf_virtual_method_with_no_override_still_gets_vtable(self) -> None:
        """Codex review, fresh evidence: a class with a virtual method but
        no override anywhere in the scanned codebase (arguably the single
        most common polymorphic-class shape) previously left the class
        non-polymorphic, since it never appears as either endpoint of a
        METHOD_POSSIBLE_OVERRIDE edge. Seeded instead from the `is_virtual`
        fact `override_graph.augment_graph_with_overrides` now stamps on
        the method's own decl node directly."""
        g = SourceGraphSummary()
        g.add_node(_record_node("Base"))
        g.add_node(_virtual_decl_node(BASE_RUN))

        result = augment_graph_with_vtable_presence(g)

        assert result.polymorphic_types == 1
        vtable_edges = [e for e in g.edges if e.kind == EDGE_TYPE_HAS_VTABLE]
        assert len(vtable_edges) == 1
        assert vtable_edges[0].src == "type://Base"
        assert vtable_edges[0].dst == "vtable://Base"

    def test_leaf_virtual_method_seeds_transitively_inheriting_classes_too(
        self,
    ) -> None:
        g = SourceGraphSummary()
        g.add_node(_record_node("Base"))
        g.add_node(_record_node("Derived"))
        g.add_node(_virtual_decl_node(BASE_RUN))
        g.add_edge(_inherits_edge("Derived", "Base"))

        result = augment_graph_with_vtable_presence(g)

        assert result.polymorphic_types == 2
        polymorphic_srcs = {e.src for e in g.edges if e.kind == EDGE_TYPE_HAS_VTABLE}
        assert polymorphic_srcs == {"type://Base", "type://Derived"}

    def test_is_virtual_fact_on_unknown_owner_mints_nothing(self) -> None:
        """A decl node's `is_virtual` fact whose owner class has no
        `record_type` node in the graph must mint nothing — the same
        join-only-onto-an-existing-node discipline the override-edge seed
        already follows."""
        g = SourceGraphSummary()
        g.add_node(_virtual_decl_node(BASE_RUN))

        augment_graph_with_vtable_presence(g)

        assert not [n for n in g.nodes if n.kind == NODE_VTABLE]
        assert not [e for e in g.edges if e.kind == EDGE_TYPE_HAS_VTABLE]

    def test_inheriting_but_not_overriding_class_still_gets_vtable(self) -> None:
        """Derived2 inherits from Base (which owns a virtual slot via
        Derived1's override) but declares no override of its own — it is
        still polymorphic per the Itanium ABI rule."""
        g = SourceGraphSummary()
        g.add_node(_record_node("Base"))
        g.add_node(_record_node("Derived2"))
        g.add_node(_decl_node(BASE_RUN))
        g.add_node(_decl_node(DERIVED1_RUN))
        g.add_edge(_override_edge(DERIVED1_RUN, BASE_RUN))
        g.add_edge(_inherits_edge("Derived2", "Base"))

        result = augment_graph_with_vtable_presence(g)

        assert result.polymorphic_types == 2
        by_src = {e.src: e.dst for e in g.edges if e.kind == EDGE_TYPE_HAS_VTABLE}
        assert by_src["type://Base"] == "vtable://Base"
        assert by_src["type://Derived2"] == "vtable://Derived2"

    def test_transitive_closure_through_multi_level_inheritance(self) -> None:
        """Grandchild -> Child -> Base, only Base declares a virtual slot;
        both Child and Grandchild must still resolve as polymorphic."""
        g = SourceGraphSummary()
        for name in ("Base", "Child", "Grandchild"):
            g.add_node(_record_node(name))
        g.add_node(_decl_node(BASE_RUN))
        g.add_node(_decl_node(DERIVED1_RUN))
        g.add_edge(_override_edge(DERIVED1_RUN, BASE_RUN))
        g.add_edge(_inherits_edge("Child", "Base"))
        g.add_edge(_inherits_edge("Grandchild", "Child"))

        result = augment_graph_with_vtable_presence(g)

        assert result.polymorphic_types == 3
        polymorphic_srcs = {e.src for e in g.edges if e.kind == EDGE_TYPE_HAS_VTABLE}
        assert polymorphic_srcs == {"type://Base", "type://Child", "type://Grandchild"}

    def test_never_mints_more_than_one_vtable_node_per_class(self) -> None:
        """Two independent virtual-method facts for the same owning class
        (e.g. both endpoints of an override edge resolving to the same
        record) must still mint exactly one ``vtable`` node."""
        g = SourceGraphSummary()
        g.add_node(_record_node("Base"))
        g.add_node(_decl_node(BASE_RUN))
        g.add_node(_decl_node(DERIVED1_RUN))
        g.add_node(_decl_node(DERIVED2_RUN))
        g.add_edge(_override_edge(DERIVED1_RUN, BASE_RUN))
        g.add_edge(_override_edge(DERIVED2_RUN, BASE_RUN))

        augment_graph_with_vtable_presence(g)

        vtable_nodes = [
            n for n in g.nodes if n.kind == NODE_VTABLE and n.id == "vtable://Base"
        ]
        assert len(vtable_nodes) == 1

    def test_never_mints_a_vtable_node_for_a_non_polymorphic_class(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_record_node("PlainOldData"))
        g.add_node(_record_node("AlsoPlain"))
        g.add_edge(_inherits_edge("AlsoPlain", "PlainOldData"))

        augment_graph_with_vtable_presence(g)

        assert not [n for n in g.nodes if n.kind == NODE_VTABLE]

    def test_join_only_onto_an_existing_record_type_node(self) -> None:
        """An override edge naming a method whose owner class has no
        ``record_type`` node in the graph at all must mint nothing — the
        join-only-onto-an-existing-node discipline (ADR-057 D1)."""
        g = SourceGraphSummary()
        g.add_node(_decl_node(BASE_RUN))
        g.add_node(_decl_node(DERIVED1_RUN))
        g.add_edge(_override_edge(DERIVED1_RUN, BASE_RUN))
        # No "type://Base" record_type node registered at all.

        result = augment_graph_with_vtable_presence(g)

        assert result.polymorphic_types == 0
        assert not [n for n in g.nodes if n.kind == NODE_VTABLE]
        assert not [e for e in g.edges if e.kind == EDGE_TYPE_HAS_VTABLE]

    def test_idempotent_second_run_mints_no_duplicate_node_or_edge(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_record_node("Base"))
        g.add_node(_decl_node(BASE_RUN))
        g.add_node(_decl_node(DERIVED1_RUN))
        g.add_edge(_override_edge(DERIVED1_RUN, BASE_RUN))

        augment_graph_with_vtable_presence(g)
        augment_graph_with_vtable_presence(g)

        assert len([n for n in g.nodes if n.kind == NODE_VTABLE]) == 1
        assert len([e for e in g.edges if e.kind == EDGE_TYPE_HAS_VTABLE]) == 1


# ── owner/identity helpers ───────────────────────────────────────────────────


class TestIdentityHelpers:
    def test_record_identity_strips_type_prefix(self) -> None:
        assert _record_identity("type://api::Foo") == "api::Foo"

    def test_record_identity_returns_none_for_non_type_node_id(self) -> None:
        assert _record_identity("decl://api::Foo::run") is None

    def test_owner_identity_from_simple_itanium_mangled_method(self) -> None:
        assert _owner_identity("_ZN4Base3runEv") == "Base"

    def test_owner_identity_from_namespaced_itanium_mangled_method(self) -> None:
        assert _owner_identity("_ZN3api3Foo3runEv") == "api::Foo"

    def test_owner_identity_none_for_a_free_function(self) -> None:
        assert _owner_identity("_Z4drawi") is None

    def test_owner_identity_none_for_an_unmangled_fallback_identity(self) -> None:
        # function_decl_identity's qualified_name#sha256:... fallback shape.
        assert _owner_identity("api::Foo::run#sha256:abc123") is None

    def test_owner_identity_from_msvc_mangled_method(self) -> None:
        assert _owner_identity("?run@Foo@@QEAAXXZ") == "Foo"


# ── the combined entry point + fold_ wiring ─────────────────────────────────


class TestAugmentGraphWithVirtualDispatch:
    def test_runs_both_parts_and_returns_both_results(self) -> None:
        g = _multi_override_graph()
        g.add_node(_record_node("Base"))

        a, b = augment_graph_with_virtual_dispatch(g)

        assert isinstance(a, VirtualDispatchResult)
        assert isinstance(b, VtablePresenceResult)
        assert a.dispatch_edges_added == 2
        assert b.polymorphic_types == 1


def _mark_prereqs_fully_covered(g: SourceGraphSummary) -> None:
    """Simulate a realistic post-``fold_semantic_graphs`` state where all
    three clang-backed prerequisites ran to completion over the whole
    compile DB -- the only state ``fold_virtual_dispatch_graph`` should
    itself claim full coverage from (Codex review, fresh evidence, third
    round: a hand-built fixture with none of these set is indistinguishable,
    from this pass's own vantage point, from a real run where clang was
    never even available -- see that fix's own docstring)."""
    for p in ("call_graph", "type_graph", "override_graph"):
        g.extractor_passes[p] = True


class TestFoldVirtualDispatchGraph:
    def test_wiring_stamps_extractor_pass_and_folds_edges(self) -> None:
        g = _multi_override_graph()
        g.add_node(_record_node("Base"))
        _mark_prereqs_fully_covered(g)

        fold_virtual_dispatch_graph(g)

        assert g.extractor_passes.get("virtual_dispatch_graph") is True
        assert any(e.kind == EDGE_VIRTUAL_CALL_MAY_DISPATCH_TO for e in g.edges)
        assert any(e.kind == EDGE_TYPE_HAS_VTABLE for e in g.edges)

    def test_no_prerequisite_coverage_at_all_degrades_this_pass(self) -> None:
        """Codex review, fresh evidence, third round: when ``clang``/
        ``clang++`` isn't on ``PATH`` at all, each clang-backed prerequisite
        pass returns early without setting ANY of its own
        ``extractor_passes``/``narrowed_passes``/``degraded_passes`` entry
        (there's no per-TU diagnostic to attach a degraded stamp to -- the
        extractor never ran). The second-round fix's ``narrowed``/
        ``degraded`` checks both read as ``False`` in exactly this case, so
        it fell through to claiming full coverage from zero prerequisite
        facts. A hand-built graph with none of the three prerequisites'
        coverage dicts populated at all reproduces the identical state."""
        g = SourceGraphSummary()
        fold_virtual_dispatch_graph(g)
        assert g.degraded_passes.get("virtual_dispatch_graph") is True
        assert "virtual_dispatch_graph" not in g.extractor_passes
        assert "virtual_dispatch_graph" not in g.narrowed_passes
        assert g.edges == []
        assert g.nodes == []

    def test_narrowed_prerequisite_propagates_to_this_pass(self) -> None:
        """Codex review, fresh evidence: an earlier version stamped only
        ``extractor_passes`` unconditionally, so a run scoped to
        ``changed_paths``/``scoped_units`` -- which correctly narrows
        ``call_graph``/``type_graph``/``override_graph`` -- let a reader see
        this pass as fully covered despite reading only a scoped, partial
        surface. ``narrowed_scope`` must also carry over."""
        g = _multi_override_graph()
        g.add_node(_record_node("Base"))
        _mark_prereqs_fully_covered(g)
        g.narrowed_passes["call_graph"] = True
        g.narrowed_scope["call_graph"] = frozenset({"a.cpp"})

        fold_virtual_dispatch_graph(g)

        assert g.narrowed_passes.get("virtual_dispatch_graph") is True
        assert g.narrowed_scope.get("virtual_dispatch_graph") == frozenset({"a.cpp"})
        assert not g.degraded_passes.get("virtual_dispatch_graph")
        # Mutually exclusive with the full-coverage stamp (Codex review,
        # fresh evidence, second round): a narrowed run must not also claim
        # complete coverage, matching every clang-backed pass's own
        # if/elif/elif stamping chain.
        assert "virtual_dispatch_graph" not in g.extractor_passes

    def test_degraded_prerequisite_propagates_to_this_pass(self) -> None:
        g = _multi_override_graph()
        g.add_node(_record_node("Base"))
        _mark_prereqs_fully_covered(g)
        del g.extractor_passes["override_graph"]
        g.degraded_passes["override_graph"] = True

        fold_virtual_dispatch_graph(g)

        assert g.degraded_passes.get("virtual_dispatch_graph") is True
        assert not g.narrowed_passes.get("virtual_dispatch_graph")
        assert "virtual_dispatch_graph" not in g.extractor_passes

    def test_degraded_prerequisite_outranks_a_narrowed_one(self) -> None:
        """Codex review, fresh evidence, fourth round: the three
        prerequisites stamp independently, so one can narrow cleanly while
        another degrades (a real per-TU clang failure) -- e.g. ``call_graph``
        narrows, ``override_graph`` degrades. A version that checked
        ``narrowed`` before ``degraded`` stamped only the narrowed key,
        silently dropping the untracked gap from the failed TUs, contrary to
        ``SourceGraphSummary.degraded_passes``'s own documented precedence."""
        g = _multi_override_graph()
        g.add_node(_record_node("Base"))
        _mark_prereqs_fully_covered(g)
        del g.extractor_passes["call_graph"]
        g.narrowed_passes["call_graph"] = True
        g.narrowed_scope["call_graph"] = frozenset({"a.cpp"})
        del g.extractor_passes["override_graph"]
        g.degraded_passes["override_graph"] = True

        fold_virtual_dispatch_graph(g)

        assert g.degraded_passes.get("virtual_dispatch_graph") is True
        assert "virtual_dispatch_graph" not in g.narrowed_passes
        assert "virtual_dispatch_graph" not in g.extractor_passes

    def test_unscoped_fully_covered_prerequisites_leave_this_pass_unnarrowed(
        self,
    ) -> None:
        g = _multi_override_graph()
        g.add_node(_record_node("Base"))
        _mark_prereqs_fully_covered(g)

        fold_virtual_dispatch_graph(g)

        assert "virtual_dispatch_graph" not in g.narrowed_passes
        assert "virtual_dispatch_graph" not in g.degraded_passes
        assert g.extractor_passes.get("virtual_dispatch_graph") is True
