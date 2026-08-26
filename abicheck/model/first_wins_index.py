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

"""A first-wins keyed index and the duplicate ledger building one produces.

``AbiSnapshot.index`` builds three of these — functions by mangled name,
variables by mangled name, types by name — and each previously carried its own
copy of the same loop. The rule they share is one primitive: the *first*
declaration to claim a key keeps it, and every later claimant is counted so the
caller can report what it dropped rather than silently collapsing it.

The primitive is deliberately independent of any snapshot: it takes an iterable
and a key function, so ``tests/test_first_wins_index.py`` can state its contract
as invariants over arbitrary input rather than only through the one caller.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")
K = TypeVar("K")


@dataclass(frozen=True)
class FirstWinsIndex(Generic[K, T]):
    """The mapping a first-wins scan produced, plus what it had to drop.

    ``mapping`` holds one entry per distinct key, in first-appearance order,
    bound to the first item that claimed it. ``dropped`` counts *additional*
    claimants per key — a key claimed once is absent from it entirely, so
    ``if result.dropped`` is exactly "were there duplicates".
    """

    mapping: dict[K, T]
    dropped: dict[K, int]


def build_first_wins_index(
    items: Iterable[T], key: Callable[[T], K]
) -> FirstWinsIndex[K, T]:
    """Index ``items`` by ``key``, keeping the first item to claim each key."""
    mapping: dict[K, T] = {}
    dropped: dict[K, int] = {}
    for item in items:
        item_key = key(item)
        if item_key in mapping:
            dropped[item_key] = dropped.get(item_key, 0) + 1
        else:
            mapping[item_key] = item
    return FirstWinsIndex(mapping=mapping, dropped=dropped)


def describe_dropped(dropped: Mapping[K, int]) -> str:
    """Render a ``dropped`` ledger as ``"name (\u00d7N), other (\u00d7M)"``.

    ``N`` is the total number of declarations sharing the key, not the number
    dropped, so the message reads as "this key appeared N times" — the wording
    the duplicate-symbol warning has always used.
    """
    return ", ".join(f"{key} (\u00d7{count + 1})" for key, count in dropped.items())
