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

"""Deferred assembly of a loaded snapshot's ``semantic_ir`` (ADR-062 D18,
storage-format-v2 A2.1).

A snapshot's ``declarations``, ``types`` and debug-layout sections all live
in one place once decoded: the declaration store inside
``AbiSnapshot.semantic_ir`` (``model/declaration_store.py``). Deferring
those sections therefore means deferring that one attribute.
``AbiSnapshot.semantic_ir`` is a :class:`~.lazy_graph.LazyGraphField`; a
lazily loaded snapshot holds a :class:`~.lazy_graph.PendingGraph` there
whose decoder is a :class:`DeferredSemanticIR`.

:class:`DeferredSemanticIR` rebuilds, on first access, the snapshot the
eager decode would have built -- a scratch ``AbiSnapshot`` from the eagerly
decoded fields plus the deferred sections -- runs the load-time steps that
rewrite declarations (:func:`defer_declaration_step`) on it in their eager
order, and yields its ``semantic_ir``. So the value is the eager value, and
everything ``PendingGraph`` guarantees (one decode under a lock, a failure
raised at every access and never an empty store, pickling resolves, deep
copies decode independently) carries over unchanged.

The scratch snapshot is built from the field values captured at load, not
from the live owner: a field reassigned after load and before first access
is not seen by a deferred step. Every deferred step reads only the
snapshot's binary metadata, which no load-time caller reassigns.

Model layer: knows nothing of the stored encoding; ``storage`` supplies the
builder.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .lazy_graph import pending_decoder

__all__ = ["DeferredSemanticIR", "defer_declaration_step"]

#: The snapshot attribute this module defers.
FIELD = "semantic_ir"


class DeferredSemanticIR:
    """The decoder of a pending ``semantic_ir`` cell: *build* returns the
    scratch snapshot the eager decode would have produced; the steps queued
    by :func:`defer_declaration_step` then run on it, in queue order."""

    __slots__ = ("_build", "_lock", "_sealed", "_steps")

    def __init__(self, build: Callable[[], Any]) -> None:
        self._build = build
        self._steps: list[Callable[[Any], object]] = []
        self._sealed = False
        self._lock = threading.Lock()

    def add_step(self, step: Callable[[Any], object]) -> bool:
        """Queue *step*; ``False`` once a decode has started, in which case
        the caller must run *step* on the live snapshot itself."""
        with self._lock:
            if self._sealed:
                return False
            self._steps.append(step)
            return True

    def __call__(self) -> Any:
        with self._lock:
            self._sealed = True
            steps = tuple(self._steps)
        scratch = self._build()
        for step in steps:
            step(scratch)
        return scratch.__dict__[FIELD]


def defer_declaration_step(snapshot: Any, step: Callable[[Any], object]) -> None:
    """Run load-time *step* (which rewrites declarations in place) now, or,
    when *snapshot*'s IR is still pending, as part of its deferred decode."""
    decoder = pending_decoder(snapshot, FIELD)
    if isinstance(decoder, DeferredSemanticIR) and decoder.add_step(step):
        return
    step(snapshot)
