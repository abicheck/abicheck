# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The report fields a classified pair owes before it is a :class:`CompareResult`.

The second half of :func:`abicheck.service_compare_pipeline.classify_compare_pair`:
everything after ``compare_snapshots`` and the evidence fold -- operand metadata
and the same-binary note, ``requested_depth``, analysis assurance, evidence
depths, the suppression audit, the exit decision and the resolved gate receipt.
Split out so a front end that classifies through the shared core can run these
once, in the order its own post-processing needs, instead of twice.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..confidence import note_if_same_binary_compared
from . import gate as gate_workflow
from .contracts import CompareRequest, CompareResult
from .input_resolution import collect_metadata, sniff_text_format
from .request_inputs import required_path

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from ..model import AbiSnapshot
    from .resolved_execution_context import ResolvedExecutionContext

__all__ = ["finalize_classified_pair"]


def finalize_classified_pair(
    request: CompareRequest,
    context: ResolvedExecutionContext | None,
    result: DiffResult,
    old: AbiSnapshot,
    new: AbiSnapshot,
    suppression: Any,
    evaluation_config: Any,
) -> CompareResult:
    """Attach the post-classification report fields and build the result."""
    # Hash through the full GNU ld linker-script chain to its final resolved
    # target -- resolve_side_snapshot() already followed the identical chain
    # to produce `old`/`new` above -- so a (possibly multi-hop) script vs.
    # its target DSO given as the other `CompareRequest` side still reads as
    # byte-identical (mirrors the same fix on the retired
    # `cli_scan_baseline._run_baseline_compare`).
    from ..binary_utils import resolve_linker_script_chain

    # A text snapshot/manifest can coincidentally match the INPUT()/GROUP()
    # probe -- skip linker-script resolution for it.
    def _hashable_path(p: Path) -> Path:
        return (
            p
            if sniff_text_format(p) in ("json", "symvers")
            else resolve_linker_script_chain(p)
        )

    result.old_metadata = collect_metadata(
        _hashable_path(required_path(request.old, "old"))
    )
    result.new_metadata = collect_metadata(
        _hashable_path(required_path(request.new, "new"))
    )
    # Item 4 fix: collect_metadata() is a no-op for a JSON/text snapshot
    # path, so a snapshot-input compare left note_if_same_binary_compared
    # unable to fire on content-identical snapshots. Digest fallback below
    # requires *both* sides missing metadata (`and`, not `or`): a mixed
    # live-binary-vs-snapshot compare has one side absent by design.
    old_digest = new_digest = None
    if result.old_metadata is None and result.new_metadata is None:
        from .gate import snapshot_identity_digests

        old_digest, new_digest = snapshot_identity_digests(old, new)
    note_if_same_binary_compared(
        result, old_snapshot_digest=old_digest, new_snapshot_digest=new_digest
    )

    # P0.4 follow-up (P2 review, discussion_r3787839902): stamps
    # `DiffResult.requested_depth` for `analysis_assurance` below, preferring
    # `pair.resolved_execution_context`'s value over `request.depth` only
    # when the two agree (a caller may pass a *different* `request` than
    # built `pair`; a disagreement defers to `request.depth`, what `old`/
    # `new` above were actually projected to -- Codex review).
    normalized_request_depth = (
        request.depth.lower() if request.depth is not None else None
    )
    if context is not None and context.requested_depth == normalized_request_depth:
        result.requested_depth = context.requested_depth
    elif normalized_request_depth is not None:
        result.requested_depth = normalized_request_depth
    # Depths and suppression audit: report fields every pairwise route owes
    # (F2 route parity), once set by the native CLI alone.
    from . import analysis_assurance_attach as assurance_attach
    from .suppression_audit_attach import attach_suppression_audit

    assurance_attach.attach_analysis_assurance(result, old, new)
    assurance_attach.attach_evidence_depths(result, old, new)
    attach_suppression_audit(result, suppression)
    # ADR-064/PR G2: resolve severity into the same `GateOptions` the
    # release fan-out uses, then the canonical decision. No manual
    # exit-code-scheme selector to pass any more -- the algorithm is purely
    # derived from whether `severity_preset` (or a resolved gate pack) put a
    # severity setting in effect.
    gate = gate_workflow.resolve_release_gate_options(
        None,
        severity_preset=request.severity_preset,
        severity_abi_breaking=None,
        severity_potential_breaking=None,
        severity_quality_issues=None,
        severity_addition=None,
    )
    # Abort-axes-aware (plan P3): a typed caller's own `exit_decision` reports an `--abi3` evidence-contract abort too.
    exit_decision = gate_workflow.resolve_compare_exit_decision_with_abort_axes(
        result, gate.effective_gate
    )

    # Installs the same gate onto result.contract_context; see
    # workflows.compare_gate_receipt's own docstring for the full account.
    from .compare_gate_receipt import install_resolved_gate_receipt

    install_resolved_gate_receipt(
        result,
        evaluation_config,
        gate,
        packs_forwarded=bool(request.pack_policy_overrides)
        or request.pack_internal_namespaces is not None,
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
