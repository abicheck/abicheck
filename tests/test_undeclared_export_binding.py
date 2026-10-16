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

"""Undeclared export-table findings carry the ELF binding of the symbol they
are about, so a ``binding:`` reclassify/suppress selector can match them.

The bug class: an emitter built a ``Change`` for an export the public headers
never declare, and left ``symbol_binding`` unset although the ELF symbol table
had the binding. The selector compares ``change.symbol_binding`` and an unset
value matches nothing, so a WEAK inline/template export stayed at BREAKING.
The invariant is per symbol, not per input: every kind (function or variable),
every binding, both halves (lost and gained exports) must carry the binding of
the side the finding is about, and a name whose versioned aliases disagree must
carry ``None`` rather than one alias's value.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.compare.undeclared_exports import _diff_undeclared_exports
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot
from abicheck.model.change import Change
from abicheck.model.elf_facts import SymbolBinding, SymbolType

FUNC = "_ZN2ns6detail1PIfEC1Ev"
VAR = "_ZN2ns6detail5tableE"


def _snapshot(exports: list[ElfSymbol]) -> AbiSnapshot:
    # from_headers=True keeps the snapshot out of elf_only_mode, the shape the
    # removal half exists for (no public header declares the symbol).
    return AbiSnapshot(
        library="libapi.so",
        version="1.0",
        from_headers=True,
        elf=ElfMetadata(machine="x86-64", symbols=list(exports)),
    )


def _sym(name: str, binding: SymbolBinding, sym_type: SymbolType) -> ElfSymbol:
    return ElfSymbol(name=name, binding=binding, sym_type=sym_type)


def _changes_by_symbol(old: AbiSnapshot, new: AbiSnapshot) -> dict[str, Change]:
    return {c.symbol: c for c in _diff_undeclared_exports(old, new)}


BINDINGS = [
    SymbolBinding.WEAK,
    SymbolBinding.GLOBAL,
    SymbolBinding.UNIQUE,
]


@pytest.mark.parametrize("binding", BINDINGS)
def test_removed_undeclared_function_export_carries_old_binding(
    binding: SymbolBinding,
) -> None:
    old = _snapshot([_sym(FUNC, binding, SymbolType.FUNC)])
    new = _snapshot([])
    change = _changes_by_symbol(old, new)[FUNC]
    assert change.kind is ChangeKind.FUNC_REMOVED_ELF_ONLY
    assert change.symbol_binding == binding.value


@pytest.mark.parametrize("binding", BINDINGS)
def test_removed_undeclared_variable_export_carries_old_binding(
    binding: SymbolBinding,
) -> None:
    old = _snapshot([_sym(VAR, binding, SymbolType.OBJECT)])
    new = _snapshot([])
    change = _changes_by_symbol(old, new)[VAR]
    assert change.kind is ChangeKind.VAR_REMOVED_ELF_ONLY
    assert change.symbol_binding == binding.value


@pytest.mark.parametrize("binding", BINDINGS)
def test_added_undeclared_function_export_carries_new_binding(
    binding: SymbolBinding,
) -> None:
    old = _snapshot([])
    new = _snapshot([_sym(FUNC, binding, SymbolType.FUNC)])
    change = _changes_by_symbol(old, new)[FUNC]
    assert change.kind is ChangeKind.FUNC_ADDED_ELF_ONLY
    assert change.symbol_binding == binding.value


@pytest.mark.parametrize("binding", BINDINGS)
def test_added_undeclared_variable_export_carries_new_binding(
    binding: SymbolBinding,
) -> None:
    old = _snapshot([])
    new = _snapshot([_sym(VAR, binding, SymbolType.OBJECT)])
    change = _changes_by_symbol(old, new)[VAR]
    assert change.kind is ChangeKind.VAR_ADDED_ELF_ONLY
    assert change.symbol_binding == binding.value


def test_removal_and_addition_sides_do_not_swap() -> None:
    # One name lost from OLD and a different name gained in NEW, with the
    # bindings chosen so a swapped side would be visible in both findings:
    # a removal is about OLD's symbol, an addition about NEW's.
    lost = "_ZN2ns4lostEv"
    gained = "_ZN2ns6gainedEv"
    old = _snapshot([_sym(lost, SymbolBinding.WEAK, SymbolType.FUNC)])
    new = _snapshot([_sym(gained, SymbolBinding.GLOBAL, SymbolType.FUNC)])
    changes = _changes_by_symbol(old, new)
    assert changes[lost].kind is ChangeKind.FUNC_REMOVED_ELF_ONLY
    assert changes[lost].symbol_binding == "weak"
    assert changes[gained].kind is ChangeKind.FUNC_ADDED_ELF_ONLY
    assert changes[gained].symbol_binding == "global"


def test_removal_with_disagreeing_aliases_keeps_none() -> None:
    # Two versioned aliases of one bare name with different bindings: no single
    # value is true of the name, so the finding must say None, not the last
    # alias's binding. The finding itself is still reported.
    old = _snapshot(
        [
            _sym(FUNC, SymbolBinding.GLOBAL, SymbolType.FUNC),
            _sym(FUNC, SymbolBinding.WEAK, SymbolType.FUNC),
        ]
    )
    new = _snapshot([])
    change = _changes_by_symbol(old, new)[FUNC]
    assert change.kind is ChangeKind.FUNC_REMOVED_ELF_ONLY
    assert change.symbol_binding is None


def test_addition_with_disagreeing_aliases_keeps_none() -> None:
    old = _snapshot([])
    new = _snapshot(
        [
            _sym(VAR, SymbolBinding.GLOBAL, SymbolType.OBJECT),
            _sym(VAR, SymbolBinding.UNIQUE, SymbolType.OBJECT),
        ]
    )
    change = _changes_by_symbol(old, new)[VAR]
    assert change.kind is ChangeKind.VAR_ADDED_ELF_ONLY
    assert change.symbol_binding is None
