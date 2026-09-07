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

"""``RunPlanCheck.public_header_roots``/``.generated_header_roots`` -- the
G41 Phase 2 slice of "declared project checks executable and truthful"
(``docs/contribute/plans/g41-baseline-consumer-context-and-declarative-
assurance.md``'s Phase 2, and ``docs/contribute/plans/
product-gaps-2026-09-audit.md``'s "Remaining backlog" item 2).

``RunPlanCheck.header`` already projects ``.abicheck.yml``'s *declared*
``TargetSpec.public_headers`` (see ``test_run_plan_public_headers.py``), the
same value for every profile a target runs on. What was still missing:
``build-output.json``'s own *concrete*, profile-specific
``public_header_roots``/``generated_header_roots`` -- validated to exist and
be non-empty by ``validate_build_output`` (ADR-047 §2's S10 guard) -- never
reached the generated run plan at all, so two targets built under the same
profile with genuinely different header layouts had no way to be told apart
by anything downstream of ``.abicheck.yml``'s config-level declaration.

Split out of ``test_run_plan_public_headers.py`` rather than extended in
place, matching that file's own precedent for keeping a field's tests
together without pushing either file toward the AI-readiness line-count cap.
"""

from __future__ import annotations

import json

from abicheck.buildsource.build_output import BuildOutput, BuildOutputTarget
from abicheck.buildsource.project_targets import ProjectTargetsConfig
from abicheck.buildsource.run_plan import (
    RUN_PLAN_KIND_BUNDLE,
    RUN_PLAN_KIND_TARGET,
    RunPlan,
    RunPlanCheck,
    generate_run_plan,
)


def _parsed(raw: dict) -> ProjectTargetsConfig:
    return ProjectTargetsConfig.from_dict(raw)


_TWO_TARGET_RAW = {
    "targets": {
        "liba": {
            "kind": "library",
            "binary_pattern": "build/liba*.so",
            "checks": [
                {"channel": "release", "depth": "headers", "required": True},
            ],
        },
        "libb": {
            "kind": "library",
            "binary_pattern": "build/libb*.so",
            "checks": [
                {"channel": "release", "depth": "headers", "required": True},
            ],
        },
        "consumer": {
            "kind": "app-consumer",
            "consumer_binary_pattern": "build/consumer",
            "library": "liba",
            "checks": [
                {"channel": "none", "depth": "binary", "required": False},
            ],
        },
    },
    "profiles": {"linux": {"contract": True}},
}


def _two_target_build_output() -> BuildOutput:
    """Two targets, same profile, deliberately DIFFERENT header roots and
    generated-header roots -- the Phase 2 acceptance scenario's shape
    ("two targets in one profile with different header roots and different
    generated headers")."""
    return BuildOutput(
        targets=[
            BuildOutputTarget(
                id="liba",
                binary="artifacts/liba.so",
                public_header_roots=["headers/a"],
                generated_header_roots=["generated-headers/a"],
            ),
            BuildOutputTarget(
                id="libb",
                binary="artifacts/libb.so",
                public_header_roots=["headers/b", "headers/b_compat"],
                generated_header_roots=[],
            ),
        ],
    )


