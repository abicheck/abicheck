"""gcc DWARF triviality-for-calls matches the compiler's own answer.

gcc writes no ``DW_AT_calling_convention`` on a type, so abicheck derives the
answer from the members; clang states it. The oracle is clang's attribute on
the same source, never abicheck's helper. gcc names the in-class constructor
declaration with the unified ``C4`` variant, which a ``C1``/``C2`` linkage
match never saw, so a user copy constructor read as trivial under gcc.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    # The probe reads DWARF from an ELF shared object. On macOS ``g++`` is
    # Apple clang (no gcc derivation path to test) and ld64 leaves DWARF in
    # the object files; on Windows there is no ELF output at all.
    pytest.mark.skipif(sys.platform != "linux", reason="ELF + gcc DWARF probe"),
]

_SHAPES = {
    "copy": "struct S { int a; S() = default; S(const S& o); };\nS::S(const S& o) : a(o.a) {}",
    "move": "struct S { int a; S() = default; S(S&& o); };\nS::S(S&& o) : a(o.a) {}",
    "nonconst_copy": "struct S { int a; S() = default; S(S& o); };\nS::S(S& o) : a(o.a) {}",
    "converting": "struct S { int a; S() = default; S(int v); };\nS::S(int v) : a(v) {}",
    "default_ctor": "struct S { int a; S(); };\nS::S() : a(0) {}",
    "defaulted_copy": "struct S { int a; S() = default; S(const S&) = default; };",
    "user_dtor": "struct S { int a; ~S(); };\nS::~S() {}",
    "plain": "struct S { int a; };",
}


def _struct_die(so: Path):
    from elftools.elf.elffile import ELFFile

    with so.open("rb") as fh:
        elf = ELFFile(fh)
        for cu in elf.get_dwarf_info().iter_CUs():
            for die in cu.iter_DIEs():
                name = die.attributes.get("DW_AT_name")
                if (
                    die.tag == "DW_TAG_structure_type"
                    and name is not None
                    and name.value == b"S"
                    and "DW_AT_byte_size" in die.attributes
                ):
                    yield die, cu


def _build(tmp: Path, cxx: str, src: str, tag: str) -> Path:
    cpp = tmp / f"{tag}.cpp"
    cpp.write_text(src + "\nint use(S v) { return v.a; }\n", encoding="utf-8")
    out = tmp / f"{tag}_{cxx}.so"
    subprocess.run(
        [cxx, "-std=c++17", "-g", "-O0", "-shared", "-fPIC", str(cpp), "-o", str(out)],
        check=True,
    )
    return out


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_gcc_derived_triviality_matches_clang_calling_convention(
    tmp_path: Path, shape: str
) -> None:
    if not (shutil.which("g++") and shutil.which("clang++")):
        pytest.skip("needs g++ and clang++")
    from abicheck.dwarf_advanced import _value_abi_trait_for_type

    clang_die, _ = next(_struct_die(_build(tmp_path, "clang++", _SHAPES[shape], shape)))
    cc = int(clang_die.attributes["DW_AT_calling_convention"].value)
    expected = {4: "nontrivial", 5: "trivial"}[cc]

    gcc_so = _build(tmp_path, "g++", _SHAPES[shape], shape)
    die, cu = next(_struct_die(gcc_so))
    assert "DW_AT_calling_convention" not in die.attributes  # derivation path
    assert _value_abi_trait_for_type(die, cu) == expected
