# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Unit tests for dump-time dependency scoping (``dump --include-dependencies``)."""

from __future__ import annotations

from abicheck.dumper_scoping import scope_snapshot_excluding_dependencies
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

_SYSTEM_HEADER = "/usr/include/c++/11/string"
_OWN_HEADER = "/src/myproject/include/api.h"
_OWN_PRIVATE_HEADER = "/src/myproject/src/internal.h"


def _fn(
    name: str,
    ret: str = "void",
    params: tuple[str, ...] = (),
    vis: Visibility = Visibility.PUBLIC,
    mangled: str | None = None,
    source_header: str | None = _OWN_HEADER,
) -> Function:
    return Function(
        name=name,
        mangled=mangled if mangled is not None else f"_Z{len(name)}{name}",
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=vis,
        source_header=source_header,
    )


def _rec(
    name: str,
    fields: tuple[tuple[str, str], ...] = (),
    bases: tuple[str, ...] = (),
    source_header: str | None = _OWN_HEADER,
) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        size_bits=64,
        fields=[TypeField(name=n, type=t) for n, t in fields],
        bases=list(bases),
        source_header=source_header,
    )


class TestExcludesDependencies:
    def test_drops_function_from_system_header(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[
                _fn("run"),
                _fn("std_helper", mangled="_Z10std_helper", source_header=_SYSTEM_HEADER),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [f.name for f in scoped.functions] == ["run"]

    def test_drops_type_from_system_header(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            types=[
                _rec("Own"),
                _rec("basic_string", source_header=_SYSTEM_HEADER),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [t.name for t in scoped.types] == ["Own"]

    def test_keeps_private_declaration_from_own_header(self):
        """This is the point of the flag, distinct from the old
        public-surface-only design: a private (non-exported) declaration
        from the library's own headers must be kept, not just public ones."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[
                _fn("internal_helper", vis=Visibility.HIDDEN, mangled="_Z9internal", source_header=_OWN_PRIVATE_HEADER),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [f.name for f in scoped.functions] == ["internal_helper"]

    def test_keeps_declaration_with_no_header_info(self):
        """A declaration with no source_header at all (e.g. export-only, no
        header matched it) is not confidently a dependency, so it is kept
        (conservative default: only drop what's confidently external)."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[
                _fn("exported_only", vis=Visibility.ELF_ONLY, mangled="_Z13exported_only", source_header=None),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [f.name for f in scoped.functions] == ["exported_only"]

    def test_keeps_variables_and_enums_from_own_headers_drops_system(self):
        from abicheck.model import EnumMember, EnumType

        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            variables=[
                Variable(name="own_var", mangled="own_var", type="int", source_header=_OWN_HEADER),
                Variable(name="sys_var", mangled="sys_var", type="int", source_header=_SYSTEM_HEADER),
            ],
            enums=[
                EnumType(name="OwnEnum", members=[EnumMember(name="A", value=0)], source_header=_OWN_HEADER),
                EnumType(name="errc", members=[EnumMember(name="B", value=0)], source_header=_SYSTEM_HEADER),
            ],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert [v.name for v in scoped.variables] == ["own_var"]
        assert [e.name for e in scoped.enums] == ["OwnEnum"]

    def test_keeps_typedefs_unconditionally(self):
        """typedefs carry no per-entry header provenance, so they're kept
        wholesale rather than dropped for lack of evidence to classify them."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run")],
            typedefs={"size_type": "unsigned long", "Alias": "Own"},
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.typedefs == {"size_type": "unsigned long", "Alias": "Own"}

    def test_noop_without_header_derived_declarations(self):
        """A binary-only/DWARF-only dump (from_headers=False) has no header
        info to classify against -- this is default-on behavior, so it must
        no-op, not error, unlike the old opt-in flag's usage-error design."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=False,
            functions=[_fn("run", vis=Visibility.ELF_ONLY, source_header=None)],
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped is snap

    def test_does_not_mutate_input_snapshot(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[
                _fn("run"),
                _fn("sys", mangled="_Z3sys", source_header=_SYSTEM_HEADER),
            ],
        )
        original_count = len(snap.functions)
        scope_snapshot_excluding_dependencies(snap)
        assert len(snap.functions) == original_count

    def test_lazy_lookup_indexes_rebuild_from_scoped_lists(self):
        sys_fn = _fn("sys_helper", mangled="_Z10sys_helper", source_header=_SYSTEM_HEADER)
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run"), sys_fn],
        )
        assert snap.func_by_mangled(sys_fn.mangled) is sys_fn
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.func_by_mangled(sys_fn.mangled) is None


class TestDwarfScoping:
    def test_dwarf_structs_filtered_to_kept_types(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run")],
            types=[_rec("Own")],
            dwarf=DwarfMetadata(
                has_dwarf=True,
                structs={
                    "Own": StructLayout(name="Own", byte_size=4),
                    "std::string": StructLayout(name="std::string", byte_size=32),
                },
            ),
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.dwarf is not None
        assert set(scoped.dwarf.structs) == {"Own"}


class TestDwarfAdvancedScoping:
    def test_type_and_symbol_keyed_collections_filtered(self):
        sys_fn = _fn("sys_fn", mangled="_Z6sys_fn", source_header=_SYSTEM_HEADER)
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[_fn("run"), sys_fn],
            types=[_rec("Own")],
            dwarf_advanced=AdvancedDwarfMetadata(
                has_dwarf=True,
                packed_structs={"Own", "std::string"},
                all_struct_names={"Own", "std::string"},
                calling_conventions={"_Z3run": "normal", sys_fn.mangled: "ms_abi"},
            ),
        )
        scoped = scope_snapshot_excluding_dependencies(snap)
        assert scoped.dwarf_advanced is not None
        adv = scoped.dwarf_advanced
        assert adv.packed_structs == {"Own"}
        assert adv.all_struct_names == {"Own"}
        assert set(adv.calling_conventions) == {"_Z3run"}
