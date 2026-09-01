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

"""``policy.public_surface_query.PublicSurfaceQuery`` (ADR-063 Phase 3 D5)."""

from __future__ import annotations

from abicheck.compare.surface_graph import build_public_surface_facts
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.model.identity import entity_id_for_function, entity_id_for_variable
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.policy.public_surface import PublicSurface
from abicheck.policy.public_surface_closure import (
    resolve_public_surface,
    resolve_surface_graph_nodes,
)
from abicheck.policy.public_surface_query import PublicSurfaceQuery


def _fn(name, mangled, vis=Visibility.PUBLIC, entity_id=None):
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        visibility=vis,
        entity_id=entity_id,
    )


def _var(name, mangled, vis=Visibility.PUBLIC, entity_id=None):
    return Variable(
        name=name, mangled=mangled, type="int", visibility=vis, entity_id=entity_id
    )


def _snapshot(**kwargs) -> AbiSnapshot:
    return AbiSnapshot(library="libfoo.so", version="1.0", **kwargs)


class TestResolvePublicSurfaceIsCompatible:
    """``resolve_public_surface`` is (today) a direct passthrough to the
    existing, proven ``compute_public_surface`` -- its result must be the
    exact same ``PublicSurface`` type/shape."""

    def test_returns_a_public_surface(self) -> None:
        fn = _fn("f", "_Z1fv")
        snap = _snapshot(functions=[fn])
        surf = resolve_public_surface(snap)
        assert isinstance(surf, PublicSurface)
        assert surf.resolvable

    def test_empty_snapshot_is_not_resolvable(self) -> None:
        surf = resolve_public_surface(_snapshot())
        assert not surf.resolvable


