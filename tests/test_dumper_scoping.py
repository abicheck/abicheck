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
from abicheck.dwarf_advanced import AdvancedDwarfMetadata
from abicheck.dwarf_metadata import DwarfMetadata, StructLayout
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)


def _fn(
    name: str,
    ret: str = "void",
    params: tuple[str, ...] = (),
    vis: Visibility = Visibility.PUBLIC,
    mangled: str | None = None,
) -> Function:
    return Function(
        name=name,
        mangled=mangled if mangled is not None else f"_Z{len(name)}{name}",
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=vis,
    )


def _rec(
    name: str,
    fields: tuple[tuple[str, str], ...] = (),
    bases: tuple[str, ...] = (),
) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        size_bits=64,
        fields=[TypeField(name=n, type=t) for n, t in fields],
        bases=list(bases),
    )


class TestScopeSnapshotToPublicSurface:
    """Every success-path snapshot below sets ``from_headers=True``: scoping's
    whole premise (an over-broad header-AST dump) only applies to header-parsed
    snapshots, so the scoper gates on it explicitly — see
    ``TestFromHeadersGate`` for the regression this guards against (Codex
    review)."""

    def test_drops_unreferenced_dependency_type(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
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
            from_headers=True,
            functions=[_fn("run", params=("std::string *",))],
            types=[_rec("std::string", fields=(("data", "char *"),))],
        )
        scoped = scope_snapshot_to_public_surface(snap)
        assert {t.name for t in scoped.types} == {"std::string"}

    def test_follows_transitive_field_and_base_closure(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
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
            from_headers=True,
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
            from_headers=True,
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
            from_headers=True,
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

    def test_lazy_lookup_indexes_rebuild_from_scoped_lists(self):
        """dataclasses.replace() otherwise carries the input snapshot's
        already-built lazy indexes over verbatim (Codex review): if the
        caller had already looked up something on *snap* before scoping, the
        returned copy's func_by_mangled()/type_by_name() must not keep
        resolving a declaration this scoping just dropped."""
        hidden = _fn("hidden", vis=Visibility.HIDDEN, mangled="_Z6hidden")
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Used *",)), hidden],
            types=[_rec("Used"), _rec("Unused")],
        )
        # Force the lazy indexes to build against the *unscoped* lists,
        # simulating an earlier pipeline step that already looked something up.
        assert snap.func_by_mangled(hidden.mangled) is hidden
        assert snap.type_by_name("Unused") is not None

        scoped = scope_snapshot_to_public_surface(snap)
        assert scoped.func_by_mangled(hidden.mangled) is None
        assert scoped.type_by_name("Unused") is None
        assert scoped.type_by_name("Used") is not None


class TestFromHeadersGate:
    """Regression coverage for the Codex-review finding: a DWARF-derived
    snapshot (no -H/--header at all) can carry Visibility.PUBLIC declarations
    with full type layout from debug info, making
    ``surface.PublicSurface.resolvable`` True even though this flag's whole
    premise -- a header-AST dump pulling in unreferenced transitive
    #include dependency declarations -- does not apply to it. Scoping must
    require ``from_headers`` explicitly, not just ``resolvable``."""

    def test_dwarf_derived_snapshot_without_headers_raises(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=False,  # DWARF-only / no -H: elf_only_mode is also
            # False (real type info exists from debug info), so `resolvable`
            # alone would incorrectly accept this snapshot for scoping.
            functions=[_fn("run", params=("Used *",))],
            types=[_rec("Used", fields=(("x", "int"),))],
        )
        with pytest.raises(PublicSurfaceScopingError):
            scope_snapshot_to_public_surface(snap)


