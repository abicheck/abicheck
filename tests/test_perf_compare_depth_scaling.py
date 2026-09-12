# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""End-to-end scaling guard for `compare --depth binary` / `--depth headers`.

Closes the capability gap `ci.yml` records in its own comment: the old
`tests/test_perf_binary_scan.py` guarded `scan --depth binary`/`--depth
headers` staying fast and was deleted with the `scan` command (ADR-068
Phase 6), leaving no `compare`-side equivalent.

What is missing is specifically the **end-to-end** path.
`test_performance.py` already benchmarks `compare()` over two in-memory
snapshots, and `test_perf_dump_scaling.py` benchmarks the ELF/DWARF
parsers directly — neither covers resolving two real artifacts through the
CLI at a requested evidence depth, which is what a user actually waits for
and what the deleted test measured.

Two things about *how* this asserts, both taken from prior incidents in
this repository rather than invented here:

- **A scaling exponent, not a wall-clock ceiling.** `_perf_scaling.py`
  exists because fixed per-run time bounds on a shared CI runner flake:
  one stalled tick failed an unregressed `main`. Sizes are deliberately
  not evenly log-spaced, for the reason that module's docstring gives.
- **A non-vacuity precondition.** If fixed per-invocation overhead ever
  dominates, the exponent tends to zero and the guard passes while
  measuring nothing. So each test first asserts the largest size really
  costs meaningfully more than the smallest, and fails with that
  diagnosis rather than reporting a comfortable exponent.

  Being accurate about what that buys *today*: it cannot currently fire.
  These tests invoke the CLI in-process through `CliRunner`, whose fixed
  cost measures ~27ms, against 0.36s at n=500 and 1.50s at n=2000 for
  `--depth binary` — a 4.15x cost for 4x the input, so work dominates by
  two orders of magnitude. The precondition is a forward guard, for a
  later change that makes overhead matter: a subprocess harness (~0.5s of
  interpreter startup), or sizes shrunk to keep the lane fast. Because a
  guard that cannot fire is indistinguishable from decoration, the
  predicate is exercised directly below rather than only through the
  timed paths, where it would never be reached.

Each comparison also removes one function, so every timed run does real
detection work and exits 4 (ABI break). A run that silently degraded to an
error path would be faster *and* wrong, and the assertions catch it.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from _perf_scaling import measure_scaling_exponent
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="compare --depth scaling fixture builds ELF shared libraries with gcc",
)

#: Deliberately irregular log-spacing — see `_perf_scaling.py`: an evenly
#: spaced progression makes the middle points drop out of the least-squares
#: slope, collapsing the fit to the two endpoints this helper exists to
#: avoid depending on.
_BINARY_SIZES = (500, 900, 1400, 2000)

#: Smaller for the header path: it runs a real header-AST backend per side,
#: which cost ~1.2s at n=500 when this was written, so the binary sizes
#: would make one lane dominate the integration job's wall clock.
_HEADER_SIZES = (200, 380, 560, 800)

#: True quadratic is ~2.0. Matches the bound its sibling
#: `test_perf_dump_scaling.py` settled on: generous enough not to flake on a
#: shared runner, tight enough that a real regression cannot hide under it.
_MAX_EXPONENT = 1.9

#: The largest size must cost at least this much more than the smallest,
#: otherwise fixed overhead is dominating and the exponent is noise. Set far
#: below the ~4x measured today so ordinary runner variance cannot trip it.
_MIN_COST_RATIO = 1.5


def _decls(n: int) -> str:
    return "\n".join(f"int func_{i}(int x);" for i in range(n))


def _defs(n: int) -> str:
    return "\n".join(f"int func_{i}(int x) {{ return x + {i}; }}" for i in range(n))


