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

"""The one cache wrapper every abicheck cache goes through (defect family F5).

Design-hardening plan, Phase 4: a cache or pool is an *optimization*, and an
optimization must produce the reference answer. Before this module each cache
was its own mechanism (``functools.lru_cache`` on probes that run a
subprocess, module-level dicts, ``ContextVar`` memos, two disk caches), so
"turn every optimization off" had no single meaning and no production switch.
Now there is one:

``ABICHECK_REFERENCE_MODE=1``
    Every cache built here computes instead of reading or storing (and counts
    the bypass), and :func:`abicheck.process_resources.max_threads` reports a
    budget of one thread that every pool runs inline, so a release fan-out
    takes its sequential member path. It never changes *what* is computed,
    only whether a remembered answer is reused -- the oracle harness H5
    (``tests/test_family_f5_optimization_reference.py``) requires the two
    configurations to produce byte-identical reports.

Cache kinds (all registered by name; :func:`cache_stats` reports them):

* :func:`memoized` -- process-lifetime memo of a function. The key is the
  call's *bound arguments in signature order* (so ``f(x)`` and ``f(x=x)``
  are one entry, unlike ``lru_cache``), and an optional *witness* re-checks
  freshness on every hit -- an I/O-dependent probe (a tool's ``--version``)
  passes the executable's ``stat`` so a replaced tool is re-probed.
* :func:`memoized_property` -- per-instance derived value (``cached_property``).
* :class:`MemoryCache` -- an explicitly keyed, optionally bounded (entries
  and weighed bytes) single-flight memo; keys are :func:`request_key`
  values naming every input, never positional ad-hoc tuples.
* Request-scoped, shared-scoped and per-object memos and the on-disk cache
  policy live in :mod:`abicheck.model.execution_cache_scoped`.
* :func:`register_cache` -- a cache whose storage engine has its own budget
  logic (the spelling-match caches) registers here and consults
  :func:`reference_mode`, so the switch and the accounting stay central.

Lives in ``model/`` because every layer must be able to reach it and
``model`` is the only layer every other one may import.
"""

from __future__ import annotations

import functools
import inspect
import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterator
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar, cast, overload

__all__ = [
    "REFERENCE_MODE_ENV_VAR",
    "CacheStats",
    "MISSING",
    "MemoryCache",
    "RequestKey",
    "cache_stats",
    "clear_all_caches",
    "clear_memoized",
    "memoized",
    "memoized_property",
    "path_witness",
    "reference_mode",
    "register_cache",
    "request_key",
    "reset_cache_stats",
]

_T = TypeVar("_T")
_V = TypeVar("_V")
_F = TypeVar("_F", bound=Callable[..., Any])

#: The production kill switch. ``1``/``true``/``yes``/``on`` (any case)
#: enables reference mode; anything else, or unset, leaves it off.
REFERENCE_MODE_ENV_VAR = "ABICHECK_REFERENCE_MODE"
_TRUE = frozenset({"1", "true", "yes", "on"})


# ``os.environ.get`` re-encodes the key on every call (~0.6 us here), and this
# is read on every cache hit of functions called millions of times per run.
# ``os.environ`` keeps its encoded mapping in ``_data`` (every write through
# ``os.environ`` -- including ``monkeypatch.setenv`` -- updates it), so the
# lookup is done there with the key encoded once. Falls back to the public
# API on an interpreter whose ``os.environ`` lacks those attributes.
_ENV_DATA: Any = getattr(os.environ, "_data", None)
try:
    _ENV_KEY: Any = os.environ.encodekey(REFERENCE_MODE_ENV_VAR)  # type: ignore[attr-defined]
    _ENV_DECODE: Callable[[Any], str] = os.environ.decodevalue  # type: ignore[attr-defined]
except AttributeError:  # pragma: no cover - non-CPython os.environ
    _ENV_DATA = None


