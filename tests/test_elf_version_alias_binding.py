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

"""Symbol-versioned aliases that share one bare name (known gap, ELF binding).

``ElfMetadata.symbol_map`` is ``{name: symbol}`` and keeps only the last
``.dynsym`` entry per bare name. A library exporting ``foo@VER1`` and
``foo@@VER2`` therefore hid a binding or visibility change on whichever alias
came first. These tests pin the invariant that every versioned alias is
compared, and that a declaration never inherits a binding or visibility that
its aliases disagree on.

The oracle is the expected change kind on the named symbol, written here
rather than derived from the implementation's own helpers.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, compare
from abicheck.compare.elf_alias_pairs import pair_elf_aliases
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.extract.elf_symbol_classify import _populate_elf_visibility
from abicheck.model import AbiSnapshot, Function


def _snap(elf: ElfMetadata, functions: list[Function] | None = None) -> AbiSnapshot:
    snap = AbiSnapshot(library="libx.so", version="1.0", elf=elf)
    if functions:
        snap.declarations.functions.extend(functions)
    return snap


def _alias(
    version: str,
    binding: SymbolBinding = SymbolBinding.GLOBAL,
    visibility: str = "default",
    is_default: bool = True,
) -> ElfSymbol:
    return ElfSymbol(
        name="foo",
        binding=binding,
        sym_type=SymbolType.FUNC,
        size=10,
        version=version,
        is_default=is_default,
        visibility=visibility,
    )


def _changed_kinds(result: object, symbol: str) -> set[ChangeKind]:
    return {c.kind for c in result.changes if c.symbol == symbol}  # type: ignore[attr-defined]


# Two aliases of ``foo``, both GLOBAL. Parser order is ``.dynsym`` order, so
# index 0 is the non-last alias that the old last-wins map never looked at.
_OLD_ALIASES = [
    _alias("VER2"),
    _alias("VER1", is_default=False),
]


@pytest.mark.parametrize("changed_index", [0, 1], ids=["non_last", "last"])
def test_binding_change_on_any_alias_is_reported(changed_index: int) -> None:
    new_aliases = list(_OLD_ALIASES)
    new_aliases[changed_index] = _alias(
        new_aliases[changed_index].version,
        SymbolBinding.WEAK,
        is_default=new_aliases[changed_index].is_default,
    )
    old = ElfMetadata(symbols=list(_OLD_ALIASES))
    new = ElfMetadata(symbols=new_aliases)
    kinds = _changed_kinds(compare(_snap(old), _snap(new)), "foo")
    assert ChangeKind.SYMBOL_BINDING_CHANGED in kinds


@pytest.mark.parametrize("changed_index", [0, 1], ids=["non_last", "last"])
def test_visibility_change_on_any_alias_is_reported(changed_index: int) -> None:
    new_aliases = list(_OLD_ALIASES)
    new_aliases[changed_index] = ElfSymbol(
        **{
            **new_aliases[changed_index].__dict__,
            "visibility": "protected",
        }
    )
    old = ElfMetadata(symbols=list(_OLD_ALIASES))
    new = ElfMetadata(symbols=new_aliases)
    kinds = _changed_kinds(compare(_snap(old), _snap(new)), "foo")
    assert ChangeKind.SYMBOL_ELF_VISIBILITY_CHANGED in kinds


def test_alias_order_does_not_change_the_verdict() -> None:
    # Same two aliases, listed in the opposite .dynsym order on the new side.
    # Pairing is by version, so reordering alone must not report a binding
    # change: the old last-wins map did report one here.
    old = ElfMetadata(
        symbols=[_alias("VER2"), _alias("VER1", SymbolBinding.WEAK, is_default=False)]
    )
    new = ElfMetadata(
        symbols=[_alias("VER1", SymbolBinding.WEAK, is_default=False), _alias("VER2")]
    )
    kinds = _changed_kinds(compare(_snap(old), _snap(new)), "foo")
    assert ChangeKind.SYMBOL_BINDING_CHANGED not in kinds


def test_single_version_binding_change_is_unchanged() -> None:
    # Ordinary single-version library: exactly one symbol per bare name, so
    # the alias-aware diff must still report one binding change for it.
    old = ElfMetadata(symbols=[_alias("VER1")])
    new = ElfMetadata(symbols=[_alias("VER1", SymbolBinding.WEAK)])
    result = compare(_snap(old), _snap(new))
    foo_kinds = [c.kind for c in result.changes if c.symbol == "foo"]
    assert foo_kinds == [ChangeKind.SYMBOL_BINDING_CHANGED]


def test_single_version_identical_library_reports_nothing() -> None:
    elf = ElfMetadata(symbols=[_alias("VER1")])
    result = compare(_snap(elf), _snap(ElfMetadata(symbols=[_alias("VER1")])))
    assert [c for c in result.changes if c.symbol == "foo"] == []


def test_declaration_takes_binding_when_aliases_agree() -> None:
    elf = ElfMetadata(
        symbols=[_alias("VER2"), _alias("VER1", is_default=False)],
    )
    snap = _snap(elf, [Function(name="foo", mangled="foo", return_type="int")])
    _populate_elf_visibility(snap)
    func = snap.declarations.functions[0]
    assert func.elf_binding is SymbolBinding.GLOBAL
    assert func.elf_visibility is not None


def test_declaration_gets_no_binding_when_aliases_disagree() -> None:
    # Mixed bindings across aliases: the declaration cannot honestly claim
    # either one, so it stays unset rather than inheriting the last alias.
    elf = ElfMetadata(
        symbols=[
            _alias("VER2", SymbolBinding.GLOBAL),
            _alias("VER1", SymbolBinding.WEAK, is_default=False),
        ],
    )
    snap = _snap(elf, [Function(name="foo", mangled="foo", return_type="int")])
    _populate_elf_visibility(snap)
    func = snap.declarations.functions[0]
    assert func.elf_binding is None
    assert func.elf_visibility is None


def test_declaration_gets_no_visibility_when_aliases_disagree() -> None:
    elf = ElfMetadata(
        symbols=[
            _alias("VER2", visibility="protected"),
            _alias("VER1", visibility="default", is_default=False),
        ],
    )
    snap = _snap(elf, [Function(name="foo", mangled="foo", return_type="int")])
    _populate_elf_visibility(snap)
    func = snap.declarations.functions[0]
    assert func.elf_visibility is None


def _build(cc: str, d: Path, name: str, src: str) -> Path:
    (d / f"{name}.c").write_text(src)
    out = d / f"lib{name}.so"
    proc = subprocess.run(
        [
            cc,
            "-shared",
            "-fPIC",
            "-O1",
            "-o",
            str(out),
            str(d / f"{name}.c"),
            f"-Wl,--version-script={d / 'v.map'}",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return out


@pytest.mark.skipif(
    shutil.which("gcc") is None and shutil.which("cc") is None,
    reason="needs a C compiler to build symbol-versioned fixtures",
)
def test_real_library_binding_change_on_non_last_alias(tmp_path: Path) -> None:
    from abicheck.dumper import dump

    cc = shutil.which("gcc") or shutil.which("cc")
    assert cc is not None
    (tmp_path / "v.map").write_text("VER1 { };\nVER2 { } VER1;\n")
    # foo@@VER2 is the non-last .dynsym entry; it goes GLOBAL -> WEAK.
    old_src = (
        "__attribute__((weak)) int foo_v1(void){return 1;}\n"
        "int foo_v2(void){return 2;}\n"
        '__asm__(".symver foo_v1,foo@VER1");\n'
        '__asm__(".symver foo_v2,foo@@VER2");\n'
    )
    new_src = (
        "__attribute__((weak)) int foo_v1(void){return 1;}\n"
        "__attribute__((weak)) int foo_v2(void){return 2;}\n"
        '__asm__(".symver foo_v1,foo@VER1");\n'
        '__asm__(".symver foo_v2,foo@@VER2");\n'
    )
    old_so = _build(cc, tmp_path, "old", old_src)
    new_so = _build(cc, tmp_path, "new", new_src)
    old = dump(old_so, [], version="1")
    new = dump(new_so, [], version="2")
    kinds = _changed_kinds(compare(old, new), "foo")
    assert ChangeKind.SYMBOL_BINDING_CHANGED in kinds


def _elf(*versions: str) -> ElfMetadata:
    return ElfMetadata(
        symbols=[_alias(v, is_default=(i == 0)) for i, v in enumerate(versions)]
    )


@pytest.mark.parametrize(
    ("old_versions", "new_versions", "expected"),
    [
        # One entry per name on each side: paired as before.
        (("VER1",), ("VER1",), [("VER1", "VER1")]),
        # Aliases pair by version tag whatever their .dynsym order.
        (("VER2", "VER1"), ("VER1", "VER2"), [("VER2", "VER2"), ("VER1", "VER1")]),
        # One version tag renamed, nothing else left over: a 1:1 rename pairs.
        (("VER1",), ("VER3",), [("VER1", "VER3")]),
        # Ambiguous leftovers (two unmatched on each side) pair with nothing.
        (("VER1", "VER2"), ("VER3", "VER4"), []),
        # A version that vanished on one side is not paired with an arbitrary alias.
        (("VER1", "VER2"), ("VER2",), [("VER2", "VER2")]),
    ],
)
def test_pair_elf_aliases_matches_by_version(
    old_versions: tuple[str, ...],
    new_versions: tuple[str, ...],
    expected: list[tuple[str, str]],
) -> None:
    pairs = pair_elf_aliases(_elf(*old_versions), _elf(*new_versions))
    got = sorted((old.version, new.version) for _, old, new in pairs)
    assert got == sorted(expected)


def test_pair_elf_aliases_ignores_names_absent_on_one_side() -> None:
    old = ElfMetadata(symbols=[_alias("VER1")])
    new = ElfMetadata(symbols=[ElfSymbol(name="bar", version="VER1")])
    assert pair_elf_aliases(old, new) == []