def _compile_so(src: str, path: Path) -> None:
    result = subprocess.run(
        ["gcc", "-shared", "-fPIC", "-O0", "-o", str(path), "-x", "c", "-"],
        input=src.encode(),
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip(f"gcc failed: {result.stderr.decode()[:200]}")


def _run(runner: CliRunner, argv: list[str]) -> float:
    """One timed CLI invocation, asserting it did the work we think it did."""
    start = time.monotonic()
    result = runner.invoke(main, argv)
    elapsed = max(time.monotonic() - start, 1e-3)
    # 4 == ABI break. Every pair below removes exactly one exported
    # function, so anything else means the run took a different path and
    # the time just measured is not the time this test is guarding.
    assert result.exit_code == 4, (
        f"expected an ABI-break verdict (4), got {result.exit_code}: "
        f"{result.output[:400]}"
    )
    return elapsed


def _assert_measurement_is_meaningful(
    per_size: dict[int, float], sizes: tuple[int, ...]
) -> None:
    ratio = per_size[sizes[-1]] / per_size[sizes[0]]
    assert ratio >= _MIN_COST_RATIO, (
        f"the largest input ({sizes[-1]}) cost only {ratio:.2f}x the smallest "
        f"({sizes[0]}) — fixed per-invocation overhead is dominating, so the "
        f"scaling exponent below would be measuring noise rather than work. "
        f"Raise the sizes rather than relaxing the exponent bound."
    )


@pytest.mark.integration
def test_compare_depth_binary_scaling_stays_subquadratic(tmp_path: Path) -> None:
    """A 4x-larger export table must not take ~16x longer to compare."""
    runner = CliRunner()
    built: dict[int, tuple[Path, Path]] = {}
    per_size: dict[int, float] = {}

    def _measure(n: int) -> float:
        pair = built.get(n)
        if pair is None:
            old, new = tmp_path / f"libold{n}.so", tmp_path / f"libnew{n}.so"
            _compile_so(_defs(n), old)
            _compile_so(_defs(n - 1), new)  # one export removed
            built[n] = pair = (old, new)
        old, new = pair
        elapsed = _run(
            runner,
            ["compare", str(old), str(new), "--depth", "binary", "-o", "json=-"],
        )
        per_size[n] = elapsed
        return elapsed

    exponent = measure_scaling_exponent(_measure, _BINARY_SIZES)
    _assert_measurement_is_meaningful(per_size, _BINARY_SIZES)
    assert exponent < _MAX_EXPONENT, (
        f"compare --depth binary scaling exponent {exponent:.2f} regressed "
        f"toward O(n^2)"
    )


@pytest.mark.integration
def test_compare_depth_headers_scaling_stays_subquadratic(tmp_path: Path) -> None:
    """The same guard one evidence layer up, where a header-AST backend runs
    per side — the layer the binary test above cannot observe at all."""
    runner = CliRunner()
    built: dict[int, tuple[Path, Path, Path, Path]] = {}
    per_size: dict[int, float] = {}

    def _measure(n: int) -> float:
        parts = built.get(n)
        if parts is None:
            old, new = tmp_path / f"hlibold{n}.so", tmp_path / f"hlibnew{n}.so"
            _compile_so(_defs(n), old)
            _compile_so(_defs(n - 1), new)
            old_h, new_h = tmp_path / f"old{n}.h", tmp_path / f"new{n}.h"
            old_h.write_text(_decls(n))
            new_h.write_text(_decls(n - 1))
            built[n] = parts = (old, new, old_h, new_h)
        old, new, old_h, new_h = parts
        elapsed = _run(
            runner,
            [
                "compare",
                str(old),
                str(new),
                "-H",
                f"old={old_h}",
                "-H",
                f"new={new_h}",
                "--depth",
                "headers",
                "-o",
                "json=-",
            ],
        )
        per_size[n] = elapsed
        return elapsed

    exponent = measure_scaling_exponent(_measure, _HEADER_SIZES)
    _assert_measurement_is_meaningful(per_size, _HEADER_SIZES)
    assert exponent < _MAX_EXPONENT, (
        f"compare --depth headers scaling exponent {exponent:.2f} regressed "
        f"toward O(n^2)"
    )


def test_the_non_vacuity_precondition_can_actually_fire() -> None:
    """Direct test of the predicate, not routed through a timed run.

    The timed tests above cannot reach its failing branch today (see this
    module's docstring), so without this the precondition would be an
    assertion nobody has ever seen hold or fail.
    """
    sizes = (500, 900, 1400, 2000)
    overhead_dominated = {500: 1.00, 900: 1.02, 1400: 1.05, 2000: 1.10}
    with pytest.raises(AssertionError, match="fixed per-invocation overhead"):
        _assert_measurement_is_meaningful(overhead_dominated, sizes)

    # And it accepts the shape actually measured when this was written, so
    # it cannot be satisfied by simply always raising.
    _assert_measurement_is_meaningful(
        {500: 0.36, 900: 0.63, 1400: 1.02, 2000: 1.50}, sizes
    )
