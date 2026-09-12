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

"""A run-scoped memo for ``snapshot_content_digest``.

The digest is a pure function of an ``AbiSnapshot``'s persisted content,
but computing it means serializing the whole snapshot -- on a real oneAPI
operand, seconds. It has two independent consumers
(``workflows.gate.snapshot_identity_digest`` and
``storage.snapshot_encode.same_persisted_content``, the latter serializing
*both* sides), and a single ``compare`` run reaches them six times over
two distinct snapshots: ``checker.compare``'s own assurance attach, the
front end's re-attach with the real evidence pack, and the
same-binary-compared digest fallback. Four of those six serializations are
recomputations of a value already produced earlier in the same run.

Why a run-scoped cache rather than a module-level memo or a field on the
snapshot:

* ``AbiSnapshot`` is a plain mutable dataclass, and the resolve half of a
  compare genuinely mutates it (evidence backfill, provenance tagging,
  pack capping). A memo with no lifetime would answer from before a
  mutation; a lazily-populated field on the DTO would have to be
  invalidated by every one of those writers, which is the same bug with
  more places to forget it. An explicitly-scoped cache is only ever
  entered where the snapshots are already final.
* Keys are ``id()``, which is only sound while the object is alive, so the
  cache holds a *strong* reference to each snapshot it has answered for.
  Within a run that costs nothing -- the run holds them anyway -- and it
  makes id reuse across a GC unrepresentable rather than merely unlikely.

Outside an active scope this module is inert and
``snapshot_content_digest`` behaves exactly as it did before: no caching,
no staleness surface, and no behavior to reason about for the callers
(tests, tooling, one-off scripts) that never enter one.
"""

from __future__ import annotations

import contextvars
import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, TypeVar, cast

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..model.snapshot import AbiSnapshot

__all__ = [
    "digest_scope",
    "memoized_digest",
    "run_scoped_digest_cache",
    "scope_is_active",
]

# id(snapshot) -> (the snapshot itself, its digest). The snapshot is kept
# so its id cannot be reused by a later object while the entry stands.
_SCOPE: contextvars.ContextVar[dict[int, tuple[Any, str]] | None] = (
    contextvars.ContextVar("abicheck_snapshot_digest_scope", default=None)
)


@contextmanager
def digest_scope() -> Iterator[None]:
    """Memoize ``snapshot_content_digest`` per snapshot for this block.

    Re-entrant: a nested scope reuses the outer one, so a front end that
    wraps a whole run and an inner helper that wraps its own phase share a
    single memo instead of the inner one shadowing the outer.

    Enter this only where the snapshots involved are final for the rest of
    the block. Mutating a snapshot inside an active scope after its digest
    has been taken is the one way to get a stale answer.
    """
    if _SCOPE.get() is not None:
        yield
        return
    token = _SCOPE.set({})
    try:
        yield
    finally:
        _SCOPE.reset(token)


def scope_is_active() -> bool:
    """Whether a :func:`digest_scope` is currently open."""
    return _SCOPE.get() is not None


def memoized_digest(snap: AbiSnapshot, compute: Callable[[AbiSnapshot], str]) -> str:
    """*compute(snap)*, at most once per snapshot per open scope."""
    cache = _SCOPE.get()
    if cache is None:
        return compute(snap)
    key = id(snap)
    hit = cache.get(key)
    if hit is not None:
        return hit[1]
    digest = compute(snap)
    cache[key] = (snap, digest)
    return digest


_F = TypeVar("_F", bound=Callable[..., Any])


def run_scoped_digest_cache(func: _F) -> _F:
    """Run *func* inside a :func:`digest_scope`.

    For a front end whose whole body is one comparison of one pair: the
    scope then spans every consumer that asks for a digest during it --
    ``checker.compare``'s own assurance attach, the front end's re-attach
    with the real evidence pack, and the same-binary-compared fallback --
    which is the point, since those are the six serializations of two
    snapshots this exists to collapse to two. Scoping it per call rather
    than per process is also what bounds the memo: a release fan-out
    comparing many libraries releases each pair's entry when its own
    comparison returns.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with digest_scope():
            return func(*args, **kwargs)

    return cast("_F", wrapper)
