"""Integration: ELF/DWARF parse scaling on real compiled ``.so`` files.

The pure-Python scaling harness (``scripts/benchmark_scaling.py``) builds
``AbiSnapshot`` objects directly, so it cannot exercise the snapshot/dump
*parsing* stage. This guards the two parse slices that need only ``gcc`` (no
castxml) by compiling shared libraries with a growing export count and checking
each parse stays sub-quadratic:

- ``parse_elf_metadata`` — the ELF symbol-table parse (always available).
- ``parse_dwarf_metadata`` — the DWARF debug-info parse (``-g`` build), which is
  the dominant real-library dump cost the field eval measured (ICU 18.6 MB
  snapshot, openblas 23 MB / 9.5 s). PE/PDB parsing still needs a committed
  binary or a synthetic byte-stream generator and remains unbenchmarked.

Requires ``gcc`` on Linux (gcc produces Mach-O on macOS, PE on Windows).
"""

from __future__ import annotations

import math
import subprocess
import sys
import time
from pathlib import Path

import pytest

from abicheck.dwarf_metadata import parse_dwarf_metadata
from abicheck.elf_metadata import parse_elf_metadata

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="ELF parse scaling requires Linux (gcc produces Mach-O/PE elsewhere)",
)


def _gen_source(n: int) -> str:
    return "\n".join(f"int func_{i}(int x) {{ return x + {i}; }}" for i in range(n))


def _gen_dwarf_source(n: int) -> str:
    """Functions plus a distinct struct each, so the DWARF DIE tree grows with n."""
    return "\n".join(
        f"struct S{i} {{ int a; long b; char c; }};\n"
        f"int func_{i}(struct S{i} *p) {{ return p->a + {i}; }}"
        for i in range(n)
    )


def _compile_so(src: str, path: Path, *, debug: bool = False) -> None:
    cmd = ["gcc", "-shared", "-fPIC", "-O0"]
    if debug:
        cmd.append("-g")
    cmd += ["-o", str(path), "-x", "c", "-"]
    res = subprocess.run(cmd, input=src.encode(), capture_output=True)
    if res.returncode != 0:
        pytest.skip(f"gcc failed: {res.stderr.decode()[:200]}")


@pytest.mark.integration
def test_elf_parse_scaling_stays_subquadratic(tmp_path: Path) -> None:
    """Parsing a 4x-larger symbol table must not take ~16x longer."""
    timings: list[tuple[int, float]] = []
    for n in (500, 2000):
        so = tmp_path / f"lib{n}.so"
        _compile_so(_gen_source(n), so)
        start = time.monotonic()
        meta = parse_elf_metadata(so)
        timings.append((n, max(time.monotonic() - start, 1e-3)))
        exported = sum(1 for s in meta.symbols if s.name.startswith("func_"))
        assert exported >= n // 2, f"expected ~{n} exports, parsed {exported}"

    (n1, t1), (n2, t2) = timings
    exponent = math.log(t2 / t1) / math.log(n2 / n1)
    # True quadratic would be ~2.0; generous bound catches a real regression
    # without flaking on shared CI runners.
    assert exponent < 1.9, (
        f"ELF parse scaling exponent {exponent:.2f} regressed toward O(n^2)"
    )


@pytest.mark.integration
def test_dwarf_parse_scaling_stays_subquadratic(tmp_path: Path) -> None:
    """Parsing a 4x-larger DWARF DIE tree must not take ~16x longer.

    Guards ``parse_dwarf_metadata`` — the debug-info parse that dominates real
    library dump time — against an O(n^2) regression in the DIE walk.
    """
    timings: list[tuple[int, float]] = []
    for n in (500, 2000):
        so = tmp_path / f"libdw{n}.so"
        _compile_so(_gen_dwarf_source(n), so, debug=True)
        start = time.monotonic()
        meta = parse_dwarf_metadata(so)
        timings.append((n, max(time.monotonic() - start, 1e-3)))
        # The DWARF parse should recover the per-TU structs (best-effort: skip
        # if this toolchain emitted no usable DWARF rather than asserting a count
        # that depends on the gcc/DWARF version).
        if not meta.structs:
            pytest.skip("no DWARF structs parsed (toolchain emitted no usable DWARF)")

    (n1, t1), (n2, t2) = timings
    exponent = math.log(t2 / t1) / math.log(n2 / n1)
    assert exponent < 1.9, (
        f"DWARF parse scaling exponent {exponent:.2f} regressed toward O(n^2)"
    )