def reference_mode() -> bool:
    """Whether ``ABICHECK_REFERENCE_MODE`` disables every optimization.

    Read on every call, not once at import: a long-lived process (a test
    session, an embedding service) can switch it between runs.
    """
    if _ENV_DATA is not None:
        encoded = _ENV_DATA.get(_ENV_KEY)
        if not encoded:
            return False
        raw: str | None = _ENV_DECODE(encoded)
    else:  # pragma: no cover - non-CPython os.environ
        raw = os.environ.get(REFERENCE_MODE_ENV_VAR)
    if not raw:
        return False
    return raw.strip().lower() in _TRUE


# ── keys ────────────────────────────────────────────────────────────────────


class RequestKey(tuple):  # type: ignore[type-arg]
    """A cache key that names every input: sorted ``(field, value)`` pairs.

    Built only by :func:`request_key`, so two call sites that agree on the
    inputs' *names* agree on the key, and a key can never silently drop an
    input by position the way a hand-assembled tuple can.
    """

    __slots__ = ()

    def fields(self) -> dict[str, Hashable]:
        return dict(cast("tuple[tuple[str, Hashable], ...]", self))


def request_key(**fields: Hashable) -> RequestKey:
    """The :class:`RequestKey` for a request whose inputs are *fields*."""
    return RequestKey(sorted(fields.items()))


# ── stats and registry ──────────────────────────────────────────────────────


@dataclass
class CacheStats:
    """What one cache did. Counters are best-effort under free threading."""

    name: str
    kind: str
    hits: int = 0
    misses: int = 0
    bypasses: int = 0
    stores: int = 0
    stale: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "bypasses": self.bypasses,
            "stores": self.stores,
            "stale": self.stale,
        }


@dataclass
class _Registered:
    stats: CacheStats
    clear: Callable[[], None] = field(default=lambda: None)


_REGISTRY_LOCK = threading.Lock()
#: name -> registration. The wrapper's own bookkeeping, not a cache: it holds
#: one record per cache *definition* (fixed at import), never per request.
_REGISTRY: dict[str, _Registered] = {}


def register_cache(
    name: str, kind: str, clear: Callable[[], None] | None = None
) -> CacheStats:
    """Register a cache under *name*; return the :class:`CacheStats` it updates.

    Names are unique; re-registering a name (a module reloaded by a test)
    replaces the old record so its stats do not leak into the new one.
    """
    stats = CacheStats(name, kind)
    with _REGISTRY_LOCK:
        _REGISTRY[name] = _Registered(stats, clear or (lambda: None))
    return stats


def cache_stats() -> dict[str, dict[str, int]]:
    """``{name: {hits, misses, bypasses, stores, stale}}`` for every cache."""
    with _REGISTRY_LOCK:
        return {n: r.stats.as_dict() for n, r in sorted(_REGISTRY.items())}


def reset_cache_stats() -> None:
    with _REGISTRY_LOCK:
        records = list(_REGISTRY.values())
    for r in records:
        s = r.stats
        s.hits = s.misses = s.bypasses = s.stores = s.stale = 0


def clear_all_caches() -> None:
    """Drop every process-lifetime entry (memory caches and memoized functions)."""
    with _REGISTRY_LOCK:
        records = list(_REGISTRY.values())
    for r in records:
        r.clear()


# ── bounded single-flight memory cache ──────────────────────────────────────

#: Returned by ``peek`` methods on a miss.
MISSING: Any = object()
_MISSING = MISSING