class TestPublicSurfaceQueryResolve:
    """The bare-membership ``frozenset[EntityId]`` convenience."""

    def test_public_function_with_entity_id_is_included(self) -> None:
        eid = entity_id_for_function((), "f", mangled_name="_Z1fv")
        fn = _fn("f", "_Z1fv", entity_id=eid)
        snap = _snapshot(functions=[fn])
        ids = PublicSurfaceQuery.resolve(snap)
        assert ids == frozenset({eid})

    def test_public_variable_with_entity_id_is_included(self) -> None:
        eid = entity_id_for_variable((), "g", mangled_name="_ZN1gE")
        var = _var("g", "_ZN1gE", entity_id=eid)
        snap = _snapshot(variables=[var])
        ids = PublicSurfaceQuery.resolve(snap)
        assert ids == frozenset({eid})

    def test_non_public_declaration_is_excluded(self) -> None:
        # A second, genuinely public declaration keeps the snapshot
        # resolvable (has_public=True) -- isolating the assertion to "the
        # hidden one's id is excluded," not the separate whole-snapshot
        # unresolvable case covered below.
        eid_hidden = entity_id_for_function((), "f", mangled_name="_Z1fv")
        eid_public = entity_id_for_function((), "g", mangled_name="_Z1gv")
        fn_hidden = _fn("f", "_Z1fv", vis=Visibility.HIDDEN, entity_id=eid_hidden)
        fn_public = _fn("g", "_Z1gv", entity_id=eid_public)
        snap = _snapshot(functions=[fn_hidden, fn_public])
        assert PublicSurfaceQuery.resolve(snap) == frozenset({eid_public})

    def test_declaration_with_unpopulated_entity_id_is_silently_excluded(self) -> None:
        # One public declaration has a resolved entity_id, a sibling public
        # declaration's carrier is None (a kind no producer resolves one
        # for yet) -- entity-id resolution is genuinely *available* on this
        # snapshot (a further declaration has one), so this is the
        # per-declaration degradation, not the whole-snapshot None case
        # covered by test_snapshot_with_no_entity_id_data_returns_none.
        eid = entity_id_for_function((), "f", mangled_name="_Z1fv")
        fn_with_id = _fn("f", "_Z1fv", entity_id=eid)
        fn_without_id = _fn("g", "_Z1gv", entity_id=None)
        snap = _snapshot(functions=[fn_with_id, fn_without_id])
        assert PublicSurfaceQuery.resolve(snap) == frozenset({eid})

    def test_snapshot_with_no_entity_id_data_returns_none(self) -> None:
        # A public function whose entity_id carrier is None, and no other
        # declaration on the snapshot has one either (a pre-ADR-063-Phase-2
        # snapshot) -- entity-id resolution is wholesale *unavailable* here,
        # which every *_public_entity_ids consumer must read as "fall back
        # to the legacy Visibility.PUBLIC-only answer," not "confirmed
        # empty public surface" (Codex review, PR #962).
        fn = _fn("f", "_Z1fv", entity_id=None)
        snap = _snapshot(functions=[fn])
        assert PublicSurfaceQuery.resolve(snap) is None

    def test_unresolvable_snapshot_returns_none(self) -> None:
        # No declarations at all -> compute_public_surface() itself is
        # unresolvable (surf.resolvable is False) -- same "fall back to
        # legacy behavior" answer as the no-entity-id-data case above, for
        # the same reason: this query genuinely cannot answer, so it must
        # not claim a confirmed-empty public surface.
        assert PublicSurfaceQuery.resolve(_snapshot()) is None

    def test_hidden_overload_does_not_inherit_a_public_siblings_bare_name(
        self,
    ) -> None:
        # Two overloads share the demangled name "f": f(int) is public,
        # f(double) is hidden. surface.py's own _seed_public_roots unions
        # BOTH the mangled name and the bare name into public_symbols for
        # the public overload alone -- a plain "mangled in ... or name in
        # ..." check on the hidden overload still matches via that shared
        # bare name, since nothing about a bare-name hit says which
        # specific overload it came from (Codex review, PR #962).
        eid_public = entity_id_for_function((), "f", mangled_name="_Z1fi")
        eid_hidden = entity_id_for_function((), "f", mangled_name="_Z1fd")
        fn_public = _fn("f", "_Z1fi", vis=Visibility.PUBLIC, entity_id=eid_public)
        fn_hidden = _fn("f", "_Z1fd", vis=Visibility.HIDDEN, entity_id=eid_hidden)
        snap = _snapshot(functions=[fn_public, fn_hidden])
        assert PublicSurfaceQuery.resolve(snap) == frozenset({eid_public})

    def test_two_public_declarations_both_included(self) -> None:
        eid_f = entity_id_for_function((), "f", mangled_name="_Z1fv")
        eid_g = entity_id_for_variable((), "g", mangled_name="_ZN1gE")
        fn = _fn("f", "_Z1fv", entity_id=eid_f)
        var = _var("g", "_ZN1gE", entity_id=eid_g)
        snap = _snapshot(functions=[fn], variables=[var])
        assert PublicSurfaceQuery.resolve(snap) == frozenset({eid_f, eid_g})

    def test_publicly_reachable_type_is_included_too(self) -> None:
        # resolve() is NOT function/variable-only -- a record reachable from
        # a public function's return type is part of the same resolved set,
        # per PublicSurfaceQuery's own contract (surface_graph.py's
        # public_roots() is what later filters this down to roots only).
        from abicheck.model.identity import entity_id_for_type

        eid_fn = entity_id_for_function((), "make", mangled_name="_Z4makev")
        eid_rec = entity_id_for_type((), "Widget")
        fn = Function(
            name="make",
            mangled="_Z4makev",
            return_type="Widget*",
            visibility=Visibility.PUBLIC,
            entity_id=eid_fn,
        )
        rec = RecordType(name="Widget", kind="struct", entity_id=eid_rec)
        snap = _snapshot(functions=[fn], types=[rec])
        ids = PublicSurfaceQuery.resolve(snap)
        assert eid_fn in ids
        assert eid_rec in ids


class TestGraphNodeCollisionDoesNotBlurReachability:
    """A public, narrow-signature overload and a hidden, private-type-taking
    overload sharing one demangled name with no mangled name and no
    resolved ``entity_id`` collide onto the *same* approximate graph node id
    (``compare.surface_graph``'s own documented fallback). The public
    overload's own resolved reachability must not inherit the hidden
    sibling's private parameter type merely because both collapsed onto one
    graph node -- confirmed to fail against a version of the traversal that
    trusts the graph's per-node union unconditionally instead of falling
    back to each declaration's own signature on a detected collision."""

    def test_public_overload_does_not_inherit_hidden_siblings_private_type(
        self,
    ) -> None:
        public_overload = Function(
            name="over", mangled="", return_type="int", visibility=Visibility.PUBLIC
        )
        hidden_overload = Function(
            name="over",
            mangled="",
            return_type="int",
            params=[Param(name="s", type="Secret *")],
            visibility=Visibility.HIDDEN,
        )
        secret = RecordType(name="Secret", kind="struct")
        snap = _snapshot(
            functions=[public_overload, hidden_overload], types=[secret]
        )
        surf = resolve_public_surface(snap)
        assert "Secret" not in surf.public_types


