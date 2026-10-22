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

"""Click rendering of ``compare``'s input-validation refusals.

The decisions live in :mod:`abicheck.workflows.compare.budget` (``--budget``)
and :mod:`abicheck.workflows.operand_rules` (operand combinations); this
module only turns those typed refusals into the usage errors ``compare``
prints, and enters the ``--budget`` deadline for the Click invocation.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import click

from ...cli_resolve import classify_compare_operand
from ...workflows.compare.budget import (
    BudgetError,
    budget_deadline,
    parse_budget_seconds,
)
from ...workflows.operand_rules import DebugFormatNotElfError, ManifestOperandError
from .operand_diagnostics import _reject_application_operand


def parse_budget_option(value: str | None) -> float | None:
    """Parse ``--budget``, rendering a refusal as a Click ``BadParameter``."""
    try:
        return parse_budget_seconds(value)
    except BudgetError as exc:
        raise click.BadParameter(str(exc)) from exc


def enter_budget_scope(ctx: click.Context, budget_s: float | None) -> None:
    """Set ``--budget``'s deadline for the rest of this Click invocation.

    Entered once, as early as possible -- inline ``--sources``/
    ``--build-info`` embedding included (Codex review: entering only around
    resolution left it unbounded). ``ctx.call_on_close`` (not a ``with``
    wrapping the rest of this function) releases it on every early exit
    Click already tears the context down for, so it never leaks into the
    next ``CliRunner`` invocation in the same test process. A no-op for
    ``budget_s is None`` (``--budget`` omitted) -- unbounded, as today.
    """
    if budget_s is None:
        return
    scope = budget_deadline(budget_s)
    scope.__enter__()
    ctx.call_on_close(lambda: scope.__exit__(None, None, None))


def classify_and_reject_operands(old_input: Path, new_input: Path) -> tuple[str, str]:
    """Classify both compare operands and reject an application/PIE operand.

    ADR-037 D7 input-type dispatch: a directory/package operand fans out to a
    per-library comparison; an application/PIE operand is not a library `compare`
    can pair (hint at `appcompat`). A single .so / snapshot / dump falls through.
    """
    old_kind = classify_compare_operand(old_input)
    new_kind = classify_compare_operand(new_input)
    if old_kind == "app" or new_kind == "app":
        _reject_application_operand(old_input, new_input, old_kind, new_kind)
    return old_kind, new_kind


@contextmanager
def operand_rule_errors() -> Iterator[None]:
    """Word a refused operand combination the way ``compare`` words its flags.

    The rules are decided in :mod:`abicheck.workflows.operand_rules`; this only
    renders the typed refusal as the usage error (or ``.abicheck.yml`` key
    message) this command has always printed.
    """
    try:
        yield
    except DebugFormatNotElfError as exc:
        raise click.BadParameter(
            f"debug.format: {exc.debug_format} (.abicheck.yml) is only supported "
            f"for ELF binaries, but the {exc.side} input is {exc.binary_fmt.upper()}."
        ) from exc
    except ManifestOperandError as exc:
        raise click.UsageError(str(exc)) from exc
