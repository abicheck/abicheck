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

"""Primitive-level tests for :mod:`abicheck.workflows.resolved_execution_context`
-- ``one-semantic-pipeline.md``'s plan, "PR 1". Pins the type's shape and its
composition-only contract directly (construct a few contexts by hand), the
way ``model/semantic_ir.py`` was pinned before any consumer migrated onto it
-- this module has no live caller yet, so these tests are the only thing
guarding its shape.
"""

from __future__ import annotations

import pytest

from abicheck.compatibility_evaluation_config import (
    AssuranceConfig,
    CompatibilityEvaluationConfig,
    CompatibilityPolicyConfig,
    ContractConfig,
    EvidenceConfig,
    GateConfig,
    ImmutableIdentity,
    ScopedGateSelection,
    SurfaceConfig,
    ValueProvenance,
)
from abicheck.compile_context import CompileContext
from abicheck.contract_relevance_types import ContractMode, SelectorLayer
from abicheck.workflows.plan import AnalysisPlan, SidePlan
from abicheck.workflows.resolved_execution_context import (
    EvidenceView,
    ResolvedExecutionContext,
)


def _identity(identity_id: str = "strict_abi") -> ImmutableIdentity:
    return ImmutableIdentity(id=identity_id, version=1, sha256="digest")


def _evaluation_config(**overrides) -> CompatibilityEvaluationConfig:
    fields = dict(
        contract=ContractConfig(mode=ContractMode.PUBLIC),
        evidence=EvidenceConfig(),
        surface=SurfaceConfig(),
        assurance=AssuranceConfig(),
        policy=CompatibilityPolicyConfig(base=_identity()),
        gate=GateConfig(),
    )
    fields.update(overrides)
    return CompatibilityEvaluationConfig(**fields)


def _plan(
    operation: str = "compare", requested_depth: str | None = "headers"
) -> AnalysisPlan:
    side = SidePlan(
        label="old",
        requested_depth=requested_depth,
        lang="c++",
        frontend="castxml",
        sources=None,
        build_info=None,
        build_targets=(),
        gcc_path=None,
    )
    return AnalysisPlan(
        operation=operation, requested_depth=requested_depth, sides=(side,)
    )


class TestConstruction:
    def test_bare_construction_defaults(self):
        ctx = ResolvedExecutionContext(operation="compare")
        assert ctx.operation == "compare"
        assert ctx.requested_depth is None
        assert ctx.evaluation_config is None
        assert dict(ctx.compile_contexts) == {}

    def test_compile_contexts_mapping_is_frozen(self):
        ctx = ResolvedExecutionContext(
            operation="compare", compile_contexts={"old": CompileContext()}
        )
        with pytest.raises(TypeError):
            ctx.compile_contexts["new"] = CompileContext()  # type: ignore[index]

    def test_mutating_the_source_dict_after_construction_does_not_leak_in(self):
        source = {"old": CompileContext()}
        ctx = ResolvedExecutionContext(operation="dump", compile_contexts=source)
        source["new"] = CompileContext(gcc_path="/usr/bin/gcc-13")
        assert set(ctx.compile_contexts) == {"old"}

    def test_dataclass_itself_is_frozen(self):
        ctx = ResolvedExecutionContext(operation="compare")
        with pytest.raises(
            Exception
        ):  # dataclasses.FrozenInstanceError is a TypeError/AttributeError
            ctx.operation = "scan"  # type: ignore[misc]