def _outer_inner_snapshot() -> AbiSnapshot:
    """A public function taking ``Outer *``, where ``Outer`` has a field of
    type ``Inner`` -- ``Inner`` is only transitively reachable through that
    field, never directly referenced by a declaration."""
    inner = RecordType(name="Inner", kind="struct")
    outer = RecordType(
        name="Outer", kind="struct", fields=[TypeField(name="i", type="Inner")]
    )
    fn = Function(
        name="f",
        mangled="_Z1fv",
        return_type="void",
        params=[Param(name="o", type="Outer *")],
        visibility=Visibility.PUBLIC,
    )
    return _snapshot(functions=[fn], types=[outer, inner])


class TestUnpopulatedAttachedGraphIsBackfilled:
    """``service_header_graph_attach._attach_header_graph`` installs an L5
    ``surface_graph`` on essentially every real dump without ever calling
    ``build_public_surface_facts`` itself (deferred for cost reasons -- see
    that builder's own docstring) -- so ``snap.surface_graph`` being
    non-``None`` does not mean its nodes carry ``referenced_identifiers``/
    ``identifiers_collision`` (Codex review, PR #979: the ordinary, default
    dump path, not merely a stale-schema edge case).
    ``resolve_surface_graph_nodes`` must still populate those attrs on such
    an already-attached, evidence-incomplete graph rather than trusting it
    as-is, which would otherwise silently read every node as "references
    nothing" and collapse the transitive type closure."""

    def _snapshot_with_unpopulated_graph(self) -> AbiSnapshot:
        snap = _outer_inner_snapshot()
        # An L5 graph attached but never run through
        # build_public_surface_facts -- exactly _attach_header_graph's own
        # documented behavior.
        snap.surface_graph = SourceGraphSummary()
        return snap

    def test_transitively_reachable_type_survives_an_unpopulated_graph(self) -> None:
        snap = self._snapshot_with_unpopulated_graph()
        surf = resolve_public_surface(snap)
        assert "Inner" in surf.public_types

    def test_the_attached_graph_object_is_enriched_in_place_not_replaced(
        self,
    ) -> None:
        snap = self._snapshot_with_unpopulated_graph()
        attached = snap.surface_graph
        nodes = resolve_surface_graph_nodes(snap)
        assert snap.surface_graph is attached
        assert any(n.attrs.get("referenced_identifiers") for n in nodes.values())


class TestStrippedGraphAttrsAreReconstructedNotTrusted:
    """A persisted ``surface_graph`` whose nodes once carried
    ``referenced_identifiers``/``identifiers_collision`` but no longer do
    (e.g. an older on-disk format) must be treated the same as the
    never-populated case above: re-derived, not read as "references
    nothing"."""

    def _snapshot_with_stripped_graph(self) -> AbiSnapshot:
        snap = _outer_inner_snapshot()
        fresh_graph = SourceGraphSummary()
        build_public_surface_facts(snap, fresh_graph)
        for node in fresh_graph.nodes:
            node.attrs.pop("referenced_identifiers", None)
            node.attrs.pop("identifiers_collision", None)
        snap.surface_graph = fresh_graph
        return snap

    def test_transitively_reachable_type_survives_a_stripped_graph(self) -> None:
        snap = self._snapshot_with_stripped_graph()
        surf = resolve_public_surface(snap)
        assert "Inner" in surf.public_types


class TestPublicSurfaceQueryResolvePublicDomain:
    def test_matches_resolve_public_surface(self) -> None:
        fn = _fn("f", "_Z1fv")
        snap = _snapshot(functions=[fn])
        assert PublicSurfaceQuery.resolve_public_domain(snap) == resolve_public_surface(
            snap
        )


class TestPublicSurfaceQueryResolveExportDomain:
    def test_returns_an_export_surface(self) -> None:
        from abicheck.export_surface import ExportSurface

        snap = _snapshot(functions=[_fn("f", "_Z1fv")])
        result = PublicSurfaceQuery.resolve_export_domain(snap)
        assert isinstance(result, ExportSurface)


