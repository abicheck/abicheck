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

"""When the header graph's own clang parse starts: beside the primary dump.

The parse itself is :func:`service_header_graphacquire_header_graph_ast`;
this module only schedules it on a budgeted thread and settles it when the
dump it ran beside fails.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import Future
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING

from ...service_header_graph_attach import HeaderGraphAst

if TYPE_CHECKING:
    from pathlib import Path

    from ...compile_context import CompileContext


def prefetch_header_graph_ast(
    headers: list[Path],
    includes: list[Path],
    lang: str | None,
    compile: CompileContext | None,
) -> Future[HeaderGraphAst]:
    """Start ``acquire_header_graph_ast`` on a budgeted thread now.

    Lets the header graph's own clang parse run while the primary castxml
    dump runs. Its thread comes from the process-wide budget
    (``ABICHECK_MAX_THREADS``); with the budget spent it runs inline, which
    is exactly the old, sequential order.
    """
    import contextvars

    from ...process_resources import BudgetedExecutor

    # Looked up per call, so a patched ``acquire_header_graph_ast`` applies.
    from ...service_header_graph_attach import acquire_header_graph_ast

    pool = BudgetedExecutor(1, thread_name_prefix="abicheck-hgraph")
    ctx = contextvars.copy_context()
    future = pool.submit(
        ctx.run, acquire_header_graph_ast, headers, includes, lang, compile
    )
    future.add_done_callback(lambda _f: pool.shutdown(wait=False))
    return future


#: Longest a failed dump waits for its abandoned header-graph prefetch.
_PREFETCH_SETTLE_TIMEOUT_S = 10.0


@contextmanager
def prefetch_settled_on_failure(
    future: Future[HeaderGraphAst] | None,
) -> Iterator[None]:
    """Wait for *future* to finish before re-raising a failure of the block.

    A prefetch runs beside the primary dump; if that dump fails, nothing will
    ever consume the prefetched graph, but its parse keeps running and keeps
    its thread-budget slot (released only when it completes). Settling it
    here returns the slot before the failing call returns, so a failed dump
    never leaves an orphaned parse behind to starve a later pool.
    """
    try:
        yield
    except Exception:
        # Bounded: a prefetch stuck in a slow parse must not hold the error
        # hostage. Past the bound the slot is returned when the parse ends,
        # exactly the pre-fix behavior, but the common case (a parse that is
        # finishing or already done) is settled before the error propagates.
        if future is not None:
            with suppress(BaseException):
                future.result(timeout=_PREFETCH_SETTLE_TIMEOUT_S)
        raise
    # KeyboardInterrupt / SystemExit propagate immediately: an interrupt must
    # never wait on a background parse (up to clang's own timeout).


def prefetch_graph_if_useful(
    wanted: bool,
    resolved_backend: str,
    headers: list[Path],
    includes: list[Path],
    lang: str | None,
    compile: CompileContext | None,
) -> Future[HeaderGraphAst] | None:
    """Start the header graph's clang parse now, when that is worth doing.

    The parse depends only on the headers and compile context, never on the
    snapshot, so under castxml -- which never produces the clang AST the
    graph needs -- it can run alongside the primary dump. Measured on a cold
    header cache: 11-15% off a single-library compare, neutral on a warm
    cache and on a release whose members already fill the cores. Under the
    clang frontend the attach reuses the primary pass's AST instead, so a
    prefetch there would parse the same headers twice; ``None`` then, and
    whenever the graph is not wanted at all.
    """
    if not (wanted and headers and resolved_backend == "castxml"):
        return None
    return prefetch_header_graph_ast(headers, includes, lang, compile)