class TestFromPlan:
    def test_composes_operation_and_requested_depth_from_the_plan(self):
        plan = _plan(operation="dump", requested_depth="source")
        ctx = ResolvedExecutionContext.from_plan(plan)
        assert ctx.operation == "dump"
        assert ctx.requested_depth == "source"
        assert ctx.evaluation_config is None
        assert dict(ctx.compile_contexts) == {}

    def test_never_re_derives_requested_depth_from_the_plans_sides(self):
        """`AnalysisPlan.requested_depth` is the single top-level request; a
        per-side `SidePlan.requested_depth` can legitimately differ (or be
        absent) without `from_plan` trying to reconcile them -- it reads the
        plan's own top-level field verbatim, never the sides."""
        side = SidePlan(
            label="old",
            requested_depth="binary",  # deliberately disagrees with the plan
            lang="c++",
            frontend="castxml",
            sources=None,
            build_info=None,
            build_targets=(),
            gcc_path=None,
        )
        plan = AnalysisPlan(
            operation="compare", requested_depth="headers", sides=(side,)
        )
        ctx = ResolvedExecutionContext.from_plan(plan)
        assert ctx.requested_depth == "headers"

    def test_accepts_an_evaluation_config_and_compile_contexts_alongside_the_plan(self):
        plan = _plan()
        cfg = _evaluation_config()
        compile_contexts = {
            "old": CompileContext(gcc_path="/usr/bin/gcc"),
            "new": CompileContext(),
        }
        ctx = ResolvedExecutionContext.from_plan(
            plan, evaluation_config=cfg, compile_contexts=compile_contexts
        )
        assert ctx.evaluation_config is cfg
        assert dict(ctx.compile_contexts) == compile_contexts


class TestProvenanceFor:
    def test_none_when_no_evaluation_config_resolved(self):
        ctx = ResolvedExecutionContext(operation="compare")
        assert ctx.provenance_for("contract.mode") is None

    def test_none_when_field_has_no_recorded_provenance(self):
        ctx = ResolvedExecutionContext(
            operation="compare", evaluation_config=_evaluation_config()
        )
        assert ctx.provenance_for("contract.mode") is None

    def test_delegates_to_the_evaluation_configs_own_provenance_map(self):
        prov = ValueProvenance(layer=SelectorLayer.EXPLICIT_CLI, source_kind="cli_flag")
        cfg = _evaluation_config(provenance={"contract.mode": prov})
        ctx = ResolvedExecutionContext(operation="compare", evaluation_config=cfg)
        assert ctx.provenance_for("contract.mode") is prov


