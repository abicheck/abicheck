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

"""Tests for the unified impact-assessment model (G29 Phase 3 slice 1, ADR-052)."""

from __future__ import annotations

import json

from abicheck import reporter
from abicheck.checker_policy import ChangeKind, Confidence, ReachabilityState, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.impact import FindingDecision, GraphProofPath, ImpactAssessment, ProofStep
from abicheck.impact.engine import assess_change


def _change(**overrides: object) -> Change:
    base: dict[str, object] = {
        "kind": ChangeKind.FUNC_REMOVED,
        "symbol": "ns::internal::helper",
        "description": "helper removed",
    }
    base.update(overrides)
    return Change(**base)  # type: ignore[arg-type]


class TestProofStep:
    def test_from_dict_node(self) -> None:
        step = ProofStep.from_dict(
            {"type": "node", "id": "decl://pub", "kind": "source_decl", "label": "pub"}
        )
        assert step.step_type == "node"
        assert step.label == "pub"
        assert step.kind == "source_decl"
        assert step.role is None

    def test_from_dict_node_preserves_id_distinct_from_label(self) -> None:
        """A node's id (stable) and label (human-readable, possibly
        colliding across nodes) are different things -- losing the id would
        make two same-label nodes indistinguishable (Codex review)."""
        step = ProofStep.from_dict(
            {
                "type": "node",
                "id": "decl://ns::pub",
                "kind": "source_decl",
                "label": "pub",
            }
        )
        assert step.node_id == "decl://ns::pub"
        assert step.label == "pub"

    def test_node_to_dict_includes_id(self) -> None:
        step = ProofStep(step_type="node", label="pub", node_id="decl://ns::pub")
        d = step.to_dict()
        assert d["id"] == "decl://ns::pub"

    def test_from_dict_edge(self) -> None:
        step = ProofStep.from_dict(
            {
                "type": "edge",
                "kind": "DECL_CALLS_DECL",
                "role": "call",
                "confidence": "high",
            }
        )
        assert step.step_type == "edge"
        assert step.kind == "DECL_CALLS_DECL"
        assert step.role == "call"
        assert step.confidence == "high"

    def test_node_falls_back_to_id_when_label_absent(self) -> None:
        step = ProofStep.from_dict({"type": "node", "id": "decl://pub"})
        assert step.label == "decl://pub"

    def test_to_dict_omits_unset_fields(self) -> None:
        step = ProofStep(step_type="node", label="pub")
        d = step.to_dict()
        assert d == {"type": "node", "label": "pub"}


class TestGraphProofPath:
    def test_to_dict_minimal(self) -> None:
        path = GraphProofPath(target="ns::internal::helper")
        assert path.to_dict() == {"target": "ns::internal::helper"}

    def test_to_dict_full(self) -> None:
        step = ProofStep(step_type="node", label="pub", kind="source_decl")
        path = GraphProofPath(
            target="ns::internal::helper",
            root="pub",
            is_direct=True,
            steps=(step,),
            prose="fn:pub → helper",
        )
        d = path.to_dict()
        assert d["root"] == "pub"
        assert d["is_direct"] is True
        assert d["steps"] == [step.to_dict()]
        assert d["prose"] == "fn:pub → helper"


class TestFindingDecision:
    def test_default_is_kept_with_no_extras(self) -> None:
        assert FindingDecision().to_dict() == {"state": "kept"}

    def test_suppressed_with_verdict_override(self) -> None:
        decision = FindingDecision(
            state="suppressed", reason_code="pattern_x", verdict_override="compatible"
        )
        d = decision.to_dict()
        assert d["state"] == "suppressed"
        assert d["reason_code"] == "pattern_x"
        assert d["verdict_override"] == "compatible"
        assert "suppression_rule" not in d


