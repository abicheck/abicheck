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

"""The one place a directory/package ``compare`` exits.

``_exit_compare_release`` is where a directory/package ``compare`` turns
one already-resolved release decision into a process status. The precedence
over the axes a release can be decided on lives in
``policy.exit_decision_precedence.resolve_release_exit_decision_for_report``
-- the same function the persisted ``exit`` block is built from -- so the
process status and the report cannot disagree *by construction* rather than
by an asserted invariant over two implementations (Codex review, P1).

Moved out of :mod:`abicheck.cli_compare_release_helpers` (PR #1195), which
sat at its `architecture/debt.yaml` ``no_growth`` baseline with no room for
a new axis. That is the sanctioned remedy -- move responsibility out, never
trim the file to fit -- and this function is a responsibility in its own
right, not a helper: it is the release's exit contract, and
`tests/test_exit_code_integrity.py` gates it directly.
:mod:`abicheck.cli_compare_release_helpers` and
:mod:`abicheck.cli_compare_release` both re-export the name, so every
existing importer (including tests that import it from either) is
unaffected.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ...report.release_assurance import release_assurance_notice
from .release_evidence_contract import evidence_contract_notice

if TYPE_CHECKING:
    # Via `report`, which re-exports it: `frontends -> policy` is forbidden
    # even for a type-only import (`architecture/modules.yaml`).
    from ...report.release_assurance import ReleaseAssuranceDecision

__all__ = ["_exit_compare_release"]


def _exit_compare_release(
    worst_verdict: str,
    fail_on_removed: bool,
    removed_keys: list[str],
    severity_exit_code: int | None = None,
    *,
    contract_coverage_exit_contribution: int = 0,
    library_results: list[dict[str, object]] | None = None,
    release_global_verdict: str = "NO_CHANGE",
    incomplete_scope_exit_contribution: int = 0,
    no_comparison_completed_exit_contribution: int = 0,
    assurance_decision: ReleaseAssuranceDecision | None = None,
) -> None:
    """Exit a directory/package ``compare`` with the release's own status code.

    Resolves nothing itself. Every axis a release can be decided on -- the
    verdict/severity code, a proven removed required library, ADR-064's
    evidence-contract axis, ADR-049's contract-coverage floor, ADR-065's two
    completeness floors, ADR-050's ``not_comparable`` -- and the precedence
    order over them belong to
    :func:`~abicheck.policy.exit_decision_precedence.resolve_release_exit_
    decision_for_report`, which is also what the persisted ``exit`` block is
    built from. This function reads that one decision and translates it to a
    process status, which is all a frontend owes (`abicheck/frontends/
    AGENTS.md`).

    It used to reimplement that precedence as a parallel ladder of
    ``sys.exit`` calls, with the report resolver's docstring asserting the
    two agreed as an *invariant* rather than by construction (Codex review,
    P1). A reachable-state parity harness confirmed they did agree across
    2240 states -- so this is not a bug fix but the removal of the
    conditions for one: PR #1195 added a fifth axis and had to add it twice,
    and the second copy is exactly what let this branch's own first attempt
    rank a proven removal below the evidence axis in one implementation
    only.

    *library_results* is the per-member list the resolver reads the
    evidence-contract and operational-error axes off. ``compare
    --bundle-facts`` has no such list (its whole release is one folded
    result) and passes none; its ``"ERROR"`` sentinel reaches the resolver
    through *worst_verdict* instead, which is why that resolver takes the
    union of both signals rather than scanning the list alone.

    Exits only for a nonzero code, so a clean release returns and lets its
    caller finish normally.
    """
    # Via `workflows.gate`, not `policy` directly: `frontends -> policy` is
    # forbidden (`architecture/modules.yaml`), and that rule is precisely why
    # this function once carried its own copy of the precedence instead of
    # reading the canonical decision. `workflows` is the sanctioned seam and
    # already re-exports both names for exactly this purpose.
    from ...workflows.gate import (
        release_evidence_contract_contribution,
        resolve_release_exit_decision_for_report,
    )

    members = list(library_results or [])
    decision = resolve_release_exit_decision_for_report(
        worst_verdict,
        fail_on_removed,
        removed_keys,
        severity_exit_code,
        contract_coverage_exit_contribution,
        members,
        release_global_verdict,
        incomplete_scope_contribution=incomplete_scope_exit_contribution,
        no_comparison_completed_contribution=no_comparison_completed_exit_contribution,
        analysis_assurance_contribution=(
            assurance_decision.exit_contribution if assurance_decision else 0
        ),
    )
    # ADR-070's assurance notice, emitted here for the same reason the
    # evidence-contract one below is (see that comment) -- and *formatted*
    # here rather than by each caller, which is the part that matters: its
    # wording turns on the compatibility axis's own exit code ("floored to 1"
    # vs. "below the compatibility axis's own exit 4, which stands"), and the
    # resolved decision just above is the only place that number is actually
    # known. A caller passing its own guess got this wrong: `compare
    # --bundle-facts` has no severity code to guess from and would have
    # claimed a floor beside a real break.
    if assurance_decision is not None:
        assurance_notice = release_assurance_notice(
            assurance_decision, base_exit=decision.compatibility_contribution
        )
        if assurance_notice:
            click.echo(assurance_notice, err=True)
    # Emitted here rather than by each caller: a release document is
    # rendered before the exit is taken, and the fan-out has already
    # discarded every member's `DiffResult` by then, so nothing downstream
    # can explain a bare exit 7. Attaching it to the exit itself is also
    # what stops a *second* caller from forgetting it -- `compare
    # --bundle-facts` reaches this function without ever having emitted
    # one (Codex P1 follow-through).
    notice = evidence_contract_notice(
        members, release_evidence_contract_contribution(members)
    )
    if notice:
        click.echo(notice, err=True)
    if decision.code != 0:
        sys.exit(decision.code)