class MemoryCache(Generic[_V]):
    """A process-lifetime, thread-safe memo keyed by :class:`RequestKey`.

    * *max_entries* / *max_bytes* (with *weigh*) bound what is retained,
      least-recently-used first; a value heavier than the whole byte budget
      is returned but never stored.
    * Single-flight: concurrent misses on one key compute once; the others
      wait for that result (and retry themselves if it raised).
    * ``get_or_compute(..., witness=w)`` stores ``w()`` with the entry and
      recomputes when a later ``w()`` differs -- the freshness check an
      I/O-dependent value needs in a long-lived process.
    * ``keep=`` decides whether a computed value may be stored (a degraded
      result is returned but not remembered).
    * *copy* is applied to every value handed out, for values a caller owns.
    * *field* names the single input a one-input cache is keyed on; its keys
      are then that input's values.
    """

    def __init__(
        self,
        name: str,
        *,
        max_entries: int | None = None,
        max_bytes: int | None = None,
        weigh: Callable[[_V], int] | None = None,
        copy: Callable[[_V], _V] | None = None,
        field: str | None = None,
    ) -> None:
        self.name = name
        #: When set, the request identity is this one named input and callers
        #: pass its value directly (no :class:`RequestKey` built per lookup on
        #: a hot path); otherwise every key must come from :func:`request_key`.
        self.field = field
        self._max = max_entries
        self._max_bytes = max_bytes
        self._weigh = weigh
        self._copy = copy
        self._bytes = 0
        self._entries: OrderedDict[Hashable, tuple[_V, int, Hashable]] = OrderedDict()
        self._in_flight: dict[Hashable, Future[_V]] = {}
        self._lock = threading.Lock()
        self.stats = register_cache(name, "memory", self.clear)

    def _check_key(self, key: Hashable) -> None:
        if self.field is None and not isinstance(key, RequestKey):
            raise TypeError(f"{self.name}: cache keys are built by request_key()")

    def _out(self, value: _V) -> _V:
        return self._copy(value) if self._copy is not None else value

    def get_or_compute(
        self,
        key: Hashable,
        compute: Callable[[], _V],
        *,
        witness: Callable[[], Hashable] | None = None,
        witness_of: Callable[[_V], Hashable] | None = None,
        still_fresh: Callable[[Any], bool] | None = None,
        keep: Callable[[_V], bool] | None = None,
    ) -> _V:
        """The value for *key*, computing (outside the lock) on a miss.

        Freshness: *witness* is taken before computing, *witness_of* from the
        computed value; either is stored with the entry. A hit is served only
        when ``still_fresh(stored)`` holds -- by default, when *witness*
        taken now equals the stored one. A witness that comes back ``None``
        means "could not be verified", and such a value is never stored.
        """
        self._check_key(key)
        if reference_mode():
            self.stats.bypasses += 1
            return compute()
        while True:
            hit = self._fresh_hit(key, witness, still_fresh)
            if hit is not _MISSING:
                return self._out(hit)
            with self._lock:
                pending = self._in_flight.get(key)
                if pending is None:
                    future: Future[_V] = Future()
                    self._in_flight[key] = future
                    self.stats.misses += 1
                    break
            try:
                return self._out(pending.result())
            except Exception:
                continue  # the owner failed: retry (a loop, not recursion)
        try:
            value = self._compute_and_store(key, compute, witness, witness_of, keep)
        except BaseException as exc:
            with self._lock:
                self._in_flight.pop(key, None)
            future.set_exception(exc)
            raise
        with self._lock:
            self._in_flight.pop(key, None)
        future.set_result(value)
        return self._out(value)

    def _fresh_hit(
        self,
        key: Hashable,
        witness: Callable[[], Hashable] | None,
        still_fresh: Callable[[Any], bool] | None,
    ) -> Any:
        """The stored value if it is still fresh, else :data:`MISSING`
        (dropping a stale entry)."""
        with self._lock:
            hit = self._entries.get(key)
        if hit is None:
            return _MISSING
        if still_fresh is not None:
            fresh = still_fresh(hit[2])
        else:
            fresh = witness is None or witness() == hit[2]
        with self._lock:
            if self._entries.get(key) is not hit:
                return _MISSING
            if fresh:
                self._entries.move_to_end(key)
                self.stats.hits += 1
                return hit[0]
            self.stats.stale += 1
            self._drop(key)
        return _MISSING

    def _compute_and_store(
        self,
        key: Hashable,
        compute: Callable[[], _V],
        witness: Callable[[], Hashable] | None,
        witness_of: Callable[[_V], Hashable] | None,
        keep: Callable[[_V], bool] | None,
    ) -> _V:
        verified = witness is not None or witness_of is not None
        seen = witness() if witness is not None else None
        value = compute()
        if witness_of is not None:
            seen = witness_of(value)
        if (keep is None or keep(value)) and not (verified and seen is None):
            self._store(key, value, seen)
        return value

    def peek(self, key: Hashable, default: Any = None) -> Any:
        """The stored value for *key* (no freshness check), else *default*.

        For a cache that is *filled* by a batch producer and *read* point by
        point, often hundreds of thousands of times per comparison: the read
        takes no lock and does not refresh recency (eviction stays in
        insertion order for entries only ever peeked). Reference mode always
        reports a miss.
        """
        if (_ENV_DATA is None or _ENV_DATA.get(_ENV_KEY)) and reference_mode():
            self.stats.bypasses += 1
            return default
        hit = self._entries.get(key)
        if hit is None:
            self.stats.misses += 1
            return default
        self.stats.hits += 1
        return self._out(hit[0])

    def raw_get(self) -> Callable[[Hashable], Any]:
        """The storage's own ``get``: ``key -> (value, weight, witness) | None``.

        No switch, no counters, no copy: only for a hot loop that has itself
        just checked :func:`reference_mode` (and treats a hit as a miss when it
        is set) -- ``demangle``'s per-symbol lookup, ~10^5 per comparison.
        Stays valid across :meth:`clear`.
        """
        return self._entries.get

    def reader(self, default: Any = None) -> Callable[[Hashable], Any]:
        """A bound, stats-free :meth:`peek` for a lookup in a hot loop.

        Same answers as :meth:`peek` (reference mode still always misses),
        minus the counters and the method dispatch: for a cache consulted
        hundreds of thousands of times per comparison, where each lookup must
        cost about what a plain ``dict`` membership test did. Stays valid
        across :meth:`clear`.
        """
        entries_get = self._entries.get
        env_get = _ENV_DATA.get if _ENV_DATA is not None else None
        copy = self._copy

        def read(key: Hashable) -> Any:
            if (env_get is None or env_get(_ENV_KEY)) and reference_mode():
                return default
            hit = entries_get(key)
            if hit is None:
                return default
            return copy(hit[0]) if copy is not None else hit[0]

        return read

    def put(self, key: Hashable, value: _V) -> None:
        """Store *value* for *key* unconditionally (a no-op in reference mode)."""
        self._check_key(key)
        if reference_mode():
            self.stats.bypasses += 1
            return
        self._store(key, value, None)

    def _drop(self, key: Hashable) -> None:
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._bytes -= previous[1]

    def _store(self, key: Hashable, value: _V, seen: Hashable) -> None:
        weight = self._weigh(value) if self._weigh is not None else 0
        if self._max_bytes is not None and weight > self._max_bytes:
            return
        stored = self._copy(value) if self._copy is not None else value
        with self._lock:
            self._drop(key)
            self._entries[key] = (stored, weight, seen)
            self._bytes += weight
            self.stats.stores += 1
            while (self._max is not None and len(self._entries) > self._max) or (
                self._max_bytes is not None and self._bytes > self._max_bytes
            ):
                _, (_, dropped, _) = self._entries.popitem(last=False)
                self._bytes -= dropped

    def __contains__(self, key: object) -> bool:
        """Whether *key* is stored (introspection; no stats, no freshness)."""
        with self._lock:
            return key in self._entries

    def keys(self) -> list[RequestKey]:
        """Stored keys, least recently used first (introspection)."""
        with self._lock:
            return cast("list[RequestKey]", list(self._entries))

    def __iter__(self) -> Iterator[RequestKey]:
        return iter(self.keys())

    def values(self) -> list[_V]:
        """Stored values, least recently used first (introspection)."""
        with self._lock:
            return [v for v, _, _ in self._entries.values()]

    def discard(self, key: RequestKey) -> None:
        with self._lock:
            self._drop(key)

    @property
    def retained_bytes(self) -> int:
        return self._bytes

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def path_witness(*paths: str | os.PathLike[str] | None) -> Hashable:
    """``(path, device, inode, mtime_ns, size)`` per path, for a *witness*.

    A bare executable name is resolved on ``PATH`` first, so a probe keyed on
    ``"clang"`` notices when ``clang`` is replaced or a different one comes
    first. A path that cannot be stat'ed contributes ``-1`` fields: still
    comparable, so its later appearance is noticed too.
    """
    import shutil

    out: list[tuple[str, int, int, int, int]] = []
    for p in paths:
        if p is None:
            continue
        name = os.fspath(p)
        resolved = (
            name
            if os.sep in name or (os.altsep is not None and os.altsep in name)
            else (shutil.which(name) or name)
        )
        try:
            st = os.stat(resolved)
        except OSError:
            out.append((resolved, -1, -1, -1, -1))
            continue
        out.append((resolved, st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size))
    return tuple(out)


