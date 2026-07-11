"""Integration: DwarfSession sharing — regression + scaling guard.

Closes a gap left by the DWARF-session merge (PR #525, F5b follow-up): that
change was validated once, manually, on a single hand-built fixture — not by
anything committed to the repo, so nothing in CI would catch a regression back
to independent per-pass ELF opens, and the win was never checked at scale.

This file compiles *real* multi-CU C++ binaries reproducing the pvxs
pathology that motivated the change — the same ``std::``/template type
repeated across many compilation units, each pulling in its own physical DIE
subtree — and:

- ``test_session_reuse_faster_than_independent_opens`` — a same-binary,
  same-process A/B comparison. Guards the actual mechanism: sharing one
  ``DWARFInfo`` across the metadata + snapshot passes must stay reliably
  faster than the legacy independent-open path. Deliberately conservative
  (a fraction of the ~8x measured in validation) so it flags a real
  regression to "no sharing" without flaking on noisy CI runners.
- ``test_dwarf_only_dump_scaling_with_cu_count_stays_subquadratic`` — the
  production ``dumper.dump(..., dwarf_only=True)`` entry point, timed at a
  growing compilation-unit count, mirroring the sub-quadratic exponent gate
  ``test_perf_dump_scaling.py`` already applies to single-CU symbol/struct
  growth — but scaling the axis this PR actually touches (CU count), which
  that file's generator does not exercise.

``scripts/benchmark_scaling.py`` (the PR-vs-base regression lane in
``.github/workflows/performance.yml``) is not extended for this: it builds
``AbiSnapshot`` objects directly and deliberately never invokes a real
compiler (see ``test_benchmark_scaling.py``), so it cannot exercise DWARF
parsing at all. This file's ``integration`` marker is what wires it into
every PR instead — ``ci.yml``'s ``integration-tests`` job runs `-m
integration` unconditionally on every push/PR, no path filter required.

Requires ``g++`` on Linux (gcc/g++ produce Mach-O/PE elsewhere).
"""

from __future__ import annotations

import math
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "linux",
        reason="ELF DWARF tests require Linux (macOS/Windows compilers produce Mach-O/PE)",
    ),
]


def _require_tool(name: str) -> None:
    import shutil
    if shutil.which(name) is None:
        pytest.skip(f"{name} not found in PATH")


_HEADER_SRC = """
    #pragma once
    #include <vector>
    #include <string>
    #include <map>
    namespace scale {
    struct Point { int x; int y; double z; };
    template <typename T> struct Box { T value; std::vector<T> history; std::string label; };
    struct Registry { std::map<std::string, Box<int>> items; Point origin; };
    }
"""


