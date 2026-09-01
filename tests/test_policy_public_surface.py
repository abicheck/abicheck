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

from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    Variable,
    Visibility,
)
from abicheck.model.identity import entity_id_for_function, entity_id_for_variable
from abicheck.policy.public_surface import PublicSurface
from abicheck.policy.public_surface_closure import resolve_public_surface
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
