# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""SARIF's invocation exit contract, owned by ``report`` (ADR-061 gap C).

``report/AGENTS.md`` states the rule this module exists to satisfy: *renderers
do not own process exit behavior*. SARIF's ``invocations[0].exitCode`` is not
a presentation choice -- it is the number the abicheck process itself exits
with, published inside the artifact so a consumer reading the artifact alone
sees the same answer the CI job saw. It was computed inline in
``sarif.to_sarif``, from a severity gate that function resolved for itself.

:func:`compute_sarif_invocation_exit` is that computation, moved here
verbatim, taking the *already-decided* gate properties as input. The
:class:`~abicheck.report.envelope.ReportEnvelope` resolves the gate once for
the whole render and SARIF projects it, so SARIF's published exit contract
and the report's own can no longer be derived from two different gate
resolutions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..checker_types import DiffResult


@dataclass(frozen=True, slots=True)
class SarifInvocationExit:
    """SARIF's ``exitCode``/``exitCodeDescription`` pair for one run."""

    exit_code: int
    description: str


def compute_sarif_invocation_exit(
    result: DiffResult,
    severity_gate: dict[str, Any] | None,
    scoped_gate: dict[str, Any] | None,
) -> SarifInvocationExit:
    """The exit code and description SARIF publishes for *result*.

    ADR-049 Phase 7: the orthogonal contract-coverage floor is folded into
    the invocation's exit code exactly as the process folds it. Leaving it
    out published ``exitCode: 0`` beside a process that exited 1, and a
    consumer reading the artifact accepted a run its own notifications said
    was gated (Codex review, reproduced). ``max``, for the same reason the
    process uses it: the axis raises a clean 0 and never lowers a real
    break's code.

    ``executionSuccessful`` is deliberately *not* folded here (it stays
    ``True`` at the call site): per the SARIF spec it reports whether the
    tool ran to completion, not whether it found blocking issues -- the
    spec's own example pairs ``exitCode: 1`` with ``executionSuccessful:
    true``. Incomplete evidence is a finding about the comparison, not a
    failed execution.

    Workstream D-S1: *scoped_gate* never contributes to the exit code any
    more (see ``sarif._scoped_gate_properties``'s docstring) -- the base exit
    code always comes from the full-library severity/verdict, and a supplied
    ``--used-by``/``--required-symbol`` consumer's own assessment is appended
    to the description as informational text only.
    """
    from ..contract_coverage_exit import coverage_exit_floor
    from ..policy.severity import Verdict

    coverage_floor = coverage_exit_floor(result)
    base_exit_code = (
        severity_gate["exitCode"]
        if severity_gate is not None
        else (
            4
            if result.verdict == Verdict.BREAKING
            else 2
            if result.verdict == Verdict.API_BREAK
            else 0
        )
    )
    description = (
        f"{result.verdict.value} (severity-gated)"
        if severity_gate is not None
        else result.verdict.value
    )
    if scoped_gate is not None:
        # Informational only -- appended, never substituted (workstream D-S1).
        description += (
            f" [consumer-scoped assessment: {scoped_gate['gateVerdict']} "
            f"(scope: {scoped_gate['gateScope']}), informational only]"
        )
    if coverage_floor:
        # Names the axis rather than only moving the number, so a reader of
        # the artifact alone can tell a coverage floor from a gate decision.
        description += " + incomplete contract coverage (exit 1)"
    return SarifInvocationExit(
        exit_code=max(base_exit_code, coverage_floor), description=description
    )
