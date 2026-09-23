# SPDX-License-Identifier: Apache-2.0
"""A memo scoped to one detector pass over one (old, new) pair.

Several detectors ask the same pure, whole-snapshot question -- "which
stdlib records does this snapshot's public surface reference directly?" --
and each used to recompute it from scratch: a real oneDAL comparison made 34
such calls over 4 distinct inputs, ~21.7 s of main-thread time for ~2.5 s of
distinct work.

Keying on the snapshot object alone would be unsound outside a pass: a
snapshot is a mutable dataclass, and an ``id()`` can be reused once one is
collected. So the memo exists only while :func:`detection_memo_scope` is
open (``DetectorRegistry.run_all`` opens it around the detector loop), it
holds a strong reference to every snapshot it keys on (so no ``id`` can be
reused while the entry lives), and it is discarded when the pass ends.
Outside a scope :func:`memoized` simply calls through, so behaviour with or
without the scope is identical -- only the repeat cost differs.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, TypeVar

_T = TypeVar("_T")

_MEMO: ContextVar[dict[tuple[Hashable, ...], tuple[Any, Any]] | None] = ContextVar(
    "abicheck_detection_memo", default=None
)


@contextmanager
def detection_memo_scope() -> Iterator[None]:
    """Open a fresh memo for the duration of one detector pass.

    Nested scopes reuse the outer memo rather than shadowing it, so a
    detector that itself runs a nested pass does not throw away work.
    """
    if _MEMO.get() is not None:
        yield
        return
    token = _MEMO.set({})
    try:
        yield
    finally:
        _MEMO.reset(token)


def memoized(
    name: str, subject: object, extra: Hashable, compute: Callable[[], _T]
) -> _T:
    """``compute()``, reused for the same (*name*, *subject* identity, *extra*)
    inside an open :func:`detection_memo_scope`.

    *subject* is kept alive by the entry itself, so its ``id`` cannot be
    recycled for a different object while the memo lives.
    """
    memo = _MEMO.get()
    if memo is None:
        return compute()
    key = (name, id(subject), extra)
    hit = memo.get(key)
    if hit is not None and hit[0] is subject:
        return hit[1]  # type: ignore[no-any-return]
    value = compute()
    memo[key] = (subject, value)
    return value
