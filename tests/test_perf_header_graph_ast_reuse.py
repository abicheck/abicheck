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

"""Performance regression guard for the G31 Phase C AST-reuse mechanism (#648).

The original regression this PR fixed (scan wall-clock going from ~10-15s to
5-6 minutes once the header-only semantic graph became unconditional, G31
Phase A) was found by wall-clock measurement, not by a unit test -- every
test added alongside the fix itself (``test_dumper_clang.py``,
``test_service_unit.py``, ``test_cli_dump_helpers_coverage.py``) checks the
*mechanism* (is ``ast_memoize_scope()`` active, is the memo slot populated/
consumed) via mocks, not real wall-clock time, so none of them would catch a
future change that silently stops reusing the in-process AST while leaving
the scope/slot bookkeeping itself intact.

Mirrors ``test_perf_dwarf_session_scaling.py``'s
``test_session_reuse_faster_than_independent_opens`` pattern: a same-process,
same-input A/B timing comparison with a conservative, CI-noise-tolerant
margin, rather than an absolute wall-clock budget.

Requires ``clang`` on ``PATH`` (any platform -- this only runs
``clang -ast-dump=json`` over a synthetic header, no shared-library
compilation). ``integration`` marker wires it into ``ci.yml``'s
``integration-tests`` job (``-m integration`` unconditionally on every
push/PR), same as the DWARF-session scaling guard above.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from abicheck import dumper, dumper_cache

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("clang") is None,
        reason="clang required for the header-only AST pass",
    ),
]


def _gen_large_header(n: int) -> str:
    """A header with *n* distinct structs/functions -- large enough that its
    clang AST JSON dump takes measurable time to disk-read and re-parse."""
    lines = ["#pragma once"]
    for i in range(n):
        lines.append(f"struct S{i} {{ int a; long b; double c; char d[8]; }};")
        lines.append(f"int func_{i}(struct S{i} *p, int x);")
    return "\n".join(lines)


def test_header_graph_attach_reuse_faster_than_disk_reparse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for the actual mechanism G31 Phase C introduced
    (``dumper_cache.ast_memoize_scope``/``load_cached_ast``/
    ``store_cached_ast``).

    Reproduces, in miniature, the exact cost this PR eliminated: the
    header-only semantic graph attach step re-reading and re-parsing a
    multi-MB clang AST JSON dump the main snapshot pass had already parsed
    moments earlier. Times a same-process A/B on the identical cached AST:

    - "no reuse" -- a disk-cache hit with no in-process memo, i.e. what
      ``_attach_header_graph`` did before this PR, and what any caller that
      never opens ``dumper_cache.ast_memoize_scope()`` still gets today
      (e.g. ``appcompat.check_app_compatibility``, deliberately -- see
      ``dumper_cache.py``'s own docstring).
    - "reuse" -- an in-process memo hit, i.e. the current production
      behaviour for ``service.run_dump``'s format branches and
      ``cli_dump_helpers.perform_elf_dump``.

    Both read the exact same on-disk cache file, so this isolates the memo's
    own win from clang-invocation or filesystem-cache variance.
    """
    header = tmp_path / "big.h"
    header.write_text(_gen_large_header(1500), encoding="utf-8")
    cache_path = tmp_path / "ast_cache.json"
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: cache_path)
    # Every _clang_header_dump call -- memo hit or not -- first resolves the
    # host system-include dirs via a real, uncached ``cc -E -v``-style probe
    # (dumper_sysinc._resolve_clang_system_includes, called twice for C mode)
    # *before* it even checks the memo. That probe's cost is identical on
    # both sides of this A/B and was observed to fully swamp the actual
    # memo-vs-disk-reparse signal on a loaded macOS CI runner (both timed
    # calls landing within noise of each other) -- disabled here to isolate
    # the mechanism this test actually exists to guard, matching the same
    # opt-out ``test_dumper_clang.py``'s real-fixture tests already use.
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")

    # Prime the disk cache with one real clang parse -- not timed (mirrors
    # the main snapshot pass that already ran before _attach_header_graph
    # follows it).
    root0, _, _ = dumper._clang_header_dump(
        [header], [], compiler="c", lang="c", memoize=False
    )
    assert cache_path.exists()
    # Sanity: the generated AST is actually large enough to make a JSON
    # reparse take measurable time, or the timing comparison below is
    # meaningless noise, not a real regression guard.
    cache_size = cache_path.stat().st_size
    assert cache_size > 1_000_000, (
        f"generated AST cache is only {cache_size} bytes -- too small to "
        "produce a measurable reparse cost; increase the header size"
    )

    # "No reuse": disk-cache hit, no in-process memo.
    t0 = time.perf_counter()
    root1, _, _ = dumper._clang_header_dump(
        [header], [], compiler="c", lang="c", memoize=False
    )
    no_reuse_elapsed = max(time.perf_counter() - t0, 1e-6)

    # "Reuse": prime the per-thread memo slot (mirrors the primary snapshot
    # pass's own scoped call, memoize=None -> True while the scope is
    # active), then pop it (mirrors _attach_header_graph's own
    # memoize=False consumer call).
    with dumper_cache.ast_memoize_scope():
        dumper._clang_header_dump([header], [], compiler="c", lang="c")
        t1 = time.perf_counter()
        root2, _, _ = dumper._clang_header_dump(
            [header], [], compiler="c", lang="c", memoize=False
        )
        reuse_elapsed = max(time.perf_counter() - t1, 1e-6)

    # Sanity: all three parses produced the identical AST -- the timing
    # comparison is only meaningful if "reuse" and "no reuse" did the same
    # real work, not different (or degenerate/empty) work.
    assert root0 == root1 == root2

    # Local measurement (see this module's docstring) showed roughly a 7x
    # win; require only a modest, CI-noise-tolerant fraction of that so this
    # doesn't flake on a busy runner while still catching a regression back
    # to "always re-read and re-parse from disk" (where reuse_elapsed would
    # be >= no_reuse_elapsed, not meaningfully less).
    assert reuse_elapsed < no_reuse_elapsed * 0.5, (
        f"in-process AST memo reuse ({reuse_elapsed:.4f}s) is not reliably "
        f"faster than a disk-cache-hit JSON reparse ({no_reuse_elapsed:.4f}s) "
        "-- the G31 Phase C AST-reuse win appears to have regressed"
    )
