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
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...deadline import CancelToken, cancel_scope
from ...service_header_graph_attach import HeaderGraphAst

if TYPE_CHECKING:
    from pathlib import Path

    from ...compile_context import CompileContext


@dataclass(frozen=True)
class HeaderGraphPrefetch:
    """A running header-graph parse, and the token that cancels it."""

    future: Future[HeaderGraphAst]
    token: CancelToken

    def cancel(self) -> None:
        """Stop the parse: kill its clang child so its thread returns soon."""
        self.token.cancel()
        # Stops a parse still queued; a running one is stopped by the token.
        self.future.cancel()


def prefetch_header_graph_ast(
    headers: list[Path],
    includes: list[Path],
    lang: str | None,
    compile: CompileContext | None,
) -> HeaderGraphPrefetch:
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

    token = CancelToken()

    def _acquire() -> HeaderGraphAst:
        with cancel_scope(token):
            return acquire_header_graph_ast(headers, includes, lang, compile)

    pool = BudgetedExecutor(1, thread_name_prefix="abicheck-hgraph")
    ctx = contextvars.copy_context()
    future = pool.submit(ctx.run, _acquire)
    future.add_done_callback(lambda _f: pool.shutdown(wait=False))
    return HeaderGraphPrefetch(future, token)


#: Longest a failed dump waits for its cancelled header-graph prefetch.
_PREFETCH_SETTLE_TIMEOUT_S = 10.0


@contextmanager
def prefetch_settled_on_failure(
    prefetch: HeaderGraphPrefetch | None,
) -> Iterator[None]:
    """Cancel *prefetch* if the block fails, then re-raise the failure.

    A prefetch runs beside the primary dump. If that dump fails, nothing will
    ever consume the prefetched graph, so it is cancelled: its clang child is
    killed and its thread-budget slot comes back when its thread returns.
    An ordinary failure waits a bounded time for that, so the error is not
    held up by a slow kill. An interrupt cancels and propagates at once.
    """
    try:
        yield
    except Exception:
        if prefetch is not None:
            prefetch.cancel()
            with suppress(BaseException):
                prefetch.future.result(timeout=_PREFETCH_SETTLE_TIMEOUT_S)
        raise
    except BaseException:
        if prefetch is not None:
            prefetch.cancel()
        raise


def prefetch_graph_if_useful(
    wanted: bool,
    resolved_backend: str,
    headers: list[Path],
    includes: list[Path],
    lang: str | None,
    compile: CompileContext | None,
) -> HeaderGraphPrefetch | None:
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
