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

"""``--budget`` grammar, its deadline scope, and its Click rendering."""

from __future__ import annotations

import click
import pytest

from abicheck.deadline import current_deadline_ts
from abicheck.frontends.cli.compare_input_validation import (
    enter_budget_scope,
    parse_budget_option,
)
from abicheck.workflows.compare.budget import (
    BudgetError,
    budget_deadline,
    parse_budget_seconds,
)


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("900", 900.0),
        ("900s", 900.0),
        ("15m", 900.0),
        ("1h", 3600.0),
        ("  2M  ", 120.0),
        ("0.5h", 1800.0),
        ("0", 0.0),
    ],
)
def test_parse_budget_seconds_accepts_each_unit(value: str, seconds: float) -> None:
    assert parse_budget_seconds(value) == pytest.approx(seconds)


@pytest.mark.parametrize("value", [None, ""])
def test_parse_budget_seconds_absent_means_no_budget(value: str | None) -> None:
    assert parse_budget_seconds(value) is None


@pytest.mark.parametrize("value", ["abc", "15x", "m", "1.2.3h"])
def test_parse_budget_seconds_rejects_unparseable(value: str) -> None:
    with pytest.raises(BudgetError, match="invalid --budget"):
        parse_budget_seconds(value)


# "1e308h" is finite as written, but scaling it by 3600 overflows to infinity.
@pytest.mark.parametrize("value", ["nan", "inf", "-5m", "-1", "1e308h"])
def test_parse_budget_seconds_rejects_non_finite_or_negative(value: str) -> None:
    # nan/inf would store a deadline that never trips; a negative one trips at once.
    with pytest.raises(BudgetError, match="finite, non-negative"):
        parse_budget_seconds(value)


def test_budget_deadline_is_unbounded_without_a_budget() -> None:
    with budget_deadline(None):
        assert current_deadline_ts() is None


def test_budget_deadline_bounds_the_block_and_releases_it() -> None:
    assert current_deadline_ts() is None
    with budget_deadline(60.0):
        assert current_deadline_ts() is not None
    assert current_deadline_ts() is None


def test_parse_budget_option_renders_refusal_as_bad_parameter() -> None:
    with pytest.raises(click.BadParameter) as excinfo:
        parse_budget_option("-5m")
    assert "--budget must be a finite, non-negative duration" in str(excinfo.value)


def test_parse_budget_option_passes_valid_value_through() -> None:
    assert parse_budget_option("15m") == pytest.approx(900.0)


def test_enter_budget_scope_is_a_no_op_without_a_budget() -> None:
    ctx = click.Context(click.Command("compare"))
    enter_budget_scope(ctx, None)
    assert current_deadline_ts() is None
    ctx.close()


def test_enter_budget_scope_releases_on_context_close() -> None:
    ctx = click.Context(click.Command("compare"))
    enter_budget_scope(ctx, 60.0)
    assert current_deadline_ts() is not None
    ctx.close()
    assert current_deadline_ts() is None
