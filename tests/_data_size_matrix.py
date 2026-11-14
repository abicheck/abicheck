"""The record matrix the data-size integration tests share, and its oracle.

Oracle: the compilers' own layout. For each record ``R`` a probe compiles
``struct D : R { char z; };`` and reads ``__builtin_offsetof(D, z)``: a
``char`` is placed at the first byte past ``R``'s data size, so the offset is
dsize(R) in bytes (``sizeof(R)`` when R is POD for layout). It is measured
with g++ and clang++ in C++11, C++17 and C++20; a record whose answers
differ is one the compilers disagree on, so a derivation must leave it
unknown.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NamedTuple


class Case(NamedTuple):
    decl: str
    #: castxml must derive a value (not leave it unknown).
    castxml_known: bool


EMPTY = "struct Empty {};"
CASES: dict[str, Case] = {
    "Pod": Case("struct Pod { int a; char c; };", True),
    "Ctor": Case("struct Ctor { Ctor(); int a; char c; };", True),
    "InlineCtor": Case("struct InlineCtor { InlineCtor() {} int a; char c; };", True),
    "IntCtor": Case("struct IntCtor { IntCtor(int); int a; char c; };", True),
    "DefCtor": Case("struct DefCtor { DefCtor() = default; int a; char c; };", False),
    "DelCopy": Case("struct DelCopy { DelCopy(const DelCopy &) = delete; int a; char c; };", False),
    "Nsdmi": Case("struct Nsdmi { int a = 1; char c; };", True),
    "Priv": Case("struct Priv { private: int a; public: char c; };", True),
    "AllPriv": Case("class AllPriv { int a; char c; public: int get(); };", True),
    "Dtor": Case("struct Dtor { ~Dtor(); int a; char c; };", True),
    "DefDtor": Case("struct DefDtor { ~DefDtor() = default; int a; char c; };", True),
    "CopyAs": Case("struct CopyAs { CopyAs &operator=(const CopyAs &); int a; char c; };", True),
    "MoveAs": Case("struct MoveAs { MoveAs &operator=(MoveAs &&); int a; char c; };", True),
    "Base0": Case("struct Base0 { int a; };", True),
    "Der": Case("struct Der : Base0 { char c; };", True),
    "EB": Case("struct EB : Empty { int a; char c; };", True),
    "EmptyNP": Case("struct EmptyNP : Empty {};", True),
    "Virt": Case("struct Virt { virtual void f(); char c; };", True),
    "VirtOnly": Case("struct VirtOnly { virtual void f(); };", True),
    "VirtDer": Case("struct VirtDer : Virt { void f() override; char d; };", True),
    "Bits": Case("struct Bits { Bits(); int a : 3; };", True),
    "Bits2": Case("struct Bits2 { Bits2(); char c; int a : 11; };", True),
    "UnnamedBF": Case("struct UnnamedBF { UnnamedBF(); char c; int : 12; };", True),
    "HasNonPod": Case("struct HasNonPod { Ctor m; };", True),
    "HasNonPod2": Case("struct HasNonPod2 { Ctor m; char x; };", True),
    "ArrNonPod": Case("struct ArrNonPod { Ctor m[2]; };", True),
    "HasPod": Case("struct HasPod { Pod m; char x; };", True),
    "Ref": Case("struct Ref { int &r; char c; };", True),
    "StaticM": Case("struct StaticM { static int s; int a; char c; };", True),
    "MemFn": Case("struct MemFn { void f(); int a; char c; };", True),
    "NpBaseD": Case("struct NpBaseD : Ctor {};", True),
    "NpBaseMore": Case("struct NpBaseMore : Ctor { short s; };", True),
    "PrivBase": Case("struct PrivBase : Priv { char d; };", True),
    "VB": Case("struct VB : virtual Base0 { char c; };", False),
    "NUA": Case("struct NUA { NUA(); int a; [[no_unique_address]] Empty e; char c; };", False),
    "NUAPod": Case("struct NUAPod { [[no_unique_address]] Empty e; int a; char c; };", False),
    "NUANP": Case("struct NUANP { NUANP(); [[no_unique_address]] Ctor m; };", False),
    "NUAMacro": Case("#define MY_NUA [[no_unique_address]]\nstruct NUAMacro { NUAMacro(); MY_NUA Ctor m; };", False),
    "EmptyM": Case("struct EmptyM { EmptyM(); int a; char c; Empty e; };", True),
    # nvsize != dsize: Empty is a base of NvB at 0, so NvX's own Empty base
    # goes past NvB's data (offset 5): dsize(NvX) 5, nvsize(NvX) 6. NvY
    # allocates NvX by its nvsize.
    "NvB": Case("struct NvB : Empty { int i; char c; };", True),
    "NvX": Case("struct NvX : NvB, Empty {};", True),
    "NvY": Case("struct NvY : NvX {};", True),
}  # fmt: skip

COMPILERS = ("g++", "clang++")


def target_triple(cc: str) -> str | None:
    """*cc*'s default target triple, or ``None`` when it does not say."""
    run = subprocess.run([cc, "-dumpmachine"], capture_output=True, text=True)
    return run.stdout.strip() or None if run.returncode == 0 else None


STDS = ("c++11", "c++17", "c++20")


def header_text() -> str:
    body = "\n".join(case.decl for case in CASES.values())
    return f"#pragma once\n{EMPTY}\n{body}\n"


def compiler_dsizes(
    tmp: Path, header: Path, compilers: tuple[str, ...] = COMPILERS
) -> dict[str, set[int]]:
    """Every (compiler, dialect) data size in bits, per record name."""
    probe = tmp / "probe.cpp"
    lines = [f'#include "{header.name}"', "#include <cstdio>", "int main() {"]
    for name in CASES:
        lines.append(
            f'{{ struct D : {name} {{ char z; }}; std::printf("{name} %zu\\n", '
            f"(size_t)__builtin_offsetof(D, z)); }}"
        )
    lines.append("}")
    probe.write_text("\n".join(lines) + "\n")
    out: dict[str, set[int]] = {name: set() for name in CASES}
    for cc in compilers:
        for std in STDS:
            exe = tmp / f"probe_{cc}_{std}"
            subprocess.run(
                [cc, "-w", f"-std={std}", str(probe), "-o", str(exe)], check=True
            )
            run = subprocess.run([str(exe)], check=True, capture_output=True, text=True)
            for row in run.stdout.splitlines():
                name, value = row.split()
                out[name].add(int(value) * 8)
    return out
