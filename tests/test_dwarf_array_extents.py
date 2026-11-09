"""DWARF array members keep their bounds (family F14, generation 44).

Before generation 44 the DWARF extractor spelled every non-vector array as
``T[]``, so ``char tag[8]`` -> ``char tag[6]`` inside a record whose size did
not move read ``NO_CHANGE`` from a DWARF-only comparison, and a packed record
reported only its size change.

Oracles, none of them the extractor's own helper:

* unit cases: the spelling C gives each declarator (``int m[2][3]`` is
  ``int[2][3]``), hand-written per DIE shape;
* integration: the clang header frontend's spelling of the same member (an
  independent AST read; castxml reverses multi-dimensional bounds, see
  ``docs/contribute/known-gaps.md``), and the byte size ``sizeof`` reports
  in a compiled program;
* comparisons: whether the edit changed the declared bound, fixed per shape
  by construction.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.dwarf_unified import parse_dwarf
from abicheck.dwarf_utils import array_extents, array_type_info
from abicheck.elf_metadata import parse_elf_metadata
from abicheck.model import AbiSnapshot
from abicheck.serialization import load_snapshot
from abicheck.workflows.dwarf_snapshot_assembly import build_snapshot_from_dwarf
from tests._abicheck_subprocess import abicheck_command

# ── unit: one fake DIE per subrange shape ────────────────────────────────────


def _attr(value: object, form: str = "DW_FORM_data1") -> SimpleNamespace:
    return SimpleNamespace(value=value, form=form)


def _die(tag: str, attrs: dict[str, object], children: tuple = ()) -> SimpleNamespace:
    return SimpleNamespace(
        tag=tag,
        attributes={
            k: _attr(*v) if isinstance(v, tuple) else _attr(v) for k, v in attrs.items()
        },
        iter_children=lambda: iter(children),
    )


def _sub(**attrs: object) -> SimpleNamespace:
    return _die("DW_TAG_subrange_type", {f"DW_AT_{k}": v for k, v in attrs.items()})


def _array(
    *subranges: SimpleNamespace, byte_size: int | None = None
) -> SimpleNamespace:
    attrs = {} if byte_size is None else {"DW_AT_byte_size": byte_size}
    return _die("DW_TAG_array_type", attrs, subranges)


# (case, DIE, element (name, size), expected spelling, expected byte size)
_UNIT_CASES = [
    ("gcc_upper_bound", _array(_sub(upper_bound=7)), ("char", 1), "char[8]", 8),
    ("clang_count", _array(_sub(count=8)), ("char", 1), "char[8]", 8),
    (
        "two_dims_outermost_first",
        _array(_sub(upper_bound=1), _sub(upper_bound=2)),
        ("int", 4),
        "int[2][3]",
        24,
    ),
    (
        "lower_bound",
        _array(_sub(lower_bound=1, upper_bound=4)),
        ("int", 4),
        "int[4]",
        16,
    ),
    ("flexible_member", _array(_sub()), ("char", 1), "char[]", 0),
    ("zero_length_count", _array(_sub(count=0)), ("double", 8), "double[0]", 0),
    # GCC writes ``T[0]`` as ``upper = -1`` in the form's own width; the same
    # value in a wider form is a real bound.
    (
        "zero_length_all_ones_data4",
        _array(_sub(upper_bound=(0xFFFFFFFF, "DW_FORM_data4"))),
        ("double", 8),
        "double[0]",
        0,
    ),
    (
        "zero_length_all_ones_data8",
        _array(_sub(upper_bound=(0xFFFFFFFFFFFFFFFF, "DW_FORM_data8"))),
        ("double", 8),
        "double[0]",
        0,
    ),
    (
        "zero_length_all_ones_data1",
        _array(_sub(upper_bound=(0xFF, "DW_FORM_data1"))),
        ("char", 1),
        "char[0]",
        0,
    ),
    (
        "zero_length_minus_one_sdata",
        _array(_sub(upper_bound=(-1, "DW_FORM_sdata"))),
        ("char", 1),
        "char[0]",
        0,
    ),
    (
        "huge_bound_in_a_wider_form",
        _array(_sub(upper_bound=(0xFFFFFFFF, "DW_FORM_data8"))),
        ("char", 1),
        "char[4294967296]",
        4294967296,
    ),
    (
        "vla_expression_bound",
        _array(_sub(upper_bound=([0x91, 0x68], "DW_FORM_exprloc"))),
        ("int", 4),
        "int[]",
        0,
    ),
    # A reference-valued bound decodes as an integer DIE offset; it names a
    # variable, not a count.
    (
        "vla_reference_bound",
        _array(_sub(upper_bound=(0x2A, "DW_FORM_ref4"))),
        ("int", 4),
        "int[]",
        0,
    ),
    (
        "vla_reference_count",
        _array(_sub(count=(0x2A, "DW_FORM_ref4"))),
        ("int", 4),
        "int[]",
        0,
    ),
    (
        "reference_lower_bound",
        _array(_sub(lower_bound=(0x2A, "DW_FORM_ref4"), upper_bound=4)),
        ("int", 4),
        "int[]",
        0,
    ),
    (
        "declared_byte_size_wins",
        _array(_sub(count=3), byte_size=12),
        ("int", 4),
        "int[3]",
        12,
    ),
    # An array DIE whose element is another array DIE: the element's bounds
    # are the inner dimensions, so they follow this DIE's own.
    ("nested_array_die", _array(_sub(count=2)), ("int[3]", 12), "int[2][3]", 24),
    (
        "nested_twice",
        _array(_sub(count=2)),
        ("int[3][4]", 48),
        "int[2][3][4]",
        96,
    ),
    ("nested_unbounded_outer", _array(_sub()), ("int[3]", 12), "int[][3]", 0),
    ("no_subrange", _array(), ("int", 4), "int[]", 0),
    ("unknown_element_size", _array(_sub(count=3)), ("T", 0), "T[3]", 0),
]


@pytest.mark.parametrize(
    ("die", "element", "spelling", "size"),
    [pytest.param(d, e, s, n, id=c) for c, d, e, s, n in _UNIT_CASES],
)
def test_array_spelling_keeps_each_bound(
    die: SimpleNamespace, element: tuple[str, int], spelling: str, size: int
) -> None:
    assert array_type_info(die, element) == (spelling, size)


def test_extents_are_read_outermost_first() -> None:
    die = _array(_sub(count=2), _sub(), _sub(upper_bound=4))
    assert array_extents(die) == (2, None, 5)


def test_unresolved_element_keeps_the_array_fallback() -> None:
    assert array_type_info(_array(_sub(count=4)), None) == ("array", 0)


# ── integration: real gcc/clang DWARF ────────────────────────────────────────

_COMPILERS = ("gcc", "clang")


def _need(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        pytest.skip(f"needs {tool}")
    return path


def _build(
    work: Path, cc: str, header: str, extra: tuple[str, ...] = ()
) -> AbiSnapshot:
    work.mkdir(parents=True, exist_ok=True)
    (work / "api.h").write_text(header)
    (work / "api.c").write_text('#include "api.h"\nstruct S g_s;\n')
    so = work / "libapi.so"
    proc = subprocess.run(
        [
            _need(cc),
            "-shared",
            "-fPIC",
            "-g",
            *extra,
            "-o",
            str(so),
            str(work / "api.c"),
        ],
        capture_output=True,
        text=True,
        cwd=work,
    )
    assert proc.returncode == 0, proc.stderr
    meta, adv = parse_dwarf(so)
    return build_snapshot_from_dwarf(so, parse_elf_metadata(so), meta, adv)


def _fields(snap: AbiSnapshot, record: str) -> dict[str, str]:
    rec = next(t for t in snap.declarations.types if t.name == record)
    return {f.name: f.type for f in rec.fields}


_MEMBERS = (
    "struct P { int x; };\n"
    "struct S { int n; char a[8]; int m[2][3]; struct P ps[3]; double q[1][2][5];"
    " char tail[]; };\n"
)


def _clang_header_spellings(work: Path) -> dict[str, str]:
    """The clang header frontend's spelling of each member of ``S``."""
    _need("clang")
    (work / "hdr").mkdir(parents=True, exist_ok=True)
    out = work / "hdr" / "snap.json"
    so = work / "libapi.so"
    proc = subprocess.run(
        abicheck_command("dump", str(so), "-H", str(work / "api.h"), "-o", str(out)),
        capture_output=True,
        text=True,
        env={**os.environ, "ABICHECK_AST_FRONTEND": "clang"},
    )
    # ``_need("clang")`` is the capability check: a dump that fails with
    # clang present is a failure, not a missing tool.
    assert proc.returncode == 0, proc.stderr[-2000:]
    snap = load_snapshot(out)
    rec = next(t for t in snap.declarations.types if t.name == "S")
    return {f.name: f.type.replace("struct ", "") for f in rec.fields}


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="builds ELF shared objects")
@pytest.mark.parametrize("cc", _COMPILERS)
def test_dwarf_member_spelling_matches_the_clang_header_frontend(
    tmp_path: Path, cc: str
) -> None:
    snap = _build(tmp_path, cc, _MEMBERS)
    dwarf = _fields(snap, "S")
    assert dwarf == _clang_header_spellings(tmp_path)
    assert dwarf["m"] == "int[2][3]" and dwarf["tail"] == "char[]"


