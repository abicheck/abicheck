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

"""Unit tests for bug class
``evidence.depth_projection_diverges_from_shallow_dump``: a snapshot projected
to ``--depth binary`` must be judged on the evidence a binary-only dump of the
same library carries, no more and no less.

The catalog-wide statement over real compiled libraries is
``tests/test_depth_projection_matches_shallow_dump.py``. These pin each
mechanism on hand-built snapshots, each against an oracle that is not the
projection's own helper:

* every ``Function``/``Variable`` field of a projected header declaration
  equals what the binary-only dumper's own export-table constructor
  (``extract.export_symbol_identity``) produces for that symbol, except the
  platform symbol-table facts, so a header attribute added to the model later
  cannot leak (the fields are enumerated, not listed);
* a header-only attribute change produces no finding at ``--depth binary``,
  while the unprojected comparison reports it;
* an export no header declares still reaches the projected comparison;
* projecting a binary-only (debug-info) pair is the identity;
* the rebuilt layout records hold only layout-channel facts.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from abicheck import checker
from abicheck.compare.debug_layout_view import debug_layout_declarations
from abicheck.extract.export_symbol_identity import (
    itanium_export_function,
    itanium_export_variable,
)
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    ElfVisibility,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    Variable,
    Visibility,
)
from abicheck.model.dwarf_facts import DwarfMetadata, EnumInfo, FieldInfo, StructLayout
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model.fact import Fact, FactStatus
from abicheck.policy.depth_projection import (
    project_pair_to_depth,
    project_snapshot_to_depth,
)
from abicheck.policy.depth_projection_declarations import (
    binary_level_function,
    binary_level_variable,
    export_only_declarations,
    pair_layout_scope,
)

#: Platform symbol-table facts the projection carries over from the
#: declaration; the export-table constructor leaves them to a later
#: ``_populate_elf_visibility`` pass, so they are not part of its output.
_SYMBOL_TABLE_FIELDS = frozenset({"elf_visibility", "elf_binding", "elf_binding_fact"})
#: The identity: the dumper builds one from the symbol; the projection builds
#: none, since only a producer may (``tests/test_entity_id_carrier.py``).
_IDENTITY_FIELDS = frozenset({"entity_id"})
#: The two header surface facts: unknown in both, with a different reason.
_SURFACE_FACT_FIELDS = frozenset(
    {"declared_in_headers_fact", "in_public_contract_fact"}
)


def _header_function(**overrides: Any) -> Function:
    """A header declaration carrying a non-default value for every header
    attribute the binary view cannot see."""
    fields: dict[str, Any] = {
        "name": "Widget::bar",
        "mangled": "_ZN6Widget3barEv",
        "return_type": "int",
        "params": [Param(name="x", type="int")],
        "visibility": Visibility.PUBLIC,
        "is_virtual": True,
        "is_noexcept": True,
        "vtable_index": 3,
        "source_location": "w.h:4",
        "is_static": True,
        "is_const": True,
        "is_volatile": True,
        "is_pure_virtual": True,
        "is_deleted": True,
        "is_inline": True,
        "access": AccessLevel.PROTECTED,
        "return_pointer_depth": 1,
        "elf_visibility": ElfVisibility.PROTECTED,
        "elf_binding": SymbolBinding.WEAK,
        "ref_qualifier": "&&",
        "is_explicit": True,
        "source_header": "w.h",
        "origin": ScopeOrigin.PUBLIC_HEADER,
        "exception_spec": "noexcept",
        "deprecated": "use baz",
        "is_override": True,
        # A header dump that found the symbol in the export table.
        "binary_exported_fact": Fact.present(True),
    }
    fields.update(overrides)
    return Function(**fields)


def _header_variable() -> Variable:
    return Variable(
        name="ns::g",
        mangled="_ZN2ns1gE",
        type="const int",
        visibility=Visibility.PUBLIC,
        source_location="w.h:9",
        is_const=True,
        value="42",
        access=AccessLevel.PRIVATE,
        elf_visibility=ElfVisibility.DEFAULT,
        elf_binding=SymbolBinding.GLOBAL,
        source_header="w.h",
        origin=ScopeOrigin.PUBLIC_HEADER,
        alignment_bits=64,
        deprecated="old",
        binary_exported_fact=Fact.present(True),
    )


def _differing_fields(projected: object, oracle: object) -> set[str]:
    return {
        f.name
        for f in dataclasses.fields(projected)  # type: ignore[arg-type]
        if getattr(projected, f.name) != getattr(oracle, f.name)
    }


class TestProjectedDeclarationIsTheExportEntry:
    def test_function_matches_the_dumpers_export_constructor(self) -> None:
        header = _header_function()
        projected = binary_level_function(header)
        oracle = itanium_export_function(header.mangled)
        assert _differing_fields(projected, oracle) <= (
            _SYMBOL_TABLE_FIELDS | _SURFACE_FACT_FIELDS | _IDENTITY_FIELDS
        )
        # Not carried from the header (it pairs by source spelling) and not
        # recomputed outside a producer.
        assert projected.entity_id is None
        assert projected.elf_visibility is ElfVisibility.PROTECTED
        assert projected.elf_binding is SymbolBinding.WEAK

    def test_variable_matches_the_dumpers_export_constructor(self) -> None:
        header = _header_variable()
        projected = binary_level_variable(header)
        oracle = itanium_export_variable(header.mangled)
        assert _differing_fields(projected, oracle) <= (
            _SYMBOL_TABLE_FIELDS | _SURFACE_FACT_FIELDS | _IDENTITY_FIELDS
        )
        assert projected.entity_id is None

    @pytest.mark.parametrize("symbol", ["plain_c", "_Z3fooi", "_ZN2ns3fooEv"])
    def test_extern_c_follows_the_mangling(self, symbol: str) -> None:
        projected = binary_level_function(
            _header_function(mangled=symbol, is_extern_c=False)
        )
        oracle = itanium_export_function(symbol)
        assert projected.is_extern_c is oracle.is_extern_c

    def test_header_surface_facts_read_unknown_not_negative(self) -> None:
        projected = binary_level_function(_header_function())
        for name in _SURFACE_FACT_FIELDS:
            assert getattr(projected, name).status is FactStatus.NOT_COLLECTED


def _elf(*symbols: ElfSymbol) -> ElfMetadata:
    return ElfMetadata(machine="EM_X86_64", symbols=list(symbols))


def _sym(name: str, sym_type: SymbolType = SymbolType.FUNC) -> ElfSymbol:
    return ElfSymbol(name=name, sym_type=sym_type, is_default=True)


def _header_snap(
    version: str, functions: list[Function], elf: ElfMetadata
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libw.so",
        version=version,
        from_headers=True,
        functions=functions,
        elf=elf,
    )


#: ``(attribute, new value)``: header-only attributes of one exported method.
_HEADER_ONLY_CHANGES: list[tuple[str, Any]] = [
    ("is_static", True),
    ("is_const", True),
    ("is_volatile", True),
    ("is_virtual", True),
    ("is_pure_virtual", True),
    ("vtable_index", 2),
    ("ref_qualifier", "&"),
    ("access", AccessLevel.PRIVATE),
    ("deprecated", "gone soon"),
    ("is_noexcept", True),
    ("is_deleted", True),
    ("return_type", "long"),
    ("params", [Param(name="x", type="double")]),
]


class TestHeaderOnlyChangesStayOutOfABinaryRun:
    @pytest.mark.parametrize(("attr", "value"), _HEADER_ONLY_CHANGES)
    def test_no_finding_at_binary_depth(self, attr: str, value: Any) -> None:
        base = {
            "name": "Widget::bar",
            "mangled": "_ZN6Widget3barEv",
            "return_type": "int",
            "deprecated": None,
            "is_inline": False,
        }
        old = _header_snap("1", [Function(**base)], _elf(_sym("_ZN6Widget3barEv")))
        new = _header_snap(
            "2", [Function(**{**base, attr: value})], _elf(_sym("_ZN6Widget3barEv"))
        )
        old_b, new_b = project_pair_to_depth(old, new, "binary")
        result = checker.compare(old_b, new_b)
        assert [c.kind.value for c in result.changes] == []

    @pytest.mark.parametrize(
        ("attr", "value"),
        [
            ("is_static", True),
            ("ref_qualifier", "&"),
            ("deprecated", "gone soon"),
            ("return_type", "long"),
        ],
    )
    def test_control_the_same_change_is_reported_with_headers(
        self, attr: str, value: Any
    ) -> None:
        base = {
            "name": "Widget::bar",
            "mangled": "_ZN6Widget3barEv",
            "return_type": "int",
            "deprecated": None,
            "is_inline": False,
        }
        old = _header_snap("1", [Function(**base)], _elf(_sym("_ZN6Widget3barEv")))
        new = _header_snap(
            "2", [Function(**{**base, attr: value})], _elf(_sym("_ZN6Widget3barEv"))
        )
        assert checker.compare(old, new).changes


class TestUndeclaredExportsReachTheProjection:
    def test_records_for_undeclared_exports_only(self) -> None:
        snap = _header_snap(
            "1",
            [],
            _elf(
                _sym("declared"),
                _sym("helper"),
                _sym("_ZTV6Widget", SymbolType.OBJECT),
                _sym("tagged", SymbolType.NOTYPE),
            ),
        )
        functions, variables = export_only_declarations(snap, {"declared"})
        assert [f.mangled for f in functions] == ["helper", "tagged"]
        assert [v.mangled for v in variables] == ["_ZTV6Widget"]
        assert all(f.visibility is Visibility.ELF_ONLY for f in functions)
        assert all(
            f.binary_exported_fact is not None and f.binary_exported_fact.value is True
            for f in (*functions, *variables)
        )

    def test_uncaptured_export_table_adds_nothing(self) -> None:
        snap = _header_snap(
            "1",
            [],
            ElfMetadata(symbols=[_sym("helper")]),  # no `machine`
        )
        assert export_only_declarations(snap, set()) == ([], [])

    @pytest.mark.parametrize(
        ("lost", "sym_type", "kinds"),
        [
            ("helper", SymbolType.FUNC, {"func_removed", "func_removed_elf_only"}),
            ("_ZTV6Widget", SymbolType.OBJECT, {"var_removed", "var_removed_elf_only"}),
        ],
    )
    def test_losing_an_undeclared_export_is_reported(
        self, lost: str, sym_type: SymbolType, kinds: set[str]
    ) -> None:
        declared = Function(name="api", mangled="api", return_type="int")
        old = _header_snap("1", [declared], _elf(_sym("api"), _sym(lost, sym_type)))
        new = _header_snap("2", [declared], _elf(_sym("api")))
        old_b, new_b = project_pair_to_depth(old, new, "binary")
        result = checker.compare(old_b, new_b)
        assert {(c.kind.value, c.symbol) for c in result.changes} & {
            (k, lost) for k in kinds
        }
        assert result.verdict == checker.Verdict.BREAKING


def _debug_snap(version: str, functions: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(
        library="libw.so",
        version=version,
        from_headers=False,
        functions=functions,
        dwarf=DwarfMetadata(has_dwarf=True),
        elf=_elf(
            *(_sym(f.mangled) for f in functions if f.visibility is Visibility.PUBLIC)
        ),
    )


class TestBinaryOnlyPairIsUnchanged:
    def test_hidden_but_defined_function_keeps_its_visibility_finding(self) -> None:
        def fn(vis: Visibility) -> Function:
            return Function(
                name="impl", mangled="impl", return_type="int", visibility=vis
            )

        old = _debug_snap("1", [fn(Visibility.PUBLIC)])
        new = _debug_snap("2", [fn(Visibility.HIDDEN)])
        old_b, new_b = project_pair_to_depth(old, new, "binary")
        projected = {
            (c.kind.value, c.symbol) for c in checker.compare(old_b, new_b).changes
        }
        unprojected = {
            (c.kind.value, c.symbol) for c in checker.compare(old, new).changes
        }
        assert projected == unprojected
        assert ("func_visibility_changed", "impl") in projected

    def test_debug_signature_survives(self) -> None:
        snap = _debug_snap(
            "1",
            [
                Function(
                    name="f",
                    mangled="f",
                    return_type="long",
                    params=[Param(name="a", type="int")],
                )
            ],
        )
        projected = project_snapshot_to_depth(snap, "binary")
        assert projected.declarations.functions == snap.declarations.functions


def _layout_snap(
    structs: dict[str, StructLayout], enums: dict[str, EnumInfo]
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libw.so",
        version="1",
        dwarf=DwarfMetadata(has_dwarf=True, structs=structs, enums=enums),
    )


class TestLayoutOnlyRecords:
    @pytest.mark.parametrize("vptr", ["_vptr.Handle", "_vptr$Handle"])
    def test_vptr_member_is_the_vptr_offset_not_a_field(self, vptr: str) -> None:
        layout = StructLayout(
            name="Handle",
            byte_size=16,
            fields=[
                FieldInfo(
                    name=vptr, type_name="int (**)()", byte_offset=0, byte_size=8
                ),
                FieldInfo(name="id_", type_name="int", byte_offset=8, byte_size=4),
            ],
        )
        (record,), _ = debug_layout_declarations(_layout_snap({"Handle": layout}, {}))
        assert [f.name for f in record.fields] == ["id_"]
        assert record.vptr_offset_bits_fact is not None
        assert record.vptr_offset_bits_fact.value == 0

    def test_a_field_merely_named_like_a_vptr_stays_a_field(self) -> None:
        layout = StructLayout(
            name="S",
            byte_size=4,
            fields=[
                FieldInfo(name="_vptrish", type_name="int", byte_offset=0, byte_size=4)
            ],
        )
        (record,), _ = debug_layout_declarations(_layout_snap({"S": layout}, {}))
        assert [f.name for f in record.fields] == ["_vptrish"]
        assert record.vptr_offset_bits_fact is None or not (
            record.vptr_offset_bits_fact.is_present
        )

    def test_relations_and_qualifiers_are_not_claimed(self) -> None:
        layout = StructLayout(
            name="S",
            byte_size=8,
            alignment=4,
            fields=[FieldInfo(name="a", type_name="int", byte_offset=4, byte_size=4)],
            is_union=True,
        )
        (record,), _ = debug_layout_declarations(_layout_snap({"S": layout}, {}))
        assert (record.kind, record.is_union) == ("union", True)
        assert (record.size_bits, record.alignment_bits) == (64, 32)
        assert record.fields[0].offset_bits == 32
        for fact in (record.bases_fact, record.virtual_bases_fact, record.vtable_fact):
            assert fact is None or not fact.is_present
        assert record.fields[0].is_const_fact.status is FactStatus.UNSUPPORTED

    @pytest.mark.parametrize(
        ("size", "underlying"),
        [(1, "char"), (2, "short"), (4, "int"), (8, "long long"), (3, "int")],
    )
    def test_enum_underlying_type_from_byte_size(
        self, size: int, underlying: str
    ) -> None:
        info = EnumInfo(name="E", underlying_byte_size=size, members={"A": 1, "B": 2})
        _, (enum,) = debug_layout_declarations(_layout_snap({}, {"E": info}))
        assert enum.underlying_type == underlying
        assert [(m.name, m.value) for m in enum.members] == [("A", 1), ("B", 2)]


def _header_pair_with_layouts(
    old_layouts: dict[str, int], new_layouts: dict[str, int]
) -> tuple[AbiSnapshot, AbiSnapshot]:
    """Two header snapshots, each declaring record ``Pub`` (and naming every
    layout's type), whose debug layouts are *old_layouts*/*new_layouts*
    (``name -> byte size``)."""

    def snap(version: str, layouts: dict[str, int]) -> AbiSnapshot:
        return AbiSnapshot(
            library="libw.so",
            version=version,
            from_headers=True,
            types=[
                RecordType(name=name, kind="struct", size_bits=size * 8)
                for name, size in layouts.items()
            ],
            dwarf=DwarfMetadata(
                has_dwarf=True,
                structs={
                    name: StructLayout(name=name, byte_size=size)
                    for name, size in layouts.items()
                },
            ),
            elf=_elf(),
        )

    return snap("1", old_layouts), snap("2", new_layouts)


class TestPairLayoutScope:
    def test_a_layout_one_side_lacks_is_not_a_removal(self) -> None:
        old, new = _header_pair_with_layouts({"Kept": 4, "Gone": 4}, {"Kept": 8})
        records, _ = pair_layout_scope(frozenset({"Kept", "Gone"}), None, old, new)
        assert records == {"Kept"}
        old_b, new_b = project_pair_to_depth(old, new, "binary")
        kinds = {
            (c.kind.value, c.symbol) for c in checker.compare(old_b, new_b).changes
        }
        assert ("type_removed", "Gone") not in kinds
        assert ("type_size_changed", "Kept") in kinds

    def test_no_header_scope_keeps_the_layouts_on_both_sides(self) -> None:
        old, new = _header_pair_with_layouts({"A": 4, "B": 4}, {"A": 4})
        assert pair_layout_scope(None, None, old, new) == ({"A"}, frozenset())

    def test_scope_only_narrows(self) -> None:
        old, new = _header_pair_with_layouts({"A": 4, "B": 4}, {"A": 4, "B": 4})
        records, _ = pair_layout_scope(frozenset({"A"}), None, old, new)
        assert records <= {"A"}
