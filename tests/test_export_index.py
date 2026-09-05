# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-063 T7 — the canonical raw export index and its named projections.

Each projection function here states the exact contract one of the five
retired sibling implementations used to hand-roll on its own
(``policy.depth_projection``, ``buildsource.crosscheck_base``,
``buildsource.snapshot_exports``, ``post_manifest``,
``diff_unnamed_types``) — these are the primitive-level property/contract
tests the root ``AGENTS.md`` calls for on a new shared merge/projection
primitive, decoupled from any one caller's own test module.
"""

from __future__ import annotations

from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.macho_metadata import MachoExport, MachoMetadata
from abicheck.model import AbiSnapshot
from abicheck.model.export_index import (
    RawExportEntry,
    RawExportIndex,
    all_export_names,
    build_raw_export_index,
    build_raw_export_index_from_elf,
    build_raw_export_index_from_macho,
    build_raw_export_index_from_pe,
    callable_export_names,
    default_versioned_names,
    export_names_or_modeled_fallback,
    linked_export_names,
    macho_callable_names,
    named_pe_exports,
    ordinal_only_pe_exports,
)
from abicheck.pe_metadata import PeExport, PeMetadata


def _snap(**kwargs: object) -> AbiSnapshot:
    return AbiSnapshot(library="libfoo.so", version="1", **kwargs)  # type: ignore[arg-type]


class TestBuildRawExportIndex:
    def test_none_when_no_platform_table_at_all(self) -> None:
        assert build_raw_export_index(_snap()) is None

    def test_elf_table_selected_when_present(self) -> None:
        snap = _snap()
        snap.elf = ElfMetadata()
        snap.elf.symbols = [ElfSymbol(name="_Z3foov")]
        index = build_raw_export_index(snap)
        assert index is not None
        assert index.platform == "elf"
        assert index.entries == (
            RawExportEntry(name="_Z3foov", is_default=True, sym_type="FUNC"),
        )

    def test_confirmed_empty_table_is_not_none(self) -> None:
        """A parsed-but-empty table (a real hidden-only library) is a real,
        zero-entry index -- never conflated with "no table at all"."""
        snap = _snap()
        snap.elf = ElfMetadata()
        snap.elf.symbols = []
        index = build_raw_export_index(snap)
        assert index == RawExportIndex(platform="elf", entries=())

    def test_pe_table_selected_when_present(self) -> None:
        snap = _snap()
        snap.pe = PeMetadata()
        snap.pe.exports = [PeExport(name="CreateFoo", ordinal=1)]
        index = build_raw_export_index(snap)
        assert index == build_raw_export_index_from_pe(snap.pe)
        assert index.platform == "pe"

    def test_macho_table_selected_when_present(self) -> None:
        snap = _snap()
        snap.macho = MachoMetadata()
        snap.macho.exports = [MachoExport(name="_foo")]
        index = build_raw_export_index(snap)
        assert index == build_raw_export_index_from_macho(snap.macho)
        assert index.platform == "macho"


class TestDefaultVersionedNames:
    def test_elf_excludes_non_default_version_alias(self) -> None:
        index = build_raw_export_index_from_elf(
            ElfMetadata(
                symbols=[
                    ElfSymbol(name="_Z3foov", version="LIB_1", is_default=True),
                    ElfSymbol(name="_Z3oldv", version="LIB_1", is_default=False),
                    ElfSymbol(name="_Z3barv"),
                ]
            )
        )
        assert default_versioned_names(index) == {"_Z3foov", "_Z3barv"}

    def test_pe_keeps_every_named_export_no_versioning_concept(self) -> None:
        index = build_raw_export_index_from_pe(
            PeMetadata(
                exports=[PeExport(name="CreateFoo"), PeExport(name="DestroyFoo")]
            )
        )
        assert default_versioned_names(index) == {"CreateFoo", "DestroyFoo"}

    def test_macho_strips_one_leading_underscore_by_default(self) -> None:
        index = build_raw_export_index_from_macho(
            MachoMetadata(
                exports=[MachoExport(name="_foo"), MachoExport(name="__Z3barv")]
            )
        )
        assert default_versioned_names(index) == {"foo", "_Z3barv"}

    def test_macho_underscore_strip_is_optional(self) -> None:
        index = build_raw_export_index_from_macho(
            MachoMetadata(exports=[MachoExport(name="_foo")])
        )
        assert default_versioned_names(index, normalize_macho=False) == {"_foo"}

    def test_empty_names_are_dropped(self) -> None:
        index = build_raw_export_index_from_pe(PeMetadata(exports=[PeExport(name="")]))
        assert default_versioned_names(index) == set()


class TestLinkedExportNames:
    def test_macho_keeps_the_once_stripped_spelling(self) -> None:
        """`macho_metadata` already stripped the platform's own single
        underscore -- the L4 linker keeps that form, unlike
        `default_versioned_names`'s dumper-matching double strip."""
        index = build_raw_export_index_from_macho(
            MachoMetadata(exports=[MachoExport(name="_ZN1A3fooEv")])
        )
        assert linked_export_names(index) == {"_ZN1A3fooEv"}
        assert default_versioned_names(index) == {"ZN1A3fooEv"}

    def test_elf_and_pe_identical_to_default_versioned(self) -> None:
        elf_index = build_raw_export_index_from_elf(
            ElfMetadata(
                symbols=[
                    ElfSymbol(name="_Z3foov"),
                    ElfSymbol(name="_Z3oldv", is_default=False),
                ]
            )
        )
        assert linked_export_names(elf_index) == default_versioned_names(elf_index)


