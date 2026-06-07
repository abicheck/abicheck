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

"""CLI audit-ledger printers.

Small stderr printers for the disclosure ledgers abicheck keeps so a demotion
is always auditable: the ADR-024 public-surface ledger and the ADR-027
pattern-aware modulation ledger. Split out of :mod:`abicheck.cli` to keep that
module under the AI-readiness file-size cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from .checker_types import DiffResult


def echo_filtered_surface(result: DiffResult) -> None:
    """Print the public-surface audit ledger (ADR-024 §D5 traceability)."""
    n = result.out_of_surface_count
    click.echo(
        f"\nFiltered as non-public ABI surface ({n} "
        f"{'finding' if n == 1 else 'findings'}, --scope-public-headers):",
        err=True,
    )
    for c in result.out_of_surface_changes:
        loc = f" [{c.source_location}]" if c.source_location else ""
        reason = (
            f" ({c.surface_exclusion_reason})" if c.surface_exclusion_reason else ""
        )
        click.echo(f"  - {c.kind.value}: {c.symbol}{loc}{reason}", err=True)


def echo_pattern_modulations(result: DiffResult) -> None:
    """Print the pattern-aware modulation ledger (ADR-027 A4 --explain-patterns)."""
    mods = result.pattern_modulations
    if not mods:
        click.echo("\nNo pattern-aware modulations applied.", err=True)
        return
    click.echo(
        f"\nPattern-aware modulations ({len(mods)}, --pattern-verdicts):",
        err=True,
    )
    for m in mods:
        sym = m.get("symbol", "?")
        rule = m.get("rule_id", "?")
        reason = m.get("reason", "")
        oc = m.get("original_category", "?")
        nc = m.get("new_category", "?")
        click.echo(f"  - {sym}: {oc} -> {nc} [{rule}: {reason}]", err=True)
        edges = m.get("edges_matched") or []
        if isinstance(edges, list):
            for e in edges:
                click.echo(f"      · {e}", err=True)
