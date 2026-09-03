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
    SurfaceConfig,
    ValueProvenance,
)
from abicheck.compile_context import CompileContext
from abicheck.contract_relevance_types import ContractMode, SelectorLayer
from abicheck.workflows.plan import AnalysisPlan, SidePlan
from abicheck.workflows.resolved_execution_context import ResolvedExecutionContext


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
            operation="compare", requested_depth="headers", evaluation_config=cfg
        )
        b = ResolvedExecutionContext(
            operation="compare", requested_depth="source", evaluation_config=cfg
        )
        assert a.resolution_digest() != b.resolution_digest()

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

    def test_independent_of_provenance_mapping_insertion_order(self):
        """Codex review, PR #1027: `CompatibilityEvaluationConfig.provenance`
        is a `Mapping`, and dataclass equality already ignores its insertion
        order -- so two configs a resolver would treat as equal (the same
        entries, assembled in a different order by a different front end)
        must not hash differently."""
        prov_a = ValueProvenance(
            layer=SelectorLayer.EXPLICIT_CLI, source_kind="cli_flag"
        )
        prov_b = ValueProvenance(
            layer=SelectorLayer.PROJECT_CONFIG, source_kind="config_file"
        )
        cfg_ab = _evaluation_config(
            provenance={"contract.mode": prov_a, "gate.exit_code_scheme": prov_b}
        )
        cfg_ba = _evaluation_config(
            provenance={"gate.exit_code_scheme": prov_b, "contract.mode": prov_a}
        )
        assert cfg_ab == cfg_ba  # sanity: the dataclass itself already agrees
        a = ResolvedExecutionContext(operation="compare", evaluation_config=cfg_ab)
        b = ResolvedExecutionContext(operation="compare", evaluation_config=cfg_ba)
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
        only equal mappings should hash equal."""
        prov = ValueProvenance(layer=SelectorLayer.EXPLICIT_CLI, source_kind="cli_flag")
        cfg_with = _evaluation_config(provenance={"contract.mode": prov})
        cfg_without = _evaluation_config(provenance={})
        a = ResolvedExecutionContext(operation="compare", evaluation_config=cfg_with)
        b = ResolvedExecutionContext(operation="compare", evaluation_config=cfg_without)
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
