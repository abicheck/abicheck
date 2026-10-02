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

"""Request-scoped, instance and disk caches on the central wrapper.

The second half of :mod:`abicheck.model.execution_cache` (split only to keep
each file under the architecture line ceiling): caches whose *lifetime* is
not the process -- one request or pass (:class:`ScopedCache`), one bulk
operation shared by its worker threads (:class:`SharedScopedCache`), one
object (:class:`InstanceMemo`) -- and the read/write policy of an owner's
on-disk cache (:class:`DiskCache`). Every one registers with the same
registry and honours the same ``ABICHECK_REFERENCE_MODE`` switch.
"""

from __future__ import annotations

import contextvars
import functools
import threading
from collections.abc import Callable, Hashable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar, cast

from .execution_cache import MISSING, RequestKey, reference_mode, register_cache

__all__ = ["DiskCache", "InstanceMemo", "ScopedCache", "SharedScopedCache"]

_T = TypeVar("_T")
_F = TypeVar("_F", bound=Callable[..., Any])


# ── request-scoped cache ────────────────────────────────────────────────────


class _Scope:
    __slots__ = ("lock", "values")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        # key -> (pinned subject or None, value)
        self.values: dict[Hashable, tuple[object, Any]] = {}


class ScopedCache:
    """A memo that exists only inside :meth:`scope` (one request or pass).

    Nested scopes join the outermost. Scopes are per execution context: a
    worker thread that did not inherit the context computes for itself.
    *pin*, when given, is kept alive by the entry and must be the very
    object a hit was stored for -- the safe way to key on ``id(obj)``.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._var: contextvars.ContextVar[_Scope | None] = contextvars.ContextVar(
            f"abicheck_scoped_cache:{name}", default=None
        )
        self.stats = register_cache(name, "scoped")

    @contextmanager
    def scope(self) -> Iterator[None]:
        if self._var.get() is not None:
            yield
            return
        s = _Scope()
        token = self._var.set(s)
        try:
            yield
        finally:
            self._var.reset(token)
            s.values.clear()

    def scoped(self, fn: _F) -> _F:
        """Decorator: run *fn* inside :meth:`scope`."""

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with self.scope():
                return fn(*args, **kwargs)

        return cast("_F", wrapper)

    def active(self) -> bool:
        return self._var.get() is not None

    def get_or_compute(
        self, key: RequestKey, compute: Callable[[], _T], *, pin: object = None
    ) -> _T:
        if not isinstance(key, RequestKey):
            raise TypeError(f"{self.name}: cache keys are built by request_key()")
        s = self._var.get()
        if s is None:
            return compute()
        if reference_mode():
            self.stats.bypasses += 1
            return compute()
        with s.lock:
            hit = s.values.get(key)
        if hit is not None and hit[0] is pin:
            self.stats.hits += 1
            return cast("_T", hit[1])
        self.stats.misses += 1
        value = compute()
        with s.lock:
            stored = s.values.setdefault(key, (pin, value))
            if stored[0] is not pin:
                s.values[key] = stored = (pin, value)
            self.stats.stores += 1
            return cast("_T", stored[1])


class SharedScopedCache:
    """A memo shared by every thread while at least one :meth:`scope` is open.

    For a pure function hammered during one bulk operation that may fan out
    to worker threads (a stored graph load normalizes the same few strings
    hundreds of thousands of times): the scope is depth-counted process-wide
    rather than per execution context, and the memo is dropped when the last
    open scope exits, so nothing is retained after the operation.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._lock = threading.Lock()
        self._depth = 0
        self._values: dict[Hashable, Any] | None = None
        self.stats = register_cache(name, "shared_scoped")

    @contextmanager
    def scope(self) -> Iterator[None]:
        with self._lock:
            self._depth += 1
            if self._values is None:
                self._values = {}
        try:
            yield
        finally:
            with self._lock:
                self._depth -= 1
                if self._depth == 0:
                    self._values = None

    def active(self) -> bool:
        return self._values is not None

    def get_or_compute(self, key: RequestKey, compute: Callable[[], _T]) -> _T:
        values = self._values
        if values is None:
            return compute()
        if reference_mode():
            self.stats.bypasses += 1
            return compute()
        hit = values.get(key, MISSING)
        if hit is not MISSING:
            self.stats.hits += 1
            return cast("_T", hit)
        self.stats.misses += 1
        value = compute()
        values[key] = value
        self.stats.stores += 1
        return value


