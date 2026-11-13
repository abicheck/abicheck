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

from .analysis_caveats import CAVEAT_HEADING, analysis_caveats_for

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from .envelope import ReportEnvelope


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
    ``--used-by``/``consumers.required_symbols`` consumer's own assessment is appended
    to the description as informational text only.
    """
    from ..policy.contract_coverage_exit import coverage_exit_floor
    from ..policy.severity import Verdict
    from ..policy.unconfirmed_compatibility import (
        report_compatibility,
        unconfirmed_exit_contribution,
    )

    coverage_floor = coverage_exit_floor(result)
    unconfirmed_floor = unconfirmed_exit_contribution(result)
    compatibility = report_compatibility(result)
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
        f"{compatibility} (severity-gated)"
        if severity_gate is not None
        else compatibility
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
    if unconfirmed_floor:
        # ADR-090 D3: the same `max` fold the process applies.
        description += " + no type-level evidence on a compared side (exit 1)"
    return SarifInvocationExit(
        exit_code=max(base_exit_code, coverage_floor, unconfirmed_floor),
        description=description,
    )


def run_notifications(
    result: DiffResult, envelope: ReportEnvelope | None = None
) -> dict[str, object]:
    """The invocation's ``toolExecutionNotifications``, or ``{}`` for none.

    Two run-level conditions are notifications rather than ``results[]``
    entries, because each is the tool saying the evidence needed to decide
    was missing, not another finding a consumer would count:

    * contract coverage (ADR-049 plan Section 6.1): one ``error`` entry per
      coverage failure. Present, possibly empty, whenever the run built a
      contract context -- "checked, nothing missing" and "never checked"
      differ -- and absent otherwise. ``level: "error"`` regardless of what
      the ledger contributed: it describes the evidence, not the gate.
    * ADR-090 ``UNCONFIRMED``: one ``warning`` entry (maintainer answer 5:
      ``note`` would repeat the clean-looking mistake) naming the sides
      without type-level evidence and the remedies.

    * an incomplete analysis (``report/analysis_caveats.py``): one
      ``warning`` entry per caveat, ``descriptor.id``
      ``abicheck.analysis_incomplete.<key>``, so a failed section, a forced
      not-comparable run or a ``NOT_EVALUATED`` finding is machine-readable
      in SARIF too and a clean-looking empty ``results[]`` is qualified.

    ``executionSuccessful`` stays untouched by any of them (the tool ran to
    completion); the invocation's ``exitCode`` folds both axes in
    :func:`compute_sarif_invocation_exit`.
    """
    from ..policy.coverage_ledger import coverage_failures_for_context
    from ..policy.unconfirmed_compatibility import (
        unconfirmed_block,
        unconfirmed_summary,
    )

    notifications: list[dict[str, object]] | None = None
    ctx = getattr(result, "contract_context", None)
    if ctx is not None:
        notifications = [
            {
                "level": "error",
                "message": {
                    "text": (
                        f"contract coverage: {f.provider} ({f.side}) could not "
                        f"close the {f.mode} domain ({f.reason})"
                    )
                },
                "descriptor": {"id": f"abicheck.coverage.{f.reason}"},
                "properties": {
                    "provider": f.provider,
                    "side": f.side,
                    "recordId": f.record_id,
                    "contractMode": f.mode,
                    # Stated per notification so a consumer reading one in
                    # isolation knows it cannot be waived by a suppression
                    # rule (plan Section 6.2).
                    "suppressible": False,
                },
            }
            for f in coverage_failures_for_context(ctx)
        ]
    block = unconfirmed_block(result)
    if block is not None:
        notifications = [
            *(notifications or []),
            {
                "level": "warning",
                "message": {"text": unconfirmed_summary(result)},
                "descriptor": {"id": "abicheck.unconfirmed_compatibility"},
                "properties": block,
            },
        ]
    caveats: list[dict[str, object]] = [
        {
            "level": "warning",
            "message": {"text": f"{CAVEAT_HEADING}: {c.message}"},
            "descriptor": {"id": f"abicheck.analysis_incomplete.{c.key}"},
            "properties": {"caveat": c.key},
        }
        for c in analysis_caveats_for(result, envelope)
        # The per-failure coverage notifications above already state a
        # partial contract coverage, one `error` per provider.
        if not (c.key == "contract_coverage_partial" and notifications)
    ]
    if caveats:
        notifications = [*(notifications or []), *caveats]
    if notifications is None:
        return {}
    return {"toolExecutionNotifications": notifications}