# ── memoized functions ──────────────────────────────────────────────────────


class _LruStats(CacheStats):
    """:class:`CacheStats` whose hits/misses/stores are read from the storage
    engine's own counters (zeroing them records a baseline)."""

    def __init__(self, name: str, info: Callable[[], Any]) -> None:
        self._info = info
        self._base = [0, 0]
        super().__init__(name, "memoized")

    @property  # type: ignore[override]
    def hits(self) -> int:
        return int(self._info().hits) - self._base[0]

    @hits.setter
    def hits(self, value: int) -> None:
        self._base[0] = int(self._info().hits) - value

    @property  # type: ignore[override]
    def misses(self) -> int:
        return int(self._info().misses) - self._base[1]

    @misses.setter
    def misses(self, value: int) -> None:
        self._base[1] = int(self._info().misses) - value

    @property  # type: ignore[override]
    def stores(self) -> int:
        return self.misses

    @stores.setter
    def stores(self, value: int) -> None:
        return None


def _memoize_with_lru(
    fn: Callable[..., Any],
    maxsize: int | None,
    positional: int,
    key_of: Callable[[tuple[Any, ...], dict[str, Any]], Hashable],
) -> Callable[..., Any]:
    """The storage for a witness-free memo with a plain positional signature.

    ``functools.lru_cache`` is used *only* as the storage engine, behind the
    wrapper: every call is normalized to the bound positional argument tuple
    first (so ``f(x)`` and ``f(x=x)`` are one entry) and reference mode never
    reaches it. Its C implementation keeps a hit within a few times a bare
    call, which a pure-Python store could not (~0.4 us per hit measured).
    """
    cached = functools.lru_cache(maxsize=maxsize)(fn)
    name = f"{fn.__module__}.{fn.__qualname__}"
    stats = _LruStats(name, cached.cache_info)
    with _REGISTRY_LOCK:
        _REGISTRY[name] = _Registered(stats, cached.cache_clear)
    env_get = _ENV_DATA.get if _ENV_DATA is not None else None

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if (env_get(_ENV_KEY) if env_get is not None else True) and reference_mode():
            stats.bypasses += 1
            return fn(*args, **kwargs)
        if kwargs or len(args) != positional:
            args = cast("tuple[Any, ...]", key_of(args, kwargs))
        return cached(*args)

    wrapper.cache_clear = cached.cache_clear  # type: ignore[attr-defined]
    wrapper.cache_len = lambda: cached.cache_info().currsize  # type: ignore[attr-defined]
    wrapper.stats = stats  # type: ignore[attr-defined]
    return wrapper