class TestPublicSurfaceBackCompatReexports:
    """``resolve_public_surface``/``PublicSurfaceQuery`` historically lived
    directly in ``policy.public_surface`` before this migration split them
    into sibling modules -- the lazy ``__getattr__`` shim at the bottom of
    that module must keep the historical import path resolving (Codex
    review, PR #979)."""

    def test_resolve_public_surface_resolves_via_the_old_import_path(self) -> None:
        import abicheck.policy.public_surface as old_path
        from abicheck.policy.public_surface_closure import (
            resolve_public_surface as moved,
        )

        assert old_path.resolve_public_surface is moved

    def test_public_surface_query_resolves_via_the_old_import_path(self) -> None:
        import abicheck.policy.public_surface as old_path
        from abicheck.policy.public_surface_query import (
            PublicSurfaceQuery as moved,
        )

        assert old_path.PublicSurfaceQuery is moved

    def test_public_surface_resolution_is_the_public_surface_alias(self) -> None:
        import abicheck.policy.public_surface as old_path

        assert old_path.PublicSurfaceResolution is old_path.PublicSurface

    def test_star_import_carries_all_four_historical_names(self) -> None:
        ns: dict[str, object] = {}
        exec("from abicheck.policy.public_surface import *", ns)  # noqa: S102
        for name in (
            "PublicSurface",
            "PublicSurfaceQuery",
            "PublicSurfaceResolution",
            "resolve_public_surface",
        ):
            assert name in ns, name

    def test_unknown_attribute_still_raises_attribute_error(self) -> None:
        import abicheck.policy.public_surface as old_path

        try:
            old_path.definitely_not_a_real_attribute
        except AttributeError:
            pass
        else:
            raise AssertionError("expected AttributeError")


class TestSurfaceGraphRefinalizedAfterEnrichment:
    """``_attach_header_graph`` finalizes the L5 graph (stamping ``graph_id``/
    ``coverage``) before installing it as ``snap.surface_graph``.
    ``resolve_surface_graph_nodes`` then adds public-surface nodes/edges the
    L5 pass never saw -- leaving the old ``graph_id``/``coverage`` in place
    would silently disagree with the graph's own, now-larger content on a
    later ``save_snapshot``/``to_dict`` (Codex review, PR #979)."""

    def test_graph_id_reflects_the_enriched_node_set(self) -> None:
        snap = _outer_inner_snapshot()
        attached = SourceGraphSummary()
        attached.finalize()
        stale_graph_id = attached.graph_id
        snap.surface_graph = attached

        resolve_surface_graph_nodes(snap)

        assert attached.graph_id != stale_graph_id
        assert attached.graph_id == attached.compute_graph_id()


class TestResolveSurfaceGraphNodesIsNotIdentityCached:
    """A real CI perf gate measured a 30-100%+ regression across nearly
    every ``benchmark_scaling.py`` scenario once ``resolve_surface_graph_
    nodes`` started calling ``build_public_surface_facts`` unconditionally
    on every call -- a typical compare resolves the public surface more
    than once per side. An identity-keyed cache (on *snap* or *graph*) was
    tried as a fix and reverted before landing: it silently served a stale
    result once a caller mutated the snapshot in place and queried again,
    which is a real, already-tested usage pattern (Codex review, PR #979 --
    see the module's own docstring for the full accounting, and
    ``docs/contribute/known-gaps.md`` for why the perf regression itself
    stays open rather than being "fixed" by an unsafe cache). This test
    pins that correctness requirement directly, so a future memoization
    attempt has an immediate, explicit signal if it reintroduces the same
    hazard."""

    def test_mutating_the_snapshot_between_calls_is_reflected_immediately(
        self,
    ) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[
                Function(
                    name="f",
                    mangled="_Z1fv",
                    return_type="void",
                    params=[Param(name="a", type="A *")],
                    visibility=Visibility.PUBLIC,
                )
            ],
            types=[RecordType(name="Bystander", kind="struct")],
            typedefs={"A": "Gone"},
        )
        assert snap.surface_graph is None

        first = resolve_surface_graph_nodes(snap)
        first_ids = set(first)

        snap.typedefs = {"A": "Here"}
        snap.types = [RecordType(name="Here", kind="struct")]
        second = resolve_surface_graph_nodes(snap)

        assert set(second) != first_ids
        assert any(node_id.endswith("Here") for node_id in second)
