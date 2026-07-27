# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Unit tests for dump-time public-surface scoping (``dump --public-surface-only``)."""

from __future__ import annotations

import pytest

from abicheck.dumper_scoping import (
    PublicSurfaceScopingError,
    scope_snapshot_to_public_surface,
)
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)


def _fn(name, ret="void", params=(), vis=Visibility.PUBLIC, mangled=None):
    return Function(
        name=name,
        mangled=mangled if mangled is not None else f"_Z{len(name)}{name}",
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=vis,
    )


def _rec(name, fields=(), bases=()):
    return RecordType(
        name=name,
        kind="struct",
        size_bits=64,
        fields=[TypeField(name=n, type=t) for n, t in fields],
        bases=list(bases),
    )


class TestScopeSnapshotToPublicSurface:
    def test_drops_unreferenced_dependency_type(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("run", params=("Used *",))],
            types=[
                _rec("Used", fields=(("x", "int"),)),
                _rec("std::internal_unused"),
            ],
        )
        scoped = scope_snapshot_to_public_surface(snap)
        assert {t.name for t in scoped.types} == {"Used"}

    def test_keeps_directly_referenced_dependency_type(self):
        """A stdlib/SYCL type actually named in a public signature must survive
        scoping — dropping it would blind layout detectors to a real break in a
        used dependency type, not just shrink unused noise."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("run", params=("std::string *",))],
            types=[_rec("std::string", fields=(("data", "char *"),))],
        )
        scoped = scope_snapshot_to_public_surface(snap)
        assert {t.name for t in scoped.types} == {"std::string"}

    def test_follows_transitive_field_and_base_closure(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("run", params=("Outer *",))],
            types=[
                _rec("Outer", fields=(("inner", "Inner"),), bases=("Base",)),
                _rec("Inner", fields=(("x", "int"),)),
                _rec("Base", fields=(("y", "int"),)),
                _rec("Unrelated"),
            ],
        )
        scoped = scope_snapshot_to_public_surface(snap)
        assert {t.name for t in scoped.types} == {"Outer", "Inner", "Base"}

    def test_drops_private_visibility_functions_and_variables(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn("public_fn", vis=Visibility.PUBLIC),
                _fn("hidden_fn", vis=Visibility.HIDDEN, mangled="_Z9hidden_fn"),
            ],
            variables=[
                Variable(
                    name="pub_var",
                    mangled="pub_var",
                    type="int",
                    visibility=Visibility.PUBLIC,
                ),
                Variable(
                    name="hidden_var",
                    mangled="hidden_var",
                    type="int",
                    visibility=Visibility.HIDDEN,
                ),
            ],
        )
        scoped = scope_snapshot_to_public_surface(snap)
        assert [f.name for f in scoped.functions] == ["public_fn"]
        assert [v.name for v in scoped.variables] == ["pub_var"]

    def test_keeps_reachable_typedef(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("run", params=("Alias *",))],
            types=[_rec("Real", fields=(("x", "int"),))],
            typedefs={"Alias": "Real", "Unreached": "AlsoUnrelated"},
        )
        scoped = scope_snapshot_to_public_surface(snap)
        assert scoped.typedefs == {"Alias": "Real"}
        assert {t.name for t in scoped.types} == {"Real"}

    def test_no_resolvable_public_surface_raises(self):
        """A binary-only (ELF_ONLY) dump has nothing to scope from — scoping it
        would silently drop everything, so this must be a loud usage error."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("run", vis=Visibility.ELF_ONLY)],
        )
        with pytest.raises(PublicSurfaceScopingError):
            scope_snapshot_to_public_surface(snap)

    def test_empty_snapshot_raises(self):
        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        with pytest.raises(PublicSurfaceScopingError):
            scope_snapshot_to_public_surface(snap)

    def test_does_not_mutate_input_snapshot(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn("run", params=("Used *",)),
                _fn("hidden", vis=Visibility.HIDDEN, mangled="_Z6hidden"),
            ],
            types=[_rec("Used"), _rec("Unused")],
        )
        original_fn_count = len(snap.functions)
        original_type_count = len(snap.types)
        scope_snapshot_to_public_surface(snap)
        assert len(snap.functions) == original_fn_count
        assert len(snap.types) == original_type_count
