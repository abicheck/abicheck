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

from ..contract_coverage_ledger import (
    coverage_exit_contribution as coverage_exit_contribution,
    coverage_failures_for_context as coverage_failures_for_context,
)

#: ``contract.unresolved`` value that accepts incomplete contract coverage.
#: The *only* thing it changes is this module's floor (Section 6.2).
ACCEPT_UNRESOLVED = "warn"

#: How a CLI user reads the full ledger and accepts incomplete coverage.
#: Both halves are reachable from `compare` and `scan --against`, which have
#: `--format` and `--pack`.
CLI_MITIGATION = (
    "Use --format json for the full contract_coverage_failures ledger, "
    "or set contract.unresolved=warn to accept incomplete coverage."
)

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


def coverage_failure_diagnostic(
    result: Any, *, base_exit: int = 0, mitigation: str = CLI_MITIGATION
) -> str | None:
    """Why this run's exit code was affected by coverage, or ``None``.

    Only ``--format json`` carries ``contract_coverage_failures``; markdown,
    review, html, sarif, and junit do not. Without this, a compatible
    comparison under a domain that cannot close prints "safe to merge" and
    exits 1 with nothing anywhere saying why (Codex review).

    *base_exit* is the compatibility axis's own code, and it decides the
    wording rather than merely whether to speak: beside a real ABI break
    ``max`` keeps the ``4``, so claiming the exit "was floored to 1" would be
    false (Codex review). The incomplete coverage is still worth reporting
    there -- it is why part of the comparison could not be checked -- so the
    message states the contribution instead of a status change it did not
    make.

    Names the providers rather than just the count: "old/export_table" is
    actionable ("this snapshot has no export table"), a bare "2 coverage
    failures" is not.
    """
    ctx = getattr(result, "contract_context", None)
    failures = coverage_failures_for_context(ctx)
    if not failures:
        return None
    return _coverage_message(
        sorted({f"{f.side}/{f.provider}" for f in failures}),
        coverage_exit_for_context(ctx),
        base_exit,
        mitigation,
    )


def coverage_diagnostic_from_summary(
    summary: Any, *, base_exit: int = 0
) -> str | None:
    """The same notice, built from a rendered ``scan`` summary dict.

    ``scan``'s CLI never holds the ``DiffResult`` -- ``_run_baseline_compare``
    is shared with ``service_scan.run_scan()`` and returns a summary, which is
    exactly why the announcement cannot live there. But that summary already
    carries the ledger, so the command can explain its own exit without any
    of the result plumbing (Codex review).
    """
    if not isinstance(summary, dict):
        return None
    failures = summary.get("contract_coverage_failures") or []
    if not failures:
        return None
    floor = summary.get("contract_coverage_exit_contribution") or 0
    where = sorted(
        f"{f.get('side')}/{f.get('provider')}" for f in failures if isinstance(f, dict)
    )
    return _coverage_message(where, floor, base_exit)


def _coverage_message(
    where: list[str], floor: int, base_exit: int, mitigation: str = CLI_MITIGATION
) -> str:
    """The one wording, so the two entry points cannot describe it differently.

    *mitigation* is the only part a surface may vary, and it has to: the
    advice is "here is how to see the rest and how to accept it", which is
    not the same sentence for a caller that has no ``--format`` and no
    ``--pack``. Everything before it -- what fell short, and what that did to
    the exit code -- is the same fact for everyone.
    """
    if floor == 0:
        # `contract.unresolved=warn` accepted these. Staying silent here
        # would make `warn` *hide* the failures for every renderer that
        # omits the ledger -- the exact opposite of the distinction this
        # feature draws between accepting incomplete assurance and
        # pretending it was complete (Codex review).
        effect = (
            "Accepted by contract.unresolved=warn, so it contributes 0 to "
            "the exit code"
        )
    elif base_exit < floor:
        effect = f"Exit code floored to {floor}"
    elif base_exit == floor:
        # The two axes agree on the number. Saying the contribution is
        # "below" it would be false, and saying it floored the exit would
        # claim a change it did not make on its own (CodeRabbit review).
        effect = f"Contributes {floor} to an exit that was already {base_exit}"
    else:
        effect = (
            f"Contributes {floor}, below the compatibility axis's own exit "
            f"{base_exit}, which stands"
        )
    return (
        "Contract coverage incomplete for the selected --contract domain: "
        + ", ".join(where)
        + f". {effect} (ADR-049 contract-coverage axis). "
        + mitigation
    )


