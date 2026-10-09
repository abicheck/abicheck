"""CastXML's derived ``is_trivially_copyable`` never contradicts the compiler.

Oracle: g++'s own ``__is_trivially_copyable(T)`` builtin, evaluated on the
same header. ``record_traits.trivially_copyable`` may answer ``None``
(unproven) but a ``True``/``False`` must match the compiler, and the plain
shapes the derivation is built to prove must not stay unknown.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from xml.etree.ElementTree import parse

import pytest

from abicheck.extract.headers.castxml.context import CastxmlParserContext
from abicheck.extract.headers.castxml.record_traits import trivially_copyable

pytestmark = pytest.mark.integration

_DECLS = {
    "Plain": ("struct Plain { double x; double y; };", True),
    "UserDtor": ("struct UserDtor { int x; ~UserDtor() {} };", False),
    "DefaultedDtor": ("struct DefaultedDtor { int x; ~DefaultedDtor() = default; };", None),
    "OutOfLineDtor": ("struct OutOfLineDtor { int x; ~OutOfLineDtor(); };", False),
    "UserCopy": ("struct UserCopy { int x; UserCopy(const UserCopy &); UserCopy(); };", False),
    "UserAssign": ("struct UserAssign { int x; UserAssign &operator=(const UserAssign &o) { x = o.x; return *this; } };", False),
    "Virtual": ("struct Virtual { virtual void f(); int x; };", False),
    "VirtualBase": ("struct VirtualBase : virtual Plain { int y; };", False),
    "Derived": ("struct Derived : Plain { int y; };", True),
    "HasUserDtorMember": ("struct HasUserDtorMember { UserDtor m; };", False),
    "HasArray": ("struct HasArray { Plain a[3]; const int c[2]; };", True),
    "HasPointer": ("struct HasPointer { UserDtor *p; int (*fn)(int); };", True),
    "OtherCtorOnly": ("struct OtherCtorOnly { int x; OtherCtorOnly(int v) : x(v) {} };", True),
}  # fmt: skip


def _compiler_trait(header: Path, name: str) -> bool:
    probe = header.parent / f"probe_{name}.cpp"
    probe.write_text(
        f'#include "{header.name}"\nstatic_assert(__is_trivially_copyable({name}), "");\n'
    )
    r = subprocess.run(
        ["g++", "-std=c++17", "-fsyntax-only", str(probe)], capture_output=True
    )
    return r.returncode == 0


def test_trait_agrees_with_compiler(tmp_path: Path) -> None:
    for tool in ("g++", "castxml"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")
    header = tmp_path / "traits.hpp"
    body = "\n".join(decl for decl, _ in _DECLS.values())
    # Fields of class type need every function used declared; keep it a TU.
    header.write_text(f"#pragma once\n{body}\n")
    xml = tmp_path / "traits.xml"
    subprocess.run(
        ["castxml", "--castxml-output=1", "--castxml-cc-gnu", "g++", "-std=c++17",
         "-o", str(xml), str(header)],
        check=True,
    )  # fmt: skip
    ctx = CastxmlParserContext(parse(xml).getroot(), set(), set())
    ctx.build_id_map()
    traits = {
        el.get("name"): trivially_copyable(ctx, el)
        for el in ctx.record_els
        if el.get("name") in _DECLS
    }
    for name, (_, expected) in _DECLS.items():
        got = traits[name]
        if got is not None:
            assert got is _compiler_trait(header, name), name
        assert got is expected, (name, got)
