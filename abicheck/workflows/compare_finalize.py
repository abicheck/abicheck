# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The gate a classified pair is scored under, and its :class:`CompareResult`.

The last step of :func:`abicheck.workflows.compare.execute.classify_compare_pair`,
after :func:`~abicheck.workflows.compare_classify.classify_resolved_pair` has
classified the pair and attached its report fields: the suppression audit, the
exit decision and the resolved gate receipt. The gate is resolved from a typed
request's own settings; the native ``compare`` CLI resolves its gate from its
``ResolvedCompareConfig`` instead (``cli_compare_receipt.record_resolved_config``)
and audits suppressions after its ``--used-by`` scoping, so it does not call
this.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import gate as gate_workflow
from .service_requests import CompareRequest, CompareResult

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from .compare_classify import ClassifiedPair
    from .resolved_execution_context import ResolvedExecutionContext

__all__ = ["finalize_classified_pair"]


def finalize_classified_pair(
    request: CompareRequest,
    context: ResolvedExecutionContext | None,
    classified: ClassifiedPair,
    suppression: Any,
    evaluation_config: Any,
    *,
    pack_application: Any = None,
) -> CompareResult:
    """Audit suppressions, resolve the gate and build the result.

    *pack_application* is the request's selected ``pack_paths`` contribution
    (``RequestPolicyInputs.pack_application``): a gate pack's severity levels
    fold into the gate here, as the native CLI folds them into its own."""
    from .suppression_audit_attach import attach_suppression_audit

    result, old, new = classified.result, classified.old, classified.new
    attach_suppression_audit(result, suppression)
    # ADR-064/PR G2: resolve severity into the same `GateOptions` the
    # release fan-out uses, then the canonical decision. No manual
    # exit-code-scheme selector to pass any more -- the algorithm is purely
    # derived from whether `severity_preset` (or a resolved gate pack) put a
    # severity setting in effect.
    gate = gate_workflow.resolve_release_gate_options(
        pack_application,
        severity_preset=request.severity_preset,
        severity_abi_breaking=request.severity_abi_breaking,
        severity_potential_breaking=request.severity_potential_breaking,
        severity_quality_issues=request.severity_quality_issues,
        severity_addition=request.severity_addition,
    )
    # The same derivation the native CLI uses (`effective_gate_for_compare`),
    # so a typed caller with the same severity and assurance inputs gets the
    # same exit code. Abort-axes-aware (plan P3): a typed caller's own
    # `exit_decision` reports an `--abi3` evidence-contract abort too.
    exit_decision = gate_workflow.resolve_compare_exit_decision_with_abort_axes(
        result,
        gate_workflow.effective_gate_for_compare(
            gate.severity,
            require_complete_analysis=request.require_complete_analysis,
            result=result,
        ),
    )

    # Installs the same gate onto result.contract_context; see
    # workflows.compare_gate_receipt's own docstring for the full account.
    from .compare_gate_receipt import install_resolved_gate_receipt

    install_resolved_gate_receipt(
        result,
        evaluation_config,
        gate,
        packs_forwarded=bool(request.pack_policy_overrides)
        or request.pack_internal_namespaces is not None
        or bool(request.pack_paths),
    )
    if context is not None:
        context = context.for_classification(evaluation_config, result.requested_depth)

    # ADR-055 D2/D4: `suppression` is carried out so a front end applying a
    # post-classification concern (appcompat's `scope_diff_to_app`) reuses the
    # list this call already resolved instead of loading it a second time.
    return CompareResult(
        diff=result,
        old_snapshot=old,
        new_snapshot=new,
        suppression=suppression,
        exit_decision=exit_decision,
        severity_config=gate.severity,
        resolved_execution_context=context,
    )


def _merge_redundant_changes(result: DiffResult) -> None:
    """Re-merge redundant changes back into the main change list."""
    for c in result.changes:
        c.caused_count = 0
    for c in result.redundant_changes:
        c.caused_by_type = None
    result.changes = result.changes + result.redundant_changes
    result.redundant_changes = []
    result.redundant_count = 0