class TestImpactAssessmentHasSignal:
    def test_all_defaults_has_no_signal(self) -> None:
        assessment = ImpactAssessment()
        assert assessment.has_signal() is False

    def test_public_reachable_has_signal(self) -> None:
        assessment = ImpactAssessment(public_reachable=True)
        assert assessment.has_signal() is True

    def test_proven_unreachable_has_signal(self) -> None:
        assessment = ImpactAssessment(
            reachability_state=ReachabilityState.PROVEN_UNREACHABLE
        )
        assert assessment.has_signal() is True

    def test_proof_path_has_signal(self) -> None:
        assessment = ImpactAssessment(
            proof_path=GraphProofPath(target="ns::internal::helper")
        )
        assert assessment.has_signal() is True

    def test_verdict_override_has_signal(self) -> None:
        assessment = ImpactAssessment(
            decision=FindingDecision(verdict_override="compatible")
        )
        assert assessment.has_signal() is True

    def test_suppressed_state_has_signal(self) -> None:
        """A suppressed finding with no proof path/reachability/confidence/
        modulation metadata still has a non-default decision.state
        ("suppressed") -- impact_assessment is the only object carrying that
        decision, so omitting it here would silently drop the one thing this
        assessment had to say (Codex review)."""
        assessment = ImpactAssessment(decision=FindingDecision(state="suppressed"))
        assert assessment.has_signal() is True

    def test_non_high_confidence_has_signal(self) -> None:
        """A finding whose only non-default impact field is a reduced
        confidence (e.g. the vtable/RTTI layout findings in
        diff_elf_layout.py, which set MEDIUM with no reachability/proof
        metadata) must still surface impact_assessment -- otherwise the
        advertised per-finding confidence is silently never serialized
        (Codex review)."""
        assert ImpactAssessment(confidence=Confidence.MEDIUM).has_signal() is True
        assert ImpactAssessment(confidence=Confidence.LOW).has_signal() is True
        assert ImpactAssessment(confidence=Confidence.HIGH).has_signal() is False

    def test_to_dict_shape(self) -> None:
        assessment = ImpactAssessment(
            reachability_state=ReachabilityState.PROVEN_REACHABLE,
            public_reachable=True,
            reachability_kind="direct_public_symbol",
            confidence=Confidence.HIGH,
            proof_path=GraphProofPath(target="x", prose="fn:pub → x"),
            decision=FindingDecision(),
            evidence_category="build_context",
            correlated_change_kind="inline_body_changed",
        )
        d = assessment.to_dict()
        assert d["reachability_state"] == "reachable"
        assert d["public_reachable"] is True
        assert d["reachability_kind"] == "direct_public_symbol"
        assert d["confidence"] == "high"
        assert d["decision"] == {"state": "kept"}
        assert d["proof_path"] == {"target": "x", "prose": "fn:pub → x"}
        assert d["evidence_category"] == "build_context"
        assert d["correlated_change_kind"] == "inline_body_changed"