class TestResolutionDigest:
    def test_deterministic_for_equal_inputs(self):
        plan = _plan()
        contexts = {"old": CompileContext(gcc_path="/usr/bin/gcc")}
        a = ResolvedExecutionContext.from_plan(
            plan,
            evaluation_config=_evaluation_config(),
            compile_contexts=dict(contexts),
        )
        b = ResolvedExecutionContext.from_plan(
            plan,
            evaluation_config=_evaluation_config(),
            compile_contexts=dict(contexts),
        )
        assert a.resolution_digest() == b.resolution_digest()

    def test_changes_when_requested_depth_changes(self):
        cfg = _evaluation_config()
        a = ResolvedExecutionContext(
            operation="compare",
            evidence=EvidenceView.for_request("headers"),
            evaluation_config=cfg,
        )
        b = ResolvedExecutionContext(
            operation="compare",
            evidence=EvidenceView.for_request("source"),
            evaluation_config=cfg,
        )
        assert a.resolution_digest() != b.resolution_digest()

    def test_distinguishes_an_empty_string_requested_depth_from_none(self):
        """Codex review, PR #1027, sixth round: neither `EvidenceView` nor
        `AnalysisPlan` rejects an empty-string depth, so
        `requested_depth=""` is a real, distinct, constructible state from
        `requested_depth=None` -- a bare `or ""` fallback previously
        collapsed both onto the identical digest input."""
        cfg = _evaluation_config()
        empty_string = ResolvedExecutionContext(
            operation="compare",
            evidence=EvidenceView.for_request(""),
            evaluation_config=cfg,
        )
        none = ResolvedExecutionContext(
            operation="compare",
            evidence=EvidenceView.for_request(None),
            evaluation_config=cfg,
        )
        assert empty_string.evidence.requested_depth == ""
        assert none.evidence.requested_depth is None
        assert empty_string.resolution_digest() != none.resolution_digest()

    def test_scoped_gate_targets_order_does_not_affect_the_digest(self):
        """Codex review, PR #1027, seventh round: `ScopedGateSelection`'s
        own docstring says its `targets` order is preserved in the
        *stored* object but must be sorted by a digest consumer (the same
        way `effective_config_digest._gate_scope_str()` already does) --
        two runs selecting the same `--used-by` apps in a different
        argument order must hash identically."""
        cfg_ab = _evaluation_config(
            gate=GateConfig(
                scope=ScopedGateSelection(kind="used_by", targets=("app_a", "app_b"))
            )
        )
        cfg_ba = _evaluation_config(
            gate=GateConfig(
                scope=ScopedGateSelection(kind="used_by", targets=("app_b", "app_a"))
            )
        )
        a = ResolvedExecutionContext(operation="compare", evaluation_config=cfg_ab)
        b = ResolvedExecutionContext(operation="compare", evaluation_config=cfg_ba)
        assert a.resolution_digest() == b.resolution_digest()

    def test_scoped_gate_targets_content_difference_still_changes_the_digest(self):
        cfg_with = _evaluation_config(
            gate=GateConfig(
                scope=ScopedGateSelection(kind="used_by", targets=("app_a",))
            )
        )
        cfg_without = _evaluation_config(gate=GateConfig(scope=None))
        a = ResolvedExecutionContext(operation="compare", evaluation_config=cfg_with)
        b = ResolvedExecutionContext(operation="compare", evaluation_config=cfg_without)
        assert a.resolution_digest() != b.resolution_digest()

    def test_unaffected_by_effective_depth_alone(self):
        """`resolution_digest()` fingerprints the resolved *input*
        (`evidence.requested_depth`), never the post-execution
        `effective_depth`/`depth_satisfied` -- those are outcomes, not
        inputs (see module docstring)."""
        cfg = _evaluation_config()
        a = ResolvedExecutionContext(
            operation="compare",
            evidence=EvidenceView(requested_depth="headers", effective_depth="headers"),
            evaluation_config=cfg,
        )
        b = ResolvedExecutionContext(
            operation="compare",
            evidence=EvidenceView(requested_depth="headers", effective_depth="binary"),
            evaluation_config=cfg,
        )
        assert a.resolution_digest() == b.resolution_digest()

    def test_changes_when_evaluation_config_changes(self):
        a = ResolvedExecutionContext(
            operation="compare", evaluation_config=_evaluation_config()
        )
        b = ResolvedExecutionContext(
            operation="compare",
            evaluation_config=_evaluation_config(
                contract=ContractConfig(mode=ContractMode.EXPORTS)
            ),
        )
        assert a.resolution_digest() != b.resolution_digest()

    def test_changes_when_a_compile_context_changes(self):
        a = ResolvedExecutionContext(
            operation="dump",
            compile_contexts={"old": CompileContext(gcc_path="/usr/bin/gcc-12")},
        )
        b = ResolvedExecutionContext(
            operation="dump",
            compile_contexts={"old": CompileContext(gcc_path="/usr/bin/gcc-13")},
        )
        assert a.resolution_digest() != b.resolution_digest()

    def test_independent_of_compile_contexts_mapping_iteration_order(self):
        old_ctx = CompileContext(gcc_path="/usr/bin/gcc")
        new_ctx = CompileContext(gcc_path="/usr/bin/g++")
        a = ResolvedExecutionContext(
            operation="compare", compile_contexts={"old": old_ctx, "new": new_ctx}
        )
        b = ResolvedExecutionContext(
            operation="compare", compile_contexts={"new": new_ctx, "old": old_ctx}
        )
        assert a.resolution_digest() == b.resolution_digest()

    def test_independent_of_provenance_content_entirely(self):
        """Codex review, PR #1027, third round: `resolution_digest()`
        deliberately excludes `CompatibilityEvaluationConfig.provenance`
        altogether, not merely its insertion order -- the CLI and the typed
        Python API resolving the identical effective input legitimately
        produce different provenance (`SelectorLayer.EXPLICIT_CLI` vs.
        `API_REQUEST`, a different flag vs. field spelling in
        `source_kind`), and `cross_front_end_differences()` already treats
        that as no divergence at all. Two configs differing *only* in
        provenance -- one populated, one empty -- must hash identically."""
        prov = ValueProvenance(layer=SelectorLayer.EXPLICIT_CLI, source_kind="cli_flag")
        cfg_with_provenance = _evaluation_config(
            provenance={"contract.mode": prov, "gate.exit_code_scheme": prov}
        )
        cfg_without_provenance = _evaluation_config(provenance={})
        assert cfg_with_provenance != cfg_without_provenance  # sanity: genuinely differ
        a = ResolvedExecutionContext(
            operation="compare", evaluation_config=cfg_with_provenance
        )
        b = ResolvedExecutionContext(
            operation="compare", evaluation_config=cfg_without_provenance
        )
        assert a.resolution_digest() == b.resolution_digest()

    def test_independent_of_policy_overrides_mapping_insertion_order(self):
        """Same gap, the other `Mapping`-typed field
        (`CompatibilityPolicyConfig.overrides`)."""
        from abicheck.change_registry_types import Verdict

        overrides_ab = {
            "func_removed": Verdict.BREAKING,
            "func_added": Verdict.COMPATIBLE,
        }
        overrides_ba = {
            "func_added": Verdict.COMPATIBLE,
            "func_removed": Verdict.BREAKING,
        }
        cfg_ab = _evaluation_config(
            policy=CompatibilityPolicyConfig(base=_identity(), overrides=overrides_ab)
        )
        cfg_ba = _evaluation_config(
            policy=CompatibilityPolicyConfig(base=_identity(), overrides=overrides_ba)
        )
        assert cfg_ab == cfg_ba
        a = ResolvedExecutionContext(operation="compare", evaluation_config=cfg_ab)
        b = ResolvedExecutionContext(operation="compare", evaluation_config=cfg_ba)
        assert a.resolution_digest() == b.resolution_digest()

    def test_compile_context_labels_are_encoded_injectively(self):
        """Codex review, PR #1027, second round: a hand-rolled
        ``"\\x1f".join(f"{label}={value!r}" for ...)`` encoding is not
        injective when *label* is caller-supplied and unrestricted -- a
        crafted single-entry mapping whose one label itself contains the
        join delimiter and an ``=``-joined encoding of a real two-entry
        mapping's first part can reproduce that mapping's own joined
        string byte-for-byte. Confirms the two distinct mappings this
        exact construction identifies no longer collide."""
        from abicheck.workflows.resolved_execution_context import _canonical_repr

        c1 = CompileContext(gcc_path="/usr/bin/gcc")
        c2 = CompileContext(gcc_path="/usr/bin/g++")
        two_entries = ResolvedExecutionContext(
            operation="compare", compile_contexts={"a": c1, "b": c2}
        )
        crafted_label = "a=" + _canonical_repr(c1) + "\x1fb"
        one_entry = ResolvedExecutionContext(
            operation="compare", compile_contexts={crafted_label: c2}
        )
        assert two_entries.resolution_digest() != one_entry.resolution_digest()

    def test_still_changes_when_a_mappings_content_actually_differs(self):
        """The order-independence fix must not collapse a real difference --
        only equal mappings should hash equal. Uses `policy.overrides` (a
        resolved *value*, unlike `provenance`, which the digest now
        deliberately excludes -- see
        `test_independent_of_provenance_content_entirely`)."""
        from abicheck.change_registry_types import Verdict

        cfg_with_override = _evaluation_config(
            policy=CompatibilityPolicyConfig(
                base=_identity(), overrides={"func_removed": Verdict.BREAKING}
            )
        )
        cfg_without_override = _evaluation_config(
            policy=CompatibilityPolicyConfig(base=_identity(), overrides={})
        )
        a = ResolvedExecutionContext(
            operation="compare", evaluation_config=cfg_with_override
        )
        b = ResolvedExecutionContext(
            operation="compare", evaluation_config=cfg_without_override
        )
        assert a.resolution_digest() != b.resolution_digest()

    def test_does_not_collide_a_shared_config_across_different_operations(self):
        """Not a `CompatibilityEvaluationConfig`-only fingerprint -- the
        composed object's other fields (here, `operation`) must genuinely
        participate, or two different runs sharing a resolved config would
        be indistinguishable."""
        cfg = _evaluation_config()
        a = ResolvedExecutionContext(operation="compare", evaluation_config=cfg)
        b = ResolvedExecutionContext(operation="scan", evaluation_config=cfg)
        assert a.resolution_digest() != b.resolution_digest()

    def test_is_a_sha256_prefixed_string_like_the_effective_config_digest(self):
        digest = ResolvedExecutionContext(operation="compare").resolution_digest()
        assert digest.startswith("sha256:")
        assert len(digest) == len("sha256:") + 64


