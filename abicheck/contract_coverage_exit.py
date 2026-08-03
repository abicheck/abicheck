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

"""ADR-049 Phase 7: the contract-coverage exit, finally applied.

``contract_coverage_ledger`` has computed a ``0``/``1`` contribution since
Phase 5 and every consumer *reported* it -- deliberately, so a user could
see what the flip would do before it did it. Nothing turned it into a real
exit code. This module is that step, and only that step: it reads the
already-derived ledger and answers what the invocation's exit floor is.

**Orthogonal, per Section 7's exit aggregation.** The plan is explicit that
no new global integer ordering is introduced: the coverage axis contributes
``0`` or ``1`` *independently* of the compatibility verdict and of the
configured gate. Folding is therefore :func:`max`, which has exactly the two
properties the axis needs -- a coverage failure raises a clean ``0`` to
``1``, and it can never lower a gate's ``2``/``4`` to ``1``, i.e. it never
converts a real ABI break into "warnings only". It equally never *rewrites*
a finding's null compatibility decision or its zero gate contribution
(Section 6.1); this is a floor on the process exit status, nothing more.

**``contract.unresolved`` is what makes the axis configurable**, and this is
its first engine consumer -- the reason the field sat in
``pack_application.UNAPPLIED_PACK_FIELDS`` until now. Section 6.2 fixes its
meaning precisely: ``warn`` "changes only the orthogonal contract-coverage
contribution, not ``GateDecision``, evidence, or labels". So ``warn`` zeroes
the floor computed here and changes nothing else anywhere -- the failures
stay in the ledger, stay in every report, and stay unsuppressible. It is an
acceptance of incomplete assurance, not a way to hide it.

A leaf: it imports the ledger and nothing else of abicheck's, so the CLI can
call it without acquiring a dependency the import-cycle gate would flag.
"""

from __future__ import annotations

from typing import Any

from .contract_coverage_ledger import (
    coverage_exit_contribution,
    coverage_failures_for_context,
)

#: ``contract.unresolved`` value that accepts incomplete contract coverage.
#: The *only* thing it changes is this module's floor (Section 6.2).
ACCEPT_UNRESOLVED = "warn"


def coverage_exit_for_context(ctx: Any) -> int:
    """The exit floor a persisted contract context imposes (``0``/``1``).

    The single derivation, shared by the exit path and by every report that
    emits ``contract_coverage_exit_contribution``. Now that the number is
    *applied*, the two must be the same answer -- a field named "exit
    contribution" that disagrees with the actual exit status is a trap, and
    the run's `contract_coverage_failures` list remains the separate,
    unsuppressible record of what was found regardless of what was accepted.

    ``0`` whenever no context exists: a run without contract evaluation has
    no selected domain to be short of evidence *for*, and inventing a floor
    there would fail invocations that never asked the question.
    """
    if ctx is None or _accepts_unresolved(ctx):
        return 0
    return coverage_exit_contribution(coverage_failures_for_context(ctx))


def coverage_exit_floor(result: Any) -> int:
    """:func:`coverage_exit_for_context` for a ``DiffResult``.

    Read off *result*'s own persisted context, so it answers for the contract
    mode the run was actually scored under rather than for this process's
    current flags.
    """
    return coverage_exit_for_context(getattr(result, "contract_context", None))


def _accepts_unresolved(ctx: Any) -> bool:
    """Did the run configure ``contract.unresolved=warn``?

    Read off the persisted context rather than a CLI parameter, for the same
    reason the failures are: a replayed or re-evaluated context must answer
    from what it recorded, not from this process's flags.
    """
    context = getattr(ctx, "evaluation_context", None)
    config = getattr(context, "resolved_config", None)
    contract = getattr(config, "contract", None)
    return getattr(contract, "unresolved", None) == ACCEPT_UNRESOLVED


def coverage_failure_diagnostic(result: Any) -> str | None:
    """Why this run's exit code was floored, or ``None`` if it was not.

    Only ``--format json`` carries ``contract_coverage_failures``; markdown,
    review, html, sarif, and junit do not. Without this, a compatible
    comparison under a domain that cannot close prints "safe to merge" and
    exits 1 with nothing anywhere saying why (Codex review). Emitted to
    stderr by the caller, so it reaches every format without corrupting a
    machine-readable stdout.

    Names the providers rather than just the count: "old/new export_table"
    is actionable ("this snapshot has no export table"), a bare "2 coverage
    failures" is not.
    """
    ctx = getattr(result, "contract_context", None)
    if coverage_exit_for_context(ctx) == 0:
        return None
    failures = coverage_failures_for_context(ctx)
    where = sorted({f"{f.side}/{f.provider}" for f in failures})
    return (
        "Contract coverage incomplete for the selected --contract domain: "
        + ", ".join(where)
        + ". Exit code floored to 1 (ADR-049 contract-coverage axis). "
        "Use --format json for the full contract_coverage_failures ledger, "
        "or set contract.unresolved=warn to accept incomplete coverage."
    )


#: The one output format that already carries the ledger, so the stderr
#: notice would be a second copy of what the report states. Every other
#: format renders none of it, which is the gap the notice exists to close.
_LEDGER_BEARING_FORMAT = "json"


def fold_coverage_exit(base: int, result: Any, *, fmt: str | None = None) -> int:
    """*base* raised to the coverage floor, announcing the floor if it bites.

    One function so every command folds the axis the same way *and* explains
    it the same way. ``max`` rather than "1 when the ledger fails", because
    the two axes are independent and the compatibility one is strictly more
    severe when it speaks at all.

    The notice goes to stderr whenever the floor bites, except under
    ``--format json``, whose report already carries
    ``contract_coverage_failures`` and the applied contribution -- there the
    message would restate the data next to it, and its "use --format json"
    advice would be nonsense. Keeping the announcement here rather than at
    each exit site is what stops a second exit path from acquiring the floor
    without the explanation.
    """
    import click

    if fmt != _LEDGER_BEARING_FORMAT:
        diagnostic = coverage_failure_diagnostic(result)
        if diagnostic is not None:
            click.echo(diagnostic, err=True)
    return max(base, coverage_exit_floor(result))