class TestAssessChange:
    def test_derives_from_change_defaults(self) -> None:
        change = _change()
        assessment = assess_change(change)
        assert assessment.reachability_state == ReachabilityState.UNKNOWN
        assert assessment.public_reachable is False
        assert assessment.proof_path is None
        assert assessment.decision.state == "kept"
        assert assessment.has_signal() is False

    def test_proven_unreachable_distinguishable_from_unknown(self) -> None:
        """The gap ADR-052 fixes: two changes both leave public_reachable
        False, but one was proven unreachable and one was never examined --
        assess_change must keep those apart."""
        unreachable = _change(reachability_state=ReachabilityState.PROVEN_UNREACHABLE)
        unknown = _change(reachability_state=ReachabilityState.UNKNOWN)
        assert (
            assess_change(unreachable).reachability_state
            != assess_change(unknown).reachability_state
        )
        assert assess_change(unreachable).has_signal() is True
        assert assess_change(unknown).has_signal() is False

    def test_public_reachable_change_carries_kind_and_prose(self) -> None:
        change = _change(
            public_reachable=True,
            reachability_kind="value_embedding",
            reachability_proof_path="fn:pub → base:detail::Base",
            reachability_state=ReachabilityState.PROVEN_REACHABLE,
        )
        assessment = assess_change(change)
        assert assessment.public_reachable is True
        assert assessment.reachability_kind == "value_embedding"
        assert assessment.proof_path is not None
        assert assessment.proof_path.prose == "fn:pub → base:detail::Base"
        assert assessment.proof_path.target == change.symbol

    def test_structured_proof_path_becomes_typed_steps(self) -> None:
        change = _change(
            affected_public_roots=["pub"],
            impact_proof_path=[
                {
                    "type": "node",
                    "id": "decl://pub",
                    "kind": "source_decl",
                    "label": "pub",
                },
                {"type": "edge", "kind": "DECL_CALLS_DECL", "role": "call"},
                {
                    "type": "node",
                    "id": "decl://helper",
                    "kind": "source_decl",
                    "label": "helper",
                },
            ],
            impact_is_direct=True,
        )
        assessment = assess_change(change)
        assert assessment.proof_path is not None
        assert assessment.proof_path.root == "pub"
        assert assessment.proof_path.is_direct is True
        assert len(assessment.proof_path.steps) == 3
        assert assessment.proof_path.steps[0].step_type == "node"
        assert assessment.proof_path.steps[0].node_id == "decl://pub"
        assert assessment.proof_path.steps[1].step_type == "edge"
        assert assessment.proof_path.steps[1].kind == "DECL_CALLS_DECL"
        assert assessment.proof_path.steps[2].node_id == "decl://helper"
        assert assessment.proof_path.target == "helper"

    def test_alternative_path_is_direct_counts_edge_hops_not_raw_steps(
        self,
    ) -> None:
        """CodeRabbit review: structured_proof_path's shape is
        node, edge, node, ... -- a single-hop (direct) path already has 3
        raw entries (one edge, two nodes), so is_direct must count edge
        steps, not len(raw_steps), or every direct alternative would be
        mislabeled transitive."""
        direct_alt = [
            {"type": "node", "id": "decl://pub", "label": "pub"},
            {"type": "edge", "kind": "DECL_CALLS_DECL"},
            {"type": "node", "id": "decl://direct", "label": "direct"},
        ]
        transitive_alt = [
            {"type": "node", "id": "decl://pub", "label": "pub"},
            {"type": "edge", "kind": "DECL_CALLS_DECL"},
            {"type": "node", "id": "decl://mid", "label": "mid"},
            {"type": "edge", "kind": "DECL_CALLS_DECL"},
            {"type": "node", "id": "decl://leaf", "label": "leaf"},
        ]
        change = _change(
            affected_public_roots=["pub"],
            impact_proof_path=direct_alt,
            impact_is_direct=True,
            impact_alternative_paths=[direct_alt, transitive_alt],
        )
        assessment = assess_change(change)
        assert assessment.proof_path is not None
        alternatives = assessment.proof_path.alternative_paths
        assert len(alternatives) == 2
        assert alternatives[0].is_direct is True
        assert alternatives[1].is_direct is False

    def test_target_derives_from_last_node_not_symbol_when_symbol_is_the_root(
        self,
    ) -> None:
        """Mirrors PUBLIC_API_INTERNAL_DEPENDENCY_ADDED
        (source_graph_findings._internal_dependency_findings): Change.symbol
        is set to the *public entry* label, identical to
        affected_public_roots[0], while the actually-affected internal
        entity is the last node of the structured path. target must not
        collapse onto root just because symbol == root (Codex review)."""
        change = _change(
            symbol="pub",
            affected_public_roots=["pub"],
            impact_proof_path=[
                {"type": "node", "id": "decl://pub", "label": "pub"},
                {"type": "edge", "kind": "DECL_REFERENCES_DECL"},
                {"type": "node", "id": "type://Internal", "label": "ns::Internal"},
            ],
            impact_is_direct=False,
        )
        assessment = assess_change(change)
        assert assessment.proof_path is not None
        assert assessment.proof_path.root == "pub"
        assert assessment.proof_path.target == "ns::Internal"
        assert assessment.proof_path.target != assessment.proof_path.root

    def test_suppressed_flag_sets_decision_state(self) -> None:
        change = _change()
        assessment = assess_change(change, suppressed=True)
        assert assessment.decision.state == "suppressed"
        assert assessment.has_signal() is True

    def test_suppression_rule_read_from_change(self) -> None:
        """G29 Phase 3 slice 2 (ADR-052 follow-up): Change.suppression_rule
        (set by checker.py/post_processing.py at suppression time) flows
        into FindingDecision.suppression_rule -- the piece slice 1 left
        unwired."""
        change = _change(suppression_rule="workaround-123")
        assessment = assess_change(change, suppressed=True)
        assert assessment.decision.suppression_rule == "workaround-123"

    def test_suppression_rule_none_for_kept_change(self) -> None:
        change = _change()
        assessment = assess_change(change)
        assert assessment.decision.suppression_rule is None

    def test_modulation_and_verdict_override_carried_into_decision(self) -> None:
        change = _change(
            modulation_reason="idiom_pattern_matched",
            modulation_rule="rule-1",
            effective_verdict=Verdict.COMPATIBLE,
        )
        assessment = assess_change(change)
        assert assessment.decision.reason_code == "idiom_pattern_matched"
        assert assessment.decision.verdict_override == "COMPATIBLE"
        assert assessment.has_signal() is True

    def test_verdict_override_also_carries_escalations_not_just_demotions(
        self,
    ) -> None:
        """effective_verdict can *raise* a finding's category too (e.g.
        STDLIB_IMPLEMENTATION_CHANGED promoted to BREAKING when layout
        evidence proves public std:: embedding) -- the field name must not
        imply every override is a downgrade (Codex review)."""
        change = _change(effective_verdict=Verdict.BREAKING)
        assessment = assess_change(change)
        assert assessment.decision.verdict_override == "BREAKING"

    def test_evidence_category_and_correlated_kind_pass_through(self) -> None:
        change = _change(
            evidence_category="source_only",
            correlated_change_kind="inline_body_changed",
        )
        assessment = assess_change(change)
        assert assessment.evidence_category == "source_only"
        assert assessment.correlated_change_kind == "inline_body_changed"
        assert assessment.has_signal() is True

    def test_duck_typed_object_without_change_fields(self) -> None:
        """assess_change must not blow up on an object that doesn't carry
        every Change field (mirrors _change_to_dict's own `c: object` duck
        typing in reporter.py)."""

        class Bare:
            symbol = "x"

        assessment = assess_change(Bare())
        assert assessment.reachability_state == ReachabilityState.UNKNOWN
        assert assessment.has_signal() is False


