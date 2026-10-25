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

"""A header-mode export shrink never vanishes (known gap of 2026-10-22).

Bug class: in header mode, with no declared contract, exports that no public
header declares are not ``functions``/``variables`` entries, so a shrink of
them could read as "no finding". ADR-063's 2026-10-01 amendment fixes the
outcome without ``--contract``: an undeclared export's removal is still a
break (catalog case182). ADR-067: every removal is recorded before any
disposition.

Invariant, checked against an oracle that is a plain set difference of the two
export tables (not abicheck's own export enumeration): for every shrink size,
C and C++-mangled names, versioned and unversioned symbols, functions and
data, and headers covering none or some of the exports, every lost
default-visibility export appears as a removal finding, and the verdict is
BREAKING. Negative control: an unchanged export set produces no removal. The
real-binary end-to-end test (``test_undeclared_export_shrink_integration``)
adds the hidden-visibility control, which only a real ``.dynsym`` can model.
"""

from __future__ import annotations

import pytest

from abicheck.checker import Verdict, compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, Variable
from abicheck.model.elf_facts import SymbolType

_REMOVAL_KINDS = {
    "func_removed",
    "func_removed_elf_only",
    "var_removed",
    "var_removed_elf_only",
}


def _name(style: str, i: int) -> str:
    if style == "c":
        return f"lib_internal_{i}"
    # Itanium mangling of q::fN(int), written out independently.
    leaf = f"f{i}"
    return f"_ZN1q{len(leaf)}{leaf}Ei"


def _syms(names: list[str], *, version: str, sym_type: SymbolType) -> list[ElfSymbol]:
    return [ElfSymbol(name=n, sym_type=sym_type, version=version) for n in names]


def _snapshot(
    version: str,
    elf_syms: list[ElfSymbol],
    declared: list[str],
    declared_vars: list[str],
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libshrink.so",
        version=version,
        from_headers=True,
        functions=[Function(name=n, mangled=n, return_type="int") for n in declared],
        variables=[Variable(name=n, mangled=n, type="int") for n in declared_vars],
        elf=ElfMetadata(machine="x86-64", symbols=elf_syms),
    )


def _removed_symbols(result) -> set[str]:
    return {c.symbol for c in result.changes if c.kind.value in _REMOVAL_KINDS}


@pytest.mark.parametrize("old_count,new_count", [(2, 1), (10, 7), (281, 138), (50, 0)])
@pytest.mark.parametrize("style", ["c", "cxx"])
@pytest.mark.parametrize("version", ["", "LIB_1.0"])
@pytest.mark.parametrize("sym_type", [SymbolType.FUNC, SymbolType.OBJECT])
@pytest.mark.parametrize("declared_share", [0, 3])
def test_every_lost_export_is_reported_and_the_run_is_not_clean(
    old_count: int,
    new_count: int,
    style: str,
    version: str,
    sym_type: SymbolType,
    declared_share: int,
) -> None:
    anchor = "api_entry"
    old_names = [_name(style, i) for i in range(old_count)]
    new_names = old_names[:new_count]
    # Headers declare every third export when declared_share > 0 (some, not all).
    declared = [
        n for i, n in enumerate(old_names) if declared_share and i % declared_share == 0
    ]
    is_func = sym_type is SymbolType.FUNC

    def side(ver: str, names: list[str]) -> AbiSnapshot:
        in_header = [n for n in declared if n in names]
        elf = _syms([anchor], version=version, sym_type=SymbolType.FUNC) + _syms(
            names, version=version, sym_type=sym_type
        )
        funcs = [anchor] + (in_header if is_func else [])
        return _snapshot(ver, elf, funcs, [] if is_func else in_header)

    result = compare(side("1", old_names), side("2", new_names))

    oracle = set(old_names) - set(new_names)
    assert oracle <= _removed_symbols(result)
    assert result.verdict is Verdict.BREAKING


@pytest.mark.parametrize("style", ["c", "cxx"])
def test_unchanged_export_set_reports_no_removal(style: str) -> None:
    names = ["api_entry"] + [_name(style, i) for i in range(20)]
    old = _snapshot(
        "1", _syms(names, version="", sym_type=SymbolType.FUNC), ["api_entry"], []
    )
    new = _snapshot(
        "2", _syms(names, version="", sym_type=SymbolType.FUNC), ["api_entry"], []
    )
    result = compare(old, new)
    assert _removed_symbols(result) == set()
    assert result.verdict is Verdict.NO_CHANGE