def _memoize(
    fn: Callable[..., Any],
    maxsize: int | None,
    witness: Callable[..., Hashable] | None,
) -> Callable[..., Any]:
    """Build the callable :func:`memoized` returns.

    A closure rather than a class instance: this runs on every call of
    functions hit millions of times per run, and local-variable access is
    what keeps a hit within a few times ``lru_cache``'s C implementation.
    """
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    positional = sum(
        1 for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    )
    simple = positional == len(params)
    # No ``*args``/``**kwargs``: every call binds to one value per parameter,
    # so the key is the value tuple in signature order. A call passing just
    # the positional parameters takes the keyword-only ones' defaults without
    # binding (``inspect.Signature.bind`` costs ~10 us).
    fixed = all(p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in params)
    kw_defaults: tuple[Any, ...] | None = tuple(
        p.default for p in params if p.kind == p.KEYWORD_ONLY
    )
    if any(p.default is p.empty for p in params if p.kind == p.KEYWORD_ONLY):
        kw_defaults = None
    lock = threading.Lock()
    entries: dict[Hashable, tuple[Any, Hashable]] = {}
    env_get = _ENV_DATA.get if _ENV_DATA is not None else None

    def key_of(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Hashable:
        # The request identity is the bound argument list in signature
        # order; the common all-positional call needs no binding at all.
        if not kwargs and len(args) == positional:
            if simple:
                return args
            if fixed and kw_defaults is not None:
                return args + kw_defaults
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        if fixed:  # the same shape the fast paths build
            return tuple(bound.arguments.values())
        return tuple(bound.arguments.items())

    def cache_clear() -> None:
        with lock:
            entries.clear()

    if witness is None and simple:
        return _memoize_with_lru(fn, maxsize, positional, key_of)

    stats = register_cache(
        f"{fn.__module__}.{fn.__qualname__}", "memoized", cache_clear
    )

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if (env_get(_ENV_KEY) if env_get is not None else True) and reference_mode():
            stats.bypasses += 1
            return fn(*args, **kwargs)
        key: Hashable = key_of(args, kwargs)
        seen = witness(*args, **kwargs) if witness is not None else None
        entry = entries.get(key, _MISSING)
        if entry is not _MISSING:
            if entry[1] == seen:
                # No recency update on a hit: the bound evicts in insertion
                # order. A lock plus a reorder per hit cost ~10x the hit.
                stats.hits += 1
                return entry[0]
            stats.stale += 1
        stats.misses += 1
        value = fn(*args, **kwargs)
        with lock:
            entries.pop(key, None)
            entries[key] = (value, seen)
            stats.stores += 1
            if maxsize is not None:
                while len(entries) > maxsize:
                    del entries[next(iter(entries))]
        return value

    wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]
    wrapper.cache_len = lambda: len(entries)  # type: ignore[attr-defined]
    wrapper.stats = stats  # type: ignore[attr-defined]
    return wrapper


