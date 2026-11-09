"""C++ member functions in DWARF-only snapshots (extractor generation 44).

A member's definition DIE names its in-class declaration only through
``DW_AT_specification``; before generation 44 the walker dropped both, so a
DWARF-only snapshot held no method and a dropped ``const`` read as an ELF
symbol removal plus addition (the config-matrix corpus's ``method_const_drop``
and ``virtual_become_pure`` cells).

Oracles, independent of the DWARF walker:

* the Itanium mangling of each method (``_ZNK`` const, ``_ZNV`` volatile),
  and the castxml header lane's reading of the same class (``is_const``,
  ``is_static``, ``is_virtual`` per mangled name);
* for comparisons, the kind fixed per edit by what the edit is.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.dumper import dump
from abicheck.model import AbiSnapshot

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="builds ELF shared objects"),
]

_CXX = {"gcc": "g++", "clang": "clang++"}

_CLASS = """\
namespace lib {
class Shape {
public:
    virtual ~Shape();
    virtual int area() const;
    virtual int kind() const;
    int plain();
    int touch() volatile;
    static int count();
    int w;
};
int shape_area(Shape& s);
}
"""
_SOURCE = """\
#include "api.h"
namespace lib {
Shape::~Shape() {}
int Shape::area() const { return w; }
int Shape::kind() const { return 1; }
int Shape::plain() { return w; }
int Shape::touch() volatile { return w; }
int Shape::count() { return 3; }
int shape_area(Shape& s) { return s.area(); }
}
"""


def _need(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        pytest.skip(f"needs {tool}")
    return path


def _build(work: Path, compiler: str, header: str, source: str) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    (work / "api.h").write_text(header)
    (work / "api.cpp").write_text(source)
    so = work / "libapi.so"
    proc = subprocess.run(
        [_need(_CXX[compiler]), "-shared", "-fPIC", "-g", "-o", str(so), "api.cpp"],
        capture_output=True,
        text=True,
        cwd=work,
    )
    assert proc.returncode == 0, proc.stderr
    return so


def _dwarf(so: Path) -> AbiSnapshot:
    return dump(so, headers=[])


def _qualifiers(snap: AbiSnapshot) -> dict[str, tuple[bool, bool, bool, bool]]:
    return {
        f.mangled: (f.is_const, f.is_volatile, f.is_static, f.is_virtual)
        for f in snap.declarations.functions
        if f.mangled.startswith("_ZN")
        and "Shape" in f.mangled
        and "D" not in f.mangled[-4:]
    }


def _without_volatile(q: tuple[bool, bool, bool, bool] | None) -> tuple | None:
    return None if q is None else (q[0], q[2], q[3])


@pytest.mark.parametrize("compiler", sorted(_CXX))
def test_dwarf_records_each_method_with_the_header_lane_qualifiers(
    tmp_path: Path, compiler: str
) -> None:
    _need("castxml")
    so = _build(tmp_path, compiler, _CLASS, _SOURCE)
    from_dwarf = _qualifiers(_dwarf(so))
    from_headers = _qualifiers(dump(so, headers=[tmp_path / "api.h"]))
    expected = {
        "_ZNK3lib5Shape4areaEv": (True, False, False, True),
        "_ZNK3lib5Shape4kindEv": (True, False, False, True),
        "_ZN3lib5Shape5plainEv": (False, False, False, False),
        "_ZNV3lib5Shape5touchEv": (False, True, False, False),
        "_ZN3lib5Shape5countEv": (False, False, True, False),
    }
    assert {k: from_dwarf.get(k) for k in expected} == expected
    # castxml records no method ``volatile`` (the mangling's ``V`` and DWARF
    # do), so the header lane is the oracle for the other three facts only.
    assert {k: _without_volatile(from_headers.get(k)) for k in expected} == {
        k: _without_volatile(v) for k, v in expected.items()
    }


def _kinds(tmp_path: Path, compiler: str, old: tuple[str, str], new: tuple[str, str]):  # type: ignore[no-untyped-def]
    a = _dwarf(_build(tmp_path / "old", compiler, *old))
    b = _dwarf(_build(tmp_path / "new", compiler, *new))
    result = compare(a, b)
    return result, {c.kind for c in result.changes}


# (edit id, header from -> to, source from -> to, the kind the edit is)
_CV_EDITS = [
    (
        "const_drop",
        ("virtual int area() const;", "virtual int area();"),
        ("int Shape::area() const", "int Shape::area()"),
        ChangeKind.FUNC_CV_CHANGED,
    ),
    (
        "const_add",
        ("int plain();", "int plain() const;"),
        ("int Shape::plain()", "int Shape::plain() const"),
        ChangeKind.FUNC_CV_CHANGED,
    ),
    (
        "volatile_drop",
        ("int touch() volatile;", "int touch();"),
        ("int Shape::touch() volatile", "int Shape::touch()"),
        ChangeKind.FUNC_CV_CHANGED,
    ),
    (
        "static_to_member",
        ("static int count();", "int count();"),
        ("", ""),
        ChangeKind.FUNC_STATIC_CHANGED,
    ),
]


@pytest.mark.parametrize("compiler", sorted(_CXX))
@pytest.mark.parametrize(
    ("header", "source", "kind"),
    [pytest.param(h, s, k, id=i) for i, h, s, k in _CV_EDITS],
)
def test_a_qualifier_edit_reads_as_its_own_kind_from_dwarf(
    tmp_path: Path,
    compiler: str,
    header: tuple[str, str],
    source: tuple[str, str],
    kind: ChangeKind,
) -> None:
    new_header = _CLASS.replace(*header)
    new_source = _SOURCE.replace(*source) if source[0] else _SOURCE
    assert new_header != _CLASS
    result, kinds = _kinds(
        tmp_path, compiler, (_CLASS, _SOURCE), (new_header, new_source)
    )
    assert kind in kinds, kinds
    assert result.verdict == Verdict.BREAKING


@pytest.mark.parametrize("compiler", sorted(_CXX))
def test_an_unchanged_class_reads_no_change(tmp_path: Path, compiler: str) -> None:
    result, kinds = _kinds(tmp_path, compiler, (_CLASS, _SOURCE), (_CLASS, _SOURCE))
    assert result.verdict == Verdict.NO_CHANGE, kinds


def _became_pure(tmp_path: Path, compiler: str):  # type: ignore[no-untyped-def]
    new_header = _CLASS.replace(
        "virtual int kind() const;", "virtual int kind() const = 0;"
    )
    new_source = _SOURCE.replace("int Shape::kind() const { return 1; }\n", "")
    return _kinds(tmp_path, compiler, (_CLASS, _SOURCE), (new_header, new_source))


def test_a_virtual_that_became_pure_is_reported_from_clang_dwarf(
    tmp_path: Path,
) -> None:
    result, kinds = _became_pure(tmp_path, "clang")
    assert ChangeKind.FUNC_VIRTUAL_BECAME_PURE in kinds, kinds
    assert result.verdict == Verdict.BREAKING


@pytest.mark.known_gap_canary
@pytest.mark.xfail(
    strict=True,
    reason="GCC 13 writes DW_VIRTUALITY_virtual for a pure virtual, so DWARF cannot "
    "tell it became pure (docs/contribute/known-gaps.md)",
)
def test_a_virtual_that_became_pure_is_reported_from_gcc_dwarf(tmp_path: Path) -> None:
    result, kinds = _became_pure(tmp_path, "gcc")
    assert ChangeKind.FUNC_VIRTUAL_BECAME_PURE in kinds, kinds
    assert result.verdict == Verdict.BREAKING
