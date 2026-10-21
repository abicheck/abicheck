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

"""Memo tables for the snapshot decoder's per-row hot loops.

A large snapshot carries hundreds of thousands of serialized ``Fact`` dicts
and enum strings, nearly all drawn from a handful of distinct shapes
(``{"status": "present", "value": false}``, ``"public"``...). Decoding each
one from scratch -- an ``Enum.__call__`` per string, a ``FactStatus`` lookup
plus ``Fact._make`` per fact -- was ~25% of a cache-entry load. These tables
answer a repeat shape with the object the full decode already produced for
it, so the result is the same value (and, for shared facts, the same
instance) the slow path returns; anything outside the memoizable shape, and
every first occurrence, still takes the slow path, so validation and error
behaviour on malformed input are unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any, TypeVar

from ..model import Fact
from ..model.execution_cache import MemoryCache

__all__ = ["enum_decoder", "memo_fact"]

_E = TypeVar("_E", bound=Enum)

#: Bound on the fact memo. Distinct shapes are few (a 100 MB snapshot holds
#: ~280k serialized facts in ~13k shapes, most of them one-off strings), so
#: this only caps memory on a pathological document.
#: An LRU bound, so a long-lived process decoding many documents keeps the
#: recently-seen shapes and never grows past it; registered with
#: ``execution_cache`` so reference mode bypasses it and stats report it.
_FACT_MEMO_LIMIT = 16384
_FACT_MEMO: MemoryCache[Fact[Any]] = MemoryCache(
    "storage.decode_memo.fact", max_entries=_FACT_MEMO_LIMIT, field="fact_shape"
)
_FACT_MEMO_READ = _FACT_MEMO.reader()
_FACT_KEYS = frozenset({"status", "value", "diagnostics", "producer"})
#: Immutable scalars only: a list/dict value is per-fact mutable state the
#: decode hands over as-is, so it is never shared; a float is excluded
#: because ``-0.0 == 0.0`` would merge two distinct values.
_SCALAR_TYPES = frozenset({type(None), bool, int, str})


def memo_fact(
    raw: Any, build: Callable[[Any], Fact[Any]], namespace: tuple[Any, ...] = ()
) -> Fact[Any]:
    """``build(raw)``, served from a memo when *raw* is a plain fact dict
    (``status``/``value``/``diagnostics``/``producer`` only) whose value is a
    JSON scalar and whose diagnostics are strings. ``Fact`` is frozen, so a
    shared instance is indistinguishable from a fresh equal one.

    The key is everything *build* can observe: the key set (so a dict
    missing a required key is never answered by a complete one), each
    value with its type (``True == 1``), and *namespace* -- whatever else
    the caller's *build* closes over (one memo entry per distinct *build*
    behaviour). Only a successful build is memoized, so a malformed
    document still raises from *build* itself."""
    if type(raw) is dict and _FACT_KEYS.issuperset(raw):
        value = raw.get("value")
        status = raw.get("status")
        producer = raw.get("producer")
        diagnostics = raw.get("diagnostics")
        value_type = type(value)
        if (
            value_type in _SCALAR_TYPES
            and type(status) is str
            and (producer is None or type(producer) is str)
            and (
                not diagnostics
                or (
                    type(diagnostics) is list
                    and all(type(item) is str for item in diagnostics)
                )
            )
        ):
            key = (
                namespace,
                tuple(raw),
                status,
                value_type,
                value,
                tuple(diagnostics) if diagnostics else (),
                producer,
            )
            fact: Fact[Any] | None = _FACT_MEMO_READ(key)
            if fact is None:
                fact = build(raw)
                _FACT_MEMO.put(key, fact)
            return fact
    return build(raw)


def enum_decoder(enum_cls: type[_E]) -> Callable[[Any], _E]:
    """A drop-in for ``enum_cls(raw)`` that resolves a member's exact value
    by one dict lookup. Anything else (an alias, a ``_missing_`` hook, a
    malformed or unhashable value) falls through to ``enum_cls(raw)`` itself,
    so it resolves or raises exactly as before."""
    table: dict[Any, _E] = {
        member.value: member for member in enum_cls if type(member.value) is str
    }

    def decode(raw: Any) -> _E:
        if type(raw) is str:
            member = table.get(raw)
            if member is not None:
                return member
        return enum_cls(raw)

    return decode