class TestPeOrdinalProjections:
    def test_named_pe_exports_excludes_ordinal_only(self) -> None:
        index = build_raw_export_index_from_pe(
            PeMetadata(
                exports=[
                    PeExport(name="CreateFoo", ordinal=1),
                    PeExport(name="", ordinal=2),
                ]
            )
        )
        assert named_pe_exports(index) == {"CreateFoo"}

    def test_ordinal_only_pe_exports_excludes_named(self) -> None:
        index = build_raw_export_index_from_pe(
            PeMetadata(
                exports=[
                    PeExport(name="CreateFoo", ordinal=1),
                    PeExport(name="", ordinal=2),
                ]
            )
        )
        assert ordinal_only_pe_exports(index) == {2}


class TestMachoCallableNames:
    def test_excludes_data_exports_no_underscore_strip(self) -> None:
        index = build_raw_export_index_from_macho(
            MachoMetadata(
                exports=[
                    MachoExport(name="_pp_foo", is_data=False),
                    MachoExport(name="_pp_data", is_data=True),
                ]
            )
        )
        assert macho_callable_names(index) == {"_pp_foo"}


class TestCallableExportNames:
    def test_filters_by_type_and_default_version(self) -> None:
        from abicheck.model.elf_facts import SymbolType

        index = build_raw_export_index_from_elf(
            ElfMetadata(symbols=[ElfSymbol(name="pp_foo", sym_type=SymbolType.FUNC)])
        )
        assert callable_export_names(index, frozenset({"FUNC", "IFUNC", "NOTYPE"})) == {
            "pp_foo"
        }

    def test_data_object_symbol_excluded(self) -> None:
        from abicheck.model.elf_facts import SymbolType

        index = build_raw_export_index_from_elf(
            ElfMetadata(symbols=[ElfSymbol(name="pp_data", sym_type=SymbolType.OBJECT)])
        )
        assert (
            callable_export_names(index, frozenset({"FUNC", "IFUNC", "NOTYPE"}))
            == set()
        )

    def test_non_default_version_alias_excluded(self) -> None:
        from abicheck.model.elf_facts import SymbolType

        index = build_raw_export_index_from_elf(
            ElfMetadata(
                symbols=[
                    ElfSymbol(
                        name="pp_foo",
                        sym_type=SymbolType.FUNC,
                        version="POST_1",
                        is_default=False,
                    )
                ]
            )
        )
        assert (
            callable_export_names(index, frozenset({"FUNC", "IFUNC", "NOTYPE"}))
            == set()
        )


class TestAllExportNames:
    def test_includes_non_default_version_alias(self) -> None:
        index = build_raw_export_index_from_elf(
            ElfMetadata(
                symbols=[
                    ElfSymbol(name="_Z3foov", is_default=True),
                    ElfSymbol(name="_Z3oldv", version="LIB_1", is_default=False),
                ]
            )
        )
        assert all_export_names(index) == {"_Z3foov", "_Z3oldv"}


class TestExportNamesOrModeledFallback:
    def test_raw_table_authoritative_even_when_empty(self) -> None:
        from abicheck.model import Function

        snap = _snap()
        snap.functions = [
            Function(name="foo", mangled="_Z3foov", return_type="void", params=[])
        ]
        snap.elf = ElfMetadata()
        snap.elf.symbols = []  # confirmed-empty real export table
        assert export_names_or_modeled_fallback(snap) == ()

    def test_falls_back_to_modeled_names_without_any_raw_table(self) -> None:
        from abicheck.model import Function, Variable

        snap = _snap()
        snap.functions = [
            Function(name="foo", mangled="_Z3foov", return_type="void", params=[])
        ]
        snap.variables = [Variable(name="g", mangled="_Z1g", type="int")]
        assert export_names_or_modeled_fallback(snap) == ("_Z1g", "_Z3foov")

    def test_raw_table_used_alone_not_unioned_with_modeled_names(self) -> None:
        from abicheck.model import Function

        snap = _snap()
        # A DWARF-modeled ctor whose linkage name is the non-ABI C4 unified tag,
        # never present in the real export table.
        snap.functions = [
            Function(
                name="Foo::Foo", mangled="_ZN3FooC4Ev", return_type="void", params=[]
            )
        ]
        snap.elf = ElfMetadata()
        snap.elf.symbols = [ElfSymbol(name="_ZN3FooC1Ev")]
        exports = export_names_or_modeled_fallback(snap)
        assert exports == ("_ZN3FooC1Ev",)
        assert "_ZN3FooC4Ev" not in exports