def clear_memoized(fn: Callable[..., Any]) -> None:
    """Drop every entry of a :func:`memoized` function (typed access to
    ``cache_clear``, which the decorator's ``_F``-preserving signature hides)."""
    getattr(fn, "cache_clear")()  # noqa: B009 - typed access for mypy


@overload
def memoized(fn: _F, /) -> _F: ...
@overload
def memoized(
    *, maxsize: int | None = None, witness: Callable[..., Hashable] | None = None
) -> Callable[[_F], _F]: ...
def memoized(
    fn: Any = None,
    /,
    *,
    maxsize: int | None = None,
    witness: Callable[..., Hashable] | None = None,
) -> Any:
    """Memoize a function for the process lifetime (see the module docstring).

    *witness* is called with the same arguments on every call; a hit whose
    stored witness differs is recomputed. Required in spirit for any function
    whose answer depends on the filesystem or a subprocess.
    """

    def wrap(f: _F) -> _F:
        return cast("_F", _memoize(f, maxsize, witness))

    return wrap(fn) if fn is not None else wrap


class memoized_property(Generic[_T]):  # noqa: N801 - mirrors cached_property
    """``functools.cached_property`` that honours reference mode and counts."""

    def __init__(self, func: Callable[[Any], _T]) -> None:
        self.func = func
        self.attrname: str | None = None
        self.__doc__ = func.__doc__
        self.stats = register_cache(
            f"{func.__module__}.{func.__qualname__}", "memoized_property"
        )

    def __set_name__(self, owner: type, name: str) -> None:
        self.attrname = name
        # Stored under a private key, not the attribute's own name: an instance
        # ``__dict__`` entry named like a non-data descriptor shadows it, so
        # every later access would skip ``__get__`` -- no hit counted, and a
        # value stored before reference mode was switched on served after it.
        self._slot = f"_memoized_{name}"

    @overload
    def __get__(
        self, instance: None, owner: type | None = None
    ) -> memoized_property[_T]: ...
    @overload
    def __get__(self, instance: object, owner: type | None = None) -> _T: ...
    def __get__(self, instance: object | None, owner: type | None = None) -> Any:
        if instance is None:
            return self
        name = self.attrname
        assert name is not None
        if reference_mode():
            self.stats.bypasses += 1
            return self.func(instance)
        cache = instance.__dict__
        value = cache.get(self._slot, _MISSING)
        if value is not _MISSING:
            self.stats.hits += 1
            return value
        self.stats.misses += 1
        value = self.func(instance)
        cache[self._slot] = value
        self.stats.stores += 1
        return value
