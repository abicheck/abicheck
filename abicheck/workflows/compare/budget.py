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
from contextlib import AbstractContextManager

from ...deadline import deadline_scope
from ...errors import ValidationError

#: ADR-068 §3 #19: ``time``-style unit suffixes. A bare number is seconds.
DURATION_UNITS: dict[str, int] = {"s": 1, "m": 60, "h": 3600}


class BudgetError(ValidationError):
    """A ``--budget`` value that is not a finite, non-negative duration."""


def parse_budget_seconds(value: str | None) -> float | None:
    """Parse a ``time``-style duration (``15m``/``900s``/``1h``) to seconds.

    ``None``/empty means no budget. Raises :class:`BudgetError` for an
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


def budget_deadline(budget_s: float | None) -> AbstractContextManager[None]:
    """The wall-clock scope a parsed ``--budget`` bounds, entered by the caller.

    ``None`` (``--budget`` omitted) is an unbounded scope, as before.
    """
    return deadline_scope(budget_s)
