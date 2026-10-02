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
way ``model/semantic_ir.py`` was pinned before any consumer migrated onto it.
``service_compare_pipeline.resolve_compare_request`` is now the first real
call site (see ``TestResolvedExecutionContextWiring`` in
``tests/test_service_compare_pipeline.py``); these tests still cover the
primitive directly, unit-style, rather than only through that one caller.
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
    SurfaceConfig,
)
from abicheck.compile_context import CompileContext
from abicheck.contract_relevance_types import ContractMode
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


class TestEvidenceView:
    def test_bare_construction_defaults(self):
        evidence = EvidenceView()
        assert evidence.requested_depth is None
        assert evidence.effective_depth is None
        assert evidence.depth_satisfied is None

    def test_available_depths_is_the_public_depth_ladder(self):
        from abicheck.model.evidence_depth_levels import USER_DEPTHS

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

    def test_from_plan_lower_cases_a_case_insensitive_plan_depth(self):
        """A typed-API caller can spell a valid ``--depth`` value
        case-insensitively (e.g. ``"HEADERS"``) and ``AnalysisPlan`` itself
        does not normalize it -- ``service_compare_pipeline.classify_
        compare_pair`` only normalizes ``DiffResult.requested_depth`` later,
        after this context already exists. Without normalizing here too,
        the mixed-case value fails to appear in its own
        ``available_depths`` (the ladder is lower-case) and differs from an
        equivalent lower-case request (Codex review, PR #1031)."""
        plan = _plan(requested_depth="HEADERS")
        ctx = ResolvedExecutionContext.from_plan(plan)
        assert ctx.evidence.requested_depth == "headers"
        assert ctx.evidence.requested_depth in ctx.evidence.available_depths

        lower_ctx = ResolvedExecutionContext.from_plan(_plan(requested_depth="headers"))
        assert ctx.evidence == lower_ctx.evidence

    def test_from_plan_normalized_depth_is_the_from_assurance_fallback(self):
        """The lower-cased plan depth also feeds ``EvidenceView.from_assurance``'s
        fallback (used when *assurance* itself carries no ``requested_depth``,
        e.g. a ``not_comparable`` short-circuit) -- so that path can't
        reintroduce the same mixed-case leak through the back door."""
        from abicheck.analysis_assurance import AnalysisAssurance

        plan = _plan(requested_depth="HEADERS")
        assurance = AnalysisAssurance(requested_depth=None, effective_depth=None)
        ctx = ResolvedExecutionContext.from_plan(plan, assurance=assurance)
        assert ctx.evidence.requested_depth == "headers"

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
