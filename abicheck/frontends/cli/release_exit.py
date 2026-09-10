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

``_exit_compare_release`` is where every axis a release can be decided on --
the verdict/severity code, a removed required library, ADR-064's
evidence-contract axis, ADR-049's contract-coverage floor, ADR-065's two
completeness floors, ADR-050's ``not_comparable`` -- meets one precedence
order and one ``sys.exit``. Its numeric output is required to equal
``policy.exit_decision_precedence.resolve_release_exit_decision_for_report``'s
``.code`` for the same run (that function's own docstring states the
invariant), which is why every axis has to be folded *here* rather than
short-circuited by whichever caller computed it.

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

__all__ = ["_exit_compare_release"]


def _exit_compare_release(
    worst_verdict: str,
    fail_on_removed: bool,
    removed_keys: list[str],
    severity_exit_code: int | None = None,
    *,
    contract_coverage_exit_contribution: int = 0,
    evidence_contract_error_contribution: int = 0,
    incomplete_scope_exit_contribution: int = 0,
    no_comparison_completed_exit_contribution: int = 0,
) -> None:
    """Exit compare-release with ABI-compatible status code mapping.

    *incomplete_scope_exit_contribution*/*no_comparison_completed_exit_
    contribution* (ADR-065 D6/D7) are two more ``0``/``1`` orthogonal
    floors with exactly the coverage axis's rank in both schemes, so they
    are folded into one floor with it below and then treated identically.
    *removed_keys* is the **proven** removal set since S2 (D2), never the
    raw ``unmatched_old`` set difference.

    When *severity_exit_code* is not None, the severity-aware scheme is in
    effect: that code replaces the verdict-based 2/4 mapping, except that
    (a) a removed library still exits 8 in preference to the severity code, and
    (b) an operational ERROR verdict (a library failed to dump/extract/compare)
    still floors the exit at 4 — such failures produce no ``DiffResult.changes``
    so the severity aggregation cannot see them, and must never be downgraded.
    When None, the legacy verdict-based mapping is unchanged.

    ``worst_verdict == "not_comparable"`` (ADR-050 D2) is checked first, in
    both schemes, ahead of even ``--fail-on-removed-library``'s exit 8: a
    not_comparable result means the comparison couldn't establish what
    changed at all, so an apparent "library removed" reading from an
    incomparable pair is an unproven inference, not a real removal finding
    entitled to its own exit code. Exits 16 — identical to native
    ``compare``'s own not_comparable code, since it fires before severity
    classification or the removed-library check ever run.

    *contract_coverage_exit_contribution* is ADR-049 Phase 7's orthogonal
    axis (release/package parity, CLI-audit P1), already aggregated with
    max() across every library by the caller. Folded in with max() at every
    exit point below (mirroring ``contract_coverage_exit.fold_coverage_exit``
    for a single-pair ``compare``) except ``not_comparable``, which fires
    before any library was even scored: it can raise a clean 0 to 1, never
    lower a real 2/4/8, and is `0` (a no-op fold) for every run that never
    passed ``--contract``.
    """
    contract_coverage_exit_contribution = max(
        contract_coverage_exit_contribution,
        incomplete_scope_exit_contribution,
        no_comparison_completed_exit_contribution,
    )
    if worst_verdict == "not_comparable":
        sys.exit(16)
    # ADR-064's exit-7 axis, ranked directly below 16 -- same rank the
    # persisted `exit` block applies (`resolve_release_exit_decision`), which
    # this function's numeric output is required to agree with.
    if evidence_contract_error_contribution:
        sys.exit(evidence_contract_error_contribution)
    if severity_exit_code is not None:
        # Severity-aware scheme: removed-library 8 takes precedence over the
        # severity code, otherwise emit the aggregated severity exit code.
        if fail_on_removed and removed_keys:
            sys.exit(8)
        code = severity_exit_code
        if worst_verdict == "ERROR":
            code = max(code, 4)
        code = max(code, contract_coverage_exit_contribution)
        if code != 0:
            sys.exit(code)
        return
    # ERROR is a compare-release-specific operational-failure sentinel (not a
    # Verdict); it floors at 4. Otherwise the verdict→code mapping is the shared
    # canonical one, so compare and compare-release never disagree (C7).
    if worst_verdict == "ERROR":
        sys.exit(max(4, contract_coverage_exit_contribution))
    from ...checker_policy import Verdict
    from ...workflows.gate import legacy_exit_code

    code = (
        legacy_exit_code(Verdict[worst_verdict])
        if worst_verdict in Verdict.__members__
        else 0
    )
    if code != 0:
        # A real verdict-based break always wins outright; folding coverage
        # in here is a no-op in practice (its own floor is 0/1, never above
        # a real 2/4) but keeps the "never lowers a real code" invariant
        # explicit rather than implicit in max()'s commutativity.
        sys.exit(max(code, contract_coverage_exit_contribution))
    if fail_on_removed and removed_keys:
        # A removed library stays its own, separately-aggregated signal
        # (AGENTS.md: "не смешивая его с entity contract relevance") --
        # it is checked ahead of the coverage-only fallback below, mirroring
        # the severity-scheme branch above.
        sys.exit(8)
    if contract_coverage_exit_contribution != 0:
        sys.exit(contract_coverage_exit_contribution)
