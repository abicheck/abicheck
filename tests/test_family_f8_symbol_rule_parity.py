# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Harness for F8: a symbol-level rule must hold on every object format.

Bug class ``symbol_rule.single_object_format``: a rule about a symbol's
visibility, linkage or the trustworthiness of a dependency read was implemented
for one object format only (ELF), so the same library built as a Mach-O or PE
image silently got the other answer. Three cases, each run over the formats:

* ``private_not_exported`` -- a symbol the object marks hidden or private is not
  an export. ELF: ``STV_HIDDEN``; Mach-O: ``N_PEXT``. A PE export directory has
  no hidden bit, so the only non-export is absence from the directory and the
  cell is not applicable (skipped with that reason).
* ``weak_binding_applies`` -- a removed weak export carries its linkage, so a
  policy ``binding: weak`` rule demotes it and ``binding: global`` does not.
  ELF and Mach-O run end to end (dump, compare, suppression). A PE export has
  no linkage flag, so its removal reads unknown and the weak rule must not
  demote it (fail closed).
* ``unread_types_not_empty`` -- a side whose dependency types cannot be
  classified reports that, rather than an empty set. ELF and Mach-O have an
  Itanium-readable export table and classify; PE is unread.

The oracle is the object format's own convention, written here, not the
helper under test. Each cell asserts the same finding-class outcome for the
format's twin input.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.extract.elf_symbol_classify import _pyelftools_exported_symbols
from abicheck.macho_metadata import parse_macho_metadata
from abicheck.model import AbiSnapshot, Function
from abicheck.model.entities import RecordType, TypeField
from abicheck.model.pe_facts import PeExport, PeMetadata
from abicheck.workflows.dump.dependency_internals import dependency_internals
from abicheck.workflows.dump.dwarf_dependency_types import (
    UnreachableDependencyTypes,
    unreachable_dependency_type_reading,
)
from tests._macho_synthetic import (
    N_EXT,
    N_PEXT,
    N_SECT,
    N_WEAK_DEF,
    MachoSymbol,
    write_dylib,
)

FORMATS = ("elf", "macho", "pe")
#: A function removal reads as ``func_removed`` where DWARF/headers back the
#: old declaration and as ``func_removed_elf_only`` for an export-table-only
#: one; both carry the same ``symbol_binding``.
_REMOVAL_KINDS = frozenset({"func_removed", "func_removed_elf_only"})

_needs_gcc = pytest.mark.skipif(
    not (shutil.which("gcc") and shutil.which("g++")),
    reason="needs gcc to build an ELF twin",
)


def _format_param(fmt: str, *, marks: tuple = ()) -> object:
    return pytest.param(fmt, id=fmt, marks=marks)


def _build_elf(tmp: Path, name: str, body: str) -> Path:
    src = tmp / f"{name}.c"
    src.write_text(body)
    lib = tmp / f"lib{name}.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-O0", str(src), "-o", str(lib)], check=True
    )
    return lib


_HIDDEN_BODY = (
    '__attribute__((visibility("hidden"))) int hidden_fn(void) { return 1; }\n'
    "int public_fn(void) { return 2; }\n"
)
_WEAK_BODY = "int public_fn(void) { return 2; }\n__attribute__((weak)) int weak_gone(void) { return 3; }\n"
_WEAK_NEW_BODY = "int public_fn(void) { return 2; }\n"


# -- rule 1: a hidden/private symbol is not an export -------------------------


def _elf_exports(lib: Path) -> set[str]:
    dynamic, _static = _pyelftools_exported_symbols(lib)
    return dynamic


def _macho_exports(lib: Path) -> set[str]:
    return {e.name for e in parse_macho_metadata(lib).exports}


