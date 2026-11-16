"""DWARF standard-layout derivation, checked against the compiler's own answer.

The oracle is ``std::is_standard_layout`` compiled by g++ in the same translation
unit as the record DIEs read back through pyelftools, so neither side is the
implementation under test. Each shape is one rule of [class.prop]/3 (N4659);
several shapes per rule, so a rule cannot pass by matching one cell.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from elftools.elf.elffile import ELFFile

from abicheck.extract.dwarf_standard_layout import StandardLayoutCollector

pytestmark = pytest.mark.integration

_RECORD_TAGS = {"DW_TAG_structure_type", "DW_TAG_class_type", "DW_TAG_union_type"}

# (name, declaration) -- each record is top-level, so its DIE name is unique.
_SHAPES: list[tuple[str, str]] = [
    # [class.prop]/3.3: one access control for every data member.
    ("PlainStruct", "struct PlainStruct { int a; int b; };"),
    ("MixedAccessStruct", "struct MixedAccessStruct { int a; protected: int b; };"),
    ("ClassSameAccess", "class ClassSameAccess { int a; int b; };"),
    (
        "ClassPublicPrivate",
        "class ClassPublicPrivate { public: int a; private: int b; };",
    ),
    (
        "ClassPrivateThenPublic",
        "class ClassPrivateThenPublic { int a; public: int b; };",
    ),
    # Access mixed only in a member's own nested record ([class.prop]/3.4 via 3.3).
    (
        "OuterOfMixed",
        "struct InnerMixedAccess { int a; private: int b; };\n"
        "struct OuterOfMixed { InnerMixedAccess i; };",
    ),
    # [class.prop]/3.2: no virtual function, no virtual base.
    ("VirtualMethod", "struct VirtualMethod { virtual void f() {} int a; };"),
    (
        "VirtualBase",
        "struct VB { int v; };\nstruct VirtualBase : virtual VB { int a; };",
    ),
    (
        "InheritedVirtual",
        "struct PolyBase { virtual void f() {} int p; };\n"
        "struct InheritedVirtual : PolyBase { int q; };",
    ),
    # [class.prop]/3.1: no reference data member.
    ("LvalueRefMember", "struct LvalueRefMember { int &r; };"),
    ("RvalueRefMember", "struct RvalueRefMember { int &&r; };"),
    # [class.prop]/3.5: data members in at most one class of the hierarchy.
    (
        "DataInBaseAndDerived",
        "struct DataBase { int x; };\nstruct DataInBaseAndDerived : DataBase { int y; };",
    ),
    (
        "DerivedNoData",
        "struct BaseWithData { int x; };\nstruct DerivedNoData : BaseWithData {};",
    ),
    (
        "TwoBasesWithData",
        "struct BaseX { int a; };\nstruct BaseY { int b; };\n"
        "struct TwoBasesWithData : BaseX, BaseY {};",
    ),
    (
        "EmptyB",
        "struct EmptyA {};\nstruct EmptyB : EmptyA {};",
    ),
    # [class.prop]/3.4: a non-standard-layout member or base is non-standard.
    (
        "HoldsPoly",
        "struct Poly { virtual void f() {} int a; };\nstruct HoldsPoly { Poly p; int b; };",
    ),
    (
        "HoldsPlain",
        "struct Plain2 { int a; };\nstruct HoldsPlain { Plain2 p; int b; };",
    ),
    (
        "HoldsPolyArray",
        "struct Poly2 { virtual void f() {} int a; };\nstruct HoldsPolyArray { Poly2 p[2]; };",
    ),
    # [class.prop]/3.6: no base class of the first member's type.
    (
        "FirstMemberIsBase",
        "struct FBase { int e; };\nstruct FirstMemberIsBase : FBase { FBase first; };",
    ),
    (
        "FirstMemberUnrelated",
        "struct GBase { int e; };\nstruct FirstMemberUnrelated : GBase { int other; };",
    ),
    ("UnionPlain", "union UnionPlain { int a; float b; };"),
    # [class.prop]/3.7: a base subobject occurs twice along two paths (g++ rejects).
    (
        "RepeatedBase",
        "struct RA { int a; };\nstruct RB : RA { int b; };\nstruct RC : RA { int c; };\n"
        "struct RepeatedBase : RB, RC {};",
    ),
    # A static data member is not a non-static member: DWARF 4 writes it as a
    # DW_TAG_member with DW_AT_external and DW_AT_declaration.
    (
        "StaticMemberClass",
        "class StaticMemberClass { int a; public: static int s; };",
    ),
]


#: Shapes with a virtual function or base, and the polymorphic records they hold.
_VIRTUAL = (
    "VirtualMethod",
    "VB",
    "VirtualBase",
    "PolyBase",
    "InheritedVirtual",
    "Poly",
    "Poly2",
)


def _compile_and_oracle(tmp: Path, dwarf: int) -> tuple[Path, dict[str, bool]]:
    """Build the shapes into one executable and return the compiler's answers."""
    gxx = shutil.which("g++")
    if gxx is None:
        pytest.fail("g++ is required by the integration marker")
    body = "\n".join(decl for _, decl in _SHAPES)
    # A class with an inline virtual function gets a full DWARF definition only
    # once its constructor (and so its vtable) is emitted, so construct each one.
    constructs = "\n".join(
        f"  {{ volatile {name}* p = new {name}; delete p; }}" for name in _VIRTUAL
    )
    prints = "\n".join(
        f'  std::printf("{name} %d\\n", (int)std::is_standard_layout<{name}>::value);'
        for name, _ in _SHAPES
    )
    src = tmp / "shapes.cpp"
    src.write_text(
        "#include <cstdio>\n#include <type_traits>\n"
        f"{body}\nint main() {{\n{constructs}\n{prints}\n  return 0;\n}}\n",
        encoding="utf-8",
    )
    exe = tmp / "shapes"
    subprocess.run(
        [
            gxx,
            "-std=c++17",
            "-g",
            f"-gdwarf-{dwarf}",
            "-O0",
            # GCC drops the DWARF of a type nothing uses; the shapes are only
            # queried by the oracle, so keep every definition.
            "-fno-eliminate-unused-debug-types",
            str(src),
            "-o",
            str(exe),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    run = subprocess.run([str(exe)], check=True, capture_output=True, text=True)
    oracle = {}
    for line in run.stdout.splitlines():
        name, value = line.split()
        oracle[name] = value == "1"
    return exe, oracle


@pytest.mark.parametrize("dwarf", [4, 5])
def test_dwarf_standard_layout_matches_the_compiler(tmp_path: Path, dwarf: int) -> None:
    exe, oracle = _compile_and_oracle(tmp_path, dwarf)
    shapes = dict(_SHAPES)
    collector = StandardLayoutCollector()
    holders: dict[str, SimpleNamespace] = {}
    with exe.open("rb") as fh:
        dwarf = ELFFile(fh).get_dwarf_info()
        # Every complete record of the unit goes in: a shape's member or base is
        # resolved through its own summary, as the snapshot builder does.
        for cu in dwarf.iter_CUs():
            for die in cu.iter_DIEs():
                if die.tag not in _RECORD_TAGS or "DW_AT_declaration" in die.attributes:
                    continue
                name = (
                    die.attributes["DW_AT_name"].value.decode()
                    if "DW_AT_name" in die.attributes
                    else ""
                )
                holder = SimpleNamespace(
                    is_standard_layout=None, is_standard_layout_fact=None
                )
                collector.add(die, list(die.iter_children()), holder)
                if name in shapes:
                    holders[name] = holder
    collector.finalize()
    derived = {name: holder.is_standard_layout for name, holder in holders.items()}
    assert set(derived) == set(oracle) == set(shapes)
    mismatches = {
        name: (derived[name], oracle[name])
        for name in oracle
        if derived[name] is not oracle[name]
    }
    assert not mismatches, f"DWARF derivation vs compiler: {mismatches}"
