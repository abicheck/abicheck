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
from typing import Generic, TypeVar

_V = TypeVar("_V")


def content_digest(content: bytes) -> bytes:
    """A 64-byte BLAKE2b digest of *content*."""
    return hashlib.blake2b(content).digest()


class DigestMemo(Generic[_V]):
    """Least-recently-used memo of at most *max_entries* results."""

    def __init__(self, max_entries: int = 4096) -> None:
        self._max = max_entries
        self._entries: OrderedDict[Hashable, _V] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_compute(self, key: Hashable, compute: Callable[[], _V]) -> _V:
        """The memoised value for *key*, computing (outside the lock) on a miss."""
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                return self._entries[key]
        value = compute()
        with self._lock:
            self._entries[key] = value
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
        return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
