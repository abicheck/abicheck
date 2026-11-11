"""The native GCC header backend records vptr and vtable slots as castxml does.

Bug class: a header backend leaves a fact empty, or records it from a form
that depends on unrelated state. The GCC plugin recorded no vptr offset, so
``vptr_introduced`` never fired, and it read vtable slots off the vtable
initializer, where a pure virtual is ``__cxa_pure_virtual`` and an
undefined virtual destructor's two slots are ``0`` until its clones exist.
Defining a pure virtual (``type_lost_abstract``) therefore read as a vtable
change (BREAKING) under the gcc backend (edit corpus ``vptr_introduced``,
``type_lost_abstract``).

Oracle: castxml's ``RecordType.vtable``/``vptr_offset_bits`` for the same
header, and the Itanium rule that a dynamic class's vptr is at offset 0.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.dumper import dump
from abicheck.model.snapshot import AbiSnapshot
from tests.test_native_gcc_workflow import native_plugins  # noqa: F401 (fixture)

pytestmark = pytest.mark.integration

# (header, library source); every case declares the records named in _NAMES.
_CASES = {
    "plain_virtual": (
        "struct R { int a; virtual int get(); };",
        "int R::get() { return a; }",
    ),
    "pure_with_dtor": (
        "struct R { virtual int area() = 0; virtual ~R(); };",
        "R::~R() {}",
    ),
    "pure_defined": (
        "struct R { virtual int area(); virtual ~R(); };",
        "R::~R() {}\nint R::area() { return 0; }",
    ),
    "derived_override": (
        "struct B { virtual int f(); virtual ~B(); };\n"
        "struct R : B { int f() override; virtual void g() = 0; };",
        "int B::f() { return 1; }\nB::~B() {}\nint R::f() { return 2; }",
    ),
    "non_polymorphic": (
        "struct R { int a; int get(); };",
        "int R::get() { return a; }",
    ),
}
_NAMES = ("R", "B")


def _require() -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("ELF shared library")
    for tool in ("g++", "castxml"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")


@pytest.fixture
def gcc_env(native_plugins: tuple[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setenv("ABICHECK_GCC_PLUGIN", str(native_plugins[1]))


def _lib(root: Path, name: str, header: str, source: str) -> tuple[Path, Path]:
    d = root / name
    d.mkdir()
    (d / "a.hpp").write_text(header + "\nint use(R *r);\n")
    (d / "a.cpp").write_text(
        f'#include "a.hpp"\n{source}\nint use(R *r) {{ return 0; }}\n'
    )
    so = d / "liba.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(so), str(d / "a.cpp")], check=True
    )
    return so, d / "a.hpp"


def _dump(so: Path, hdr: Path, backend: str) -> AbiSnapshot:
    return dump(
        so,
        [hdr],
        version=so.parent.name,
        compiler="c++",
        lang="c++",
        header_backend=backend,
    )


def _facts(snap: AbiSnapshot) -> dict[str, tuple[list[str], int | None]]:
    return {
        t.name: (list(t.vtable or []), t.vptr_offset_bits)
        for t in snap.declarations.types
        if t.name in _NAMES
    }


@pytest.mark.parametrize("case", sorted(_CASES))
def test_gcc_vtable_and_vptr_match_castxml(
    case: str, tmp_path: Path, gcc_env: None
) -> None:
    _require()
    so, hdr = _lib(tmp_path, case, *_CASES[case])
    want = _facts(_dump(so, hdr, "castxml"))
    assert want, "the oracle sees the records"
    assert _facts(_dump(so, hdr, "gcc")) == want


def test_gcc_reports_a_first_virtual_as_vptr_introduced(
    tmp_path: Path, gcc_env: None
) -> None:
    _require()
    snaps = [
        _dump(*_lib(tmp_path, c, *_CASES[c]), "gcc")
        for c in ("non_polymorphic", "plain_virtual")
    ]
    assert "vptr_introduced" in {c.kind.value for c in compare(*snaps).changes}


def test_defining_a_pure_virtual_is_not_a_vtable_change_on_gcc(
    tmp_path: Path, gcc_env: None
) -> None:
    _require()
    snaps = [
        _dump(*_lib(tmp_path, c, *_CASES[c]), "gcc")
        for c in ("pure_with_dtor", "pure_defined")
    ]
    kinds = {c.kind.value for c in compare(*snaps).changes}
    assert "type_lost_abstract" in kinds
    assert "type_vtable_changed" not in kinds


def test_a_real_vtable_reorder_is_still_reported_on_gcc(
    tmp_path: Path, gcc_env: None
) -> None:
    """Negative control: swapping two virtuals' slots is a vtable change."""
    _require()
    headers = (
        "struct R { virtual int f(); virtual int g(); };",
        "struct R { virtual int g(); virtual int f(); };",
    )
    src = "int R::f() { return 1; }\nint R::g() { return 2; }"
    snaps = [
        _dump(*_lib(tmp_path, f"v{i}", h, src), "gcc") for i, h in enumerate(headers)
    ]
    assert "type_vtable_changed" in {c.kind.value for c in compare(*snaps).changes}