def _compile_multi_cu_lib(tmp_path: Path, label: str, n_cus: int) -> Path:
    """Build a single .so from *n_cus* translation units that all
    ``#include`` the same header — so the same template/std:: type subtree
    gets its own physical DIEs re-emitted in every CU (the pvxs pathology:
    F5b measured this at 33 CUs / 4046 types on real libpvxs)."""
    src_dir = tmp_path / label
    src_dir.mkdir()
    header = src_dir / "shared.h"
    header.write_text(_HEADER_SRC, encoding="utf-8")

    cpp_files = []
    for i in range(n_cus):
        cpp = src_dir / f"cu{i}.cpp"
        cpp.write_text(
            f'#include "shared.h"\n'
            f"extern \"C\" int use_{i}(void) {{\n"
            f"    scale::Box<int> b;\n"
            f"    b.value = {i};\n"
            f"    b.history.push_back({i});\n"
            f"    return (int)b.history.size() + {i};\n"
            f"}}\n",
            encoding="utf-8",
        )
        cpp_files.append(cpp)

    so_file = src_dir / f"lib{label}.so"
    try:
        r = subprocess.run(
            ["g++", "-shared", "-fPIC", "-g", "-Og", "-o", str(so_file)]
            + [str(c) for c in cpp_files],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("g++ compilation timed out after 60s")
    if r.returncode != 0:
        pytest.skip(f"g++ compilation failed: {r.stderr[:300]}")
    with open(so_file, "rb") as f:
        if f.read(4) != b"\x7fELF":
            pytest.skip("compiled binary is not ELF (non-Linux platform)")
    return so_file


def test_session_reuse_faster_than_independent_opens(tmp_path: Path) -> None:
    """Regression guard for the actual mechanism this PR introduced.

    Runs the legacy (independent-open) path and the current production
    (shared-session) path back-to-back on the *same* compiled binary in the
    same process, so both see identical OS/filesystem cache state. If a
    future change silently drops session reuse (e.g. build_snapshot_from_dwarf
    stops honoring ``session=``), this fails deterministically rather than
    relying on the byte-identical correctness tests (which don't check speed).
    """
    _require_tool("g++")
    from abicheck.dwarf_advanced import parse_advanced_dwarf
    from abicheck.dwarf_metadata import parse_dwarf_metadata
    from abicheck.dwarf_snapshot import build_snapshot_from_dwarf
    from abicheck.dwarf_unified import open_dwarf_session, parse_dwarf_from_session
    from abicheck.elf_metadata import parse_elf_metadata

    so = _compile_multi_cu_lib(tmp_path, "cmp", n_cus=14)
    elf_meta = parse_elf_metadata(so)

    # Legacy: three independent ELF opens (pre-DwarfSession behaviour).
    t0 = time.perf_counter()
    dwarf_meta = parse_dwarf_metadata(so)
    dwarf_adv = parse_advanced_dwarf(so)
    legacy_snap = build_snapshot_from_dwarf(so, elf_meta, dwarf_meta, dwarf_adv, version="legacy")
    legacy_elapsed = max(time.perf_counter() - t0, 1e-4)

    # Current production path: one shared DwarfSession across all three passes.
    t1 = time.perf_counter()
    sess = open_dwarf_session(so)
    assert sess is not None
    meta2, adv2 = parse_dwarf_from_session(sess)
    session_snap = build_snapshot_from_dwarf(
        so, elf_meta, meta2, adv2, version="session", session=sess
    )
    sess.close()
    session_elapsed = max(time.perf_counter() - t1, 1e-4)

    # Sanity: both paths actually extracted the same real work, not near-empty
    # snapshots that would make the timing comparison meaningless.
    assert len(legacy_snap.types) == len(session_snap.types)
    assert legacy_snap.types

    # Validation measured ~8x on a comparable fixture; require only a modest,
    # CI-noise-tolerant fraction of that so this doesn't flake on a busy
    # runner while still catching a full regression to "no sharing" (where
    # session_elapsed would be >= legacy_elapsed, not meaningfully less).
    assert session_elapsed < legacy_elapsed * 0.85, (
        f"DwarfSession reuse ({session_elapsed:.4f}s) is not reliably faster "
        f"than independent opens ({legacy_elapsed:.4f}s) — the DWARFInfo-"
        f"sharing win appears to have regressed"
    )


def test_dwarf_only_dump_scaling_with_cu_count_stays_subquadratic(tmp_path: Path) -> None:
    """The production dwarf_only dump() path must not go quadratic as the
    number of compilation units grows — guards against a hidden O(n^2) in the
    session/cache path itself (e.g. a per-CU cache lookup that degrades),
    which the single-CU symbol/struct scaling in test_perf_dump_scaling.py
    cannot see because it never varies CU count.
    """
    _require_tool("g++")
    from abicheck.dumper import dump

    timings: list[tuple[int, float]] = []
    for n_cus in (8, 32):
        so = _compile_multi_cu_lib(tmp_path, f"scale{n_cus}", n_cus=n_cus)
        start = time.perf_counter()
        snap = dump(so, [], dwarf_only=True)
        elapsed = max(time.perf_counter() - start, 1e-3)
        timings.append((n_cus, elapsed))
        assert snap.functions, f"expected exported functions at n_cus={n_cus}"

    (n1, t1), (n2, t2) = timings
    exponent = math.log(t2 / t1) / math.log(n2 / n1)
    # True quadratic would be ~2.0; matches the generous bound used by the
    # sibling single-CU scaling test to avoid flaking on shared CI runners.
    assert exponent < 1.9, (
        f"dwarf_only dump CU-count scaling exponent {exponent:.2f} regressed toward O(n^2)"
    )
