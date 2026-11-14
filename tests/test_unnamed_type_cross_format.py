# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""UNNAMED_TYPE_IN_PUBLIC_ABI is format-neutral: the same newly exported
unnamed-type mangling is flagged in an ELF, Mach-O or PE library, and an
ordinary new export is flagged in none of them."""

from __future__ import annotations

import pytest

from abicheck.checker import ChangeKind, compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import AbiSnapshot
from abicheck.model.macho_facts import MachoExport, MachoMetadata
from abicheck.model.pe_facts import PeExport, PeMetadata


def _elf(*names: str) -> AbiSnapshot:
    syms = [
        ElfSymbol(name=n, binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC)
        for n in names
    ]
    return AbiSnapshot(
        library="l.so.1", version="1", elf=ElfMetadata(symbols=syms), elf_only_mode=True
    )


def _macho(*names: str) -> AbiSnapshot:
    return AbiSnapshot(
        library="l.dylib",
        version="1",
        macho=MachoMetadata(exports=[MachoExport(name=n) for n in names]),
    )


def _pe(*names: str) -> AbiSnapshot:
    return AbiSnapshot(
        library="l.dll",
        version="1",
        pe=PeMetadata(
            exports=[PeExport(name=n, ordinal=i + 1) for i, n in enumerate(names)]
        ),
    )


# (builder, baseline export, new leaking export, new ordinary export) with
# real compiler manglings for each container's scheme.
_CASES = [
    pytest.param(_elf, "_Z3fooi", "_ZNK4g_cbMUliE_clEi", "_Z3bazv", id="elf-lambda"),
    pytest.param(_elf, "_Z3fooi", "_Z3barP3FooIUt_E", "_Z3bazv", id="elf-unnamed"),
    # Mach-O readers store the once-stripped spelling ...
    pytest.param(
        _macho, "_Z3fooi", "_ZNK4g_cbMUliE_clEi", "_Z3bazv", id="macho-lambda"
    ),
    pytest.param(_macho, "_Z3fooi", "_Z3barP3FooIUt_E", "_Z3bazv", id="macho-unnamed"),
    # ... and a still-decorated platform spelling reads the same.
    pytest.param(_macho, "__Z3fooi", "__Z3barP3FooIUt_E", "__Z3bazv", id="macho-raw"),
    pytest.param(
        _pe,
        "?foo@@YAXH@Z",
        "??R<lambda_1>@?0??main@@YAHXZ@QEBA@H@Z",
        "?baz@@YAXXZ",
        id="pe-lambda",
    ),
    pytest.param(
        _pe,
        "?foo@@YAXH@Z",
        "?f@@YAXPEAU<unnamed-type-s>@@@Z",
        "?baz@@YAXXZ",
        id="pe-unnamed",
    ),
]


def _flagged(old: AbiSnapshot, new: AbiSnapshot) -> list[str]:
    return [
        c.symbol
        for c in compare(old, new).changes
        if c.kind == ChangeKind.UNNAMED_TYPE_IN_PUBLIC_ABI
    ]


@pytest.mark.parametrize(("build", "base", "leak", "plain"), _CASES)
def test_new_unnamed_type_export_flagged_in_every_format(build, base, leak, plain):
    assert _flagged(build(base), build(base, leak)) == [leak]


@pytest.mark.parametrize(("build", "base", "leak", "plain"), _CASES)
def test_preexisting_leak_and_ordinary_export_not_flagged(build, base, leak, plain):
    assert _flagged(build(base, leak), build(base, leak, plain)) == []


def test_c_export_with_ut_letters_not_flagged_on_pe():
    # An undecorated C export can contain any letters; only decorated
    # placeholders count.
    assert _flagged(_pe("foo"), _pe("foo", "Ut_lambda_")) == []


def test_missing_export_table_disables_detector():
    old = AbiSnapshot(library="l.dylib", version="1")
    assert _flagged(old, _macho("_Z3barP3FooIUt_E")) == []