class TestDwarfScoping:
    """Regression coverage for the Codex-review finding: an ELF snapshot with
    both a header model and DWARF debug info left ``snap.dwarf`` completely
    unfiltered. When the scoped flat ``types`` list is empty (e.g. a public
    API using only primitive types), ``diff_platform._diff_dwarf`` treats an
    empty allow-set as "no header model at all" and falls back to comparing
    every DWARF struct/enum -- including private ones scoping was supposed to
    exclude -- producing a false breaking finding even between two
    ``--public-surface-only`` snapshots."""

    def test_dwarf_structs_and_enums_filtered_to_public_closure(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Used *",))],
            types=[_rec("Used", fields=(("x", "int"),))],
            dwarf=DwarfMetadata(
                has_dwarf=True,
                structs={
                    "Used": StructLayout(name="Used", byte_size=4),
                    "Private": StructLayout(name="Private", byte_size=8),
                },
            ),
        )
        scoped = scope_snapshot_to_public_surface(snap)
        assert scoped.dwarf is not None
        assert set(scoped.dwarf.structs) == {"Used"}

    def test_no_false_positive_dwarf_finding_when_public_api_is_primitive_only(
        self,
    ):
        """End-to-end reproduction of the exact scenario Codex described: a
        public API that only uses primitive types (so the scoped flat
        `types` list is empty on both sides), plus a private DWARF struct
        whose size genuinely changes between old and new. Without dwarf
        scoping, diff_platform._diff_dwarf's empty-allow-set fallback would
        compare every DWARF struct and flag this private-only size change as
        breaking, even though neither side's public API reaches it."""
        from abicheck.diff_platform import _diff_dwarf

        def _snap(private_size):
            return AbiSnapshot(
                library="libfoo.so",
                version="1.0",
                from_headers=True,
                functions=[_fn("run", params=("int",))],  # primitive-only API
                dwarf=DwarfMetadata(
                    has_dwarf=True,
                    structs={
                        "Private": StructLayout(name="Private", byte_size=private_size)
                    },
                ),
            )

        old_scoped = scope_snapshot_to_public_surface(_snap(8))
        new_scoped = scope_snapshot_to_public_surface(_snap(16))
        assert old_scoped.types == [] and new_scoped.types == []  # the hazard condition
        assert _diff_dwarf(old_scoped, new_scoped) == []


class TestDwarfAdvancedScoping:
    """Regression coverage for the Codex-review finding: dwarf_advanced
    (Sprint 4 metadata -- packed_structs/all_struct_names, and per-function
    calling_conventions/value_abi_traits/etc.) was left completely unfiltered
    by scoping. Unlike diff_platform._diff_dwarf, dwarf_advanced.diff_advanced_dwarf
    has no allow-set/fallback mechanism of its own -- it always compares
    whatever these collections contain -- so a private, unreachable type's
    packing change (or a private function's ABI-trait drift) still produced a
    breaking finding between two --public-surface-only snapshots regardless
    of the flat-list scoping."""

    def test_type_and_symbol_keyed_collections_filtered(self):
        hidden = _fn("hidden", vis=Visibility.HIDDEN, mangled="_Z6hidden")
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Used *",)), hidden],
            types=[_rec("Used"), _rec("Private")],
            dwarf_advanced=AdvancedDwarfMetadata(
                has_dwarf=True,
                packed_structs={"Used", "Private"},
                all_struct_names={"Used", "Private"},
                calling_conventions={"_Z3run": "normal", hidden.mangled: "ms_abi"},
                value_abi_traits={"_Z3run": "v", hidden.mangled: "v"},
                return_value_sizes={"_Z3run": 4, hidden.mangled: 8},
                return_memory_classified={hidden.mangled},
                frame_registers={"_Z3run": "rbp", hidden.mangled: "rsp"},
                callee_saved_regs={hidden.mangled: frozenset({"rbx"})},
            ),
        )
        scoped = scope_snapshot_to_public_surface(snap)
        assert scoped.dwarf_advanced is not None
        adv = scoped.dwarf_advanced
        assert adv.packed_structs == {"Used"}
        assert adv.all_struct_names == {"Used"}
        assert set(adv.calling_conventions) == {"_Z3run"}
        assert set(adv.value_abi_traits) == {"_Z3run"}
        assert set(adv.return_value_sizes) == {"_Z3run"}
        assert adv.return_memory_classified == set()
        assert set(adv.frame_registers) == {"_Z3run"}
        assert adv.callee_saved_regs == {}

    def test_no_false_positive_struct_packing_finding_for_private_type(self):
        """End-to-end reproduction: a private struct's packing changes between
        old and new, but the public API only uses primitive types. Without
        dwarf_advanced scoping, _diff_struct_packing would still compare the
        unfiltered packed_structs sets and flag this as breaking."""
        from abicheck.dwarf_advanced import _diff_struct_packing

        def _snap(packed):
            return AbiSnapshot(
                library="libfoo.so",
                version="1.0",
                from_headers=True,
                functions=[_fn("run", params=("int",))],  # primitive-only API
                dwarf_advanced=AdvancedDwarfMetadata(
                    has_dwarf=True,
                    packed_structs={"Private"} if packed else set(),
                    all_struct_names={"Private"},
                ),
            )

        old_scoped = scope_snapshot_to_public_surface(_snap(False))
        new_scoped = scope_snapshot_to_public_surface(_snap(True))
        assert old_scoped.types == [] and new_scoped.types == []  # the hazard condition
        assert (
            _diff_struct_packing(old_scoped.dwarf_advanced, new_scoped.dwarf_advanced)
            == []
        )


