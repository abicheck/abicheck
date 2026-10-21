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
"""The shared L4/L5 run: one AST dump per translation unit, read by both tiers.

:func:`~.source_replay.run_source_replay` hands its cache misses and an L5 run
(:class:`~.l5_ast_run.L5AstRun`) here when the extractor reads a clang AST dump
(:func:`shares_ast_dump`). A TU that both tiers read is dumped once: the worker
takes the dump, lets L4 read the raw tree, lets the L5 passes read the
materialized form (the run's ``consume`` hook), and only then releases it.

The L5 side arrives as hooks on the run, so this module imports nothing from the
L5 passes and no cycle forms through :mod:`.call_graph`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from functools import partial
from typing import Any

from .build_evidence import CompileUnit
from .l5_ast_run import L5AstRun
from .source_abi import SourceAbiTu
from .source_extractors.base import SourceExtractionError
from .source_replay_pool import _extract_cache_misses

#: One TU's share of the shared run: the L4 ``(tu, diagnostic)``, and the L5
#: per-family ``(results, diagnostics)`` -- ``None`` when L5 does not read it.
SharedUnitResult = tuple[
    tuple[SourceAbiTu | None, str | None],
    tuple[list[Any], list[list[str]]] | None,
]


def shares_ast_dump(extractor: object) -> bool:
    """Whether *extractor* reads a clang AST dump the L5 passes can share.

    Duck-typed on the clang extractor's dump/extract split, so the replay does
    not import the clang extractor (castxml and android never share).
    """
    return callable(getattr(extractor, "dump_for", None)) and callable(
        getattr(extractor, "source_abi_from_dump", None)
    )


def shared_tu_work(
    extractor: Any,
    roots: list[str],
    consume: Callable[[Any, CompileUnit], tuple[list[Any], list[list[str]]]],
    item: tuple[CompileUnit, bool, bool],
) -> SharedUnitResult:
    """Dump one TU once, then let each tier that wants it read that dump.

    Module-level so it pickles for the process pool. L4 reads the raw tree
    first; the L5 hook then materializes it in place; the tree is released
    before the macro subprocess, which is the last step.
    """
    cu, want_l4, want_l5 = item
    dump = extractor.dump_for(cu)
    tu: SourceAbiTu | None = None
    l4_error: str | None = None
    if want_l4:
        try:
            tu = extractor.source_abi_from_dump(cu, dump, public_header_roots=roots)
        except SourceExtractionError as exc:
            l4_error = f"{cu.source or cu.id}: {exc}"
    l5 = consume(dump, cu) if want_l5 else None
    dump.release()
    if tu is not None:
        tu = extractor.finish_source_abi(cu, tu, public_header_roots=roots)
    return (tu, l4_error), l5


def run_shared_dumps(
    extractor: Any,
    roots: Sequence[str] | None,
    units: list[CompileUnit],
    misses: list[int],
    l5_run: L5AstRun,
    *,
    jobs_for: Callable[[int], int],
    use_process_pool: bool,
) -> tuple[int, list[tuple[SourceAbiTu | None, str | None]]]:
    """The L4 cache misses and the L5 scope, in one pool, one dump per TU.

    A TU in both sets is dumped once and read by both tiers. Returns the worker
    count and the L4 ``(tu, diagnostic)`` pairs for *misses*, in order, and
    fills ``l5_run.outcomes`` from the L5 results in the L5 scope's own order
    (the same fold the standalone pass applies).
    """
    if l5_run.consume is None or l5_run.fold is None:
        raise ValueError("a shared L4/L5 run needs its L5 hooks (plan_l5_run)")
    l5_units = [cu for cu in l5_run.target.compile_units if cu.source]
    l5_ids = {cu.id for cu in l5_units}
    miss_ids = {units[i].id for i in misses}
    work = [(units[i], True, units[i].id in l5_ids) for i in misses]
    work += [(cu, False, True) for cu in l5_units if cu.id not in miss_ids]
    jobs = jobs_for(len(work))
    worker = partial(shared_tu_work, extractor, list(roots or []), l5_run.consume)
    started = time.monotonic()
    done = _extract_cache_misses(worker, work, jobs, use_process_pool=use_process_pool)
    elapsed = time.monotonic() - started
    l4_by_id: dict[str, tuple[SourceAbiTu | None, str | None]] = {}
    l5_by_id: dict[str, tuple[list[Any], list[list[str]]]] = {}
    for (cu, want_l4, _want_l5), (l4, l5) in zip(work, done, strict=True):
        if want_l4:
            l4_by_id[cu.id] = l4
        if l5 is not None:
            l5_by_id[cu.id] = l5
    l5_run.outcomes = l5_run.fold(
        [l5_by_id[cu.id] for cu in l5_units], jobs=jobs, elapsed=elapsed
    )
    return jobs, [l4_by_id[units[i].id] for i in misses]