class TestAssessChangeRootCauseEvidence:
    """G29 Phase 6 follow-up: wiring RootCauseCorrelator's output into
    ImpactAssessment.root_cause_evidence."""

    def test_none_by_default(self) -> None:
        change = _change()
        assessment = assess_change(change, root_cause=("id", "display"))
        assert assessment.root_cause_evidence is None
        assert "root_cause_evidence" not in assessment.to_dict()

    def test_evidence_dict_surfaces_in_to_dict(self) -> None:
        change = _change()
        evidence = {
            "evidence_level": "artifact_proven",
            "strongest_evidence_level": "consumer_proven",
            "evidence_levels": ["artifact_proven", "consumer_proven"],
        }
        assessment = assess_change(
            change, root_cause=("id", "display"), root_cause_evidence=evidence
        )
        assert assessment.root_cause_evidence == evidence
        d = assessment.to_dict()
        assert d["root_cause_evidence"] == evidence
        # to_dict copies rather than aliasing the caller's dict.
        assert d["root_cause_evidence"] is not evidence

    def test_evidence_alone_makes_has_signal_true(self) -> None:
        change = _change()
        assessment = assess_change(
            change,
            root_cause_evidence={"evidence_level": "artifact_proven"},
        )
        assert assessment.has_signal() is True

    def test_root_cause_evidence_not_read_from_cache(self) -> None:
        cached = ImpactAssessment(root_cause_evidence={"evidence_level": "stale"})
        change = _change(impact_assessment=cached)
        assessment = assess_change(
            change, root_cause_evidence={"evidence_level": "fresh"}
        )
        assert assessment.root_cause_evidence == {"evidence_level": "fresh"}


