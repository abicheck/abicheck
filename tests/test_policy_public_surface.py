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

"""``policy.public_surface.PublicSurfaceQuery`` (ADR-063 Phase 3 D5)."""

from __future__ import annotations

from abicheck.model import AbiSnapshot, Function, Variable, Visibility
from abicheck.model.identity import entity_id_for_function, entity_id_for_variable
from abicheck.policy.public_surface import PublicSurfaceQuery, resolve_public_surface
from abicheck.surface import PublicSurface


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
        eid = entity_id_for_function((), "f", mangled_name="_Z1fv")
        fn = _fn("f", "_Z1fv", vis=Visibility.HIDDEN, entity_id=eid)
        snap = _snapshot(functions=[fn])
        assert PublicSurfaceQuery.resolve(snap) == frozenset()

    def test_declaration_with_unpopulated_entity_id_is_silently_excluded(self) -> None:
        # A public function whose entity_id carrier is None (a pre-ADR-063-
        # Phase-2 snapshot, or a kind no producer resolves one for) must not
        # raise or be mistakenly included -- it degrades the same way
        # public_roots()'s own Visibility.PUBLIC fallback already does.
        fn = _fn("f", "_Z1fv", entity_id=None)
        snap = _snapshot(functions=[fn])
        assert PublicSurfaceQuery.resolve(snap) == frozenset()

    def test_unresolvable_snapshot_returns_empty_set(self) -> None:
        assert PublicSurfaceQuery.resolve(_snapshot()) == frozenset()

    def test_two_public_declarations_both_included(self) -> None:
        eid_f = entity_id_for_function((), "f", mangled_name="_Z1fv")
        eid_g = entity_id_for_variable((), "g", mangled_name="_ZN1gE")
        fn = _fn("f", "_Z1fv", entity_id=eid_f)
        var = _var("g", "_ZN1gE", entity_id=eid_g)
        snap = _snapshot(functions=[fn], variables=[var])
        assert PublicSurfaceQuery.resolve(snap) == frozenset({eid_f, eid_g})


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
