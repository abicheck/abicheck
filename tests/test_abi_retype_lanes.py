"""A retype between ABI-identical fundamentals, end to end in every evidence lane.

Schema v69 (model/abi_fundamental.py): a parameter retyped between two types the
compiler passes alike is a source-level change, and a retype the convention can
see (a sign at 4 bytes, an integer against a float) stays a break. Each case is
built as a real shared object and compared through the CLI in these lanes:

* ``A`` -- DWARF: ``-g`` builds, no headers;
* ``B`` -- headers without debug info: ``-H old=... -H new=...`` (castxml);
* ``C`` -- dump each side to JSON, then compare the two dumps (castxml);
* ``B-clang`` / ``C-clang`` -- the same two header lanes with the clang JSON-AST
  frontend pinned by ``ABICHECK_AST_FRONTEND=clang``. The dump lane asserts the
  dump's recorded ``ast_producer`` is clang, so a silent castxml fallback fails.

A typedef case declares ``tdef_t`` over its base on each side and retypes the
parameter by name, so the change is a typedef retarget (``typedef_base_changed``
for a real break, the retype finding for an ABI-identical base).

The float vector against ``double`` is pinned here rather than in the
differential matrix, because the matrix's disassembly oracle reads a codegen
difference (a movlps/movsd store) in that pair as a convention difference.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from tests._abicheck_subprocess import abicheck_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="builds ELF shared objects with GNU ld options",
    ),
]

REPO_ROOT = Path(__file__).resolve().parents[1]

_RETYPE = "abi_equivalent_type_changed"
_BREAK_KINDS = frozenset(
    {
        "func_params_changed",
        "func_return_changed",
        "type_field_type_changed",
        "var_type_changed",
        "atomic_qualifier_changed",
        "typedef_base_changed",
    }
)
_PRE = "enum PE_E { PE_A, PE_B };\n"
_TYPEDEF = "typedef "
_CLANG = {"ABICHECK_AST_FRONTEND": "clang"}

#: (case id, old spelling, new spelling, ABI-identical). A spelling that starts
#: with ``typedef `` declares ``tdef_t`` over the rest and is used by that name.
CASES = [
    ("vec8f_vs_double", "float __attribute__((vector_size(8)))", "double", True),
    ("char_vs_schar", "char", "signed char", True),
    ("long_vs_llong", "long", "long long", True),
    ("i128_vs_u128", "__int128", "unsigned __int128", True),
    ("enum_vs_uint", "enum PE_E", "unsigned int", True),
    # _Atomic is part of the interface contract (catalog case116): the
    # qualifier change stays a break even where the layout is identical.
    ("atomic_vs_int", "_Atomic int", "int", False),
    ("int_vs_uint", "int", "unsigned int", False),
    ("char_vs_uchar", "char", "unsigned char", False),
    ("bool_vs_uchar", "_Bool", "unsigned char", False),
    ("float_vs_double", "float", "double", False),
    ("typedef_long_vs_llong", "typedef long", "typedef long long", True),
    ("typedef_long_vs_int", "typedef long", "typedef int", False),
]

LANES = ["A", "B", "C", "B-clang", "C-clang"]

#: Cases the clang lanes do not pin. The clang JSON AST gives an enum parameter
#: only its tag, not its underlying integer type, so it has no builtin fact and
#: keeps the name-based verdict (a conservative break, never a clean claim).
_CLANG_NO_BUILTIN_FACT = frozenset({"enum_vs_uint"})


def _need(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        pytest.skip(f"{tool} not available")
    return path


def _declare(spelling: str) -> tuple[str, str]:
    """``(declarations, parameter type)`` for a case spelling."""
    if spelling.startswith(_TYPEDEF):
        base = spelling[len(_TYPEDEF) :]
        return f"typedef {base} tdef_t;\n", "tdef_t"
    return "", spelling


def _header(spelling: str) -> str:
    decl, ptype = _declare(spelling)
    return f"{_PRE}{decl}void f({ptype} p);\n"


def _build(work: Path, case: str, side: str, spelling: str, debug: bool) -> Path:
    cc = _need("gcc")
    header = work / f"{case}_{side}.h"
    header.write_text(_header(spelling), encoding="utf-8")
    _, ptype = _declare(spelling)
    src = work / f"{case}_{side}.c"
    src.write_text(
        f'#include "{header.name}"\nvoid f({ptype} p) {{ (void)p; }}\n',
        encoding="utf-8",
    )
    lib = work / f"{case}_{side}_{'g' if debug else 'n'}.so"
    argv = [
        cc,
        *(["-g"] if debug else []),
        "-O0",
        "-shared",
        "-fPIC",
        "-Wl,-soname,libx.so.1",
        "-I",
        str(work),
        str(src),
        "-o",
        str(lib),
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        pytest.fail(f"compile failed: {' '.join(argv)}\n{proc.stderr}")
    return lib


def _run(
    *args: str, env_extra: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = None if env_extra is None else {**os.environ, **env_extra}
    return subprocess.run(
        abicheck_command(*args),
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env=env,
    )


def _kinds(report: Path) -> set[str]:
    return {
        c.get("kind")
        for c in json.loads(report.read_text(encoding="utf-8")).get("changes", [])
    }


def _producer(snapshot: Path) -> str:
    """The header frontend a dump recorded (``sections.debug.payload.ast_producer``)."""
    doc = json.loads(snapshot.read_text(encoding="utf-8"))
    return doc["sections"]["debug"]["payload"]["ast_producer"]


def _lane(work: Path, case: str, lane: str, old: str, new: str) -> tuple[int, set[str]]:
    report = work / f"{case}_{lane}.json"
    base, _, frontend = lane.partition("-")
    env = _CLANG if frontend == "clang" else None

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return _run(*args, env_extra=env)

    if base == "A":
        lib_old = _build(work, case, "1", old, True)
        lib_new = _build(work, case, "2", new, True)
        proc = run("compare", str(lib_old), str(lib_new), "-o", f"json={report}")
    elif base == "B":
        lib_old = _build(work, case, "1", old, False)
        lib_new = _build(work, case, "2", new, False)
        proc = run(
            "compare",
            str(lib_old),
            str(lib_new),
            "-H",
            f"old={work / f'{case}_1.h'}",
            "-H",
            f"new={work / f'{case}_2.h'}",
            "-o",
            f"json={report}",
        )
    else:
        snaps = []
        for side, spelling in (("1", old), ("2", new)):
            lib = _build(work, case, side, spelling, True)
            snap = work / f"{case}_{side}.snap.json"
            dump = run(
                "dump", str(lib), "-H", str(work / f"{case}_{side}.h"), "-o", str(snap)
            )
            assert dump.returncode == 0, dump.stderr
            expected = "clang" if frontend == "clang" else "castxml"
            assert _producer(snap) == expected, (
                f"{case} lane {lane}: dump parsed headers with {_producer(snap)!r}"
            )
            snaps.append(snap)
        proc = run("compare", str(snaps[0]), str(snaps[1]), "-o", f"json={report}")
    if not report.exists():
        pytest.fail(
            f"lane {lane} wrote no report (exit {proc.returncode}):\n{proc.stderr}"
        )
    return proc.returncode, _kinds(report)


@pytest.mark.parametrize("lane", LANES)
@pytest.mark.parametrize(
    ("case", "old", "new", "identical"), CASES, ids=[c[0] for c in CASES]
)
def test_retype_in_every_lane(
    tmp_path: Path, case: str, old: str, new: str, identical: bool, lane: str
) -> None:
    if lane.endswith("clang"):
        if case in _CLANG_NO_BUILTIN_FACT:
            pytest.skip(
                f"{case}: the clang AST spells this type by its tag, with no builtin "
                "fact (the brief covers builtin types); the castxml lanes pin it"
            )
        _need("clang")
    elif lane != "A":
        _need("castxml")
    code, kinds = _lane(tmp_path, case, lane, old, new)
    if identical:
        assert code != 4, (
            f"{case} lane {lane}: ABI-identical retype read as a break: {kinds}"
        )
        assert _RETYPE in kinds, (
            f"{case} lane {lane}: no source-level retype finding: {kinds}"
        )
        assert not (kinds & _BREAK_KINDS), (
            f"{case} lane {lane}: break kind kept: {kinds}"
        )
    else:
        assert code == 4, (
            f"{case} lane {lane}: ABI-different retype not a break (exit {code}): {kinds}"
        )
        assert kinds & _BREAK_KINDS, f"{case} lane {lane}: no break finding: {kinds}"
