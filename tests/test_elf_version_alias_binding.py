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

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.compare.elf_alias_pairs import pair_elf_aliases
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.extract.elf_symbol_classify import _populate_elf_visibility
from abicheck.model import AbiSnapshot, Function, Variable
from abicheck.model.fact import Fact


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


def _named(
    name: str,
    version: str,
    is_default: bool = True,
    **kw: object,
) -> ElfSymbol:
    return ElfSymbol(name=name, version=version, is_default=is_default, **kw)  # type: ignore[arg-type]


# Each detector reads one attribute per bare name. For every case the change
# lands on either the non-last alias (index 0) or the last alias (index 1);
# both must be reported. The expected kind is written out by hand.
def _detector_case(
    kind: str, name: str, changed: int
) -> tuple[ElfMetadata, ElfMetadata]:
    base = [
        _named(name, "VER2", sym_type=SymbolType.OBJECT),
        _named(name, "VER1", is_default=False, sym_type=SymbolType.OBJECT),
    ]
    old = ElfMetadata(symbols=list(base))
    new_list = list(base)
    if kind == "tls_size":
        base = [
            _named(name, "VER2", sym_type=SymbolType.TLS, size=8),
            _named(name, "VER1", is_default=False, sym_type=SymbolType.TLS, size=8),
        ]
        old = ElfMetadata(symbols=list(base))
        new_list = list(base)
        new_list[changed] = _named(
            name,
            base[changed].version,
            base[changed].is_default,
            sym_type=SymbolType.TLS,
            size=16,
        )
    elif kind == "protected":
        new_list[changed] = _named(
            name,
            base[changed].version,
            base[changed].is_default,
            sym_type=SymbolType.OBJECT,
            visibility="protected",
        )
    elif kind == "common":
        new_list[changed] = _named(
            name,
            base[changed].version,
            base[changed].is_default,
            sym_type=SymbolType.COMMON,
        )
    elif kind == "unique":
        new_list[changed] = _named(
            name,
            base[changed].version,
            base[changed].is_default,
            sym_type=SymbolType.OBJECT,
            binding=SymbolBinding.UNIQUE,
        )
    elif kind == "rtti_visibility":
        base = [
            _named("_ZTV3Foo", "VER2", sym_type=SymbolType.OBJECT),
            _named("_ZTV3Foo", "VER1", is_default=False, sym_type=SymbolType.OBJECT),
        ]
        old = ElfMetadata(symbols=list(base))
        new_list = list(base)
        new_list[changed] = _named(
            "_ZTV3Foo",
            base[changed].version,
            base[changed].is_default,
            sym_type=SymbolType.OBJECT,
            visibility="protected",
        )
    elif kind == "leaked_removed":
        base = [
            _named(name, "VER2", sym_type=SymbolType.FUNC, origin_lib="libdep.so"),
            _named(
                name,
                "VER1",
                is_default=False,
                sym_type=SymbolType.FUNC,
                origin_lib="libdep.so",
            ),
        ]
        old = ElfMetadata(symbols=list(base))
        new_list = [a for i, a in enumerate(base) if i != changed]
    else:
        raise AssertionError(kind)
    return old, ElfMetadata(symbols=new_list)


_DETECTOR_CASES = [
    ("tls_size", "foo", ChangeKind.TLS_VAR_SIZE_CHANGED),
    ("protected", "foo", ChangeKind.PROTECTED_VISIBILITY_CHANGED),
    ("common", "foo", ChangeKind.COMMON_SYMBOL_RISK),
    ("unique", "foo", ChangeKind.SYMBOL_BINDING_BECAME_UNIQUE),
    ("rtti_visibility", "_ZTV3Foo", ChangeKind.VTABLE_SYMBOL_IDENTITY_CHANGED),
    ("leaked_removed", "foo", ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED),
]


@pytest.mark.parametrize("changed", [0, 1], ids=["non_last", "last"])
@pytest.mark.parametrize(
    ("kind", "name", "expected"),
    _DETECTOR_CASES,
    ids=[c[0] for c in _DETECTOR_CASES],
)
def test_each_alias_reader_reports_a_change_on_any_alias(
    kind: str, name: str, expected: ChangeKind, changed: int
) -> None:
    old, new = _detector_case(kind, name, changed)
    kinds = _changed_kinds(compare(_snap(old), _snap(new)), name)
    assert expected in kinds, f"{kind} on alias {changed} not reported: {kinds}"


