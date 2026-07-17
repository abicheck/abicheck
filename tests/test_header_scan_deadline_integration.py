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
