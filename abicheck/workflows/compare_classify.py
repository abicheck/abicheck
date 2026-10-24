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

"""Classify one resolved compare pair -- the single implementation.

Every scalar ``compare`` route runs a resolved pair through
:func:`classify_resolved_pair`: the typed API (``run_compare_request`` via
``workflows.compare.execute.classify_compare_pair``), every release member
(through the same request), and the native ``compare`` CLI
(``cli_compare_helpers.run_compare``). Before this module the CLI kept its own
copy of this whole phase -- depth projection, the evidence fold, the
``compare_snapshots`` call, the evidence-contract axes, operand metadata,
requested depth, analysis assurance and evidence depths -- and each route
divergence found by the F2 route-parity harness was patched on both copies.

What the module takes is everything a front end may legitimately decide:

* the :class:`~abicheck.workflows.service_requests.CompareRequest` (the classification
  settings: policy, scope, depth, probe matrices, ``abi3_floor``, ...);
* the :class:`ResolvedComparePair` from
  ``resolve_compare_request``;
* its resolved :class:`~abicheck.workflows.compare_request_policy.
  RequestPolicyInputs` -- the typed route resolves them from the request
  (``resolve_request_policy_inputs``); the CLI from its ADR-049 configuration
  step, which reads Click parameter sources and the project config;
* *on_output*, the sink for the build-source evidence report (``None``: no
  output, the engine default).

The gate, the exit decision and the gate receipt are not here: they are
resolved from the run's configuration, which is still front-end specific
(``compare_finalize.finalize_classified_pair`` for a typed request,
``ResolvedCompareConfig`` for the CLI).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .request_inputs import required_path

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..checker_types import DiffResult
    from ..model import AbiSnapshot
    from ..service_compare_evidence import SideEvidence
    from .compare_request_policy import RequestPolicyInputs
    from .resolved_execution_context import ResolvedExecutionContext
    from .service_requests import CompareRequest

__all__ = [
    "ClassifiedPair",
    "ComparedOperands",
    "ResolvedComparePair",
    "classify_resolved_pair",
]


@dataclass(frozen=True)
class ResolvedComparePair:
    """Both sides of a comparison, resolved and ready to classify.

    The seam between ``workflows.compare.resolve.resolve_compare_request`` and
    :func:`classify_resolved_pair`: the detected binary formats, and each
    side's resolved ``SideEvidence`` (``collect_mode`` drives the embed diff).

    ``resolved_execution_context`` (One Semantic Pipeline plan, sub-phase 4B)
    is the :class:`~abicheck.workflows.resolved_execution_context.
    ResolvedExecutionContext` built from this request's own AnalysisPlan,
    plus each side's resolved ``CompileContext`` when ``compile_context_gate``
    judges it safe — still no ``evaluation_config`` (resolves one layer up).

    """

    old: AbiSnapshot
    new: AbiSnapshot
    old_fmt: str | None
    new_fmt: str | None
    old_evidence: SideEvidence
    new_evidence: SideEvidence
    resolved_execution_context: ResolvedExecutionContext | None = None


@dataclass(frozen=True)
class ComparedOperands:
    """The two operands as the run named them, and whether it extracted each.

    *old_path*/*new_path* feed operand metadata and the same-binary note;
    *old_live*/*new_live* decide whether the ``--depth build|source`` floor
    applies to a side (``input_resolution.side_is_live``);
    *candidate_name* names the candidate in an ``--abi3`` precondition
    message: the candidate operand's file name, on every route.
    """

    old_path: Path
    new_path: Path
    old_live: bool
    new_live: bool
    candidate_name: str | None = None

    @classmethod
    def from_request(cls, request: CompareRequest) -> ComparedOperands:
        """The operands a typed request names, liveness from its ``InputSpec``s."""
        from .input_resolution import input_spec_is_live

        return cls(
            old_path=required_path(request.old, "old"),
            new_path=required_path(request.new, "new"),
            old_live=input_spec_is_live(request.old),
            new_live=input_spec_is_live(request.new),
            candidate_name=required_path(request.new, "new").name,
        )


@dataclass(frozen=True)
class ClassifiedPair:
    """:func:`classify_resolved_pair`'s answer.

    *old*/*new* are the depth-projected views the result was classified from
    (what every later reader -- scoping, rendering -- must use);
    *abi3_failure* is the ``--abi3`` precondition message, already recorded on
    ``result.evidence_contract_error``, for a front end that also prints it.
    """

    result: DiffResult
    old: AbiSnapshot
    new: AbiSnapshot
    abi3_failure: str | None = None


def classify_resolved_pair(
    request: CompareRequest,
    pair: ResolvedComparePair,
    policy: RequestPolicyInputs,
    *,
    on_output: Callable[[str], None] | None = None,
) -> ClassifiedPair:
    """Classify *pair* under *request* and *policy*; attach the pair's report fields.

    In order: cap both sides to ``request.depth`` (a view, never a mutation
    of ``pair.old``/``pair.new``); fold the pair's evidence (probe matrix,
    L0 hard removals, embedded build-source facts, the candidate-side
    ``--abi3`` audit) into ``extra_changes`` so policy scores it; classify
    through ``compare_snapshots``; record ADR-064's ``--abi3`` and
    ``--depth`` evidence-contract axes; then attach operand metadata, the
    same-binary note, ``requested_depth``, analysis assurance and evidence
    depths -- the report fields every pairwise route owes.

    Raises what its steps raise, untranslated: ``ValidationError`` for a
    one-sided probe matrix, ``SnapshotError`` for an unloadable build-source
    pack, ``ProfileMismatchError``/``ScopeMismatchError`` for a pair
    ``compare_snapshots`` refuses, and ``DeadlineExceeded`` past
    ``--budget``. A front end maps them onto its own error surface.
    """
    from ..buildsource.evidence_report import attach_evidence_metrics
    from ..deadline import check as check_deadline
    from ..policy.depth_evidence_contract import record_depth_evidence_contract_error
    from ..policy.depth_projection import (
        header_scope_excluded_by_depth,
        project_pair_to_depth,
    )
    from . import abi3_audit
    from .compare_policy import compare_snapshots
    from .pair_evidence import fold_pair_evidence, load_probe_matrix_changes

    # The operands as the request names them -- every route builds its
    # request from the user's real operands, so nothing overrides these.
    operands = ComparedOperands.from_request(request)
    if request.require_same_generation:
        from ..errors import ValidationError
        from .generation_drift import generation_mismatch_message

        mismatch = generation_mismatch_message(pair.old, pair.new)
        if mismatch is not None:
            raise ValidationError(mismatch)
    # A stored-snapshot-only pair may do no extraction work at all, so nothing
    # below would otherwise consult an already-expired `budget_s` (PR #1178).
    check_deadline()
    # ADR-063 Phase 8's depth *ceiling*: narrow what classification may see
    # to the requested rung. The floor is the exit-7 axis recorded below.
    old, new = project_pair_to_depth(pair.old, pair.new, request.depth)
    # A depth below `headers` that left no header surface to scope against
    # puts public-header scoping outside the requested scope: classify the
    # pair as a binary-only comparison would be, rather than reporting a
    # scope that "failed to resolve" (manual review, partial assurance).
    scope_public = request.scope_public and not header_scope_excluded_by_depth(
        (pair.old, pair.new), (old, new), request.depth
    )
    folded = fold_pair_evidence(
        old,
        new,
        collect_mode=pair.old_evidence.collect_mode,
        extra_changes=load_probe_matrix_changes(
            request.old_probe_matrix, request.new_probe_matrix
        ),
        policy_file=policy.policy_file,
        lang=request.lang,
        abi3_floor=request.abi3_floor,
        candidate_name=operands.candidate_name,
        on_output=on_output,
    )
    extra_changes = folded.extra_changes
    result = compare_snapshots(
        old,
        new,
        suppression=policy.suppression,
        policy=request.policy,
        policy_file=policy.policy_file,
        acknowledgments=policy.acknowledgments.records,
        acknowledgment_policy=policy.acknowledgments.policy,
        scope_to_public_surface=scope_public,
        force_public_symbols=(
            set(request.force_public_symbols) if request.force_public_symbols else None
        ),
        extra_changes=extra_changes,
        public_surface_allowlist=(
            set(request.public_surface_allowlist)
            if request.public_surface_allowlist is not None
            else None
        ),
        # The request's own field: ADR-027's opt-in default stays a default
        # change needing its own amendment (PR #1154 follow-up), so a bare
        # `CompareRequest()` keeps it and the CLI states `True`.
        # `surface_metrics`/`reconcile_build_context` are not request fields:
        # `compare_snapshots` forces both on for every caller.
        pattern_verdicts=request.pattern_verdicts,
        collapse_versioned_symbols=request.collapse_versioned_symbols,
        env_matrix=request.env_matrix,
        diagnostic_comparison=request.diagnostic_comparison,
        contract_evaluation=request.contract_evaluation,
        contract_mode=request.contract_mode,
    )
    if folded.layer_coverage_rows:
        result.layer_coverage = folded.layer_coverage_rows
    # All injected findings (probe matrix + evidence), so "artifact-backed"
    # excludes them -- none come from L0-L2 diffing.
    attach_evidence_metrics(
        result, folded.evidence_metrics, extra_changes or [], on_output=on_output
    )
    abi3_audit.record_abi3_evidence_contract_error(result, folded.abi3_failure)
    # ADR-068 §3 #28 -- `compare`'s only depth-floor mechanism since PR #1195.
    record_depth_evidence_contract_error(
        result,
        request.depth,
        old,
        new,
        old_is_live=operands.old_live,
        new_is_live=operands.new_live,
    )
    _attach_pair_report_fields(request, pair, result, old, new, operands)
    return ClassifiedPair(result, old, new, folded.abi3_failure)


def _attach_pair_report_fields(
    request: CompareRequest,
    pair: ResolvedComparePair,
    result: DiffResult,
    old: AbiSnapshot,
    new: AbiSnapshot,
    operands: ComparedOperands,
) -> None:
    """Operand metadata, the same-binary note, ``requested_depth``, assurance, depths."""
    from ..binary_utils import resolve_linker_script_chain
    from ..confidence import note_if_same_binary_compared
    from . import analysis_assurance_attach as assurance_attach
    from .input_resolution import collect_metadata, sniff_text_format

    # Hash through the full GNU ld linker-script chain to its final target,
    # so a script vs. its target DSO still reads as byte-identical. A text
    # snapshot/manifest can coincidentally match the INPUT()/GROUP() probe,
    # so it skips linker-script resolution.
    def _hashable_path(p: Path) -> Path:
        return (
            p
            if sniff_text_format(p) in ("json", "symvers")
            else resolve_linker_script_chain(p)
        )

    result.old_metadata = collect_metadata(_hashable_path(operands.old_path))
    result.new_metadata = collect_metadata(_hashable_path(operands.new_path))
    # `collect_metadata` is a no-op for a snapshot path; the content-digest
    # fallback needs *both* sides missing metadata -- a mixed live-vs-snapshot
    # pair has one side absent by design.
    old_digest = new_digest = None
    if result.old_metadata is None and result.new_metadata is None:
        from .gate import snapshot_identity_digests

        old_digest, new_digest = snapshot_identity_digests(old, new)
    note_if_same_binary_compared(
        result, old_snapshot_digest=old_digest, new_snapshot_digest=new_digest
    )
    # `requested_depth` (the G30 report-identity field `analysis_assurance`
    # gates `depth_satisfied` on): the resolved context's value only when it
    # agrees with `request.depth`, which is what `old`/`new` were projected
    # to -- a caller may classify under a different request than the one
    # that built `pair` (Codex review).
    requested = request.depth.lower() if request.depth is not None else None
    context = pair.resolved_execution_context
    if context is not None and context.requested_depth == requested:
        result.requested_depth = context.requested_depth
    elif requested is not None:
        result.requested_depth = requested
    assurance_attach.attach_analysis_assurance(result, old, new)
    assurance_attach.attach_evidence_depths(result, old, new)
