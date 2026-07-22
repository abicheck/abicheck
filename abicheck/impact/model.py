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

"""Unified impact-assessment dataclasses (G29 Phase 3 slice 1, ADR-050).

``ImpactAssessment``/``GraphProofPath``/``FindingDecision`` are a shared,
queryable shape over reachability/impact fields that
``source_graph_findings.py``, ``internal_leak.py``, ``post_processing.py``,
``suppression.py``, and ``appcompat.py`` each independently set on
``Change`` today. This module only defines the shape; :mod:`.engine` builds
one from an existing ``Change`` object. See ADR-050 for the full decision
record, including which plan-described fields (``changed_entities``,
``affected_consumers``, ``affected_use_cases``, ``coverage``,
``root_cause_id``) are deliberately absent from this slice.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..checker_policy import Confidence, ReachabilityState


@dataclass(frozen=True)
class ProofStep:
    """One typed node or edge reference in a :class:`GraphProofPath`.

    The dataclass counterpart of one entry in
    ``buildsource.graph_impact.structured_proof_path``'s ``list[dict]``
    shape (``{"type": "node", "id", "kind", "label"}`` /
    ``{"type": "edge", "kind", "role", "confidence"}``).
    """

    step_type: str  # "node" | "edge"
    label: str
    kind: str | None = None
    role: str | None = None
    confidence: str | None = None
    node_id: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> ProofStep:
        step_type = str(raw.get("type", ""))
        kind = raw.get("kind")
        if step_type == "node":
            node_id = raw.get("id")
            return cls(
                step_type="node",
                label=str(raw.get("label", node_id or "")),
                kind=str(kind) if kind else None,
                node_id=str(node_id) if node_id else None,
            )
        role = raw.get("role")
        confidence = raw.get("confidence")
        return cls(
            step_type="edge",
            label=str(kind) if kind else "",
            kind=str(kind) if kind else None,
            role=str(role) if role else None,
            confidence=str(confidence) if confidence else None,
        )

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {"type": self.step_type, "label": self.label}
        if self.kind is not None:
            d["kind"] = self.kind
        if self.role is not None:
            d["role"] = self.role
        if self.confidence is not None:
            d["confidence"] = self.confidence
        if self.node_id is not None:
            d["id"] = self.node_id
        return d


@dataclass(frozen=True)
class GraphProofPath:
    """Structured reachability evidence for one finding.

    ``prose`` carries the existing ``Change.reachability_proof_path``
    string verbatim rather than re-deriving it — there is exactly one
    producer of that rendering today (``internal_leak._format_path``) and
    duplicating its logic here would be a second, driftable implementation.
    ``steps`` is empty when only the prose rendering is available (no
    structured ``impact_proof_path`` was attached for this finding).
    """

    target: str
    root: str | None = None
    is_direct: bool | None = None
    steps: tuple[ProofStep, ...] = ()
    prose: str | None = None

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {"target": self.target}
        if self.root is not None:
            d["root"] = self.root
        if self.is_direct is not None:
            d["is_direct"] = self.is_direct
        if self.steps:
            d["steps"] = [s.to_dict() for s in self.steps]
        if self.prose is not None:
            d["prose"] = self.prose
        return d


@dataclass(frozen=True)
class FindingDecision:
    """Whether a finding was kept or suppressed, and why.

    ``suppression_rule`` is deliberately left ``None`` in this slice — it
    would need ``suppression.SuppressionOutcome`` threaded through
    :func:`.engine.assess_change`'s caller, not done yet (ADR-050
    "Deliberately not implemented this slice").
    """

    state: str = "kept"  # "kept" | "suppressed"
    reason_code: str | None = None
    suppression_rule: str | None = None
    demotion: str | None = None

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {"state": self.state}
        if self.reason_code is not None:
            d["reason_code"] = self.reason_code
        if self.suppression_rule is not None:
            d["suppression_rule"] = self.suppression_rule
        if self.demotion is not None:
            d["demotion"] = self.demotion
        return d


@dataclass(frozen=True)
class ImpactAssessment:
    """A single queryable view over one finding's reachability/impact data.

    Every field is read from a ``Change`` attribute that already exists and
    is already independently populated by one of the producer modules named
    in this module's docstring — this dataclass adds no new signal, only a
    shared shape to query it through (ADR-050 D1).
    """

    reachability_state: ReachabilityState = ReachabilityState.UNKNOWN
    public_reachable: bool = False
    reachability_kind: str | None = None
    confidence: Confidence = Confidence.HIGH
    proof_path: GraphProofPath | None = None
    decision: FindingDecision = field(default_factory=FindingDecision)
    evidence_category: str | None = None
    correlated_change_kind: str | None = None

    def has_signal(self) -> bool:
        """True when this assessment carries information beyond the
        all-defaults case — the gate ``reporter.py``/``sarif.py`` use to
        decide whether emitting the full object is worth the report-size
        cost (ADR-050 D3)."""
        return (
            self.proof_path is not None
            or self.reachability_state != ReachabilityState.UNKNOWN
            or self.public_reachable
            or self.confidence != Confidence.HIGH
            or self.decision.state != "kept"
            or self.decision.reason_code is not None
            or self.decision.demotion is not None
            or self.correlated_change_kind is not None
            or self.evidence_category is not None
        )

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "reachability_state": self.reachability_state.value,
            "public_reachable": self.public_reachable,
            "confidence": self.confidence.value,
            "decision": self.decision.to_dict(),
        }
        if self.reachability_kind is not None:
            d["reachability_kind"] = self.reachability_kind
        if self.proof_path is not None:
            d["proof_path"] = self.proof_path.to_dict()
        if self.evidence_category is not None:
            d["evidence_category"] = self.evidence_category
        if self.correlated_change_kind is not None:
            d["correlated_change_kind"] = self.correlated_change_kind
        return d