class InstanceMemo:
    """A memo stored on an object's own ``__dict__`` under one attribute.

    Its lifetime is the object's: for a value derived from an immutable (or
    read-only-while-compared) object -- a symbol table's export index, a
    snapshot pair's reconciled surfaces. *pin* is matched by identity, so an
    entry keyed on a second object's ``id`` can never alias a recycled one.
    """

    def __init__(self, name: str, attr: str) -> None:
        self.name = name
        self.attr = attr
        self.stats = register_cache(name, "instance")

    def peek(self, obj: object, key: RequestKey, *, pin: object = None) -> Any:
        """The stored value, or :data:`MISSING` (always, in reference mode)."""
        if reference_mode():
            self.stats.bypasses += 1
            return MISSING
        entries = getattr(obj, "__dict__", {}).get(self.attr)
        hit = entries.get(key) if entries is not None else None
        if hit is not None and hit[0] is pin:
            self.stats.hits += 1
            return hit[1]
        self.stats.misses += 1
        return MISSING

    def put(self, obj: object, key: RequestKey, value: _T, *, pin: object = None) -> _T:
        """Store *value* (a no-op in reference mode, or on an object with no
        ``__dict__``); returns *value*."""
        if not isinstance(key, RequestKey):
            raise TypeError(f"{self.name}: cache keys are built by request_key()")
        d = getattr(obj, "__dict__", None)
        if d is None or reference_mode():
            return value
        d.setdefault(self.attr, {})[key] = (pin, value)
        self.stats.stores += 1
        return value

    def get_or_compute(
        self,
        obj: object,
        key: RequestKey,
        compute: Callable[[], _T],
        *,
        pin: object = None,
    ) -> _T:
        hit = self.peek(obj, key, pin=pin)
        if hit is not MISSING:
            return cast("_T", hit)
        return self.put(obj, key, compute(), pin=pin)

    def drop(self, obj: object) -> None:
        """Forget everything memoized on *obj*."""
        getattr(obj, "__dict__", {}).pop(self.attr, None)


# ── disk cache policy ───────────────────────────────────────────────────────


class DiskCache:
    """Whether an on-disk cache may be read or written, and what it did.

    The storage format stays with its owner (``snapshot_cache``,
    ``dumper_cache``); every read and write goes through :meth:`lookup` and
    :meth:`store` so reference mode and the counters cover both.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.stats = register_cache(name, "disk")

    def enabled(self) -> bool:
        return not reference_mode()

    def permits(self) -> bool:
        """:meth:`enabled`, counting a bypass when it is not: for an owner
        that decides once whether to use its cache at all."""
        if reference_mode():
            self.stats.bypasses += 1
            return False
        return True

    def lookup(self, read: Callable[[], _T | None]) -> _T | None:
        if reference_mode():
            self.stats.bypasses += 1
            return None
        value = read()
        if value is None:
            self.stats.misses += 1
        else:
            self.stats.hits += 1
        return value

    def record(self, *, hit: bool) -> None:
        """Count a read whose I/O the owner performed itself."""
        if reference_mode():
            self.stats.bypasses += 1
        elif hit:
            self.stats.hits += 1
        else:
            self.stats.misses += 1

    def record_bypass(self) -> None:
        self.stats.bypasses += 1

    def store(self, write: Callable[[], object]) -> None:
        if reference_mode():
            self.stats.bypasses += 1
            return
        write()
        self.stats.stores += 1
