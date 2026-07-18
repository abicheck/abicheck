# Copyright 2026 Nikolay Petrov
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

"""Integration proof that the L2 clang header-scan path actually consults the
scan-wide deadline (P0 SVS header-scan defect), using the **live** ``clang
-ast-dump=json`` frontend — not a mock of ``abicheck.deadline``.

Mirrors ``test_clang_header_backend_integration.py``'s pattern: deliberately
*not* marked ``integration`` (that marker's Linux gate requires castxml, which
this doesn't need), self-skips on its own real tool requirement (clang + g++).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from abicheck import deadline
from abicheck.dumper import dump

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="mirrors test_clang_header_backend_integration.py's Linux/ELF scoping",
)

_HEADER = """
#pragma once
int add(int a, int b);
"""

_SOURCE = """
#include "api.h"
int add(int a, int b) { return a + b; }
"""


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


@pytest.fixture
def built_lib(tmp_path: Path) -> tuple[Path, Path]:
    if not (_have("clang") and _have("g++")):
        pytest.skip("clang and g++ are required for this test")
    header = tmp_path / "api.h"
    header.write_text(_HEADER)
    src = tmp_path / "api.cpp"
    src.write_text(_SOURCE)
    so = tmp_path / "libapi.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(so), str(src), f"-I{tmp_path}"],
        check=True,
        capture_output=True,
    )
    return so, header


def test_no_active_deadline_scans_normally(built_lib: tuple[Path, Path]) -> None:
    # Baseline / no-regression check: with no --budget (no active deadline
    # scope), the ordinary header scan must succeed exactly as before.
    so, header = built_lib
    snap = dump(so, [header], header_backend="clang")
    assert {f.name for f in snap.functions} == {"add"}


def test_already_expired_deadline_aborts_before_reparsing(
    built_lib: tuple[Path, Path],
) -> None:
    # The mid-stage propagation proof: with an *already exhausted* deadline
    # active (simulating a scan whose --budget ran out during an earlier
    # stage), the clang header parse must refuse to even start — not run to
    # completion and only get caught by a post-hoc elapsed-time check.
    # dumper.py deliberately leaves DeadlineExceeded uncaught (see the comments
    # by dumper._clang_header_dump._run_clang) so scan_engine.run_scan_core can
    # map it onto _BudgetOverflow/exit 5, distinct from an ordinary parse
    # timeout; a caller that goes through dumper.dump() directly (no
    # scan_engine layer) sees the raw DeadlineExceeded.
    so, header = built_lib
    with deadline.deadline_scope(-1.0):
        with pytest.raises(deadline.DeadlineExceeded):
            dump(so, [header], header_backend="clang")


def test_deadline_scope_does_not_leak_into_later_scans(
    built_lib: tuple[Path, Path],
) -> None:
    # A previous scan's exhausted deadline must never bleed into the next
    # call (ContextVar reset on scope exit) — otherwise one budget-exceeded
    # scan would permanently wedge every later scan in the same process
    # (e.g. the MCP server, which reuses one long-lived process).
    so, header = built_lib
    with deadline.deadline_scope(-1.0):
        with pytest.raises(deadline.DeadlineExceeded):
            dump(so, [header], header_backend="clang")
    snap = dump(so, [header], header_backend="clang")
    assert {f.name for f in snap.functions} == {"add"}


def test_generous_budget_is_not_truncated_to_internal_default(
    built_lib: tuple[Path, Path],
) -> None:
    # A user-set --budget far larger than the old fixed 120s internal cap must
    # not be silently re-capped back down to 120s (see deadline.bounded_timeout
    # docstring) — the scan should simply succeed well within a 10-minute
    # deadline for this trivial header.
    so, header = built_lib
    start = time.monotonic()
    with deadline.deadline_scope(600.0):
        snap = dump(so, [header], header_backend="clang")
    assert time.monotonic() - start < 60
    assert {f.name for f in snap.functions} == {"add"}


# --- SVS-shaped pathological header: real, measurable compile cost ----------
#
# The field report's own dry-run number (0.51s) vs. real cost (>15,000s) came
# from a *small on-disk header* whose #include/template complexity a flat
# size-based estimate can't see. This reproduces that shape with a genuinely
# expensive (not simulated) clang AST dump from a 4-line header: clang's
# ``-ast-dump=json`` re-serializes each nested template instantiation inline
# inside its parent's dump, so a recursive template chain's *dumped output
# size* grows steeply super-linearly with recursion depth even though the
# *instantiation count* itself is only linear (memoized). Calibrated locally:
# depth 100 -> ~40 MB/0.2s, depth 200 -> ~280 MB/0.6s, depth 300 -> ~900 MB/
# 1.5s — kept modest here (depth 150, ~120 MB) to stay CI-safe while still
# being a real, non-trivial clang cost, not a sleep() stand-in.
_DEEP_TEMPLATE_DEPTH = 150

_DEEP_TEMPLATE_HEADER = f"""
#pragma once
template <int N> struct Deep {{
    using type = typename Deep<N - 1>::type;
    enum {{ v = Deep<N - 1>::v + 1 }};
}};
template <> struct Deep<0> {{ using type = int; enum {{ v = 0 }}; }};
constexpr int deep_value = Deep<{_DEEP_TEMPLATE_DEPTH}>::v;
int touch(int x);
"""

_DEEP_TEMPLATE_SOURCE = """
#include "deep.h"
int touch(int x) { return x + deep_value; }
"""


@pytest.fixture
def pathological_lib(tmp_path: Path) -> tuple[Path, Path]:
    if not (_have("clang") and _have("g++")):
        pytest.skip("clang and g++ are required for this test")
    header = tmp_path / "deep.h"
    header.write_text(_DEEP_TEMPLATE_HEADER)
    src = tmp_path / "deep.cpp"
    src.write_text(_DEEP_TEMPLATE_SOURCE)
    so = tmp_path / "libdeep.so"
    subprocess.run(
        [
            "g++", "-shared", "-fPIC", "-std=c++20",
            f"-ftemplate-depth={_DEEP_TEMPLATE_DEPTH + 50}",
            "-o", str(so), str(src), f"-I{tmp_path}",
        ],
        check=True,
        capture_output=True,
    )
    return so, header


def test_pathological_header_aborts_within_bounded_time_under_tiny_budget(
    pathological_lib: tuple[Path, Path],
) -> None:
    # The P0 acceptance test proper: a header that costs real, measurable
    # clang time (not a mock) must still be stopped at (roughly) the budget
    # boundary, not at whatever the header's *actual* cost turns out to be.
    # An 0.05s budget is far below this header's natural cost (see the
    # companion "completes" test below), so this proves the abort is driven
    # by the deadline, not by the header happening to finish quickly anyway.
    so, header = pathological_lib
    start = time.monotonic()
    with deadline.deadline_scope(0.05):
        # Only DeadlineExceeded: under this active deadline_scope,
        # run_bounded's own contract is to translate any in-flight timeout
        # into DeadlineExceeded, never a raw TimeoutExpired (that's reserved
        # for the fully-unbudgeted case) -- accepting either here would mask
        # a real regression in that translation (CodeRabbit review, PR #591).
        with pytest.raises(deadline.DeadlineExceeded):
            dump(so, [header], header_backend="clang")
    elapsed = time.monotonic() - start
    # Generous ceiling (kill-signal delivery + temp-file cleanup), but nowhere
    # near what an *unbounded* pathological run would cost (the SVS field
    # report: 15,000+ seconds for a header of this same shape).
    assert elapsed < 15.0, (
        f"aborted after {elapsed:.1f}s under a 0.05s budget — the deadline "
        "must bound this regardless of how expensive the real parse is"
    )


@pytest.mark.slow
def test_pathological_header_natural_cost_is_tracked(
    pathological_lib: tuple[Path, Path],
) -> None:
    # Perf-tracking companion (not a correctness test): records this header's
    # *actual, unbudgeted* clang cost so a future regression (e.g. losing the
    # AST disk cache, or a clang upgrade changing dump behaviour) shows up in
    # the existing test-duration trend artifact (tests/conftest.py's
    # ABICHECK_DURATIONS_JSON hook -> scripts/summarize_test_durations.py ->
    # CI run summary), the same mechanism docs/development/performance.md
    # already relies on for the compare()-scaling story. Loose bounds only:
    # this must complete (proving the fixture is genuinely bounded, not a
    # true hang) but is expected to take real, non-trivial time.
    so, header = pathological_lib
    start = time.monotonic()
    snap = dump(so, [header], header_backend="clang")
    elapsed = time.monotonic() - start
    assert {f.name for f in snap.functions} == {"touch"}
    assert elapsed < 60.0, (
        f"natural cost grew to {elapsed:.1f}s for depth {_DEEP_TEMPLATE_DEPTH} "
        "-- recalibrate _DEEP_TEMPLATE_DEPTH down if this fixture (or the "
        "clang/host it runs on) gets slower"
    )
