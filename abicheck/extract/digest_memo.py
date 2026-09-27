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

"""A bounded, thread-safe memo for pure per-file passes keyed on content.

Header scans run the same pure pass over the same file several times per
comparison (old, new and combined header sets, two language-mode
polarities, every release member). Callers key a :class:`DigestMemo` on
:func:`content_digest` of the bytes -- never the bytes themselves -- so
the memo retains only results, and an edited file can never be served a
stale one.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from concurrent.futures import Future
from typing import Generic, TypeVar

_V = TypeVar("_V")


def content_digest(content: bytes) -> bytes:
    """A 64-byte BLAKE2b digest of *content*."""
    return hashlib.blake2b(content).digest()


class DigestMemo(Generic[_V]):
    """Least-recently-used memo of at most *max_entries* results.

    With *weigh*, it is also bounded to *max_bytes* of retained values: a
    memo of preprocessed header text must not keep a large header tree
    resident (19 MB per MKL release, several copies per file). A single
    value heavier than the whole budget is returned but never stored.

    Single-flight: concurrent misses on one key compute it once, and the
    other callers wait for that result. A release fan-out starts every
    member's header scan at the same moment, so without this each worker
    missed the still-empty memo and repeated the identical scan -- the memo
    only ever helped callers that arrived after the first one finished.
    """

    def __init__(
        self,
        max_entries: int = 4096,
        *,
        max_bytes: int | None = None,
        weigh: Callable[[_V], int] | None = None,
    ) -> None:
        self._max = max_entries
        self._max_bytes = max_bytes
        self._weigh = weigh
        self._bytes = 0
        self._entries: OrderedDict[Hashable, tuple[_V, int]] = OrderedDict()
        self._in_flight: dict[Hashable, Future[_V]] = {}
        self._lock = threading.Lock()

    def get_or_compute(self, key: Hashable, compute: Callable[[], _V]) -> _V:
        """The memoised value for *key*, computing (outside the lock) on a miss.

        A caller that misses while another is already computing *key* waits
        for that result instead of computing it again. If that computation
        raises, each waiter retries on its own, so an exception is only ever
        raised in a thread whose own ``compute`` raised it.
        """
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None:
                self._entries.move_to_end(key)
                return hit[0]
            pending = self._in_flight.get(key)
            if pending is None:
                future: Future[_V] = Future()
                self._in_flight[key] = future
        if pending is not None:
            try:
                return pending.result()
            except BaseException:
                return self.get_or_compute(key, compute)
        try:
            value = compute()
            self._store(key, value)
        except BaseException as exc:
            with self._lock:
                self._in_flight.pop(key, None)
            future.set_exception(exc)
            raise
        with self._lock:
            self._in_flight.pop(key, None)
        future.set_result(value)
        return value

    def _store(self, key: Hashable, value: _V) -> None:
        weight = self._weigh(value) if self._weigh is not None else 0
        if self._max_bytes is not None and weight > self._max_bytes:
            return
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= previous[1]
            self._entries[key] = (value, weight)
            self._bytes += weight
            while len(self._entries) > self._max or (
                self._max_bytes is not None and self._bytes > self._max_bytes
            ):
                _, (_, dropped) = self._entries.popitem(last=False)
                self._bytes -= dropped

    @property
    def retained_bytes(self) -> int:
        return self._bytes

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def __len__(self) -> int:
        return len(self._entries)