class TestHiddenFriendScoping:
    """Regression coverage for the Codex-review finding: an inline-defined
    hidden friend (Visibility.HIDDEN, no exported symbol) of a *reachable
    public* class was unconditionally dropped by the flat Visibility.PUBLIC
    filter, silently breaking HIDDEN_FRIEND_ADDED/REMOVED detection between
    two --public-surface-only snapshots for a class that IS in scope."""

    def test_hidden_friend_of_public_class_is_kept(self):
        friend = _fn(
            "operator<<",
            vis=Visibility.HIDDEN,
            mangled="_ZlsRKN6PublicE",
        )
        friend.is_hidden_friend = True
        friend.hidden_friend_owner = "Public"
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Public *",)), friend],
            types=[_rec("Public")],
        )
        scoped = scope_snapshot_to_public_surface(snap)
        assert friend.mangled in {f.mangled for f in scoped.functions}

    def test_hidden_friend_of_private_class_is_dropped(self):
        friend = _fn(
            "operator<<",
            vis=Visibility.HIDDEN,
            mangled="_ZlsRKN7PrivateE",
        )
        friend.is_hidden_friend = True
        friend.hidden_friend_owner = "Private"
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("int",)), friend],
            types=[_rec("Private")],
        )
        scoped = scope_snapshot_to_public_surface(snap)
        assert friend.mangled not in {f.mangled for f in scoped.functions}

    def test_ordinary_hidden_function_still_dropped(self):
        """A plain hidden function (not a friend) must not be swept in just
        because is_hidden_friend/hidden_friend_owner happen to be unset."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[
                _fn("run", params=("int",)),
                _fn("internal_helper", vis=Visibility.HIDDEN, mangled="_Z8internal"),
            ],
            types=[],
        )
        scoped = scope_snapshot_to_public_surface(snap)
        assert [f.name for f in scoped.functions] == ["run"]

    def test_hidden_friend_addition_still_detected_end_to_end(self):
        """A hidden friend added to a reachable public class must still be
        observable by diff_inline_hidden_friends after scoping both sides."""
        from abicheck.diff_hidden_friends import diff_inline_hidden_friends

        friend = _fn("operator<<", vis=Visibility.HIDDEN, mangled="_ZlsRKN6PublicE")
        friend.is_hidden_friend = True
        friend.hidden_friend_owner = "Public"

        old_snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Public *",))],
            types=[_rec("Public")],
        )
        new_snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run", params=("Public *",)), friend],
            types=[_rec("Public")],
        )
        old_scoped = scope_snapshot_to_public_surface(old_snap)
        new_scoped = scope_snapshot_to_public_surface(new_snap)
        old_all = {f.mangled: f for f in old_scoped.functions}
        new_all = {f.mangled: f for f in new_scoped.functions}
        old_public = {
            f.mangled: f
            for f in old_scoped.functions
            if f.visibility == Visibility.PUBLIC
        }
        new_public = {
            f.mangled: f
            for f in new_scoped.functions
            if f.visibility == Visibility.PUBLIC
        }
        changes = diff_inline_hidden_friends(old_all, new_all, old_public, new_public)
        assert any(c.symbol == friend.mangled for c in changes)
