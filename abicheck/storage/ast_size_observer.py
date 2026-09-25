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

"""Report the byte size of each header AST a dump acquires, to whoever asks.

A release fan-out sizes its worker pool before anything is parsed, so it can
only guess what one member costs (``workflows/release_jobs.py``'s fixed
per-depth table). The size of the ``clang -ast-dump=json`` document is the
number that actually predicts a header-depth worker's peak (measured on
Intel SVS at 1-76 header roots: peak RSS is 1.6-2.5x the document), and it
is known the moment the document exists -- as clang's spilled output on a
cold run, or as the cache entry on a warm one.

This module only carries that number out. A caller that wants it opens
:func:`observe_ast_sizes` with a sink; the parse sites call
:func:`report_ast_size`, which is a single ``ContextVar`` read when nobody
is observing. A ``ContextVar`` rather than a parameter because the sites are
several layers below any caller that could pass one, and a worker thread
started with a copied context (both release fan-out and the concurrent
old/new side resolution do this) still reports to the member that owns it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

__all__ = [
    "mark_ast_intake",
    "observe_ast_sizes",
    "report_ast_size",
    "set_ast_intake_hook",
]

_SINK: ContextVar[Callable[[int], None] | None] = ContextVar(
    "abicheck_ast_size_sink", default=None
)


@contextmanager
def observe_ast_sizes(sink: Callable[[int], None]) -> Iterator[None]:
    """Send every AST size reported inside this block to *sink* (bytes)."""
    token = _SINK.set(sink)
    try:
        yield
    finally:
        _SINK.reset(token)


def report_ast_size(nbytes: int) -> None:
    """Record that a header AST of *nbytes* was just acquired."""
    sink = _SINK.get()
    if sink is not None and nbytes > 0:
        sink(nbytes)


#: Process-wide stage-boundary hook for AST intake. Installed by
#: ``workflows.memory_trace`` when it is imported: the parse sites live in
#: layers that may not import ``workflows``, and without a boundary here the
#: trace's first event landed ~35 s after a clang run's actual peak (the
#: ``read_text`` of a 1 GB cache entry), so it could not see its worst moment.
_INTAKE_HOOK: Callable[..., None] | None = None


def set_ast_intake_hook(hook: Callable[..., None] | None) -> None:
    """Install (or clear, with ``None``) the AST-intake boundary hook."""
    global _INTAKE_HOOK
    _INTAKE_HOOK = hook


def mark_ast_intake(event: str, /, **attrs: object) -> None:
    """Report an AST-intake boundary (``ast.intake:start``/``:done``)."""
    hook = _INTAKE_HOOK
    if hook is not None:
        hook(event, **attrs)