def test_unpairable_aliases_are_recorded_as_declined() -> None:
    from abicheck.compare.declined_comparisons import declined_scope
    from abicheck.compare.elf_alias_pairs import pair_elf_aliases

    old = _elf("VER1", "VER2")
    new = _elf("VER3", "VER4")
    with declined_scope() as declined:
        pairs = pair_elf_aliases(old, new)
    assert pairs == []
    assert [d.entity for d in declined] == ["foo"]


def test_single_version_leftover_pairs_only_with_matching_is_default() -> None:
    old = ElfMetadata(
        symbols=[
            _named("foo", "VER1", is_default=True),
            _named("foo", "VER2", is_default=True),
        ]
    )
    new = ElfMetadata(
        symbols=[
            _named("foo", "VER3", is_default=False),
            _named("foo", "VER2", is_default=True),
        ]
    )
    # VER1 is the only unmatched old alias and VER3 the only unmatched new one,
    # but their is_default differs, so they are not paired as a rename.
    got = {(o.version, n.version) for _, o, n in pair_elf_aliases(old, new)}
    assert got == {("VER2", "VER2")}


@pytest.mark.repo_scan
def test_no_new_code_reader_of_the_collapsed_symbol_map() -> None:
    """A new ``.symbol_map`` read would reintroduce the last-wins collapse.

    ``ElfMetadata.symbol_map`` stays for callers that really want one entry per
    bare name. Every other reader must go through ``symbol_aliases`` or
    ``pair_elf_aliases``. The allowlist is empty: the only definition is in
    ``model/elf_facts.py``, which is not a read.
    """
    import ast

    root = Path(__file__).resolve().parents[1] / "abicheck"
    readers: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "symbol_map":
                readers.append(f"{path.relative_to(root.parent)}:{node.lineno}")
    assert readers == [], "new symbol_map reader(s); use symbol_aliases:\n" + "\n".join(
        readers
    )


def test_stored_snapshot_keeps_versions_and_clears_ambiguous_facts(
    tmp_path: Path,
) -> None:
    """Round-trip through the storage codec keeps alias versions.

    Declarations with agreeing aliases keep their binding. A declaration with
    disagreeing aliases must come back unset, even when the stored snapshot
    still carries a value from an older last-wins writer.
    """
    from abicheck.serialization import load_snapshot
    from abicheck.storage.snapshot_codec import save_snapshot

    elf = ElfMetadata(
        symbols=[
            _named(
                "foo", "VER2", sym_type=SymbolType.FUNC, binding=SymbolBinding.GLOBAL
            ),
            _named(
                "foo",
                "VER1",
                is_default=False,
                sym_type=SymbolType.FUNC,
                binding=SymbolBinding.WEAK,
            ),
            _named(
                "bar", "VER1", sym_type=SymbolType.FUNC, binding=SymbolBinding.GLOBAL
            ),
        ]
    )
    snap = AbiSnapshot(library="libx.so", version="1.0", elf=elf)
    snap.declarations.functions.extend(
        [
            Function(name="foo", mangled="foo", return_type="int"),
            Function(name="bar", mangled="bar", return_type="int"),
        ]
    )
    snap.declarations.functions[0].elf_binding = SymbolBinding.GLOBAL  # legacy value
    snap.declarations.functions[1].elf_binding = SymbolBinding.GLOBAL

    path = tmp_path / "snap.json"
    save_snapshot(snap, path)
    loaded = load_snapshot(path)

    assert {(s.name, s.version) for s in loaded.elf.symbols} == {
        ("foo", "VER2"),
        ("foo", "VER1"),
        ("bar", "VER1"),
    }
    by_name = {f.mangled: f for f in loaded.declarations.functions}
    assert by_name["foo"].elf_binding is None
    assert by_name["bar"].elf_binding is SymbolBinding.GLOBAL


