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

"""A memoized function's counters count calls, whichever store backs it.

``memoized`` stores a witness-free, plain-signature function in
``functools.lru_cache`` and everything else in a dict. Clearing the
``lru_cache`` also zeroes its own counters, which ``_LruStats`` reads as
deltas from a baseline, so after a stats reset and a clear the counters read
negative: ``test_signature_normalization``'s memo check failed with
``hits=-1324`` whenever an earlier test in the same worker had zeroed stats.

The oracle is a model that counts each call as a hit or a miss by whether its
key was stored since the last clear, and zeroes only on a reset. Every
sequence of up to four operations is checked against it on both stores, and
through both ways of clearing (the function's own, and the registry's).
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import Any

import pytest

from abicheck.model import execution_cache
from abicheck.model.execution_cache import clear_memoized, memoized

_OPS = ("call:a", "call:b", "reset", "clear", "registry-clear")


def _make(store: str, tag: str) -> Callable[[str], str]:
    def body(key: str) -> str:
        return key.upper()

    body.__qualname__ = f"memo_stats_{store}_{tag}"
    if store == "lru":
        return memoized(body)
    return memoized(witness=lambda key: 0)(body)


def _apply(fn: Any, op: str) -> None:
    if op.startswith("call:"):
        assert fn(op[5:]) == op[5:].upper()
    elif op == "reset":
        fn.stats.hits = fn.stats.misses = 0
    elif op == "clear":
        clear_memoized(fn)
    else:
        execution_cache._REGISTRY[fn.stats.name].clear()


def _model(ops: tuple[str, ...]) -> tuple[int, int]:
    stored: set[str] = set()
    hits = misses = 0
    for op in ops:
        if op.startswith("call:"):
            key = op[5:]
            if key in stored:
                hits += 1
            else:
                misses += 1
                stored.add(key)
        elif op == "reset":
            hits = misses = 0
        else:
            stored.clear()
    return hits, misses


_SEQUENCES = [seq for n in range(1, 5) for seq in itertools.product(_OPS, repeat=n)]


@pytest.mark.parametrize("store", ["lru", "dict"])
def test_counters_follow_the_calls_through_any_clear(store: str) -> None:
    for i, ops in enumerate(_SEQUENCES):
        fn: Any = _make(store, str(i))
        for op in ops:
            _apply(fn, op)
        got = (fn.stats.hits, fn.stats.misses)
        assert got == _model(ops), (store, ops, got)
