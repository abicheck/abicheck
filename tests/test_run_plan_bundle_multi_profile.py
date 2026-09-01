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

"""G38 Phase 15's own acceptance test: a bundle with **two sibling ELF
profiles, both required** (the oneDAL-style CPU/DPC++ shape G38's own
"Origin" table names) -- split out of ``test_run_plan.py`` (already at 1936
lines, close enough to the 2000-line hard cap that a new class risked
tipping it over), mirroring that file's own established sibling-split
convention (``test_run_plan_gate_policy.py`` et al.).

``test_run_plan.py``'s own ``TestBundleChecks`` already proves cell
derivation for a bundle with *one* ELF profile, and ``TestImplicitSweep``
already proves "two matching profiles produce two distinct checks" for a
plain *target* (not bundle). Neither combines both: a bundle, scoped to two
sibling ELF profiles that are both declared mandatory via an explicit
``checks[].profiles:`` selector. That combination is exactly G38 Phase 15's
own acceptance bar -- "old CPU pairs only with new CPU, old DPC pairs only
with new DPC, never unioned" and "missing required DPC is a coverage
regression" -- and this file proves both hold today via the already-shipped
G30 P1.4/P1.5 declarative pipeline (``run_plan.py``/``project_targets.py``),
with no dependency on ``bundle_variants_config.py``/``pair_variants`` at
all. See the G38 plan doc's own (corrected) Phase 15 section for the full
account of why that pipeline needs no cross-job snapshot/``DiffResult``
transport: each ``(bundle, profile)`` cell resolves its own baseline live,
in-job, via ``resolve-baseline`` -- never across a job boundary.
"""

from __future__ import annotations

from abicheck.buildsource.build_output import BuildOutput, BuildOutputTarget
from abicheck.buildsource.project_targets import ProjectTargetsConfig
from abicheck.buildsource.run_plan import RUN_PLAN_KIND_BUNDLE, generate_run_plan


def _bo(*target_ids: str) -> BuildOutput:
    return BuildOutput(
        targets=[
            BuildOutputTarget(id=t, binary=f"artifacts/{t}.so") for t in target_ids
        ],
    )


def _parsed(raw: dict) -> ProjectTargetsConfig:
    return ProjectTargetsConfig.from_dict(raw)


# Two sibling ELF profiles ("cpu"/"dpc"), both `contract: true`, both named
# explicitly in the bundle's own `checks[].profiles:` selector -- the
# authoring shape that makes a missing variant a hard run-plan error rather
# than a silent, valid skip (see this module's own docstring).
_RAW = {
    "targets": {
        "libonedal_core": {
            "kind": "library",
            "binary_pattern": "lib/libonedal_core.so*",
            "bundle": "onedal-release",
        },
        "libonedal_thread": {
            "kind": "library",
            "binary_pattern": "lib/libonedal_thread.so*",
            "bundle": "onedal-release",
        },
    },
    "bundles": {
        "onedal-release": {
            "targets": ["libonedal_core", "libonedal_thread"],
            "checks": [
                {
                    "channel": "release",
                    "depth": "binary",
                    "required": True,
                    "profiles": ["cpu", "dpc"],
                },
            ],
        },
    },
    "profiles": {
        "cpu": {"contract": True},
        "dpc": {"contract": True},
    },
    "baseline": {
        "channels": {
            "release": {"source": "github-release", "asset_pattern": "onedal-*"},
        },
    },
}


class TestBundleAcrossTwoRequiredVariantProfiles:
    def test_two_required_variant_profiles_produce_two_independent_bundle_checks(
        self,
    ) -> None:
        config = _parsed(_RAW)
        plan, report = generate_run_plan(
            config,
            {
                "cpu": _bo("libonedal_core", "libonedal_thread"),
                "dpc": _bo("libonedal_core", "libonedal_thread"),
            },
        )
        assert report.ok, report.errors
        assert len(plan.checks) == 2
        by_profile = {c.profile_id: c for c in plan.checks}
        assert set(by_profile) == {"cpu", "dpc"}
        for check in by_profile.values():
            assert check.kind == RUN_PLAN_KIND_BUNDLE
            assert check.name == "onedal-release"
            assert check.bundle_members == ["libonedal_core", "libonedal_thread"]
        # Distinct check_ids -- each cell is independently addressable, so
        # nothing downstream (upload-artifact/aggregate) can conflate a
        # cpu-profile result with a dpc-profile one.
        assert by_profile["cpu"].check_id != by_profile["dpc"].check_id

    def test_missing_required_variant_profile_is_a_hard_run_plan_error(self) -> None:
        # The "dpc" profile builds only one of the bundle's two members --
        # a real-world shape for "the DPC++ build partially failed." The
        # still-good "cpu" cell stays in plan.checks (a partial, not
        # atomic, failure) -- but report.ok is what actually gates CI:
        # cli_project.py's `project plan` command does `sys.exit(0 if
        # report.ok else 1)` regardless of plan.checks, so the plan job (and
        # therefore the whole check-project.yml run, via its needs: chain)
        # fails loudly either way -- the real hard-failure signal this test
        # exists to prove.
        config = _parsed(_RAW)
        plan, report = generate_run_plan(
            config,
            {
                "cpu": _bo("libonedal_core", "libonedal_thread"),
                "dpc": _bo("libonedal_core"),
            },
        )
        assert not report.ok
        assert [c.profile_id for c in plan.checks] == ["cpu"]
        assert any("dpc" in e and "libonedal_thread" in e for e in report.errors)

    def test_required_variant_profile_entirely_absent_is_a_hard_run_plan_error(
        self,
    ) -> None:
        # The "dpc" profile never produced a build-output.json at all --
        # distinct from the above (a build that ran but came up short) --
        # still a hard error (report.ok False), not a silent skip, because
        # both profiles are named explicitly as required.
        config = _parsed(_RAW)
        plan, report = generate_run_plan(
            config, {"cpu": _bo("libonedal_core", "libonedal_thread")}
        )
        assert not report.ok
        assert [c.profile_id for c in plan.checks] == ["cpu"]
        assert any("dpc" in e for e in report.errors)