@pytest.mark.skipif(
    shutil.which("gcc") is None and shutil.which("cc") is None,
    reason="needs a C compiler to build symbol-versioned fixtures",
)
def test_real_library_alias_change_survives_a_stored_baseline(tmp_path: Path) -> None:
    from abicheck.dumper import dump
    from abicheck.serialization import load_snapshot
    from abicheck.storage.snapshot_codec import save_snapshot

    cc = shutil.which("gcc") or shutil.which("cc")
    assert cc is not None
    (tmp_path / "v.map").write_text("VER1 { };\nVER2 { } VER1;\n")
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
    stored = tmp_path / "baseline.json"
    save_snapshot(dump(old_so, [], version="1"), stored)
    baseline = load_snapshot(stored)
    new = dump(new_so, [], version="2")
    kinds = _changed_kinds(compare(baseline, new), "foo")
    assert ChangeKind.SYMBOL_BINDING_CHANGED in kinds


def test_single_alias_version_bump_is_not_a_leaked_finding() -> None:
    # One export per side with a bumped version tag: base reported nothing, and
    # the per-version matching must not start reporting a removal and an
    # addition for it.
    old = ElfMetadata(
        symbols=[
            _named("foo", "LIB_1", sym_type=SymbolType.FUNC, origin_lib="libdep.so")
        ]
    )
    new = ElfMetadata(
        symbols=[
            _named("foo", "LIB_2", sym_type=SymbolType.FUNC, origin_lib="libdep.so")
        ]
    )
    kinds = _changed_kinds(compare(_snap(old), _snap(new)), "foo")
    assert ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED not in kinds


def test_ambiguous_decline_is_attributed_to_the_detector_that_judged_it() -> None:
    # Ambiguous aliases of ``foo``; ``bar`` is a plain single export. Only the
    # per-symbol detector examines every name, so only it may record a decline
    # for ``foo``; the TLS, protected-visibility and RTTI detectors must not.
    old = ElfMetadata(
        symbols=[
            _named("foo", "VER1", sym_type=SymbolType.FUNC),
            _named("foo", "VER2", sym_type=SymbolType.FUNC),
            _named("bar", "VER1", sym_type=SymbolType.FUNC),
        ]
    )
    new = ElfMetadata(
        symbols=[
            _named("foo", "VER3", sym_type=SymbolType.FUNC),
            _named("foo", "VER4", sym_type=SymbolType.FUNC),
            _named("bar", "VER1", sym_type=SymbolType.FUNC),
        ]
    )
    result = compare(_snap(old), _snap(new))
    declining = [
        det.name
        for det in result.detector_results
        if any(entity == "foo" for entity, _ in det.declined)
    ]
    assert len(declining) == 1, declining
    assert all(
        entity != "bar" for det in result.detector_results for entity, _ in det.declined
    )


def test_tls_size_change_on_any_alias_is_breaking() -> None:
    # TLS_VAR_SIZE_CHANGED defaults to BREAKING, so a multi-alias library whose
    # hidden alias changed size now fails where it passed before. Pinned here so
    # the Merge Danger text stays true.
    base = [
        _named("tv", "VER2", sym_type=SymbolType.TLS, size=8),
        _named("tv", "VER1", is_default=False, sym_type=SymbolType.TLS, size=8),
    ]
    new = list(base)
    new[0] = _named("tv", "VER2", sym_type=SymbolType.TLS, size=16)
    result = compare(
        _snap(ElfMetadata(symbols=list(base))), _snap(ElfMetadata(symbols=new))
    )
    assert result.verdict == Verdict.BREAKING


def test_stored_and_fresh_mixed_declarations_have_equal_binding_fact(
    tmp_path: Path,
) -> None:
    from abicheck.serialization import load_snapshot
    from abicheck.storage.snapshot_codec import save_snapshot

    elf = ElfMetadata(
        symbols=[
            _named(
                "foo", "VER2", sym_type=SymbolType.FUNC, binding=SymbolBinding.GLOBAL
            ),
            _named(
                "foo",
                "VER1",
                is_default=False,
                sym_type=SymbolType.FUNC,
                binding=SymbolBinding.WEAK,
            ),
        ]
    )
    fresh = Function(name="foo", mangled="foo", return_type="int")
    snap = _snap(elf, [fresh])
    _populate_elf_visibility(snap)  # the dump path: mixed name is left unset

    stored_src = _snap(elf, [Function(name="foo", mangled="foo", return_type="int")])
    stored_src.declarations.functions[0].elf_binding = SymbolBinding.GLOBAL
    path = tmp_path / "mixed.json"
    save_snapshot(stored_src, path)
    loaded = load_snapshot(path).declarations.functions[0]

    assert loaded.elf_binding is None and fresh.elf_binding is None
    assert loaded.elf_binding_fact == fresh.elf_binding_fact


