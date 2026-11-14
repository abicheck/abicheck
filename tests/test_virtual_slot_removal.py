"""``func_virtual_removed`` for a virtual method removed with its vtable slot.

Bug class ``classification.removed_virtual_slot_read_as_plain_removal``
(family F20). Removing a virtual method read only as ``func_removed`` plus the
record-level vtable kinds; ``func_virtual_removed`` was emitted only for a
method that stayed and stopped being virtual.

Oracle: each shape states by construction whether a vtable slot was lost (a
virtual declaration deleted from a class that keeps no override filling it),
never by asking the detector's own helpers. The integration half builds real
libraries with g++ and reads them through every evidence lane.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.compare.virtual_slot_removal import virtual_slot_removals
from abicheck.dumper import dump
from abicheck.model import AbiSnapshot, Function, RecordType

# ── unit: hand-built records ────────────────────────────────────────────────

AREA, KIND = "_ZNK5Shape4areaEv", "_ZNK5Shape4kindEv"


def _snap(
    records: dict[str, list[str] | None], functions: tuple[str, ...]
) -> AbiSnapshot:
    return AbiSnapshot(
        library="lib",
        version="1",
        functions=[Function(name=m, mangled=m, return_type="int") for m in functions],
        types=[
            RecordType(
                name=name, kind="class", **({} if vt is None else {"vtable": vt})
            )
            for name, vt in records.items()
        ],
    )


# (shape, old records, old functions, new records, new functions, slot lost)
_UNIT = [
    (
        "last_removed",
        {"Shape": [AREA, KIND]},
        (AREA, KIND),
        {"Shape": [AREA]},
        (AREA,),
        True,
    ),
    (
        "first_removed",
        {"Shape": [AREA, KIND]},
        (AREA, KIND),
        {"Shape": [KIND]},
        (KIND,),
        True,
    ),
    (
        "kept_but_devirtualized",
        {"Shape": [AREA, KIND]},
        (AREA, KIND),
        {"Shape": [AREA]},
        (AREA, KIND),
        False,
    ),
    (
        "override_removed_slot_stays",
        {"Derived": ["_ZN7Derived5paintEv"]},
        ("_ZN7Derived5paintEv",),
        {"Derived": ["_ZN4Base5paintEv"]},
        (),
        False,
    ),
    ("record_gone", {"Shape": [AREA, KIND]}, (AREA, KIND), {}, (), False),
    (
        "vtable_not_recorded",
        {"Shape": [AREA, KIND]},
        (AREA, KIND),
        {"Shape": None},
        (AREA,),
        False,
    ),
    (
        "inherited_slot_lost_everywhere",
        {"Shape": [AREA, KIND], "Square": [AREA, KIND]},
        (AREA, KIND),
        {"Shape": [AREA], "Square": [AREA]},
        (AREA,),
        True,
    ),
    (
        "inherited_slot_kept_by_one_holder",
        {"Shape": [AREA, KIND], "Square": [AREA, KIND]},
        (AREA, KIND),
        {"Shape": [AREA], "Square": [AREA, KIND]},
        (AREA,),
        False,
    ),
]


@pytest.mark.parametrize(
    ("old_records", "old_fns", "new_records", "new_fns", "lost"),
    [pytest.param(o, of, n, nf, lost, id=i) for i, o, of, n, nf, lost in _UNIT],
)
def test_a_lost_slot_is_reported_exactly_when_it_left_every_vtable(
    old_records: dict[str, list[str] | None],
    old_fns: tuple[str, ...],
    new_records: dict[str, list[str] | None],
    new_fns: tuple[str, ...],
    lost: bool,
) -> None:
    findings = virtual_slot_removals(
        _snap(old_records, old_fns), _snap(new_records, new_fns)
    )
    removed = sorted(
        {e for vt in old_records.values() if vt for e in vt} - set(new_fns)
    )
    if not lost:
        assert findings == []
        return
    assert [(c.kind, c.symbol) for c in findings] == [
        (ChangeKind.FUNC_VIRTUAL_REMOVED, s)
        for s in removed
        if all(s not in (vt or []) for vt in new_records.values())
    ]


# ── integration: real g++ libraries through every evidence lane ─────────────

_CLASS = """\
class Shape {
public:
    virtual ~Shape();
    virtual int area() const;
    virtual int kind() const;
    virtual int sides() const;
    int w;
};
class Square : public Shape {
public:
    int area() const override;
};
int shape_area(Shape& s);
"""
_SOURCE = """\
#include "api.h"
Shape::~Shape() {}
int Shape::area() const { return w; }
int Shape::kind() const { return 1; }
int Shape::sides() const { return 0; }
int Square::area() const { return w * w; }
int shape_area(Shape& s) { return s.area(); }
"""

# (shape, header edit, source edit, the symbol whose slot left, or None)
_SHAPES = [
    (
        "middle_virtual_removed",
        ("    virtual int kind() const;\n", ""),
        ("int Shape::kind() const { return 1; }\n", ""),
        "_ZNK5Shape4kindEv",
    ),
    (
        "last_virtual_removed",
        ("    virtual int sides() const;\n", ""),
        ("int Shape::sides() const { return 0; }\n", ""),
        "_ZNK5Shape5sidesEv",
    ),
    (
        "override_removed",
        ("    int area() const override;\n", ""),
        ("int Square::area() const { return w * w; }\n", ""),
        None,
    ),
]

_LANES = ("dwarf", "castxml", "clang")


def _build(work: Path, header: str, source: str) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    (work / "api.h").write_text(header)
    (work / "api.cpp").write_text(source)
    so = work / "libapi.so"
    proc = subprocess.run(
        ["g++", "-shared", "-fPIC", "-g", "-o", str(so), "api.cpp"],
        capture_output=True,
        text=True,
        cwd=work,
    )
    assert proc.returncode == 0, proc.stderr
    return so


def _dump(so: Path, lane: str) -> AbiSnapshot:
    if lane == "dwarf":
        return dump(so, headers=[])
    saved = os.environ.get("ABICHECK_AST_FRONTEND")
    os.environ["ABICHECK_AST_FRONTEND"] = lane
    try:
        return dump(so, headers=[so.parent / "api.h"])
    finally:
        if saved is None:
            os.environ.pop("ABICHECK_AST_FRONTEND", None)
        else:
            os.environ["ABICHECK_AST_FRONTEND"] = saved


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="builds ELF shared objects")
@pytest.mark.parametrize("lane", _LANES)
@pytest.mark.parametrize(
    ("header", "source", "slot_symbol"),
    [pytest.param(h, s, sym, id=i) for i, h, s, sym in _SHAPES],
)
def test_a_removed_virtual_reports_its_lost_slot_on_every_lane(
    tmp_path: Path,
    lane: str,
    header: tuple[str, str],
    source: tuple[str, str],
    slot_symbol: str | None,
) -> None:
    if shutil.which("g++") is None:
        pytest.skip("needs g++")
    if (
        lane != "dwarf"
        and shutil.which("castxml" if lane == "castxml" else "clang") is None
    ):
        pytest.skip(f"needs {lane}")
    new_header, new_source = _CLASS.replace(*header), _SOURCE.replace(*source)
    assert new_header != _CLASS and new_source != _SOURCE
    old = _dump(_build(tmp_path / "old", _CLASS, _SOURCE), lane)
    new = _dump(_build(tmp_path / "new", new_header, new_source), lane)
    result = compare(old, new)
    lost = [
        c.symbol for c in result.changes if c.kind is ChangeKind.FUNC_VIRTUAL_REMOVED
    ]
    assert lost == ([slot_symbol] if slot_symbol else []), [
        (c.kind.value, c.symbol) for c in result.changes
    ]
    if slot_symbol is not None:
        removals = {c.symbol for c in result.changes if "removed" in c.kind.value}
        assert slot_symbol in removals  # the removal itself is still recorded
        assert result.verdict == Verdict.BREAKING


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="builds ELF shared objects")
def test_a_devirtualized_method_is_reported_once(tmp_path: Path) -> None:
    """case168's shape: the method stays exported and leaves the vtable. The
    signature detector already reports it; this detector adds nothing."""
    if shutil.which("g++") is None or shutil.which("castxml") is None:
        pytest.skip("needs g++ and castxml")
    new_header = _CLASS.replace(
        "    virtual int kind() const;\n", "    int kind() const;\n"
    )
    old = _dump(_build(tmp_path / "old", _CLASS, _SOURCE), "castxml")
    new = _dump(_build(tmp_path / "new", new_header, _SOURCE), "castxml")
    found = [
        c.symbol
        for c in compare(old, new).changes
        if c.kind is ChangeKind.FUNC_VIRTUAL_REMOVED
    ]
    assert found == ["_ZNK5Shape4kindEv"]
