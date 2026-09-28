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

"""Per-snapshot derived facts shared across one comparison.

One ``compare`` derives the same pure facts from each snapshot several times
over: the identity table and the export join built on it are rebuilt by the
public-surface resolution ``compare_snapshots`` runs before the comparison,
again by post-processing's scoping pass, and again by the edge-coverage
report (MKL ``libmkl_rt``: ``join_exports`` 8 calls / 9.3 s).

A comparison already treats its snapshots as read-only for its whole
duration -- ``compare.surface_reconcile`` memoizes on exactly that
assumption and scopes the memo to the call. This module gives the same
scope to any pure per-snapshot derivation: :func:`comparison_memo_scope`
opens it (nested scopes join the outermost one), and
:func:`comparison_memoized` computes a value at most once per
``(name, snapshot)`` inside it. Outside a scope nothing is cached, so a
snapshot still being built (a dump, a merge) can never be served a value
derived from an earlier state of itself.

What is memoized is chosen against memory as well as time: every entry
stays resident until the comparison ends. The public surface and the
referenced-identifier graph were tried too and dropped -- holding them for
the whole comparison raised peak allocation by ~5 MiB on the memory gate's
1-2k-declaration scenarios (``Memory regression (PR vs base)``) for a few
seconds of MKL-scale time; the identity table and export join cost ~1-2 MiB.

Keys use ``id(snapshot)``; the scope holds a strong reference to every
snapshot it keyed, so an id cannot be reused by a different object while
the scope is open, and everything is released when it closes. Only values
their consumers treat as immutable (frozen dataclasses, named tuples) may
be memoized here: the same object is handed to every caller.

Scopes are per execution context (a ``ContextVar``): a worker thread that
does not inherit the context simply computes for itself.
"""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

__all__ = [
    "comparison_memo_active",
    "comparison_memo_scope",
    "comparison_memoized",
]

_T = TypeVar("_T")


class _Scope:
    __slots__ = ("lock", "values")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        # (name, id(snapshot)) -> (snapshot, value); the snapshot reference
        # pins the id for the scope's lifetime.
        self.values: dict[tuple[str, int], tuple[object, Any]] = {}


_scope: contextvars.ContextVar[_Scope | None] = contextvars.ContextVar(
    "abicheck_comparison_memo_scope", default=None
)


@contextmanager
def comparison_memo_scope() -> Iterator[None]:
    """Open a comparison-lifetime memo scope; join the enclosing one if any."""
    if _scope.get() is not None:
        yield
        return
    scope = _Scope()
    token = _scope.set(scope)
    try:
        yield
    finally:
        _scope.reset(token)
        scope.values.clear()


def comparison_memo_active() -> bool:
    """Whether a :func:`comparison_memo_scope` is open in this context."""
    return _scope.get() is not None


def comparison_memoized(name: str, snapshot: object, compute: Callable[[], _T]) -> _T:
    """``compute()``, once per ``(name, snapshot)`` inside an open scope.

    *compute* must be a pure function of *snapshot* whose result its callers
    never mutate. Outside a scope it simply runs.
    """
    scope = _scope.get()
    if scope is None:
        return compute()
    key = (name, id(snapshot))
    with scope.lock:
        hit = scope.values.get(key)
    if hit is not None:
        return hit[1]  # type: ignore[no-any-return]
    value = compute()
    with scope.lock:
        # A concurrent caller may have stored first; keep the first value so
        # every caller shares one object.
        return scope.values.setdefault(key, (snapshot, value))[1]  # type: ignore[no-any-return]