@pytest.mark.parametrize(
    "fmt",
    [
        _format_param("elf", marks=(pytest.mark.integration, _needs_gcc)),
        _format_param("macho"),
        _format_param(
            "pe",
            marks=pytest.mark.skip(
                reason="a PE export directory has no hidden bit: absence from the "
                "directory is the only non-export, so the cell is not applicable"
            ),
        ),
    ],
)
def test_private_symbol_is_not_an_export(fmt: str, tmp_path: Path) -> None:
    if fmt == "elf":
        exports = _elf_exports(_build_elf(tmp_path, "hid", _HIDDEN_BODY))
        hidden, public = "hidden_fn", "public_fn"
    else:
        lib = write_dylib(
            tmp_path / "libhid.dylib",
            [
                MachoSymbol("_public_fn", N_SECT | N_EXT),
                MachoSymbol("_hidden_fn", N_SECT | N_EXT | N_PEXT),
            ],
        )
        exports = _macho_exports(lib)
        hidden, public = "hidden_fn", "public_fn"
    # Vacuity guard: the twin does export its public symbol.
    assert public in exports
    assert hidden not in exports


# -- rule 2: removed weak export carries binding; binding: weak applies -------


def _removal_changes(
    tmp_path: Path, old: Path, new: Path, rule_binding: str
) -> list[dict]:
    suppress = tmp_path / f"supp-{rule_binding}.yml"
    suppress.write_text(
        "version: 1\nsuppressions:\n"
        "  - symbol: weak_gone\n"
        f"    binding: {rule_binding}\n"
    )
    result = CliRunner().invoke(
        main,
        ["compare", str(old), str(new), "--suppress", str(suppress), "-o", "json=-"],
    )
    assert result.exit_code in (0, 2, 4), result.output
    return json.loads(result.output[result.output.index("{") :])["changes"]


def _elf_pair(tmp_path: Path) -> tuple[Path, Path]:
    old_dir, new_dir = tmp_path / "elf-old", tmp_path / "elf-new"
    old_dir.mkdir()
    new_dir.mkdir()
    return (
        _build_elf(old_dir, "w", _WEAK_BODY),
        _build_elf(new_dir, "w", _WEAK_NEW_BODY),
    )


def _macho_pair(tmp_path: Path) -> tuple[Path, Path]:
    old_dir, new_dir = tmp_path / "macho-old", tmp_path / "macho-new"
    old_dir.mkdir()
    new_dir.mkdir()
    return (
        write_dylib(
            old_dir / "libw.dylib",
            [
                MachoSymbol("_public_fn", N_SECT | N_EXT),
                MachoSymbol("_weak_gone", N_SECT | N_EXT, n_desc=N_WEAK_DEF),
            ],
        ),
        write_dylib(
            new_dir / "libw.dylib", [MachoSymbol("_public_fn", N_SECT | N_EXT)]
        ),
    )


def _weak_removal_outcome(tmp_path: Path, fmt: str, rule_binding: str) -> str:
    """``"demoted"`` when a ``binding: <rule_binding>`` rule suppresses the
    weak export's removal, ``"kept"`` when the removal stays in the report."""
    old, new = _elf_pair(tmp_path) if fmt == "elf" else _macho_pair(tmp_path)
    changes = _removal_changes(tmp_path, old, new, rule_binding)
    removed = [c for c in changes if c["kind"] in _REMOVAL_KINDS]
    assert len(removed) in (0, 1), changes
    return "demoted" if not removed else "kept"


@pytest.mark.parametrize(
    "fmt",
    [
        _format_param("elf", marks=(pytest.mark.integration, _needs_gcc)),
        _format_param("macho"),
    ],
)
@pytest.mark.parametrize(
    ("rule_binding", "expected"),
    [("weak", "demoted"), ("global", "kept")],
)
def test_binding_rule_outcome_on_a_removed_weak_export(
    fmt: str, rule_binding: str, expected: str, tmp_path: Path
) -> None:
    assert _weak_removal_outcome(tmp_path, fmt, rule_binding) == expected


