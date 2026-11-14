"""Covariant returns and override slots on real compiled libraries.

Built with g++ (and clang++ when installed), compared through ``abicheck
compare`` with the public header and with DWARF only. The oracle per row is
the Itanium C++ ABI's: a return narrowed to a derived class whose path to the
old class is a primary (offset-0, non-virtual) base returns the same address
and adds no slot; one whose old class is a secondary base at a non-zero offset
is a break (the address moves, and an overrider gains a thunk slot); an
override added to a derived class reuses its base's slot.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model.change_catalog.kinds import ChangeKind

pytestmark = pytest.mark.integration

# The oracle is the Itanium C++ ABI. On Windows ``clang++`` targets the MSVC
# ABI (and links no ELF-style -shared -fPIC DSO), so only MinGW g++ applies.
_COMPILERS = [
    c
    for c in ("g++", "clang++")
    if shutil.which(c) and not (sys.platform == "win32" and c == "clang++")
]
_DARWIN = sys.platform == "darwin"

# The DWARF-only lane reads record layouts from the library's own DWARF.
# abicheck reads DWARF types from ELF only: a Mach-O dylib keeps its DWARF in
# the object files (or a dSYM, read for producer signals, not types) and a PE
# carries none, so off Linux that lane has no type evidence to judge a covariant return by.
_LANES = [
    pytest.param(True, id="headers"),
    pytest.param(
        False,
        id="dwarf",
        marks=pytest.mark.skipif(
            sys.platform != "linux", reason="DWARF type extraction is ELF-only"
        ),
    ),
]

_HEADER = """#pragma once
struct R0 { virtual ~R0(); int r; };
struct Pad { virtual ~Pad(); long p; };
struct R1 { virtual ~R1(); int r; };
struct Prim : R0 { int x; };
struct Sec : Pad, R1 { int x; };
struct B { virtual ~B(); virtual int f(); };
struct D : B { int d; @@D@@ };
R0* @@PRIM@@ make_prim();
R1* @@SEC@@ make_sec();
B* make_d();
"""
_SOURCE = """#include "api.h"
R0::~R0() {}
Pad::~Pad() {}
R1::~R1() {}
B::~B() {}
int B::f() { return 1; }
@@DF@@
Prim* make_prim_impl() { return new Prim(); }
Sec* make_sec_impl() { return new Sec(); }
"""


def _render(narrow: bool, override: bool) -> tuple[str, str]:
    h = _HEADER.replace("@@D@@", "int f();" if override else "")
    s = _SOURCE.replace("@@DF@@", "int D::f() { return 2; }" if override else "")
    # The two factories' return types: R0*/R1* (old) or Prim*/Sec* (new).
    if narrow:
        h = h.replace("R0* @@PRIM@@", "Prim*").replace("R1* @@SEC@@", "Sec*")
        s += "Prim* make_prim() { return make_prim_impl(); }\n"
        s += "Sec* make_sec() { return make_sec_impl(); }\n"
    else:
        h = h.replace(" @@PRIM@@", "").replace(" @@SEC@@", "")
        s += "R0* make_prim() { return make_prim_impl(); }\n"
        s += "R1* make_sec() { return make_sec_impl(); }\n"
    s += "B* make_d() { return new D(); }\n"
    return h, s


def _build(side: Path, compiler: str, narrow: bool, override: bool) -> Path:
    side.mkdir(parents=True)
    h, s = _render(narrow, override)
    (side / "api.h").write_text(h, encoding="utf-8")
    (side / "api.cpp").write_text(s, encoding="utf-8")
    lib = side / "libcov.so"
    subprocess.run(
        [
            compiler,
            "-shared",
            "-fPIC",
            "-g",
            "-O0",
            "-std=c++17",
            f"-I{side}",
            str(side / "api.cpp"),
            "-o",
            str(lib),
            # Mach-O: the install name defaults to the output path, which
            # differs per side and would read as an LC_ID_DYLIB change; pin it
            # as a release build does.
            *(["-install_name", "@rpath/libcov.dylib"] if _DARWIN else []),
        ],
        check=True,
        capture_output=True,
    )
    return lib


def _findings(tmp: Path, compiler: str, headers: bool, override: bool):
    old = _build(tmp / "old", compiler, narrow=False, override=False)
    new = _build(tmp / "new", compiler, narrow=True, override=override)
    out = tmp / "r.json"
    args = ["compare", str(old), str(new), "-o", f"json={out}"]
    if headers:
        args += ["--header", f"old={tmp / 'old' / 'api.h'}"]
        args += ["--header", f"new={tmp / 'new' / 'api.h'}"]
    CliRunner().invoke(main, args, catch_exceptions=False)
    report = json.loads(out.read_text(encoding="utf-8"))
    return [(c["kind"], c.get("symbol", "")) for c in report["changes"]]


@pytest.mark.skipif(not _COMPILERS, reason="no C++ compiler installed")
@pytest.mark.parametrize("compiler", _COMPILERS)
@pytest.mark.parametrize("headers", _LANES)
def test_offset_zero_narrowing_is_compatible_and_a_secondary_base_is_not(
    tmp_path: Path, compiler: str, headers: bool
) -> None:
    found = _findings(tmp_path, compiler, headers, override=False)
    changed = ChangeKind.FUNC_RETURN_CHANGED.value
    assert (
        ChangeKind.FUNC_RETURN_COVARIANT_NARROWED.value,
        "_Z9make_primv",
    ) in found, found
    assert (changed, "_Z9make_primv") not in found, found
    assert (changed, "_Z8make_secv") in found, found
    assert (
        ChangeKind.FUNC_RETURN_COVARIANT_NARROWED.value,
        "_Z8make_secv",
    ) not in found, found


@pytest.mark.skipif(not _COMPILERS, reason="no C++ compiler installed")
@pytest.mark.parametrize("compiler", _COMPILERS)
@pytest.mark.parametrize("headers", [True, False], ids=["headers", "dwarf"])
def test_an_added_override_is_not_a_vtable_change(
    tmp_path: Path, compiler: str, headers: bool
) -> None:
    found = _findings(tmp_path, compiler, headers, override=True)
    kinds = {k for k, _ in found}
    assert ChangeKind.FUNC_ADDED.value in kinds, found
    assert not any(
        k == ChangeKind.TYPE_VTABLE_CHANGED.value and s == "D" for k, s in found
    ), found
