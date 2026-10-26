"""Generated TYPE CHANGE x POSITION matrix, run through the public CLI.

Each cell builds a v1/v2 pair of C++ shared libraries (``g++ -shared -fPIC``)
whose only difference is one type change placed at one API position, then runs
``python -m abicheck compare`` at two evidence levels:

* ``-g0``: no debug info, so only ELF plus public headers (L0/L2).
* ``-g``: DWARF present, so compiled layout is visible too (L1).

The expected verdicts are the explicit ``EXPECTED`` table below. They follow
ABI rules (a size or layout change of the type at a by-value, return, pointee,
field or global position breaks), not abicheck's own output.

Invariant checked for every cell: if the DWARF run is BREAKING, the header-only
run must not be a clean pass (NO_CHANGE/COMPATIBLE with exit 0).
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "linux", reason="ELF/DWARF matrix runs on Linux"
    ),
]

# ── type changes: (v1 declaration, v2 declaration), both define the name T ──
TYPE_CHANGES: dict[str, tuple[str, str]] = {
    "enum_uchar_to_uint": (
        "enum T : unsigned char { X_ = 0, Y_ = 1 };",
        "enum T : unsigned int { X_ = 0, Y_ = 1 };",
    ),
    "typedef_int_to_long": ("typedef int T;", "typedef long T;"),
    "struct_add_trailing_field": (
        "struct T { int a; };",
        "struct T { int a; int b; };",
    ),
    "struct_field_reorder": (
        "struct T { int a; long b; };",
        "struct T { long b; int a; };",
    ),
    "float_to_double": ("typedef float T;", "typedef double T;"),
}

# ── positions: how T appears in the public API: (header decl, source defn) ──
POSITIONS: dict[str, tuple[str, str]] = {
    "byval_param": (
        'extern "C" int api(T v);',
        'extern "C" int api(T v) { (void)v; return 0; }',
    ),
    "return_value": (
        'extern "C" T api(void);',
        'extern "C" T api(void) { T r{}; return r; }',
    ),
    "pointer_param": (
        'extern "C" int api(T *p);',
        'extern "C" int api(T *p) { (void)p; return 0; }',
    ),
    "struct_field_by_ptr": (
        'struct Holder { int tag; T value; };\nextern "C" int api(Holder *h);',
        'extern "C" int api(Holder *h) { (void)h; return 0; }',
    ),
    "extern_global": (
        'extern "C" T g_var;',
        'extern "C" T g_var{};',
    ),
}

# evidence level -> compiler flags for the .so build
LEVEL_FLAGS: dict[str, str] = {"-g0": "-g0", "-g": "-g"}

# ── explicit oracle: (change, position) -> {level: expected verdict} ──────
# Every cell changes the size or layout of T at its position, so each is
# BREAKING at both levels. A header-only NO_CHANGE here is a finding.
_BREAK = {"-g0": "BREAKING", "-g": "BREAKING"}
EXPECTED: dict[tuple[str, str], dict[str, str]] = {
    (change, position): dict(_BREAK)
    for change in TYPE_CHANGES
    for position in POSITIONS
}

CELLS = [(c, p) for c in TYPE_CHANGES for p in POSITIONS]
MATRIX = [(c, p, lvl) for (c, p) in CELLS for lvl in LEVEL_FLAGS]
CELL_IDS = [f"{c}-{p}" for (c, p) in CELLS]
MATRIX_IDS = [f"{c}-{p}-{lvl}" for (c, p, lvl) in MATRIX]

CLEAN_VERDICTS = {"NO_CHANGE", "COMPATIBLE"}


@dataclass(frozen=True)
class CellResult:
    verdict: str
    exit_code: int


def _write_version(
    workdir: Path, change: str, position: str, which: int, level: str
) -> tuple[Path, Path]:
    old_t, new_t = TYPE_CHANGES[change]
    type_src = old_t if which == 1 else new_t
    decl, defn = POSITIONS[position]
    tag = f"v{which}{level.replace('-', '_')}"
    header = workdir / f"api_{tag}.hpp"
    header.write_text(f"{type_src}\n{decl}\n")
    source = workdir / f"impl_{tag}.cpp"
    source.write_text(f'#include "api_{tag}.hpp"\n{defn}\n')
    lib = workdir / f"lib{tag}.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", LEVEL_FLAGS[level], "-o", str(lib), str(source)],
        check=True,
        capture_output=True,
        cwd=workdir,
    )
    return lib, header


def _run_compare(workdir: Path, change: str, position: str, level: str) -> CellResult:
    old_lib, old_h = _write_version(workdir, change, position, 1, level)
    new_lib, new_h = _write_version(workdir, change, position, 2, level)
    report = workdir / f"report_{change}_{position}_{level.replace('-', '')}.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck",
            "compare",
            str(old_lib),
            str(new_lib),
            "--header",
            f"old={old_h}",
            "--header",
            f"new={new_h}",
            "-o",
            f"json={report}",
        ],
        capture_output=True,
        text=True,
        cwd=workdir,
        timeout=600,
    )
    assert report.exists(), f"no JSON report written; stderr:\n{proc.stderr[-2000:]}"
    data = json.loads(report.read_text())
    return CellResult(verdict=data["verdict"], exit_code=proc.returncode)


@pytest.fixture(scope="module")
def cell_cache() -> dict[tuple[str, str, str], CellResult]:
    """Run each (change, position, level) once and share results across tests."""
    return {}


def _cached(
    cache: dict,
    tmp_path_factory: pytest.TempPathFactory,
    change: str,
    position: str,
    level: str,
) -> CellResult:
    key = (change, position, level)
    if key not in cache:
        workdir = tmp_path_factory.mktemp(
            f"{change}-{position}-{level.replace('-', '')}"
        )
        cache[key] = _run_compare(workdir, change, position, level)
    return cache[key]


@pytest.mark.parametrize(("change", "position", "level"), MATRIX, ids=MATRIX_IDS)
def test_matrix_verdict(
    change: str,
    position: str,
    level: str,
    cell_cache: dict,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    result = _cached(cell_cache, tmp_path_factory, change, position, level)
    expected = EXPECTED[(change, position)][level]
    assert result.verdict == expected, (
        f"{change} @ {position} [{level}]: got {result.verdict} "
        f"(exit {result.exit_code}), expected {expected}"
    )


@pytest.mark.parametrize(("change", "position"), CELLS, ids=CELL_IDS)
def test_dwarf_breaking_implies_header_not_clean(
    change: str,
    position: str,
    cell_cache: dict,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    dwarf = _cached(cell_cache, tmp_path_factory, change, position, "-g")
    if dwarf.verdict != "BREAKING":
        pytest.skip(f"DWARF run is {dwarf.verdict}; invariant applies to BREAKING only")
    header = _cached(cell_cache, tmp_path_factory, change, position, "-g0")
    assert not (header.verdict in CLEAN_VERDICTS and header.exit_code == 0), (
        f"{change} @ {position}: DWARF is BREAKING but header-only run reports "
        f"{header.verdict} with exit 0"
    )
