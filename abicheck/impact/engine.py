# Copyright 2026 Nikolay Petrov
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

"""Build an :class:`~abicheck.impact.model.ImpactAssessment` from an existing
``Change`` (G29 Phase 3 slice 1, ADR-050).

``assess_change`` is a pure read view: it does not mutate *change*, run any
graph traversal, or change any producer's behavior. It only reads attributes
already independently set on ``Change`` by ``post_processing.MarkReachability``,
``source_graph_findings.py``, ``internal_leak.py``, ``suppression.py``, and
``buildsource.graph_impact.attach_impact_metadata``.
"""

from __future__ import annotations

from typing import Any

from ..checker_policy import Confidence, ReachabilityState
from .model import FindingDecision, GraphProofPath, ImpactAssessment, ProofStep


def _proof_path_target(change: Any, steps: tuple[ProofStep, ...]) -> str:
    """The subject the proof path actually points at.

    For most findings ``Change.symbol`` already *is* the affected subject
    (e.g. a ``func_removed`` on an internal helper). But a structured path
    attached via ``buildsource.graph_impact.attach_impact_metadata`` (e.g.
    ``PUBLIC_API_INTERNAL_DEPENDENCY_ADDED``) sets ``symbol`` to the
    *public entry point* the walk started from, not the internal
    declaration/type it reached -- using ``symbol`` there would make
    ``target`` equal ``root``, pointing a consumer at the API entry instead
    of the actual affected internal entity (Codex review). When structured
    steps are present, the last node in the path is that entity; fall back
    to ``symbol`` only for prose-only (or absent) paths.
    """
    last_node = next((s for s in reversed(steps) if s.step_type == "node"), None)
    if last_node is not None:
        return last_node.label
    return str(getattr(change, "symbol", "") or "")


def _build_proof_path(change: Any) -> GraphProofPath | None:
    impact_proof_path = getattr(change, "impact_proof_path", None)
    affected_roots = getattr(change, "affected_public_roots", None)
    prose = getattr(change, "reachability_proof_path", None)
    is_direct = getattr(change, "impact_is_direct", None)
    if not impact_proof_path and not affected_roots and not prose:
        return None
    steps = tuple(ProofStep.from_dict(raw) for raw in (impact_proof_path or []))
    root = affected_roots[0] if affected_roots else None
    return GraphProofPath(
        target=_proof_path_target(change, steps),
        root=root,
        is_direct=is_direct,
        steps=steps,
        prose=prose,
    )


def assess_change(change: Any, *, suppressed: bool = False) -> ImpactAssessment:
    """Derive an ``ImpactAssessment`` from *change*'s existing fields.

    *suppressed* is caller-supplied: whether *this* call site is rendering
    ``DiffResult.changes`` or ``DiffResult.suppressed_changes`` is not
    recoverable from *change* alone. ``Change.suppression_rule`` (G29 Phase 3
    slice 2, ADR-050 follow-up) *is* set directly on the change by whichever
    suppression call site moved it into ``suppressed_changes``
    (``checker._filter_suppressed_changes``/``_filter_pattern_synthetic``,
    ``post_processing.ApplySuppression``), so it is read unconditionally
    here rather than gated on *suppressed* — reading it for a *kept* change
    is harmless (it is never set on one).
    """
    effective_verdict = getattr(change, "effective_verdict", None)
    decision = FindingDecision(
        state="suppressed" if suppressed else "kept",
        reason_code=getattr(change, "modulation_reason", None),
        suppression_rule=getattr(change, "suppression_rule", None),
        verdict_override=(
            effective_verdict.value if effective_verdict is not None else None
        ),
    )
    return ImpactAssessment(
        reachability_state=getattr(
            change, "reachability_state", ReachabilityState.UNKNOWN
        ),
        public_reachable=bool(getattr(change, "public_reachable", False)),
        reachability_kind=getattr(change, "reachability_kind", None),
        confidence=getattr(change, "confidence", Confidence.HIGH),
        proof_path=_build_proof_path(change),
        decision=decision,
        evidence_category=getattr(change, "evidence_category", None),
        correlated_change_kind=getattr(change, "correlated_change_kind", None),
    )
