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

"""Shared accounting for comparisons a detector declined (ADR-063 T9).

A detector returns ``list[Change]``, so a comparison it *declined* -- the
evidence it needed was incomplete or unsupported for one entity, so it
refused to judge rather than fabricate or silently pass -- used to leave no
trace: "no finding" and "not judged" were the same empty list. The
whole-detector case already had a route (``requires_support`` ->
``DetectorResult.not_evaluated``); this module is the per-entity one.

A detector (or a predicate it calls) records a decline with
:func:`record_declined`; ``DetectorRegistry.run_all`` opens
:func:`declined_scope` around each detector call and attaches what was
recorded to that detector's ``DetectorResult.declined``, which the JSON
report emits. Outside a scope (a direct unit-test call of a detector or
predicate) recording is a no-op, so a predicate stays callable anywhere.

Declines are deduplicated per ``(entity, reason)`` within one scope: a
predicate asked the same question twice about one pair is one decline.
Accounting only -- it never changes a finding, a verdict or an exit code.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

__all__ = ["DeclinedComparison", "declined_scope", "record_declined"]


@dataclass(frozen=True)
class DeclinedComparison:
    """One entity a detector declined to judge, and the evidence reason."""

    entity: str
    reason: str


_ACTIVE: ContextVar[dict[tuple[str, str], DeclinedComparison] | None] = ContextVar(
    "abicheck_declined_comparisons", default=None
)


@contextmanager
def declined_scope() -> Iterator[list[DeclinedComparison]]:
    """Collect declines recorded while the block runs.

    The yielded list is filled when the block exits, in first-recorded
    order. Scopes nest: an inner scope collects its own declines only.
    """
    collected: dict[tuple[str, str], DeclinedComparison] = {}
    token = _ACTIVE.set(collected)
    out: list[DeclinedComparison] = []
    try:
        yield out
    finally:
        _ACTIVE.reset(token)
        out.extend(collected.values())


def record_declined(entity: str, reason: str) -> None:
    """Record that *entity*'s comparison was declined for *reason*."""
    collected = _ACTIVE.get()
    if collected is None:
        return
    collected.setdefault((entity, reason), DeclinedComparison(entity, reason))
