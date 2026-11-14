"""Pure virtuals and abstractness read at DWARF depth, on gcc and clang.

Bug class: GCC writes ``DW_VIRTUALITY_virtual`` for a pure virtual member, so a
GCC-built DWARF snapshot never saw a virtual become pure; neither compiler's
DWARF-only snapshot derived ``is_abstract``; and a class whose only virtual is
pure (no key function, so only a declaration DIE) read as ``type_removed``.
Oracle, per shape and compiler, from the C++ rules (not abicheck's helpers):

=======================  ==================  ============  ======================
shape                    pure member seen?   is_abstract   record present?
=======================  ==================  ============  ======================
key fn + pure virtual    yes (both)          True          yes
key fn, no pure          no                  False         yes
derived, no pure         no                  None (bases)  yes
pure only, no key fn     clang only          --            not "removed"
=======================  ==================  ============  ======================
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.dumper import dump
from abicheck.model.change_catalog.kinds import ChangeKind

COMPILERS = [c for c in ("g++", "clang++") if shutil.which(c)]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux") or not COMPILERS,
        reason="needs g++ or clang++ producing ELF (Linux)",
    ),
]

_SHAPES = """
struct KeyPure { virtual int m(); virtual int z() = 0; virtual ~KeyPure(); int a; };
int KeyPure::m() { return a; }
KeyPure::~KeyPure() {}
struct KeyPlain { virtual int m(); virtual ~KeyPlain(); int a; };
int KeyPlain::m() { return a; }
KeyPlain::~KeyPlain() {}
struct Derived : KeyPlain { int m() override; };
int Derived::m() { return 2; }
int use_kp(KeyPure& k) { return k.z(); }
"""


def _build(tmp: Path, cxx: str, name: str, source: str) -> Path:
    src = tmp / f"{name}.cpp"
    src.write_text(source, encoding="utf-8")
    out = tmp / f"lib{name}.so"
    cmd = [cxx, "-g", "-O0", "-shared", "-fPIC", "-o", str(out), str(src)]
    run = subprocess.run(cmd, capture_output=True, text=True, check=False)
    # A configured compiler that ran and failed is a failure, never a skip.
    assert run.returncode == 0, f"{' '.join(cmd)}\n{run.stderr}"
    return out


@pytest.mark.parametrize("cxx", COMPILERS)
def test_abstractness_and_pure_members_per_shape(tmp_path: Path, cxx: str) -> None:
    snap = dump(_build(tmp_path, cxx, "shapes", _SHAPES), [], version="1")
    records = {t.name: t for t in snap.declarations.types}
    assert records["KeyPure"].is_abstract is True
    assert records["KeyPlain"].is_abstract is False
    assert records["Derived"].is_abstract is None
    pure = {f.name for f in snap.declarations.functions if f.is_pure_virtual}
    assert pure == {"KeyPure::z"}


@pytest.mark.parametrize("cxx", COMPILERS)
def test_a_method_becoming_pure_reads_abstract_not_removed(
    tmp_path: Path, cxx: str
) -> None:
    old_src = "struct S { virtual int m(); virtual int z(); virtual ~S(); };\n"
    old_src += "int S::m() { return 1; }\nint S::z() { return 2; }\nS::~S() {}\n"
    new_src = "struct S { virtual int m(); virtual int z() = 0; virtual ~S(); };\n"
    new_src += "int S::m() { return 1; }\nS::~S() {}\n"
    old = dump(_build(tmp_path, cxx, "old", old_src), [], version="1")
    new = dump(_build(tmp_path, cxx, "new", new_src), [], version="2")
    kinds = {c.kind for c in compare(old, new).changes}
    assert ChangeKind.FUNC_VIRTUAL_BECAME_PURE in kinds
    assert ChangeKind.TYPE_BECAME_ABSTRACT in kinds


@pytest.mark.parametrize("cxx", COMPILERS)
def test_a_pure_only_class_is_not_reported_removed(tmp_path: Path, cxx: str) -> None:
    # No key function: both compilers emit only a declaration DIE for Plain,
    # but its member get() is still defined, so the type was not removed.
    old_src = "struct Plain { int a; int get(); };\nint Plain::get() { return a; }\n"
    new_src = "struct Plain { int a; virtual int get() = 0; };\n"
    new_src += "int Plain::get() { return a; }\n"
    old = dump(_build(tmp_path, cxx, "old", old_src), [], version="1")
    new = dump(_build(tmp_path, cxx, "new", new_src), [], version="2")
    removed = [
        c.symbol for c in compare(old, new).changes if c.kind is ChangeKind.TYPE_REMOVED
    ]
    assert removed == []