class TestEvidenceView:
    def test_bare_construction_defaults(self):
        evidence = EvidenceView()
        assert evidence.requested_depth is None
        assert evidence.effective_depth is None
        assert evidence.depth_satisfied is None

    def test_available_depths_is_the_public_depth_ladder(self):
        from abicheck.buildsource.scan_levels import USER_DEPTHS

        evidence = EvidenceView()
        assert evidence.available_depths == tuple(d.value for d in USER_DEPTHS)
        assert evidence.available_depths == ("binary", "headers", "build", "source")

    def test_for_request_carries_only_the_requested_depth(self):
        evidence = EvidenceView.for_request("headers")
        assert evidence.requested_depth == "headers"
        assert evidence.effective_depth is None
        assert evidence.depth_satisfied is None

    def test_from_assurance_copies_the_real_analysis_assurance_verbatim(self):
        from abicheck.analysis_assurance import AnalysisAssurance

        assurance = AnalysisAssurance(
            requested_depth="headers", effective_depth="binary", depth_satisfied=False
        )
        evidence = EvidenceView.from_assurance(assurance)
        assert evidence.requested_depth == "headers"
        assert evidence.effective_depth == "binary"
        assert evidence.depth_satisfied is False

    def test_from_assurance_never_recomputes_never_recalculates_depth_satisfied(self):
        """A structurally-shaped stand-in (not the real `AnalysisAssurance`)
        still works -- `from_assurance` reads attributes via `getattr`, it
        never re-derives `depth_satisfied` from `requested_depth`/
        `effective_depth` itself (that would be a second, independently
        computed copy of `AnalysisAssurance`'s own logic)."""

        class _FakeAssurance:
            requested_depth = "source"
            effective_depth = "source"
            depth_satisfied = None  # deliberately not re-derived to True

        evidence = EvidenceView.from_assurance(_FakeAssurance())
        assert evidence.depth_satisfied is None

    def test_from_assurance_missing_attributes_degrade_to_none(self):
        evidence = EvidenceView.from_assurance(object())
        assert evidence.requested_depth is None
        assert evidence.effective_depth is None
        assert evidence.depth_satisfied is None

    def test_available_depths_cannot_be_overridden_via_the_constructor(self):
        """Codex review, PR #1027, fourth round: `available_depths` is a
        read-only property, not a constructor parameter -- passing it is a
        `TypeError`, not a silently-accepted competing value."""
        with pytest.raises(TypeError):
            EvidenceView(available_depths=("bogus",))  # type: ignore[call-arg]

    def test_from_assurance_falls_back_to_the_given_requested_depth_when_assurances_own_is_none(
        self,
    ):
        """Codex review, PR #1027, fourth round:
        `analysis_assurance.compute_analysis_assurance()`'s own
        `not_comparable` short-circuit returns a real `AnalysisAssurance`
        whose `requested_depth` is `None` even when a depth was genuinely
        requested -- the fallback must be used in exactly that case."""
        from abicheck.analysis_assurance import AnalysisAssurance

        not_comparable = AnalysisAssurance(status="not_comparable")
        assert (
            not_comparable.requested_depth is None
        )  # sanity: reproduces the real shape

        evidence = EvidenceView.from_assurance(
            not_comparable, requested_depth="headers"
        )
        assert evidence.requested_depth == "headers"
        assert evidence.effective_depth is None
        assert evidence.depth_satisfied is None

    def test_from_assurance_prefers_assurances_own_requested_depth_over_the_fallback(
        self,
    ):
        from abicheck.analysis_assurance import AnalysisAssurance

        assurance = AnalysisAssurance(requested_depth="build", effective_depth="build")
        evidence = EvidenceView.from_assurance(assurance, requested_depth="headers")
        assert evidence.requested_depth == "build"