#: The one output format that already carries the ledger, so the stderr
#: notice would be a second copy of what the report states -- but only when
#: the *full* report is rendered. The internal ``"oneline"`` fmt (CLI cleanup
#: phase two, PR 1 -- see ``service_render.ONELINE_FORMAT``; reached only via
#: the built-in ``quick`` --profile, ``--stat``'s sole surviving use) is a
#: summary that omits both ledger keys, so a run rendering it is ledgerless
#: whatever else it also renders (Codex review, originally about ``--stat``'s
#: `to_stat_json`, same reasoning now applies to `to_stat`).
_LEDGER_BEARING_FORMAT = "json"

#: Duplicated literal, not an import of ``service_render.ONELINE_FORMAT``:
#: both are leaf modules and this keeps them independent rather than adding
#: a cross-module coupling for one shared string.
_ONELINE_FORMAT = "oneline"


def report_carries_the_ledger(
    fmt: str | None, *, secondary_fmt: str | None = None
) -> bool:
    """Does *every* output this invocation renders already state the ledger?

    The condition for staying quiet, and it has to hold for all of them:
    `--format json --write markdown=abi.md` writes a second report that
    carries none of the ledger, so answering from the primary alone let the
    markdown say the change is safe while the process exited 1 (Codex
    review). Getting this wrong in the permissive direction is exactly what
    makes a run fail with no explanation anywhere.

    The internal one-line format is the same problem one level in: it is a
    summary that omits both ledger keys, so a run rendering it is ledgerless
    whatever its other ``--format`` says.
    """
    rendered = [fmt] + ([secondary_fmt] if secondary_fmt is not None else [])
    if _ONELINE_FORMAT in rendered:
        return False
    return all(f == _LEDGER_BEARING_FORMAT for f in rendered)


def announce_coverage_floor(
    result: Any,
    *,
    base_exit: int,
    fmt: str | None = None,
    secondary_fmt: str | None = None,
) -> None:
    """Print the coverage notice to stderr, unless the report already says it.

    The front end's half of the split: :func:`fold_coverage_exit` stays pure
    for ``service_scan.run_scan()``, and this is the only thing that writes.
    It lives beside the message rather than in each command so the decision
    "does this invocation need telling?" has one answer, and so a second CLI
    exit path cannot acquire the floor without the explanation.

    stderr, not stdout: the report a caller pipes onward stays intact.
    """
    if report_carries_the_ledger(fmt, secondary_fmt=secondary_fmt):
        return
    diagnostic = coverage_failure_diagnostic(result, base_exit=base_exit)
    if diagnostic is None:
        return
    import click

    click.echo(diagnostic, err=True)


def fold_coverage_exit(base: int, result: Any) -> int:
    """*base* raised to the coverage floor -- Section 7's orthogonal fold.

    One function so every command folds the axis the same way. ``max`` rather
    than "1 when the ledger fails", because the two axes are independent and
    the compatibility one is strictly more severe when it speaks at all.

    **Pure**, deliberately: this is on the path ``service_scan.run_scan()``
    takes, and a library call that writes to stderr is an unexpected side
    effect for a caller that already gets the coverage details back in its
    result (Codex review). Announcing belongs to the front end, which is
    also the only layer that knows the output format --
    :func:`coverage_failure_diagnostic` and :func:`report_carries_the_ledger`
    are what it uses.
    """
    return max(base, coverage_exit_floor(result))
