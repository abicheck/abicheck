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
``Change`` (G29 Phase 3 slice 1, ADR-052).

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


def _build_alternative_path(
    raw_steps: list[dict[str, object]], *, root: str | None
) -> GraphProofPath:
    """One runner-up candidate as its own (prose-less) ``GraphProofPath`` —
    ADR-046 D6's ``alternative_paths``. Shares ``root`` with the primary
    path (same walk origin); ``target``/``is_direct`` are derived from its
    own steps, since an alternative can legitimately point at a different
    subject than the primary (see :func:`_build_proof_path`).
    """
    steps = tuple(ProofStep.from_dict(raw) for raw in raw_steps)
    last_node = next((s for s in reversed(steps) if s.step_type == "node"), None)
    # CodeRabbit review: structured_proof_path's shape is
    # node, edge, node, edge, ... -- a single-hop (direct) path already has
    # 3 raw_steps entries (one edge, two nodes), so counting *all* entries
    # would mark every direct path as transitive. Count edge-type steps
    # only (the actual hop count).
    edge_hops = sum(1 for s in steps if s.step_type == "edge")
    return GraphProofPath(
        target=last_node.label if last_node is not None else (root or ""),
        root=root,
        is_direct=edge_hops <= 1 if raw_steps else None,
        steps=steps,
    )


def _build_proof_path(change: Any) -> GraphProofPath | None:
    impact_proof_path = getattr(change, "impact_proof_path", None)
    affected_roots = getattr(change, "affected_public_roots", None)
    prose = getattr(change, "reachability_proof_path", None)
    is_direct = getattr(change, "impact_is_direct", None)
    if not impact_proof_path and not affected_roots and not prose:
        return None
    steps = tuple(ProofStep.from_dict(raw) for raw in (impact_proof_path or []))
    root = affected_roots[0] if affected_roots else None
    alt_raw = getattr(change, "impact_alternative_paths", None) or []
    alternatives = tuple(_build_alternative_path(p, root=root) for p in alt_raw)
    discarded = int(getattr(change, "impact_discarded_path_count", 0) or 0)
    occurrence_id = getattr(change, "impact_occurrence_id", None)
    return GraphProofPath(
        target=_proof_path_target(change, steps),
        root=root,
        is_direct=is_direct,
        steps=steps,
        prose=prose,
        alternative_paths=alternatives,
        discarded_path_count=discarded,
        occurrence_id=occurrence_id,
    )


def assess_change(
    change: Any,
    *,
    suppressed: bool = False,
    root_cause: tuple[str, str] | None = None,
) -> ImpactAssessment:
    """Derive an ``ImpactAssessment`` from *change*'s existing fields.

    *suppressed* is caller-supplied: whether *this* call site is rendering
    ``DiffResult.changes`` or ``DiffResult.suppressed_changes`` is not
    recoverable from *change* alone. ``Change.suppression_rule`` (G29 Phase 3
    slice 2, ADR-052 follow-up) *is* set directly on the change by whichever
    suppression call site moved it into ``suppressed_changes``
    (``checker._filter_suppressed_changes``/``_filter_pattern_synthetic``,
    ``post_processing.ApplySuppression``), so it is read unconditionally
    here rather than gated on *suppressed* — reading it for a *kept* change
    is harmless (it is never set on one).

    *root_cause*, when given, is *change*'s ``(root_cause_id, root_display)``
    pair (G29 Phase 3 follow-up) — computed by the caller, not derived here,
    because correctly computing it needs whole-``DiffResult`` context (which
    findings elsewhere reference this one via ``caused_by_type`` —
    ``reporter_markdown.root_cause_lookup_for_changes``) that a single
    *change* alone can't see; this function stays a pure, single-``Change``
    read view. ``None`` (the default) leaves
    ``root_cause_id``/``root_cause_display``/``impact_group_id`` unset, same
    as any caller that doesn't have whole-result context to offer (e.g. a
    unit test constructing one bare ``Change``).

    ``Change.impact_assessment`` (ADR-052 D2 follow-up, scoped
    implementation), when a producer set it directly, supplies this
    assessment's *evidence* fields (``reachability_state``/
    ``public_reachable``/``reachability_kind``/``confidence``/``proof_path``/
    ``evidence_category``/``correlated_change_kind``) instead of re-deriving
    them from the flat fields below — both are equivalent by construction
    for a producer that built both from the same data, but reusing the
    cached object avoids recomputing ``proof_path`` from scratch.
    ``decision``/``root_cause_id``/``root_cause_display``/``impact_group_id``
    are **always** recomputed fresh here regardless, never read from a
    cached ``impact_assessment`` — those depend on *this call's*
    ``suppressed``/``root_cause`` arguments and on flat fields
    (``suppression_rule``/``modulation_reason``/``effective_verdict``) that
    can change after a producer constructs its `Change` (suppression,
    pattern modulation), so trusting a cached ``decision`` would risk
    serving a stale one.
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
    root_cause_id, root_cause_display = (
        root_cause if root_cause is not None else (None, None)
    )
    cached = getattr(change, "impact_assessment", None)
    if cached is not None:
        return ImpactAssessment(
            reachability_state=cached.reachability_state,
            public_reachable=cached.public_reachable,
            reachability_kind=cached.reachability_kind,
            confidence=cached.confidence,
            proof_path=cached.proof_path,
            decision=decision,
            evidence_category=cached.evidence_category,
            correlated_change_kind=cached.correlated_change_kind,
            root_cause_id=root_cause_id,
            root_cause_display=root_cause_display,
            impact_group_id=root_cause_id,
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
        root_cause_id=root_cause_id,
        root_cause_display=root_cause_display,
        impact_group_id=root_cause_id,
    )