class TestAssessChangeWithCachedImpactAssessment:
    """ADR-052 D2 follow-up (G29 Phase 3, scoped implementation):
    Change.impact_assessment, when a producer set it directly, supplies
    assess_change's *evidence* fields instead of re-deriving them -- but
    decision/root_cause_id are always recomputed fresh regardless."""

    def test_cached_evidence_is_reused_verbatim(self) -> None:
        cached = ImpactAssessment(
            reachability_state=ReachabilityState.PROVEN_REACHABLE,
            public_reachable=True,
            reachability_kind="value_embedding",
            confidence=Confidence.HIGH,
            proof_path=GraphProofPath(target="ns::internal::Helper", prose="fn:pub"),
            evidence_category="source_only",
            correlated_change_kind="inline_body_changed",
        )
        change = _change(impact_assessment=cached)
        assessment = assess_change(change)
        assert assessment.reachability_state == cached.reachability_state
        assert assessment.public_reachable == cached.public_reachable
        assert assessment.reachability_kind == cached.reachability_kind
        assert assessment.confidence == cached.confidence
        assert assessment.proof_path == cached.proof_path
        assert assessment.evidence_category == cached.evidence_category
        assert assessment.correlated_change_kind == cached.correlated_change_kind

    def test_decision_is_always_recomputed_not_read_from_cache(self) -> None:
        """The cached object's own decision (built when the producer
        constructed it, before suppression/modulation ran) must never leak
        through -- assess_change recomputes decision fresh from the
        Change's *current* flat fields every time, since those can change
        after construction."""
        cached = ImpactAssessment(
            public_reachable=True,
            reachability_state=ReachabilityState.PROVEN_REACHABLE,
            decision=FindingDecision(state="kept"),
        )
        # Flat fields mutated *after* the cached assessment was built --
        # e.g. a later suppression/modulation pass.
        change = _change(
            impact_assessment=cached,
            modulation_reason="idiom_pattern_matched",
            effective_verdict=Verdict.COMPATIBLE,
            suppression_rule="workaround-123",
        )
        assessment = assess_change(change, suppressed=True)
        assert assessment.decision.state == "suppressed"
        assert assessment.decision.reason_code == "idiom_pattern_matched"
        assert assessment.decision.verdict_override == "COMPATIBLE"
        assert assessment.decision.suppression_rule == "workaround-123"
        # The cached evidence is still reused untouched.
        assert assessment.public_reachable is True
        assert assessment.reachability_state == ReachabilityState.PROVEN_REACHABLE

    def test_root_cause_is_always_recomputed_not_read_from_cache(self) -> None:
        cached = ImpactAssessment(
            root_cause_id="stale-id", root_cause_display="stale-display",
            impact_group_id="stale-id",
        )
        change = _change(impact_assessment=cached)
        assessment = assess_change(change, root_cause=("fresh-id", "fresh-display"))
        assert assessment.root_cause_id == "fresh-id"
        assert assessment.root_cause_display == "fresh-display"
        assert assessment.impact_group_id == "fresh-id"

    def test_matches_uncached_derivation_for_equivalent_flat_fields(self) -> None:
        """A cached assessment built from the same flat-field values an
        on-demand derivation would use must produce an identical result --
        the two code paths must never disagree for equivalent input."""
        flat_fields: dict[str, object] = {
            "public_reachable": True,
            "reachability_kind": "value_embedding",
            "reachability_proof_path": "fn:pub → base:detail::Base",
            "reachability_state": ReachabilityState.PROVEN_REACHABLE,
        }
        uncached = assess_change(_change(**flat_fields))
        cached = assess_change(
            _change(impact_assessment=uncached, **flat_fields)
        )
        assert cached == uncached

    def test_none_cached_falls_back_to_derivation(self) -> None:
        change = _change(
            impact_assessment=None,
            public_reachable=True,
            reachability_state=ReachabilityState.PROVEN_REACHABLE,
        )
        assessment = assess_change(change)
        assert assessment.public_reachable is True


