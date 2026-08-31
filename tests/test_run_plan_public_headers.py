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

"""``RunPlanCheck.header`` -- split out of ``test_run_plan.py`` (which sits
at the AI-readiness 2000-line hard cap) to keep this addition's tests
together without pushing that file over it.

``TargetSpec.public_headers`` (ADR-047's own worked example,
``project-targets-schema.md``) was declared, validated, and round-tripped
through ``.to_dict()``/``.from_dict()``, but nothing downstream of
``project_targets.py`` ever read it -- not ``run_plan.py``'s generated
``run-plan.json`` cells, and so not ``check-project.yml``'s per-cell
``check-target`` invocation either. A project following the ADR's own
example config got silent binary/DWARF-only scoping for every
automatically-generated CI check, regardless of what it declared.
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


def _bo(*target_ids: str) -> BuildOutput:
    return BuildOutput(
        targets=[
            BuildOutputTarget(id=t, binary=f"artifacts/{t}.so") for t in target_ids
        ],
    )


def _parsed(raw: dict) -> ProjectTargetsConfig:
    return ProjectTargetsConfig.from_dict(raw)


class TestPublicHeadersProjection:
    """RunPlanCheck.header: TargetSpec.public_headers reaching the generated
    run plan, space-joined to match check-target's own `header` input
    format."""

    _RAW = {
        "targets": {
            "libfoo": {
                "kind": "library",
                "binary_pattern": "build/libfoo*.so",
                "public_headers": ["headers/foo", "headers/foo_compat"],
                "checks": [
                    {"channel": "release", "depth": "headers", "required": True},
                ],
            },
            "libbare": {
                "kind": "library",
                "binary_pattern": "build/libbare*.so",
                "checks": [
                    {"channel": "release", "depth": "headers", "required": True},
                ],
            },
            "consumer": {
                "kind": "app-consumer",
                "consumer_binary_pattern": "build/consumer",
                "library": "libfoo",
                "checks": [
                    {"channel": "none", "depth": "binary", "required": False},
                ],
            },
        },
        "profiles": {"linux": {"contract": True}},
    }

    def test_library_target_projects_its_own_public_headers(self) -> None:
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(config, {"linux": _bo("libfoo", "libbare")})
        assert report.ok
        [check] = [c for c in plan.checks if c.name == "libfoo"]
        assert check.header == "headers/foo headers/foo_compat"

    def test_library_target_with_no_public_headers_leaves_header_empty(self) -> None:
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(config, {"linux": _bo("libfoo", "libbare")})
        assert report.ok
        [check] = [c for c in plan.checks if c.name == "libbare"]
        assert check.header == ""

    def test_app_consumer_redirects_the_referenced_librarys_public_headers(
        self,
    ) -> None:
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(config, {"linux": _bo("libfoo", "libbare")})
        assert report.ok
        [check] = [c for c in plan.checks if c.name == "consumer"]
        assert check.header == "headers/foo headers/foo_compat"

    def test_bundle_checks_never_project_a_header(self) -> None:
        """kind: bundle cells stay header-empty (RunPlanCheck.header's own
        docstring) -- per-bundle-member header staging doesn't exist yet, the
        same restriction BUNDLE_CHECK_DEPTHS enforces for headers/source
        depth."""
        raw = {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "build/libfoo*.so",
                    "public_headers": ["headers/foo"],
                    "bundle": "rel",
                },
            },
            "bundles": {
                "rel": {
                    "targets": ["libfoo"],
                    "checks": [
                        {"channel": "release", "depth": "binary", "required": True},
                    ],
                },
            },
            "profiles": {"linux": {"contract": True}},
        }
        config = _parsed(raw)
        plan, report = generate_run_plan(config, {"linux": _bo("libfoo")})
        assert report.ok
        [bundle_check] = [c for c in plan.checks if c.kind == RUN_PLAN_KIND_BUNDLE]
        assert bundle_check.header == ""

    def test_header_field_round_trips(self) -> None:
        check = RunPlanCheck(
            check_id="libfoo@linux#release@headers",
            kind=RUN_PLAN_KIND_TARGET,
            target_kind="library",
            name="libfoo",
            profile_id="linux",
            baseline_channel="release",
            requested_depth="headers",
            binary_pattern="build/libfoo*.so",
            header="headers/foo headers/foo_compat",
        )
        plan = RunPlan(checks=[check])
        d = check.to_dict()
        assert d["header"] == "headers/foo headers/foo_compat"
        restored = RunPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
        assert restored == plan

    def test_header_field_omitted_from_dict_when_empty(self) -> None:
        check = RunPlanCheck(check_id="libfoo@linux#release@headers")
        d = check.to_dict()
        assert "header" not in d