class TestBuildOutputHeaderRootsProjection:
    def test_two_targets_project_distinct_public_header_roots(self) -> None:
        config = _parsed(_TWO_TARGET_RAW)
        plan, report = generate_run_plan(config, {"linux": _two_target_build_output()})
        assert report.ok
        [check_a] = [c for c in plan.checks if c.name == "liba"]
        [check_b] = [c for c in plan.checks if c.name == "libb"]
        assert check_a.public_header_roots == "headers/a\n"
        assert check_b.public_header_roots == "headers/b\nheaders/b_compat"
        # Neither cell's roots leak into the other's -- the whole point of
        # sourcing this per-target from build-output.json instead of one
        # workflow-global header input.
        assert check_a.public_header_roots != check_b.public_header_roots

    def test_two_targets_project_distinct_generated_header_roots(self) -> None:
        config = _parsed(_TWO_TARGET_RAW)
        plan, report = generate_run_plan(config, {"linux": _two_target_build_output()})
        assert report.ok
        [check_a] = [c for c in plan.checks if c.name == "liba"]
        [check_b] = [c for c in plan.checks if c.name == "libb"]
        assert check_a.generated_header_roots == "generated-headers/a\n"
        # libb's build-output entry declares no generated_header_roots at
        # all -- distinct from liba's real, non-empty root, not silently
        # defaulted to the same value.
        assert check_b.generated_header_roots == ""

    def test_target_with_no_declared_roots_leaves_both_fields_empty(self) -> None:
        config = _parsed(_TWO_TARGET_RAW)
        bo = BuildOutput(
            targets=[
                BuildOutputTarget(id="liba", binary="artifacts/liba.so"),
                BuildOutputTarget(id="libb", binary="artifacts/libb.so"),
            ],
        )
        plan, report = generate_run_plan(config, {"linux": bo})
        assert report.ok
        [check_a] = [c for c in plan.checks if c.name == "liba"]
        assert check_a.public_header_roots == ""
        assert check_a.generated_header_roots == ""

    def test_app_consumer_redirects_the_referenced_librarys_header_roots(
        self,
    ) -> None:
        # Same redirection RunPlanCheck.header already applies (ADR-047 §3):
        # an app-consumer/plugin-contract target carries no build-output.json
        # entry of its own, so its header-roots projection must follow the
        # same `library:` redirect _library_lookup_and_pattern already
        # resolves, not silently read as empty.
        config = _parsed(_TWO_TARGET_RAW)
        plan, report = generate_run_plan(config, {"linux": _two_target_build_output()})
        assert report.ok
        [check] = [c for c in plan.checks if c.name == "consumer"]
        assert check.public_header_roots == "headers/a\n"
        assert check.generated_header_roots == "generated-headers/a\n"

    def test_bundle_checks_never_project_header_roots(self) -> None:
        """Same restriction RunPlanCheck.header's own docstring already
        states -- per-bundle-member header staging doesn't exist yet."""
        raw = {
            "targets": {
                "liba": {
                    "kind": "library",
                    "binary_pattern": "build/liba*.so",
                    "bundle": "rel",
                },
            },
            "bundles": {
                "rel": {
                    "targets": ["liba"],
                    "checks": [
                        {"channel": "release", "depth": "binary", "required": True},
                    ],
                },
            },
            "profiles": {"linux": {"contract": True}},
        }
        config = _parsed(raw)
        bo = BuildOutput(
            targets=[
                BuildOutputTarget(
                    id="liba",
                    binary="artifacts/liba.so",
                    public_header_roots=["headers/a"],
                    generated_header_roots=["generated-headers/a"],
                ),
            ],
        )
        plan, report = generate_run_plan(config, {"linux": bo})
        assert report.ok
        [bundle_check] = [c for c in plan.checks if c.kind == RUN_PLAN_KIND_BUNDLE]
        assert bundle_check.public_header_roots == ""
        assert bundle_check.generated_header_roots == ""

    def test_fields_round_trip(self) -> None:
        check = RunPlanCheck(
            check_id="liba@linux#release@headers",
            kind=RUN_PLAN_KIND_TARGET,
            target_kind="library",
            name="liba",
            profile_id="linux",
            baseline_channel="release",
            requested_depth="headers",
            binary_pattern="build/liba*.so",
            public_header_roots="headers/a",
            generated_header_roots="generated-headers/a",
        )
        plan = RunPlan(checks=[check])
        d = check.to_dict()
        assert d["public_header_roots"] == "headers/a"
        assert d["generated_header_roots"] == "generated-headers/a"
        restored = RunPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
        assert restored == plan

    def test_fields_omitted_from_dict_when_empty(self) -> None:
        check = RunPlanCheck(check_id="liba@linux#release@headers")
        d = check.to_dict()
        assert "public_header_roots" not in d
        assert "generated_header_roots" not in d
