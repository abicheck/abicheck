"""C++ member functions in DWARF-only snapshots (extractor generation 45).

A member's definition DIE names its in-class declaration only through
``DW_AT_specification``; before generation 45 the walker dropped both, so a
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
    # Every method returns ``int``: read from the declaration when the
    # out-of-line definition does not repeat ``DW_AT_type``.
    returns = {f.mangled: f.return_type for f in _dwarf(so).declarations.functions}
    assert {k: returns.get(k) for k in expected} == dict.fromkeys(expected, "int")
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
    # The definition leaves ``DW_AT_type`` on the in-class declaration, and
    # the Itanium mangling does not encode a member's return type.
    (
        "return_retype",
        ("int plain();", "long plain();"),
        ("int Shape::plain()", "long Shape::plain()"),
        ChangeKind.FUNC_RETURN_CHANGED,
    ),
    (
        "static_return_retype",
        ("static int count();", "static double count();"),
        ("int Shape::count()", "double Shape::count()"),
        ChangeKind.FUNC_RETURN_CHANGED,
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


def test_a_virtual_that_became_pure_is_reported_from_gcc_dwarf(tmp_path: Path) -> None:
    # GCC 13 writes DW_VIRTUALITY_virtual for a pure virtual; the emitted
    # vtable's __cxa_pure_virtual slot carries the fact instead
    # (extract/elf_pure_virtual_slots.py). Was a known-gap canary.
    result, kinds = _became_pure(tmp_path, "gcc")
    assert ChangeKind.FUNC_VIRTUAL_BECAME_PURE in kinds, kinds
    assert result.verdict == Verdict.BREAKING


# ``extern "C"`` declared in a namespace and defined outside it: GCC writes the
# definition as a nameless DIE whose DW_AT_specification names a declaration
# with no linkage name (the C symbol is the plain name); clang writes one
# top-level DIE. The oracle is the edit itself.
_EXTERN_C = 'namespace lib {{ extern "C" {ret} c_fill({params}); }}\n'
_EXTERN_C_SOURCE = (
    '#include "api.h"\nextern "C" {ret} lib::c_fill({params}) {{ return n; }}\n'
)
_BASE = ("int", "int n, long v")
# (edit id, (return, parameters) after the edit, the kind the edit is)
_EXTERN_C_EDITS = [
    ("param_append", ("int", "int n, long v, int w"), ChangeKind.FUNC_PARAMS_CHANGED),
    ("param_retype", ("int", "int n, double v"), ChangeKind.FUNC_PARAMS_CHANGED),
    ("return_retype", ("long", "int n, long v"), ChangeKind.FUNC_RETURN_CHANGED),
    ("unchanged", _BASE, None),
]


def _extern_c(ret: str, params: str) -> tuple[str, str]:
    fmt = {"ret": ret, "params": params}
    return _EXTERN_C.format(**fmt), _EXTERN_C_SOURCE.format(**fmt)


@pytest.mark.parametrize("compiler", sorted(_CXX))
@pytest.mark.parametrize(
    ("after", "kind"),
    [pytest.param(a, k, id=i) for i, a, k in _EXTERN_C_EDITS],
)
def test_an_extern_c_definition_out_of_its_namespace_keeps_its_signature(
    tmp_path: Path, compiler: str, after: tuple[str, str], kind: ChangeKind | None
) -> None:
    snap = _dwarf(_build(tmp_path / "probe", compiler, *_extern_c(*_BASE)))
    fn = next(f for f in snap.declarations.functions if f.mangled == "c_fill")
    assert (fn.return_type, [p.type.split()[0] for p in fn.params]) == (
        "int",
        ["int", "long"],
    )
    result, kinds = _kinds(tmp_path, compiler, _extern_c(*_BASE), _extern_c(*after))
    if kind is None:
        assert result.verdict == Verdict.NO_CHANGE, kinds
        return
    assert kind in kinds, kinds
    assert ChangeKind.FUNC_REMOVED not in kinds, kinds


_SLOTS = "struct A {{ virtual ~A(); {decls} }};\n"
_SLOTS_SOURCE = (
    '#include "api.h"\nA::~A() {}\nint A::f() { return 1; }\nint A::g() { return 2; }\n'
)


def _slots(tmp_path: Path, compiler: str, decls: str) -> AbiSnapshot:
    return _dwarf(_build(tmp_path, compiler, _SLOTS.format(decls=decls), _SLOTS_SOURCE))


@pytest.mark.parametrize("compiler", sorted(_CXX))
def test_dwarf_virtuals_carry_their_itanium_vtable_slot(
    tmp_path: Path, compiler: str
) -> None:
    # Itanium: a virtual destructor takes two slots (complete, deleting), so
    # the next virtuals sit at 2 and 3 in declaration order.
    snap = _slots(tmp_path, compiler, "virtual int f(); virtual int g();")
    slots = {f.mangled: f.vtable_index for f in snap.declarations.functions}
    assert (slots["_ZN1A1fEv"], slots["_ZN1A1gEv"]) == (2, 3)


@pytest.mark.parametrize("compiler", sorted(_CXX))
def test_reordered_virtuals_read_as_a_vtable_change_from_dwarf(
    tmp_path: Path, compiler: str
) -> None:
    old = _slots(tmp_path / "old", compiler, "virtual int f(); virtual int g();")
    new = _slots(tmp_path / "new", compiler, "virtual int g(); virtual int f();")
    result = compare(old, new)
    moved = {
        c.symbol for c in result.changes if c.kind == ChangeKind.TYPE_VTABLE_CHANGED
    }
    assert {"_ZN1A1fEv", "_ZN1A1gEv"} <= moved, result.changes
    assert result.verdict == Verdict.BREAKING


@pytest.mark.parametrize("compiler", sorted(_CXX))
def test_no_abstract_constructor_or_destructor_is_recorded(
    tmp_path: Path, compiler: str
) -> None:
    # GCC writes the C4/D4 abstract root with DW_AT_inline = DW_INL_not_inlined;
    # it has no code and no symbol, so only the concrete C1/C2/D0/D1/D2 count.
    snap = _dwarf(_build(tmp_path, compiler, _CLASS, _SOURCE))
    abstract = [
        f.mangled
        for f in snap.declarations.functions
        if f.mangled.endswith(("C4Ev", "D4Ev"))
    ]
    assert abstract == []
    assert "_ZN3lib5ShapeD2Ev" in {f.mangled for f in snap.declarations.functions}
