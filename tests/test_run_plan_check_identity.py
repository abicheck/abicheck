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

"""G42 "Explicit check identifiers" tests for ``run_plan.py`` -- split out
of ``test_run_plan.py`` (AI-readiness file-size cap; that file already
covers plan generation/round-tripping in depth, this one is scoped to the
new ``checks[].id``/``checks[].analysis`` projection and the duplicate-id
guard's G42-specific error path).

Covers the plan's own "Check identity" acceptance scenario: one target/
profile/channel/depth, two ``checks[]`` entries differing only in
``analysis:`` -- rejected with an actionable "give it an ``id:``" error
absent an explicit id, and generating two distinct, non-colliding
``check_id``s once each entry declares one. See
``docs/contribute/plans/g42-check-identity-environments-and-provider-
resolution.md``.
"""

from __future__ import annotations

import json

from abicheck.buildsource.build_output import BuildOutput, BuildOutputTarget
from abicheck.buildsource.project_targets import ProjectTargetsConfig
from abicheck.buildsource.run_plan import RunPlan, RunPlanCheck, generate_run_plan


def _bo(*target_ids: str) -> BuildOutput:
    return BuildOutput(
        targets=[
            BuildOutputTarget(id=t, binary=f"artifacts/{t}.so") for t in target_ids
        ],
    )


def _parsed(raw: dict) -> ProjectTargetsConfig:
    return ProjectTargetsConfig.from_dict(raw)


def _raw_with_two_source_checks(*, ids: tuple[str, str] | None) -> dict:
    checks = [
        {
            "channel": "release",
            "depth": "source",
            "profiles": ["linux"],
            "analysis": {"evidence": "replay"},
        },
        {
            "channel": "release",
            "depth": "source",
            "profiles": ["linux"],
            "analysis": {"evidence": "clang-plugin"},
        },
    ]
    if ids is not None:
        checks[0]["id"] = ids[0]
        checks[1]["id"] = ids[1]
    return {
        "targets": {
            "libfoo": {
                "kind": "library",
                "binary_pattern": "build/libfoo*.so",
                "checks": checks,
            },
        },
        "profiles": {"linux": {"contract": True}},
        "baseline": {
            "channels": {
                "release": {"source": "github-release", "asset_pattern": "libfoo-*"},
            },
        },
    }


class TestAnalysisDifferingChecksWithoutExplicitId:
    def test_generates_an_actionable_id_error(self) -> None:
        """G42 'Explicit check identifiers': two checks[] entries sharing
        (target, profile, channel, depth) but declaring different
        analysis: collide on the generated check_id (analysis fields
        aren't folded into it) -- and the error must point at id: as the
        fix, not the generic channel/depth/profile advice (wrong here,
        since those three are deliberately identical)."""
        config = _parsed(_raw_with_two_source_checks(ids=None))
        plan, report = generate_run_plan(config, {"linux": _bo("libfoo")})
        assert not report.ok
        assert any(
            "declare different analysis:" in e and "distinct, explicit id:" in e
            for e in report.errors
        )
        # Still generated (never raises) -- report.ok is the hard-failure
        # signal, matching this module's own "report errors, don't raise"
        # contract.
        assert len(plan.checks) == 2


class TestAnalysisDifferingChecksWithExplicitIds:
    """The G42 'Check identity' acceptance scenario: one target/profile/
    channel/depth, two checks differing only in analysis.evidence, each
    given a distinct id: -- no check_id collision, two separate cells."""

    def test_no_collision_and_two_separate_reports(self) -> None:
        config = _parsed(_raw_with_two_source_checks(ids=("l4-replay", "l4-plugin")))
        plan, report = generate_run_plan(config, {"linux": _bo("libfoo")})
        assert report.ok
        assert len(plan.checks) == 2
        check_ids = {c.check_id for c in plan.checks}
        assert check_ids == {
            "libfoo@linux#release@source~l4-replay",
            "libfoo@linux#release@source~l4-plugin",
        }

    def test_explicit_id_and_analysis_fields_project_onto_each_cell(self) -> None:
        config = _parsed(_raw_with_two_source_checks(ids=("l4-replay", "l4-plugin")))
        plan, report = generate_run_plan(config, {"linux": _bo("libfoo")})
        assert report.ok
        by_id = {c.explicit_id: c for c in plan.checks}
        assert by_id["l4-replay"].analysis_evidence == "replay"
        assert by_id["l4-plugin"].analysis_evidence == "clang-plugin"


class TestRunPlanCheckRoundTripsG42Fields:
    def test_explicit_id_and_analysis_fields_round_trip(self) -> None:
        check = RunPlanCheck(
            check_id="libfoo@linux#release@source~l4-plugin",
            name="libfoo",
            profile_id="linux",
            baseline_channel="release",
            requested_depth="source",
            binary_pattern="build/libfoo*.so",
            explicit_id="l4-plugin",
            analysis_evidence="clang-plugin",
            analysis_policy="strict-abi",
            analysis_assurance="complete",
        )
        plan = RunPlan(checks=[check])
        d = check.to_dict()
        assert d["explicit_id"] == "l4-plugin"
        assert d["analysis_evidence"] == "clang-plugin"
        assert d["analysis_policy"] == "strict-abi"
        assert d["analysis_assurance"] == "complete"
        restored = RunPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
        assert restored == plan

    def test_absent_g42_fields_are_omitted(self) -> None:
        """No G42 fields declared -> to_dict() carries none of them, the
        pre-G42 shape unchanged."""
        check = RunPlanCheck(
            check_id="libfoo@linux#release@headers",
            name="libfoo",
            profile_id="linux",
            baseline_channel="release",
            requested_depth="headers",
            binary_pattern="build/libfoo*.so",
        )
        d = check.to_dict()
        assert "explicit_id" not in d
        assert "analysis_evidence" not in d
        assert "analysis_policy" not in d
        assert "analysis_assurance" not in d
