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

"""Unit tests for :func:`abicheck.buildsource.run_plan.to_aggregate_manifest`
and the ``run-plan.json`` gate/schema contract (CLI cleanup phase two, PR 2).

Split out of ``test_run_plan.py`` (AI-readiness file-size cap) -- that file
already covers plan generation/round-tripping in depth; this file is scoped
to the aggregate-manifest projection and the gate-bearing schema
discriminator specifically.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.run_plan import RunPlan, RunPlanCheck, to_aggregate_manifest


class TestToAggregateManifest:
    def test_uses_check_id_not_bare_name(self) -> None:
        plan = RunPlan(
            checks=[
                RunPlanCheck(
                    check_id="libfoo@linux#release@headers",
                    name="libfoo",
                    required=True,
                ),
                RunPlanCheck(
                    check_id="libfoo@mac#release@headers",
                    name="libfoo",
                    required=False,
                ),
            ]
        )
        manifest = to_aggregate_manifest(plan)
        assert manifest["aggregate_manifest_version"] == "2.0"
        assert manifest["targets"] == [
            {"id": "libfoo@linux#release@headers", "required": True},
            {"id": "libfoo@mac#release@headers", "required": False},
        ]
        assert "head_sha" not in manifest

    def test_head_sha_comes_from_the_plan_unless_overridden(self) -> None:
        plan = RunPlan(head_sha="deadbeef", checks=[])
        assert to_aggregate_manifest(plan)["head_sha"] == "deadbeef"
        assert (
            to_aggregate_manifest(plan, head_sha="cafef00d")["head_sha"] == "cafef00d"
        )

    def test_produces_a_manifest_aggregate_itself_accepts(self) -> None:
        """Not just shape-compatible on paper -- feed it straight into
        aggregate.ExpectedTargets, the real reader."""
        from abicheck.aggregate import ExpectedTargets

        plan = RunPlan(
            checks=[
                RunPlanCheck(check_id="libfoo@linux#release@headers", required=True),
            ]
        )
        expected = ExpectedTargets.from_manifest_data(to_aggregate_manifest(plan))
        assert expected.targets == {"libfoo@linux#release@headers": True}

    def test_gate_policy_projects_into_the_manifest(self) -> None:
        """CLI cleanup phase two, PR 2: a plan's own gate policy (stamped by
        `generate_run_plan`'s `--gate-missing-required`/
        `--gate-unexpected-target`) rides inside run-plan.json and projects
        into `--run-plan`'s manifest the same way a hand-authored
        `--manifest`'s own `gate` block does."""
        from abicheck.aggregate import ExpectedTargets, OnMissingRequired

        plan = RunPlan(
            checks=[RunPlanCheck(check_id="libfoo@linux#release@headers")],
            gate_missing_required="warn",
            gate_unexpected_target="fail",
        )
        manifest = to_aggregate_manifest(plan)
        assert manifest["gate"] == {"missing_required": "warn", "unexpected_target": "fail"}
        expected = ExpectedTargets.from_manifest_data(manifest)
        assert expected.gate_missing_required is OnMissingRequired.WARN

    def test_no_gate_policy_omits_the_gate_key(self) -> None:
        plan = RunPlan(checks=[RunPlanCheck(check_id="libfoo@linux#release@headers")])
        assert "gate" not in to_aggregate_manifest(plan)

    def test_gate_policy_round_trips_through_to_dict_from_dict(self) -> None:
        plan = RunPlan(
            checks=[],
            gate_missing_required="warn",
            gate_unexpected_target="ignore",
        )
        restored = RunPlan.from_dict(plan.to_dict())
        assert restored.gate_missing_required == "warn"
        assert restored.gate_unexpected_target == "ignore"

    def test_from_dict_with_no_gate_key_leaves_fields_none(self) -> None:
        restored = RunPlan.from_dict({"schema": RunPlan().schema, "checks": []})
        assert restored.gate_missing_required is None
        assert restored.gate_unexpected_target is None

    def test_gate_bearing_plan_stamps_the_v2_schema(self) -> None:
        """Codex review, fresh evidence: a plan carrying `gate` must not be
        stamped the unchanged v1 schema -- an old, pre-gate `RunPlan.
        from_dict()` would silently ignore the unknown key and project a
        1.0 aggregate manifest applying the hard-coded default policy
        instead of what the plan actually asked for."""
        from abicheck.buildsource.run_plan import RUN_PLAN_SCHEMA_GATE

        plan = RunPlan(checks=[], gate_missing_required="warn")
        assert plan.to_dict()["schema"] == RUN_PLAN_SCHEMA_GATE

    def test_gateless_plan_keeps_the_v1_schema(self) -> None:
        from abicheck.buildsource.run_plan import RUN_PLAN_SCHEMA

        plan = RunPlan(checks=[])
        assert plan.to_dict()["schema"] == RUN_PLAN_SCHEMA

    def test_gate_with_declared_v1_schema_is_rejected(self) -> None:
        from abicheck.aggregate import AggregateError

        with pytest.raises(AggregateError, match="schema.*v2"):
            RunPlan.from_dict(
                {
                    "schema": "abicheck.run-plan/v1",
                    "checks": [],
                    "gate": {"missing_required": "warn"},
                }
            )

    def test_gate_with_no_declared_schema_is_rejected(self) -> None:
        # No schema key at all defaults to v1 (RUN_PLAN_SCHEMA) -- a
        # hand-crafted gate-bearing plan omitting `schema` entirely is the
        # same inconsistency as declaring v1 explicitly, since this
        # codebase's own `to_dict()` always stamps v2 whenever gate is set.
        from abicheck.aggregate import AggregateError

        with pytest.raises(AggregateError, match="schema.*v2"):
            RunPlan.from_dict({"checks": [], "gate": {"missing_required": "warn"}})

    def test_future_schema_major_is_rejected(self) -> None:
        from abicheck.aggregate import AggregateError

        with pytest.raises(AggregateError, match="newer than this tool supports"):
            RunPlan.from_dict({"schema": "abicheck.run-plan/v99", "checks": []})

    def test_unrecognized_schema_string_is_accepted_without_a_gate(self) -> None:
        # An unparseable/unrecognized schema string (not the "abicheck.
        # run-plan/vN" shape) is a separate, pre-existing concern this
        # version check doesn't try to diagnose -- it only gates a *parsed*
        # version number, so a plan with no gate block still loads.
        restored = RunPlan.from_dict({"schema": "something-else", "checks": []})
        assert restored.schema == "something-else"
        assert restored.gate_missing_required is None

    @pytest.mark.parametrize(
        "bad_gate",
        [
            "not-an-object",
            {"unknown_key": "fail"},
            {"missing_required": "bogus"},
            {"unexpected_target": "bogus"},
        ],
    )
    def test_malformed_v2_gate_is_rejected_not_silently_discarded(self, bad_gate) -> None:
        """Codex review, fresh evidence: a malformed `gate` on an otherwise
        valid v2 plan must be a loud AggregateError, not silently coerced to
        "no gate" -- `to_aggregate_manifest()` would then omit the policy
        entirely and `aggregate` would apply the hard-coded fail/include
        defaults, potentially reversing the requested CI outcome. Mirrors
        `ExpectedTargets.from_manifest_data()`'s own validation for a
        hand-authored manifest's `gate` block."""
        from abicheck.aggregate import AggregateError

        with pytest.raises(AggregateError):
            RunPlan.from_dict(
                {"schema": "abicheck.run-plan/v2", "checks": [], "gate": bad_gate}
            )