class TestResolvedExecutionContextEvidenceIntegration:
    def test_requested_depth_property_reads_through_evidence(self):
        ctx = ResolvedExecutionContext(
            operation="compare", evidence=EvidenceView.for_request("build")
        )
        assert ctx.requested_depth == "build"
        assert ctx.evidence.requested_depth == "build"

    def test_from_plan_builds_a_requested_only_view_with_no_assurance(self):
        plan = _plan(requested_depth="source")
        ctx = ResolvedExecutionContext.from_plan(plan)
        assert ctx.evidence.requested_depth == "source"
        assert ctx.evidence.effective_depth is None

    def test_from_plan_with_assurance_builds_the_full_post_execution_view(self):
        from abicheck.analysis_assurance import AnalysisAssurance

        plan = _plan(requested_depth="source")
        assurance = AnalysisAssurance(
            requested_depth="source", effective_depth="build", depth_satisfied=False
        )
        ctx = ResolvedExecutionContext.from_plan(plan, assurance=assurance)
        assert ctx.evidence.requested_depth == "source"
        assert ctx.evidence.effective_depth == "build"
        assert ctx.evidence.depth_satisfied is False

    def test_with_assurance_returns_a_new_context_leaving_the_original_untouched(self):
        from abicheck.analysis_assurance import AnalysisAssurance

        plan = _plan(requested_depth="headers")
        original = ResolvedExecutionContext.from_plan(plan)
        assurance = AnalysisAssurance(
            requested_depth="headers", effective_depth="headers", depth_satisfied=True
        )
        updated = original.with_assurance(assurance)
        # The original, pre-execution context is untouched (frozen dataclass).
        assert original.evidence.effective_depth is None
        # The new one carries the full post-execution view.
        assert updated.evidence.effective_depth == "headers"
        assert updated.evidence.depth_satisfied is True
        assert updated is not original

    def test_with_assurance_preserves_every_other_field(self):
        plan = _plan(operation="dump", requested_depth="headers")
        cfg = _evaluation_config()
        contexts = {"old": CompileContext(gcc_path="/usr/bin/gcc")}
        original = ResolvedExecutionContext.from_plan(
            plan, evaluation_config=cfg, compile_contexts=contexts
        )
        updated = original.with_assurance(
            type(
                "_A",
                (),
                {
                    "requested_depth": "headers",
                    "effective_depth": "headers",
                    "depth_satisfied": True,
                },
            )()
        )
        assert updated.operation == original.operation
        assert updated.evaluation_config is original.evaluation_config
        assert dict(updated.compile_contexts) == dict(original.compile_contexts)

    def test_from_plan_with_a_not_comparable_assurance_preserves_the_requested_depth(
        self,
    ):
        """Codex review, PR #1027, fourth round: the exact real-world shape
        `compute_analysis_assurance()`'s own `not_comparable` short-circuit
        produces (`requested_depth=None`) must not erase the plan's own
        genuinely known requested depth."""
        from abicheck.analysis_assurance import AnalysisAssurance

        plan = _plan(requested_depth="headers")
        not_comparable = AnalysisAssurance(status="not_comparable")
        ctx = ResolvedExecutionContext.from_plan(plan, assurance=not_comparable)
        assert ctx.evidence.requested_depth == "headers"
        assert ctx.evidence.effective_depth is None

    def test_with_assurance_with_a_not_comparable_assurance_preserves_the_requested_depth(
        self,
    ):
        from abicheck.analysis_assurance import AnalysisAssurance

        plan = _plan(requested_depth="build")
        original = ResolvedExecutionContext.from_plan(plan)
        not_comparable = AnalysisAssurance(status="not_comparable")
        updated = original.with_assurance(not_comparable)
        assert updated.evidence.requested_depth == "build"
        assert updated.evidence.effective_depth is None

    def test_resolution_digest_unaffected_by_a_not_comparable_assurance(self):
        """The regression this whole fix guards against: attaching a
        `not_comparable` assurance must not change the resolved-input
        digest, since the requested depth it carries is unchanged."""
        from abicheck.analysis_assurance import AnalysisAssurance

        plan = _plan(requested_depth="headers")
        cfg = _evaluation_config()
        before = ResolvedExecutionContext.from_plan(plan, evaluation_config=cfg)
        after = before.with_assurance(AnalysisAssurance(status="not_comparable"))
        assert before.resolution_digest() == after.resolution_digest()
