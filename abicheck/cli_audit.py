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

from collections.abc import Sequence
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from .checker_types import Change, DiffResult


def _contract_tag(c: Change, contract_evaluation: bool) -> str:
    """A ``[contract: ...]`` suffix for an audit-ledger line, matching the
    tag rendered elsewhere (``reporter_markdown``/``cli_compare_fold``) --
    a no-op unless ``--contract`` was requested and this finding
    was already stamped (ADR-049 Phase 3; Codex review, fresh evidence: this
    stderr audit ledger was the one remaining per-finding contract-decision
    rendering site left unstamped after the JSON/Markdown fixes)."""
    if not contract_evaluation or c.contract_relevance is None:
        return ""
    tag = f" [contract: {c.contract_relevance.value}"
    if c.contract_reason_code:
        tag += f" ({c.contract_reason_code})"
    if c.contract_assurance is not None:
        tag += f", assurance: {c.contract_assurance.value}"
    return tag + "]"


def _ledger_line(c: Change, contract_evaluation: bool) -> str:
    """One ledger row: the finding, with its symbol demangled.

    Shared by both ledgers below because they had the same body twice and
    both were missed when demangling became a property of the output format
    rather than a token a user types -- the main report showed
    ``Readable [mangled]`` while these human-facing ledgers stayed raw
    (Codex review, PR #1284). ``demangle_text`` keeps the exact mangled
    spelling beside the readable name, so nothing a user might paste into
    ``nm`` or a suppression selector is lost.
    """
    from .demangle import demangle_text

    loc = f" [{c.source_location}]" if c.source_location else ""
    reason = f" ({c.surface_exclusion_reason})" if c.surface_exclusion_reason else ""
    tag = _contract_tag(c, contract_evaluation)
    return f"  - {c.kind.value}: {demangle_text(c.symbol)}{loc}{reason}{tag}"


def ledger_lines_for(
    changes: Sequence[Change], *, contract_evaluation: bool = False
) -> list[str]:
    """The ledger rows for *changes*, without printing them.

    The release fan-out needs the same rows this module echoes for a single
    comparison, but captured in a worker and printed later in library order
    (see `cli_compare_release_pairwise`), so the two cannot render the
    disposition differently.
    """
    return [_ledger_line(c, contract_evaluation) for c in changes]


def echo_filtered_surface(
    result: DiffResult, *, contract_evaluation: bool = False
) -> None:
    """Print the public-surface audit ledger (ADR-024 §D5 traceability)."""
    n = result.out_of_surface_count
    click.echo(
        f"\nFiltered as non-public ABI surface ({n} "
        f"{'finding' if n == 1 else 'findings'}, --scope-public-headers):",
        err=True,
    )
    for line in ledger_lines_for(
        result.out_of_surface_changes, contract_evaluation=contract_evaluation
    ):
        click.echo(line, err=True)


def echo_reconciled(result: DiffResult, *, contract_evaluation: bool = False) -> None:
    """Print the build-context reconciliation ledger (ADR-039 --show-filtered).

    Findings cleared as context-free header-parse artifacts are recorded here so
    the demotion is never silent: the verdict dropped them, but the user can see
    exactly what was removed and why."""
    n = result.reconciled_count
    click.echo(
        f"\nReconciled as context-free header-parse artifacts ({n} "
        f"{'finding' if n == 1 else 'findings'}, --reconcile-build-context):",
        err=True,
    )
    for c in result.reconciled_changes:
        click.echo(_ledger_line(c, contract_evaluation), err=True)


def render_pattern_modulations(result: DiffResult) -> str:
    """Render the pattern-aware modulation ledger (ADR-027 A4
    --explain-patterns) as text, without printing it.

    Split out of :func:`echo_pattern_modulations` (Codex review, fresh
    evidence: "Serialize pattern-ledger output after parallel comparison")
    so a caller that can't safely ``click.echo`` immediately -- a
    directory/package release fan-out's per-library worker thread, whose
    several independent echoes could otherwise interleave nondeterministically
    with a sibling library's -- can capture the text and print it later, in a
    deterministic order, instead of writing straight to stderr from inside
    the worker.
    """
    mods = result.pattern_modulations
    if not mods:
        return "\nNo pattern-aware modulations applied."
    lines = [f"\nPattern-aware modulations ({len(mods)}):"]
    for m in mods:
        sym = m.get("symbol", "?")
        rule = m.get("rule_id", "?")
        reason = m.get("reason", "")
        oc = m.get("original_category", "?")
        nc = m.get("new_category", "?")
        lines.append(f"  - {sym}: {oc} -> {nc} [{rule}: {reason}]")
        edges = m.get("edges_matched") or []
        if isinstance(edges, list):
            for e in edges:
                lines.append(f"      · {e}")
    return "\n".join(lines)


def echo_pattern_modulations(result: DiffResult) -> None:
    """Print the pattern-aware modulation ledger (ADR-027 A4 --explain-patterns)."""
    click.echo(render_pattern_modulations(result), err=True)