def _sizeof_members(work: Path) -> dict[str, int]:
    probe = work / "probe.c"
    probe.write_text(
        '#include <stdio.h>\n#include "api.h"\nint main(void) { struct S s;'
        ' printf("%zu %zu %zu %zu\\n", sizeof s.a, sizeof s.m, sizeof s.ps,'
        " sizeof s.q); return 0; }\n"
    )
    exe = work / "probe"
    subprocess.run([_need("gcc"), str(probe), "-o", str(exe)], check=True, cwd=work)
    values = subprocess.run(
        [str(exe)], capture_output=True, text=True, check=True
    ).stdout.split()
    return dict(zip(("a", "m", "ps", "q"), map(int, values), strict=True))


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="builds ELF shared objects")
@pytest.mark.parametrize("cc", _COMPILERS)
def test_dwarf_member_byte_size_is_what_sizeof_reports(tmp_path: Path, cc: str) -> None:
    _build(tmp_path, cc, _MEMBERS)
    meta, _ = parse_dwarf(tmp_path / "libapi.so")
    layout = meta.structs["S"]
    sizes = {
        f.name: f.byte_size for f in layout.fields if f.name in ("a", "m", "ps", "q")
    }
    assert sizes == _sizeof_members(tmp_path)


# (shape, old struct body, new struct body, expected kind or None for no change)
_SHAPES = [
    (
        "shrink_in_padding",
        "int id; double w; char tag[8];",
        "int id; double w; char tag[6];",
        True,
    ),
    (
        "grow_in_padding",
        "int id; double w; char tag[6];",
        "int id; double w; char tag[8];",
        True,
    ),
    ("reshape_same_total", "int id; int m[2][3];", "int id; int m[3][2];", True),
    ("inner_bound", "int id; int m[2][3];", "int id; int m[2][4];", True),
    (
        "struct_element_bound",
        "struct P { int x; } ps[3]; int id;",
        "struct P { int x; } ps[4]; int id;",
        True,
    ),
    (
        "unchanged",
        "int id; char tag[8]; int m[2][3];",
        "int id; char tag[8]; int m[2][3];",
        False,
    ),
    ("flexible_unchanged", "int id; char tail[];", "int id; char tail[];", False),
]


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="builds ELF shared objects")
@pytest.mark.parametrize("cc", _COMPILERS)
@pytest.mark.parametrize("packed", [False, True], ids=["natural", "packed"])
@pytest.mark.parametrize(
    ("old_body", "new_body", "changed"),
    [pytest.param(o, n, c, id=s) for s, o, n, c in _SHAPES],
)
def test_a_bound_change_is_a_member_type_change(
    tmp_path: Path, cc: str, packed: bool, old_body: str, new_body: str, changed: bool
) -> None:
    extra = ("-fpack-struct=1",) if packed else ()
    old = _build(tmp_path / "old", cc, f"struct S {{ {old_body} }};\n", extra)
    new = _build(tmp_path / "new", cc, f"struct S {{ {new_body} }};\n", extra)
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    if not changed:
        assert result.verdict == Verdict.NO_CHANGE, kinds
        return
    assert ChangeKind.TYPE_FIELD_TYPE_CHANGED in kinds, kinds
    assert result.verdict == Verdict.BREAKING