class TestReporterIntegration:
    """Codex review: two production call sites this slice initially missed --
    --report-mode leaf's own _leaf_entry() builds its dict independently of
    _change_to_dict, and _add_suppression()'s suppressed_changes list was
    never routed through assess_change(suppressed=True) at all, so the
    advertised decision.state == "suppressed" was unreachable in practice."""

    def test_leaf_mode_type_change_carries_reachability_state(self) -> None:
        change = _change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::internal::Foo",
            old_value="4",
            new_value="8",
            reachability_state=ReachabilityState.PROVEN_UNREACHABLE,
        )
        result = DiffResult(
            old_version="1.0", new_version="2.0", library="libfoo.so", changes=[change]
        )
        payload = json.loads(reporter.to_json(result, report_mode="leaf"))
        entry = payload["leaf_changes"][0]
        assert entry["reachability_state"] == "unreachable"
        assert entry["impact_assessment"]["reachability_state"] == "unreachable"

    def test_suppressed_changes_carry_suppressed_decision_state(self) -> None:
        change = _change()
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            suppressed_changes=[change],
            suppressed_count=1,
        )
        payload = json.loads(reporter.to_json(result))
        entry = payload["suppression"]["suppressed_changes"][0]
        assert entry["reachability_state"] == "unknown"
        assert entry["impact_assessment"]["decision"]["state"] == "suppressed"

    def test_suppressed_changes_carry_suppression_rule_label(self) -> None:
        """G29 Phase 3 slice 2 (ADR-052 follow-up): a suppressed change
        already carrying Change.suppression_rule (set by checker.py/
        post_processing.py at suppression time) surfaces it in
        impact_assessment.decision.suppression_rule end to end."""
        change = _change(suppression_rule="workaround-123")
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            suppressed_changes=[change],
            suppressed_count=1,
        )
        payload = json.loads(reporter.to_json(result))
        entry = payload["suppression"]["suppressed_changes"][0]
        assert entry["impact_assessment"]["decision"]["suppression_rule"] == (
            "workaround-123"
        )


class TestRootCauseEvidenceReporterIntegration:
    """G29 Phase 6 follow-up: RootCauseCorrelator's evidence-ranked groups,
    end to end through reporter.py's JSON output."""

    def _correlated_pair(self) -> tuple[Change, Change]:
        removed = _change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="internal_helper",
            description="internal_helper removed",
        )
        leaked = _change(
            kind=ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
            symbol="internal_helper",
            description="internal_helper required by public entry",
            caused_by_type="internal_helper",
        )
        return removed, leaked

    def test_default_mode_carries_root_cause_evidence(self) -> None:
        removed, leaked = self._correlated_pair()
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            changes=[removed, leaked],
        )
        payload = json.loads(reporter.to_json(result))
        evidences = [
            c["impact_assessment"]["root_cause_evidence"] for c in payload["changes"]
        ]
        assert len(evidences) == 2
        for evidence in evidences:
            assert evidence["strongest_evidence_level"] == "call_graph_proven"
            assert evidence["evidence_levels"] == [
                "artifact_proven",
                "call_graph_proven",
            ]
        assert {e["evidence_level"] for e in evidences} == {
            "artifact_proven",
            "call_graph_proven",
        }

    def test_root_cause_mode_groups_carry_evidence_summary(self) -> None:
        removed, leaked = self._correlated_pair()
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            changes=[removed, leaked],
        )
        payload = json.loads(reporter.to_json(result, report_mode="root-cause"))
        assert len(payload["root_causes"]) == 1
        group = payload["root_causes"][0]
        assert group["strongest_evidence_level"] == "call_graph_proven"
        assert group["evidence_levels"] == ["artifact_proven", "call_graph_proven"]

    def test_uncorrelated_finding_has_no_root_cause_evidence(self) -> None:
        change = _change(kind=ChangeKind.FUNC_PARAMS_CHANGED, symbol="foo")
        result = DiffResult(
            old_version="1.0", new_version="2.0", library="libfoo.so", changes=[change]
        )
        payload = json.loads(reporter.to_json(result))
        entry = payload["changes"][0]
        assessment = entry.get("impact_assessment", {})
        assert "root_cause_evidence" not in assessment