def test_one_to_one_rename_with_matching_is_default_pairs_among_other_aliases() -> None:
    # Old: VER1 (default) and VER2. New: VER2 and VER3 (default). VER2 pairs by
    # version; the leftover VER1 and VER3 are a rename with matching is_default.
    old = ElfMetadata(
        symbols=[
            _named("foo", "VER1", is_default=True),
            _named("foo", "VER2", is_default=False),
        ]
    )
    new = ElfMetadata(
        symbols=[
            _named("foo", "VER2", is_default=False),
            _named("foo", "VER3", is_default=True),
        ]
    )
    got = sorted((o.version, n.version) for _, o, n in pair_elf_aliases(old, new))
    assert got == [("VER1", "VER3"), ("VER2", "VER2")]


def test_unambiguous_symbol_of_an_absent_name_is_none() -> None:
    elf = ElfMetadata(symbols=[_named("foo", "VER1")])
    assert elf.unambiguous_symbol("missing") is None


def test_variable_takes_binding_and_visibility_from_agreeing_aliases() -> None:
    elf = ElfMetadata(
        symbols=[
            _named(
                "tv", "VER2", sym_type=SymbolType.OBJECT, binding=SymbolBinding.GLOBAL
            ),
            _named(
                "tv",
                "VER1",
                is_default=False,
                sym_type=SymbolType.OBJECT,
                binding=SymbolBinding.GLOBAL,
            ),
        ]
    )
    var = Variable(name="tv", mangled="tv", type="int")
    snap = AbiSnapshot(library="libx.so", version="1.0", elf=elf)
    snap.declarations.variables.append(var)
    _populate_elf_visibility(snap)
    assert var.elf_binding is SymbolBinding.GLOBAL
    assert var.elf_visibility is not None


def test_load_normalization_clears_a_variable_with_disagreeing_aliases() -> None:
    from abicheck.storage.snapshot_load_normalization import (
        backfill_missing_elf_binding,
    )

    elf = ElfMetadata(
        symbols=[
            _named(
                "tv", "VER2", sym_type=SymbolType.OBJECT, binding=SymbolBinding.GLOBAL
            ),
            _named(
                "tv",
                "VER1",
                is_default=False,
                sym_type=SymbolType.OBJECT,
                binding=SymbolBinding.WEAK,
            ),
        ]
    )
    var = Variable(name="tv", mangled="tv", type="int")
    var.elf_binding = SymbolBinding.GLOBAL  # legacy last-wins value
    snap = AbiSnapshot(library="libx.so", version="1.0", elf=elf)
    snap.declarations.variables.append(var)
    backfill_missing_elf_binding(snap)
    assert var.elf_binding is None
    assert var.elf_binding_fact == Fact.not_collected()


def test_load_normalization_backfills_a_variable_with_agreeing_aliases() -> None:
    from abicheck.storage.snapshot_load_normalization import (
        backfill_missing_elf_binding,
    )

    elf = ElfMetadata(
        symbols=[
            _named(
                "tv", "VER2", sym_type=SymbolType.OBJECT, binding=SymbolBinding.WEAK
            ),
            _named(
                "tv",
                "VER1",
                is_default=False,
                sym_type=SymbolType.OBJECT,
                binding=SymbolBinding.WEAK,
            ),
        ]
    )
    var = Variable(name="tv", mangled="tv", type="int")
    snap = AbiSnapshot(library="libx.so", version="1.0", elf=elf)
    snap.declarations.variables.append(var)
    backfill_missing_elf_binding(snap)
    assert var.elf_binding is SymbolBinding.WEAK


def test_load_normalization_without_an_elf_block_is_a_no_op() -> None:
    from abicheck.storage.snapshot_load_normalization import (
        backfill_missing_elf_binding,
    )

    snap = AbiSnapshot(library="libx.so", version="1.0", elf=None)
    snap.declarations.functions.append(
        Function(name="foo", mangled="foo", return_type="int")
    )
    backfill_missing_elf_binding(snap)
    assert snap.declarations.functions[0].elf_binding is None
