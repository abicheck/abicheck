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

"""``--budget`` duration parsing for ``compare`` (Click-free).

The CLI owns the flag and its usage-error rendering
(:mod:`abicheck.frontends.cli.compare_input_validation`); the grammar and the
finiteness rule live here so every front end parses a budget the same way.
"""

from __future__ import annotations

import math
from contextlib import AbstractContextManager, nullcontext

from ...deadline import current_deadline_ts, deadline_scope
from ...errors import ValidationError
from ..run_deadline import DEFAULT_BUDGET_S, DEFAULT_BUDGET_TEXT

#: ADR-068 §3 #19: ``time``-style unit suffixes. A bare number is seconds.
DURATION_UNITS: dict[str, int] = {"s": 1, "m": 60, "h": 3600}


class BudgetError(ValidationError):
    """A ``--budget`` value that is not a finite, non-negative duration."""


def parse_budget_seconds(value: str | None) -> float | None:
    """Parse a ``time``-style duration (``15m``/``900s``/``1h``) to seconds.

    ``None``/empty means none was stated (the caller applies the default). Raises :class:`BudgetError` for an
    unparseable, non-finite, or negative value.
    """
    if not value:
        return None
    raw = value.strip().lower()
    unit = 1
    if raw and raw[-1] in DURATION_UNITS:
        unit = DURATION_UNITS[raw[-1]]
        raw = raw[:-1]
    try:
        amount = float(raw)
    except ValueError as exc:
        raise BudgetError(
            f"invalid --budget {value!r}; use e.g. 15m, 900s, 1h"
        ) from exc
    # CodeRabbit review: nan/inf both pass `< 0` and would store a deadline
    # `left <= 0` never trips, silently defeating the budget.
    if not math.isfinite(amount) or amount < 0:
        raise BudgetError(
            f"--budget must be a finite, non-negative duration, got {value!r}"
        )
    # A finite amount can still overflow once scaled (``1e308h``); an infinite
    # budget never trips, so it is refused as well.
    seconds = amount * unit
    if not math.isfinite(seconds):
        raise BudgetError(
            f"--budget must be a finite, non-negative duration, got {value!r}"
        )
    return seconds


__all__ = [
    "DEFAULT_BUDGET_S",
    "DEFAULT_BUDGET_TEXT",
    "DURATION_UNITS",
    "BudgetError",
    "budget_deadline",
    "budget_overflow_hint",
    "budget_overflow_message",
    "parse_budget_seconds",
]


def budget_deadline(budget_s: float | None) -> AbstractContextManager[None]:
    """The wall-clock scope a compare run is bounded by, entered by the caller.

    An explicit *budget_s* is its own scope. ``None`` (no budget stated)
    keeps a deadline already active -- a release member's comparison runs
    under its release's budget, not a fresh one -- and otherwise is
    :data:`~abicheck.workflows.run_deadline.DEFAULT_BUDGET_S` (2 hours; ADR-068's
    2026-11 amendment). A compare run is never unbounded.
    """
    if budget_s is not None:
        return deadline_scope(budget_s)
    if current_deadline_ts() is not None:
        return nullcontext()
    return deadline_scope(DEFAULT_BUDGET_S)


def budget_overflow_hint(*, stated: bool) -> str:
    """The actionable half of a budget-overflow message, for every surface.

    Says whether a stated budget or the default ran out, and how to set a
    longer one: ``--budget`` on the CLI, the Action's ``budget`` input, or
    ``CompareRequest.budget_s`` in the typed API.
    """
    how = (
        "`--budget 4h` on the CLI, the Action's `budget` input, or "
        "CompareRequest.budget_s (seconds) in the typed API"
    )
    if not stated:
        return (
            f"no budget was set, so the default {DEFAULT_BUDGET_TEXT} applied; "
            f"set a longer one with {how}"
        )
    return f"raise it with {how}, or pin a shallower --depth"


def budget_overflow_message(budget: str | None, label: str, exc: Exception) -> str:
    """The CLI's one-line budget-overflow error for *label*, *budget* being
    ``--budget`` as typed (``None``: the default applied)."""
    stated = (
        f"--budget {budget!r}"
        if budget
        else f"the default {DEFAULT_BUDGET_TEXT} budget"
    )
    return (
        f"Error: {stated} exceeded while comparing {label}: {exc}. No verdict "
        f"was produced; {budget_overflow_hint(stated=bool(budget))}."
    )