def test_pe_removal_has_no_binding_so_the_weak_rule_stays_fail_closed() -> None:
    # A PE export directory records no linkage, so the dump leaves the
    # declaration's binding unset; the removal then carries no symbol_binding
    # and a ``binding: weak`` rule must not match it (fail closed, not weak).
    from abicheck.checker_policy import Verdict
    from abicheck.diff_symbols import _check_removed_function
    from abicheck.model.declarations import Visibility
    from abicheck.policy.reclassify import ReclassifyRule

    pe_export_decl = Function(
        name="weak_gone",
        mangled="weak_gone",
        return_type="int",
        visibility=Visibility.PUBLIC,
    )
    change = _check_removed_function("weak_gone", pe_export_decl, {}, False)
    assert change.symbol_binding is None
    rule = ReclassifyRule(
        to_verdict=Verdict.COMPATIBLE_WITH_RISK,
        to="risk",
        change_kind="func_removed",
        symbol="weak_gone",
        binding="weak",
    )
    assert not rule.matches(change)


# -- rule 3: an unread dependency side reports unread, not empty --------------

_API = "/src/lib/api.h"
_DEP = "/src/dep/fakevec/simd.h"
_SUM = "_ZN3lib3sumERKNS_5StateE"
_SCALE = "_ZN3lib5scaleEN7fakevec4simd9basic_vecIfNS1_10avx512_tagEEEf"


def _record(name: str, location: str, fields=(), size_bits: int | None = None):
    return RecordType(
        name=name,
        kind="struct",
        size_bits=size_bits,
        fields=[TypeField(name=n, type=t) for n, t in fields],
        source_location=location,
    )


def _dependency_types() -> list[RecordType]:
    return [
        _record("lib::State", f"{_API}:5", fields=[("acc", "int")]),
        _record("fakevec::simd::avx512_tag", f"{_DEP}:6", size_bits=8),
        _record("fakevec::simd::scratch_pad<float>", f"{_DEP}:22", size_bits=128),
    ]


def _side(fmt: str) -> AbiSnapshot:
    types = _dependency_types()
    if fmt == "elf":
        from abicheck.model.elf_facts import ElfMetadata, ElfSymbol

        return AbiSnapshot(
            library="lib",
            version="1",
            types=types,
            elf=ElfMetadata(
                soname="",
                needed=[],
                symbols=[
                    ElfSymbol(name=_SUM, is_default=True),
                    ElfSymbol(name=_SCALE, is_default=True),
                ],
                imports=[],
            ),
        )
    if fmt == "macho":
        from abicheck.model.macho_facts import MachoExport, MachoMetadata

        return AbiSnapshot(
            library="lib",
            version="1",
            types=types,
            macho=MachoMetadata(
                exports=[MachoExport(name=_SUM), MachoExport(name=_SCALE)]
            ),
            platform="macho",
        )
    return AbiSnapshot(
        library="lib.dll",
        version="1",
        types=types,
        pe=PeMetadata(exports=[PeExport(name="?scale@lib@@YAMXZ")]),
        platform="pe",
    )


_EXPECTED_READING = {
    "elf": UnreachableDependencyTypes(
        frozenset({"fakevec::simd::avx512_tag", "fakevec::simd::scratch_pad<float>"}),
        True,
    ),
    "macho": UnreachableDependencyTypes(
        frozenset({"fakevec::simd::avx512_tag", "fakevec::simd::scratch_pad<float>"}),
        True,
    ),
    # PE: decorated exports cannot establish an own root. Unread, and the empty
    # set must not be taken for "nothing is internal".
    "pe": UnreachableDependencyTypes(frozenset(), False),
}


@pytest.mark.parametrize("fmt", FORMATS)
def test_unread_dependency_side_reports_unread_not_empty(fmt: str) -> None:
    snap = _side(fmt)
    assert unreachable_dependency_type_reading(snap) == _EXPECTED_READING[fmt]
    assert dependency_internals(snap).types_read is _EXPECTED_READING[fmt].read
