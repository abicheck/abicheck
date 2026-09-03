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

"""Tests for ``buildsource.run_plan`` and the ``run-plan`` CLI (ADR-047
§4/§5, G30 P1.4).

Covers cell derivation from ``targets:``/``bundles:``/``profiles:`` +
``build-output.json`` (implicit sweep skips a non-matching profile silently,
an explicit ``profiles:`` selector hard-errors on one), the ``app-consumer``/
``plugin-contract`` library redirect, bundle member resolution, the
``run-plan.json`` round-trip, the ``aggregate --manifest`` projection using
``check_id`` (not the bare name), and the CLI wrapper's exit codes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.build_output import BuildOutput, BuildOutputTarget
from abicheck.buildsource.project_targets import (
    ProfileCompileSpec,
    ProjectTargetsConfig,
)
from abicheck.buildsource.run_plan import (
    RUN_PLAN_KIND_BUNDLE,
    RUN_PLAN_KIND_TARGET,
    RunPlan,
    RunPlanCheck,
    _compose_gcc_options,
    _scheduling_fields_for_profile,
    generate_run_plan,
)
from abicheck.cli import main

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _bo(*target_ids: str) -> BuildOutput:
    return BuildOutput(
        targets=[
            BuildOutputTarget(id=t, binary=f"artifacts/{t}.so") for t in target_ids
        ],
    )


_LIBRARY_ONLY_RAW = {
    "targets": {
        "libfoo": {
            "kind": "library",
            "binary_pattern": "build/libfoo*.so",
            "checks": [
                {"channel": "release", "depth": "headers", "required": True},
            ],
        },
    },
    "profiles": {
        "linux": {"contract": True},
        "mac": {"contract": True},
    },
    "baseline": {
        "channels": {
            "release": {"source": "github-release", "asset_pattern": "libfoo-*"},
        },
    },
}


def _parsed(raw: dict) -> ProjectTargetsConfig:
    return ProjectTargetsConfig.from_dict(raw)


# A single-contract-profile variant of _LIBRARY_ONLY_RAW, for tests that only
# care about one profile's coverage -- providing build-output for just
# "linux" out of _LIBRARY_ONLY_RAW's two declared profiles is now a hard
# error (a declared contract profile with no build-output.json at all,
# Codex review), so those tests would otherwise be asserting on an
# unrelated coverage-gap error instead of what they're actually testing.
_SINGLE_PROFILE_LIBRARY_RAW = {
    "targets": {
        "libfoo": {
            "kind": "library",
            "binary_pattern": "build/libfoo*.so",
            "checks": [
                {"channel": "release", "depth": "headers", "required": True},
            ],
        },
    },
    "profiles": {
        "linux": {"contract": True},
    },
    "baseline": {
        "channels": {
            "release": {"source": "github-release", "asset_pattern": "libfoo-*"},
        },
    },
}


class TestImplicitSweep:
    def test_profile_missing_from_build_outputs_entirely_is_an_error(self) -> None:
        # Distinct from test_target_absent_from_a_profiles_build_output_is_
        # silently_skipped below: a declared contract profile with NO
        # build-output.json at all (not even an empty one) almost always
        # means that profile's build/upload failed or was misnamed, so it's
        # a hard error even for the implicit sweep (Codex review) -- the
        # profiles that DID resolve still produce their checks.
        config = _parsed(_LIBRARY_ONLY_RAW)
        plan, report = generate_run_plan(config, {"linux": _bo("libfoo")})
        assert not report.ok
        assert any("mac" in e for e in report.errors)
        assert [c.check_id for c in plan.checks] == ["libfoo@linux#release@headers"]

    def test_target_absent_from_a_profiles_build_output_is_silently_skipped(
        self,
    ) -> None:
        config = _parsed(_LIBRARY_ONLY_RAW)
        plan, report = generate_run_plan(
            config, {"linux": _bo("libfoo"), "mac": _bo("some-other-lib")}
        )
        assert report.ok
        assert not report.warnings
        assert [c.check_id for c in plan.checks] == ["libfoo@linux#release@headers"]

    def test_two_matching_profiles_produce_two_distinct_checks(self) -> None:
        config = _parsed(_LIBRARY_ONLY_RAW)
        plan, report = generate_run_plan(
            config, {"linux": _bo("libfoo"), "mac": _bo("libfoo")}
        )
        assert report.ok
        assert {c.check_id for c in plan.checks} == {
            "libfoo@linux#release@headers",
            "libfoo@mac#release@headers",
        }


class TestAllowNewTargetProjection:
    _RAW = {
        "targets": {
            "libnew": {
                "kind": "library",
                "binary_pattern": "build/libnew*.so",
                "checks": [
                    {
                        "channel": "release",
                        "depth": "headers",
                        "required": False,
                        "allow_new_target": True,
                    },
                ],
            },
        },
        "profiles": {"linux": {"contract": True}},
        "baseline": {
            "channels": {
                "release": {"source": "github-release", "asset_pattern": "libnew-*"},
            },
        },
    }

    def test_target_check_projects_allow_new_target(self) -> None:
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(config, {"linux": _bo("libnew")})
        assert report.ok, report.errors
        [check] = plan.checks
        assert check.allow_new_target is True
        assert check.to_dict()["allow_new_target"] is True

    def test_default_check_does_not_project_allow_new_target(self) -> None:
        config = _parsed(_SINGLE_PROFILE_LIBRARY_RAW)
        plan, report = generate_run_plan(config, {"linux": _bo("libfoo")})
        assert report.ok, report.errors
        [check] = plan.checks
        assert check.allow_new_target is False
        assert "allow_new_target" not in check.to_dict()

    def test_bundle_checks_never_project_allow_new_target(self) -> None:
        # project_targets.validate_project_targets already rejects
        # allow_new_target: true on a bundle check's own CheckSpec, so
        # generate_run_plan is never asked to project one for a bundle
        # check -- this pins that _generate_bundle_checks itself has no
        # code path that would set it either way.
        config = _parsed(
            {
                "targets": {
                    "a": {
                        "kind": "library",
                        "binary_pattern": "a.so",
                        "bundle": "rel",
                    },
                    "b": {
                        "kind": "library",
                        "binary_pattern": "b.so",
                        "bundle": "rel",
                    },
                },
                "bundles": {
                    "rel": {
                        "targets": ["a", "b"],
                        "checks": [{"channel": "release", "depth": "binary"}],
                    }
                },
                "profiles": {"linux": {"contract": True}},
                "baseline": {
                    "channels": {"release": {"source": "github-release"}},
                },
            }
        )
        plan, report = generate_run_plan(config, {"linux": _bo("a", "b")})
        assert report.ok, report.errors
        [check] = plan.checks
        assert check.allow_new_target is False


class TestExplicitProfilesSelector:
    _RAW = {
        "targets": {
            "libfoo": {
                "kind": "library",
                "binary_pattern": "build/libfoo*.so",
                "checks": [
                    {
                        "channel": "release",
                        "depth": "headers",
                        "required": True,
                        "profiles": ["linux"],
                    },
                ],
            },
        },
        "profiles": {"linux": {"contract": True}, "mac": {"contract": True}},
        "baseline": {
            "channels": {
                "release": {"source": "github-release", "asset_pattern": "libfoo-*"},
            },
        },
    }

    def test_missing_build_output_for_an_explicit_profile_is_an_error(self) -> None:
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(config, {})
        assert not report.ok
        assert not plan.checks
        assert any("linux" in e for e in report.errors)

    def test_target_absent_from_an_explicit_profiles_build_output_is_an_error(
        self,
    ) -> None:
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(config, {"linux": _bo("some-other-lib")})
        assert not report.ok
        assert not plan.checks
        assert any("libfoo" in e and "linux" in e for e in report.errors)

    def test_matching_explicit_profile_resolves_cleanly(self) -> None:
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(config, {"linux": _bo("libfoo")})
        assert report.ok
        assert [c.check_id for c in plan.checks] == ["libfoo@linux#release@headers"]


class TestLibraryRedirect:
    _RAW = {
        "targets": {
            "libfoo": {"kind": "library", "binary_pattern": "build/libfoo*.so"},
            "consumer": {
                "kind": "app-consumer",
                "consumer_binary_pattern": "build/consumer",
                "library": "libfoo",
                "checks": [
                    {"channel": "none", "depth": "binary", "required": False},
                ],
            },
            "plugin": {
                "kind": "plugin-contract",
                "contract_file": "plugin.syms",
                "library": "libfoo",
                "checks": [
                    {"channel": "none", "depth": "binary", "required": False},
                ],
            },
        },
        "profiles": {"linux": {"contract": True}},
    }

    def test_app_consumer_redirects_baseline_target_and_binary_pattern(self) -> None:
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(config, {"linux": _bo("libfoo")})
        assert report.ok
        [check] = [c for c in plan.checks if c.name == "consumer"]
        assert check.target_kind == "app-consumer"
        assert check.baseline_target == "libfoo"
        assert check.binary_pattern == "build/libfoo*.so"
        assert check.consumer_binary_pattern == "build/consumer"
        assert check.contract_file == ""

    def test_plugin_contract_redirects_baseline_target_and_binary_pattern(self) -> None:
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(config, {"linux": _bo("libfoo")})
        assert report.ok
        [check] = [c for c in plan.checks if c.name == "plugin"]
        assert check.target_kind == "plugin-contract"
        assert check.baseline_target == "libfoo"
        assert check.binary_pattern == "build/libfoo*.so"
        assert check.contract_file == "plugin.syms"
        assert check.consumer_binary_pattern == ""

    def test_redirect_check_existence_is_gated_on_the_librarys_presence(self) -> None:
        """Neither app-consumer nor plugin-contract ever gets its own
        build-output.json targets[] entry (ADR-047 §3) -- their check's
        existence on a profile is gated on the *library*'s presence there."""
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(config, {"linux": _bo("some-other-lib")})
        assert report.ok
        assert not plan.checks


class TestBundleChecks:
    _RAW = {
        "targets": {
            "libpvxs": {
                "kind": "library",
                "binary_pattern": "lib/libpvxs.so*",
                "bundle": "pvxs-release",
            },
            "libpvxsIoc": {
                "kind": "library",
                "binary_pattern": "lib/libpvxsIoc.so*",
                "bundle": "pvxs-release",
            },
        },
        "bundles": {
            "pvxs-release": {
                "targets": ["libpvxs", "libpvxsIoc"],
                "checks": [
                    {"channel": "release", "depth": "binary", "required": True},
                ],
            },
        },
        "profiles": {"linux": {"contract": True}},
        "baseline": {
            "channels": {
                "release": {"source": "github-release", "asset_pattern": "pvxs-*"},
            },
        },
    }

    def test_bundle_check_resolves_when_every_member_is_present(self) -> None:
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(
            config, {"linux": _bo("libpvxs", "libpvxsIoc")}
        )
        assert report.ok
        [check] = plan.checks
        assert check.kind == RUN_PLAN_KIND_BUNDLE
        assert check.name == "pvxs-release"
        assert check.check_id == "pvxs-release@linux#release@binary"
        assert check.bundle_members == ["libpvxs", "libpvxsIoc"]
        assert check.member_binary_patterns == {
            "libpvxs": "lib/libpvxs.so*",
            "libpvxsIoc": "lib/libpvxsIoc.so*",
        }

    def test_bundle_check_is_silently_skipped_when_a_member_is_missing_implicit_sweep(
        self,
    ) -> None:
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(config, {"linux": _bo("libpvxs")})
        assert report.ok
        assert not plan.checks

    def test_bundle_check_errors_when_a_member_is_missing_and_profile_is_explicit(
        self,
    ) -> None:
        raw = json.loads(json.dumps(self._RAW))
        raw["bundles"]["pvxs-release"]["checks"][0]["profiles"] = ["linux"]
        config = _parsed(raw)
        plan, report = generate_run_plan(config, {"linux": _bo("libpvxs")})
        assert not report.ok
        assert not plan.checks
        assert any("libpvxsIoc" in e for e in report.errors)

    def test_bundle_check_missing_build_output_for_an_explicit_profile_is_an_error(
        self,
    ) -> None:
        raw = json.loads(json.dumps(self._RAW))
        raw["bundles"]["pvxs-release"]["checks"][0]["profiles"] = ["linux"]
        config = _parsed(raw)
        plan, report = generate_run_plan(config, {})
        assert not report.ok
        assert not plan.checks
        assert any("linux" in e for e in report.errors)

    def test_bundle_check_missing_build_output_entirely_is_an_error_even_implicit(
        self,
    ) -> None:
        # Distinct from test_bundle_check_is_silently_skipped_when_a_member_
        # is_missing_implicit_sweep above: a declared contract profile with
        # NO build-output.json at all is a hard error even for the implicit
        # sweep (Codex review).
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(config, {})
        assert not report.ok
        assert not plan.checks
        assert any("linux" in e for e in report.errors)

    def test_bundle_check_silently_skips_a_non_elf_profile_implicit_sweep(
        self,
    ) -> None:
        """abicheck/bundle.py's build_bundle_snapshot() skips non-ELF
        inputs outright -- an implicit sweep across every contract profile
        must silently skip a declared Windows/macOS profile the same way
        it already skips a profile that simply doesn't build a bundle's
        members, not treat it as a coverage-gap error (Codex review)."""
        raw = json.loads(json.dumps(self._RAW))
        raw["profiles"]["windows"] = {"contract": True, "os": "windows"}
        config = _parsed(raw)
        plan, report = generate_run_plan(
            config,
            {
                "linux": _bo("libpvxs", "libpvxsIoc"),
                "windows": _bo("libpvxs", "libpvxsIoc"),
            },
        )
        assert report.ok, report.errors
        [check] = plan.checks
        assert check.profile_id == "linux"

    def test_bundle_check_explicitly_scoped_to_a_non_elf_profile_is_an_error(
        self,
    ) -> None:
        raw = json.loads(json.dumps(self._RAW))
        raw["profiles"]["windows"] = {"contract": True, "os": "windows"}
        raw["bundles"]["pvxs-release"]["checks"][0]["profiles"] = ["windows"]
        config = _parsed(raw)
        plan, report = generate_run_plan(
            config, {"windows": _bo("libpvxs", "libpvxsIoc")}
        )
        assert not report.ok
        assert not plan.checks
        assert any("os: 'windows'" in e for e in report.errors)


class TestDuplicateCheckIdIsRejected:
    """Two checks[] entries resolving to the same check_id (profile,

    channel, depth) must be rejected at generation time, not left to
    surface as a late aggregate-projection failure.
    """

    def test_two_checks_entries_with_the_same_channel_and_depth_is_an_error(
        self,
    ) -> None:
        raw = {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "build/libfoo*.so",
                    "checks": [
                        {
                            "channel": "release",
                            "depth": "headers",
                            "required": True,
                            "profiles": ["linux"],
                        },
                        {
                            "channel": "release",
                            "depth": "headers",
                            "required": False,
                            "profiles": ["linux"],
                        },
                    ],
                },
            },
            "profiles": {"linux": {"contract": True}},
            "baseline": {
                "channels": {
                    "release": {
                        "source": "github-release",
                        "asset_pattern": "libfoo-*",
                    },
                },
            },
        }
        config = _parsed(raw)
        plan, report = generate_run_plan(config, {"linux": _bo("libfoo")})
        assert not report.ok
        assert any("libfoo@linux#release@headers" in e for e in report.errors)
        # Still generated (never raises) -- report.ok is the hard-failure
        # signal, matching this module's own "report errors, don't raise"
        # contract; the two duplicate cells are both present in plan.checks.
        assert len(plan.checks) == 2

    def test_distinct_depths_on_the_same_channel_are_not_duplicates(self) -> None:
        config = _parsed(_SINGLE_PROFILE_LIBRARY_RAW)
        plan, report = generate_run_plan(config, {"linux": _bo("libfoo")})
        assert report.ok
        assert len(plan.checks) == 1


class TestBundleOnlyTargetsHaveNoStandaloneChecks:
    def test_bundle_only_target_never_emits_its_own_check(self) -> None:
        raw = {
            "targets": {
                "libpvxs": {
                    "kind": "library",
                    "binary_pattern": "lib/libpvxs.so*",
                    "bundle": "pvxs-release",
                    "bundle_only": True,
                },
            },
            "bundles": {"pvxs-release": {"targets": ["libpvxs"]}},
        }
        config = _parsed(raw)
        plan, report = generate_run_plan(config, {"linux": _bo("libpvxs")})
        assert report.ok
        assert not plan.checks


class TestRunPlanRoundTrip:
    def test_target_check_round_trips(self) -> None:
        check = RunPlanCheck(
            check_id="libfoo@linux#release@headers",
            kind=RUN_PLAN_KIND_TARGET,
            target_kind="library",
            name="libfoo",
            profile_id="linux",
            baseline_channel="release",
            requested_depth="headers",
            required=True,
            gate_mode="local",
            binary_pattern="build/libfoo*.so",
        )
        plan = RunPlan(project="acme/foo", head_sha="deadbeef", checks=[check])
        restored = RunPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
        assert restored == plan

    def test_app_consumer_check_with_every_redirect_field_round_trips(self) -> None:
        """kind: target, target_kind != library exercises the
        baseline_target/consumer_binary_pattern/contract_file branches of
        to_dict() the plain library-kind case above never touches."""
        check = RunPlanCheck(
            check_id="consumer@linux#release@binary",
            kind=RUN_PLAN_KIND_TARGET,
            target_kind="app-consumer",
            name="consumer",
            profile_id="linux",
            baseline_channel="release",
            requested_depth="binary",
            required=True,
            gate_mode="local",
            baseline_target="libfoo",
            binary_pattern="build/libfoo*.so",
            consumer_binary_pattern="build/consumer",
        )
        plan = RunPlan(checks=[check])
        d = check.to_dict()
        assert d["baseline_target"] == "libfoo"
        assert d["consumer_binary_pattern"] == "build/consumer"
        restored = RunPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
        assert restored == plan

    def test_plugin_contract_check_with_contract_file_round_trips(self) -> None:
        check = RunPlanCheck(
            check_id="plugin@linux#release@binary",
            kind=RUN_PLAN_KIND_TARGET,
            target_kind="plugin-contract",
            name="plugin",
            profile_id="linux",
            baseline_channel="release",
            requested_depth="binary",
            baseline_target="libfoo",
            binary_pattern="build/libfoo*.so",
            contract_file="plugin.syms",
        )
        plan = RunPlan(checks=[check])
        d = check.to_dict()
        assert d["contract_file"] == "plugin.syms"
        restored = RunPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
        assert restored == plan

    def test_bundle_check_round_trips(self) -> None:
        check = RunPlanCheck(
            check_id="pvxs-release@linux#release@binary",
            kind=RUN_PLAN_KIND_BUNDLE,
            name="pvxs-release",
            profile_id="linux",
            baseline_channel="release",
            requested_depth="binary",
            required=True,
            gate_mode="local",
            bundle_members=["libpvxs", "libpvxsIoc"],
            member_binary_patterns={"libpvxs": "a", "libpvxsIoc": "b"},
        )
        plan = RunPlan(checks=[check])
        restored = RunPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
        assert restored == plan

    def test_empty_plan_round_trips(self) -> None:
        plan = RunPlan()
        restored = RunPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
        assert restored == plan

    def test_allow_new_target_round_trips(self) -> None:
        check = RunPlanCheck(
            check_id="libnew@linux#release@headers",
            kind=RUN_PLAN_KIND_TARGET,
            target_kind="library",
            name="libnew",
            profile_id="linux",
            baseline_channel="release",
            requested_depth="headers",
            required=False,
            binary_pattern="build/libnew*.so",
            allow_new_target=True,
        )
        assert check.to_dict()["allow_new_target"] is True
        plan = RunPlan(checks=[check])
        restored = RunPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
        assert restored == plan

    def test_allow_new_target_false_is_omitted_from_dict(self) -> None:
        check = RunPlanCheck(check_id="libfoo@linux#release@headers")
        assert "allow_new_target" not in check.to_dict()
        restored = RunPlanCheck.from_dict(json.loads(json.dumps(check.to_dict())))
        assert restored == check

    def test_compile_overlay_fields_round_trip(self) -> None:
        """P1 toolchain-profile audit: compile_gcc_path/compile_gcc_options
        both serialize/deserialize, independently of each other."""
        check = RunPlanCheck(
            check_id="libfoo@gcc14#release@headers",
            kind=RUN_PLAN_KIND_TARGET,
            target_kind="library",
            name="libfoo",
            profile_id="gcc14",
            baseline_channel="release",
            requested_depth="headers",
            binary_pattern="build/libfoo*.so",
            compile_gcc_path="/opt/gcc14/bin/g++",
            compile_gcc_options="-std=gnu++20 -DFOO_ABI=2",
        )
        plan = RunPlan(checks=[check])
        d = check.to_dict()
        assert d["compile_gcc_path"] == "/opt/gcc14/bin/g++"
        assert d["compile_gcc_options"] == "-std=gnu++20 -DFOO_ABI=2"
        restored = RunPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
        assert restored == plan

    def test_compile_overlay_fields_omitted_from_dict_when_empty(self) -> None:
        check = RunPlanCheck(check_id="libfoo@linux#release@headers")
        d = check.to_dict()
        assert "compile_gcc_path" not in d
        assert "compile_gcc_options" not in d

    def test_consumer_compile_overlay_fields_round_trip(self) -> None:
        """G34 Phase 0: consumer_compile_gcc_path/consumer_compile_gcc_options
        both serialize/deserialize, independently of the producer compile
        overlay's own pair."""
        check = RunPlanCheck(
            check_id="libfoo@gcc14-clang20#release@headers",
            kind=RUN_PLAN_KIND_TARGET,
            target_kind="library",
            name="libfoo",
            profile_id="gcc14-clang20",
            baseline_channel="release",
            requested_depth="headers",
            binary_pattern="build/libfoo*.so",
            compile_gcc_path="/opt/gcc14/bin/g++",
            compile_gcc_options="-std=gnu++17",
            consumer_compile_gcc_path="/opt/llvm-20/bin/clang++",
            consumer_compile_gcc_options="-std=gnu++20 -stdlib=libc++",
        )
        plan = RunPlan(checks=[check])
        d = check.to_dict()
        assert d["consumer_compile_gcc_path"] == "/opt/llvm-20/bin/clang++"
        assert d["consumer_compile_gcc_options"] == "-std=gnu++20 -stdlib=libc++"
        restored = RunPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
        assert restored == plan

    def test_consumer_compile_overlay_fields_omitted_from_dict_when_empty(
        self,
    ) -> None:
        check = RunPlanCheck(check_id="libfoo@linux#release@headers")
        d = check.to_dict()
        assert "consumer_compile_gcc_path" not in d
        assert "consumer_compile_gcc_options" not in d

    def test_compile_frontend_fields_round_trip(self) -> None:
        """G34 Phase B: compile_ast_frontend/consumer_compile_ast_frontend
        both serialize/deserialize, independently of each other and of the
        gcc_path/gcc_options fields."""
        check = RunPlanCheck(
            check_id="libfoo@gcc14-clang20#release@headers",
            kind=RUN_PLAN_KIND_TARGET,
            target_kind="library",
            name="libfoo",
            profile_id="gcc14-clang20",
            baseline_channel="release",
            requested_depth="headers",
            binary_pattern="build/libfoo*.so",
            compile_ast_frontend="castxml",
            consumer_compile_ast_frontend="clang",
        )
        plan = RunPlan(checks=[check])
        d = check.to_dict()
        assert d["compile_ast_frontend"] == "castxml"
        assert d["consumer_compile_ast_frontend"] == "clang"
        restored = RunPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
        assert restored == plan

    def test_compile_frontend_fields_omitted_from_dict_when_empty(self) -> None:
        check = RunPlanCheck(check_id="libfoo@linux#release@headers")
        d = check.to_dict()
        assert "compile_ast_frontend" not in d
        assert "consumer_compile_ast_frontend" not in d


class TestProfileCompileOverlayProjection:
    """P1 toolchain-profile audit: profiles.<id>.compile reaches the
    generated cell as compile_gcc_path/compile_gcc_options."""

    _RAW = {
        "targets": {
            "libfoo": {
                "kind": "library",
                "binary_pattern": "build/libfoo*.so",
                "checks": [
                    {"channel": "release", "depth": "headers", "required": True},
                ],
            },
        },
        "profiles": {
            "gcc14": {
                "contract": True,
                "compile": {
                    "binding": "gcc14",
                    "standard": "gnu++20",
                    "stdlib": "libstdc++",
                    "target": "x86_64-linux-gnu",
                    "abi_macros": {"FOO_ABI": "2", "BAR_FLAG": ""},
                    "args": ["-fno-rtti"],
                },
            },
            "plain": {"contract": True},
        },
        "baseline": {
            "channels": {
                "release": {"source": "github-release", "asset_pattern": "libfoo-*"},
            },
        },
    }

    def test_no_compile_overlay_leaves_both_fields_empty(self) -> None:
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(
            config, {"gcc14": _bo("libfoo"), "plain": _bo("libfoo")}
        )
        assert report.ok
        [check] = [c for c in plan.checks if c.profile_id == "plain"]
        assert check.compile_gcc_path == ""
        assert check.compile_gcc_options == ""
        assert "compile_gcc_path" not in check.to_dict()
        assert "compile_gcc_options" not in check.to_dict()

    def test_compile_overlay_composes_gcc_options_without_bindings(self) -> None:
        """standard/stdlib/target/abi_macros/args compose regardless of
        whether a resolved_bindings mapping was supplied -- only the
        binding -> path resolution needs one."""
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(
            config, {"gcc14": _bo("libfoo"), "plain": _bo("libfoo")}
        )
        assert report.ok
        [check] = [c for c in plan.checks if c.profile_id == "gcc14"]
        assert check.compile_gcc_path == ""
        assert check.compile_gcc_options == (
            "-std=gnu++20 -stdlib=libstdc++ --target=x86_64-linux-gnu "
            "-DBAR_FLAG -DFOO_ABI=2 -fno-rtti"
        )

    def test_resolved_bindings_populates_gcc_path(self) -> None:
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(
            config,
            {"gcc14": _bo("libfoo"), "plain": _bo("libfoo")},
            resolved_bindings={"gcc14": "/opt/gcc14/bin/g++"},
        )
        assert report.ok
        [check] = [c for c in plan.checks if c.profile_id == "gcc14"]
        assert check.compile_gcc_path == "/opt/gcc14/bin/g++"

    def test_binding_absent_from_resolved_bindings_leaves_gcc_path_empty(self) -> None:
        """generate_run_plan itself never errors on an unresolved binding --
        that's the CLI layer's check_profile_bindings_resolve step, kept
        separate so this module stays pure/never-raises for a valid config."""
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(
            config,
            {"gcc14": _bo("libfoo"), "plain": _bo("libfoo")},
            resolved_bindings={"some-other-id": "/opt/other/bin/cc"},
        )
        assert report.ok
        [check] = [c for c in plan.checks if c.profile_id == "gcc14"]
        assert check.compile_gcc_path == ""

    def test_bundle_check_also_gets_compile_fields(self) -> None:
        raw = {
            "targets": {
                "libpvxs": {
                    "kind": "library",
                    "binary_pattern": "lib/libpvxs.so*",
                    "bundle": "pvxs-release",
                },
            },
            "bundles": {
                "pvxs-release": {
                    "targets": ["libpvxs"],
                    "checks": [
                        {"channel": "release", "depth": "binary", "required": True},
                    ],
                },
            },
            "profiles": {
                "gcc14": {
                    "contract": True,
                    "compile": {"binding": "gcc14", "standard": "gnu++20"},
                },
            },
            "baseline": {
                "channels": {
                    "release": {
                        "source": "github-release",
                        "asset_pattern": "pvxs-*",
                    },
                },
            },
        }
        config = _parsed(raw)
        plan, report = generate_run_plan(
            config,
            {"gcc14": _bo("libpvxs")},
            resolved_bindings={"gcc14": "/opt/gcc14/bin/g++"},
        )
        assert report.ok
        [check] = plan.checks
        assert check.compile_gcc_path == "/opt/gcc14/bin/g++"
        assert check.compile_gcc_options == "-std=gnu++20"


class TestConsumerCompileOverlayProjection:
    """G34 Phase 0: profiles.<id>.consumer_compile reaches the generated
    cell as consumer_compile_gcc_path/consumer_compile_gcc_options,
    independently of (and identically resolved to) the producer compile:
    overlay's own pair."""

    _RAW = {
        "targets": {
            "libfoo": {
                "kind": "library",
                "binary_pattern": "build/libfoo*.so",
                "checks": [
                    {"channel": "release", "depth": "headers", "required": True},
                ],
            },
        },
        "profiles": {
            "gcc14-build-clang20-client": {
                "contract": True,
                "compile": {"binding": "gcc14", "standard": "gnu++17"},
                "consumer_compile": {
                    "binding": "clang20",
                    "standard": "gnu++20",
                    "stdlib": "libc++",
                },
            },
            "plain": {"contract": True},
        },
        "baseline": {
            "channels": {
                "release": {"source": "github-release", "asset_pattern": "libfoo-*"},
            },
        },
    }

    def test_no_consumer_compile_overlay_leaves_both_fields_empty(self) -> None:
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(
            config,
            {"gcc14-build-clang20-client": _bo("libfoo"), "plain": _bo("libfoo")},
        )
        assert report.ok
        [check] = [c for c in plan.checks if c.profile_id == "plain"]
        assert check.consumer_compile_gcc_path == ""
        assert check.consumer_compile_gcc_options == ""
        assert "consumer_compile_gcc_path" not in check.to_dict()
        assert "consumer_compile_gcc_options" not in check.to_dict()
        assert check.consumer_compile_active is False
        assert "consumer_compile_active" not in check.to_dict()

    def test_consumer_compile_overlay_projects_independently_of_producer(
        self,
    ) -> None:
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(
            config,
            {"gcc14-build-clang20-client": _bo("libfoo"), "plain": _bo("libfoo")},
            resolved_bindings={
                "gcc14": "/opt/gcc14/bin/g++",
                "clang20": "/opt/llvm-20/bin/clang++",
            },
        )
        assert report.ok
        [check] = [
            c for c in plan.checks if c.profile_id == "gcc14-build-clang20-client"
        ]
        # Producer compile: overlay resolves to its own pair, unaffected.
        assert check.compile_gcc_path == "/opt/gcc14/bin/g++"
        assert check.compile_gcc_options == "-std=gnu++17"
        # consumer_compile: resolves independently to its own pair.
        assert check.consumer_compile_gcc_path == "/opt/llvm-20/bin/clang++"
        assert check.consumer_compile_gcc_options == "-std=gnu++20 -stdlib=libc++"

    def test_consumer_binding_absent_from_resolved_bindings_leaves_path_empty(
        self,
    ) -> None:
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(
            config,
            {"gcc14-build-clang20-client": _bo("libfoo"), "plain": _bo("libfoo")},
            resolved_bindings={"gcc14": "/opt/gcc14/bin/g++"},
        )
        assert report.ok
        [check] = [
            c for c in plan.checks if c.profile_id == "gcc14-build-clang20-client"
        ]
        assert check.compile_gcc_path == "/opt/gcc14/bin/g++"
        assert check.consumer_compile_gcc_path == ""
        # Options still compose regardless of binding resolution.
        assert check.consumer_compile_gcc_options == "-std=gnu++20 -stdlib=libc++"

    def test_bundle_check_also_gets_consumer_compile_fields(self) -> None:
        raw = {
            "targets": {
                "libpvxs": {
                    "kind": "library",
                    "binary_pattern": "lib/libpvxs.so*",
                    "bundle": "pvxs-release",
                },
            },
            "bundles": {
                "pvxs-release": {
                    "targets": ["libpvxs"],
                    "checks": [
                        {"channel": "release", "depth": "binary", "required": True},
                    ],
                },
            },
            "profiles": {
                "gcc14-build-clang20-client": {
                    "contract": True,
                    "compile": {"binding": "gcc14"},
                    "consumer_compile": {"binding": "clang20", "standard": "gnu++20"},
                },
            },
            "baseline": {
                "channels": {
                    "release": {
                        "source": "github-release",
                        "asset_pattern": "pvxs-*",
                    },
                },
            },
        }
        config = _parsed(raw)
        plan, report = generate_run_plan(
            config,
            {"gcc14-build-clang20-client": _bo("libpvxs")},
            resolved_bindings={"clang20": "/opt/llvm-20/bin/clang++"},
        )
        assert report.ok
        [check] = plan.checks
        assert check.consumer_compile_gcc_path == "/opt/llvm-20/bin/clang++"
        assert check.consumer_compile_gcc_options == "-std=gnu++20"


class TestCompileFrontendOverlayProjection:
    """G34 Phase B: profiles.<id>.compile.frontend/consumer_compile.frontend
    reach the generated cell as compile_ast_frontend/
    consumer_compile_ast_frontend, resolved independently of each other and
    of binding-based gcc_path/gcc_options resolution."""

    _RAW = {
        "targets": {
            "libfoo": {
                "kind": "library",
                "binary_pattern": "build/libfoo*.so",
                "checks": [
                    {"channel": "release", "depth": "headers", "required": True},
                ],
            },
        },
        "profiles": {
            "gcc14-build-clang20-client": {
                "contract": True,
                "compile": {"frontend": "castxml"},
                "consumer_compile": {"frontend": "clang"},
            },
            "plain": {"contract": True},
        },
        "baseline": {
            "channels": {
                "release": {"source": "github-release", "asset_pattern": "libfoo-*"},
            },
        },
    }

    def test_no_frontend_override_leaves_both_fields_empty(self) -> None:
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(
            config,
            {"gcc14-build-clang20-client": _bo("libfoo"), "plain": _bo("libfoo")},
        )
        assert report.ok
        [check] = [c for c in plan.checks if c.profile_id == "plain"]
        assert check.compile_ast_frontend == ""
        assert check.consumer_compile_ast_frontend == ""
        assert "compile_ast_frontend" not in check.to_dict()
        assert "consumer_compile_ast_frontend" not in check.to_dict()

    def test_frontend_overrides_project_independently(self) -> None:
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(
            config,
            {"gcc14-build-clang20-client": _bo("libfoo"), "plain": _bo("libfoo")},
        )
        assert report.ok
        [check] = [
            c for c in plan.checks if c.profile_id == "gcc14-build-clang20-client"
        ]
        assert check.compile_ast_frontend == "castxml"
        assert check.consumer_compile_ast_frontend == "clang"

    def test_bundle_check_also_gets_frontend_fields(self) -> None:
        raw = {
            "targets": {
                "libpvxs": {
                    "kind": "library",
                    "binary_pattern": "lib/libpvxs.so*",
                    "bundle": "pvxs-release",
                },
            },
            "bundles": {
                "pvxs-release": {
                    "targets": ["libpvxs"],
                    "checks": [
                        {"channel": "release", "depth": "binary", "required": True},
                    ],
                },
            },
            "profiles": {
                "gcc14-build-clang20-client": {
                    "contract": True,
                    "compile": {"frontend": "castxml"},
                    "consumer_compile": {"frontend": "hybrid"},
                },
            },
            "baseline": {
                "channels": {
                    "release": {
                        "source": "github-release",
                        "asset_pattern": "pvxs-*",
                    },
                },
            },
        }
        config = _parsed(raw)
        plan, report = generate_run_plan(
            config, {"gcc14-build-clang20-client": _bo("libpvxs")}
        )
        assert report.ok
        [check] = plan.checks
        assert check.compile_ast_frontend == "castxml"
        assert check.consumer_compile_ast_frontend == "hybrid"


class TestComposeGccOptionsNotFamilyAware:
    """Regression guard for `_compose_gcc_options`'s own reverted P0 audit

    fix (see its docstring / AGENTS.md's "Toolchain-profile compiler-family
    rendering"): `compiler_family` must not affect this function's output.
    """

    _compose = staticmethod(_compose_gcc_options)

    def _spec(self, **kw: object) -> ProfileCompileSpec:
        return ProfileCompileSpec(**kw)  # type: ignore[arg-type]

    def test_gcc_family_still_emits_stdlib_and_target(self) -> None:
        spec = self._spec(
            compiler_family="gcc",
            standard="gnu++17",
            stdlib="libstdc++",
            target="x86_64-linux-gnu",
        )
        assert self._compose(spec) == (
            "-std=gnu++17 -stdlib=libstdc++ --target=x86_64-linux-gnu"
        )

    def test_clang_family_emits_stdlib_and_target(self) -> None:
        spec = self._spec(
            compiler_family="clang",
            standard="gnu++20",
            stdlib="libc++",
            target="x86_64-linux-gnu",
        )
        assert self._compose(spec) == (
            "-std=gnu++20 -stdlib=libc++ --target=x86_64-linux-gnu"
        )

    def test_unset_family_emits_stdlib_and_target(self) -> None:
        spec = self._spec(
            standard="gnu++17", stdlib="libstdc++", target="x86_64-linux-gnu"
        )
        assert self._compose(spec) == (
            "-std=gnu++17 -stdlib=libstdc++ --target=x86_64-linux-gnu"
        )

    def test_gcc_family_still_emits_standard_macros_and_args(self) -> None:
        spec = self._spec(
            compiler_family="gcc",
            standard="gnu++17",
            stdlib="libstdc++",
            abi_macros={"FOO": "1"},
            args=["-fno-rtti"],
        )
        assert self._compose(spec) == (
            "-std=gnu++17 -stdlib=libstdc++ -DFOO=1 -fno-rtti"
        )

    def test_no_fields_set_at_all_returns_empty_string(self) -> None:
        spec = self._spec(compiler_family="gcc", binding="gcc14")
        assert self._compose(spec) == ""

    def test_gcc_family_target_only_still_emitted(self) -> None:
        """The specific correctness case a review round caught: a
        GCC-family profile with only `target:` set (used with the
        direct-clang backend, which has no other way to steer parsing away
        from the host architecture) must still emit --target=."""
        spec = self._spec(compiler_family="gcc", target="aarch64-linux-gnu")
        assert self._compose(spec) == "--target=aarch64-linux-gnu"


class TestToolchainMatrixFixtureExample:
    """Loads the committed toolchain-matrix reference example
    (``tests/fixtures/run_plan/toolchain_matrix/``, task #9) — not an inline
    dict like the tests above, the actual checked-in `.abicheck.yml`/
    `toolchain-bindings.yml` pair its own README walks through — and asserts
    the exact `run-plan generate` output the README documents, so the two
    can't silently drift apart."""

    _DIR = FIXTURES_DIR / "run_plan" / "toolchain_matrix"

    def test_fixture_files_exist(self) -> None:
        assert (self._DIR / ".abicheck.yml").is_file()
        assert (self._DIR / "toolchain-bindings.yml").is_file()
        assert (self._DIR / "README.md").is_file()

    def test_two_profiles_resolve_to_documented_compile_context(self) -> None:
        import yaml

        from abicheck.buildsource.toolchain_bindings import load_bindings_file

        config = ProjectTargetsConfig.from_dict(
            yaml.safe_load((self._DIR / ".abicheck.yml").read_text(encoding="utf-8"))
        )
        bindings_file = load_bindings_file(self._DIR / "toolchain-bindings.yml")
        plan, report = generate_run_plan(
            config,
            {
                "linux-gcc14": _bo("libmatrixdemo"),
                "linux-clang20": _bo("libmatrixdemo"),
            },
            resolved_bindings=bindings_file.bindings,
        )
        assert report.ok
        by_profile = {c.profile_id: c for c in plan.checks}
        assert by_profile.keys() == {"linux-gcc14", "linux-clang20"}

        gcc = by_profile["linux-gcc14"]
        assert gcc.compile_gcc_path == "/opt/gcc-14.2.0/bin/g++"
        assert gcc.compile_gcc_options == "-std=gnu++17 -stdlib=libstdc++"

        clang = by_profile["linux-clang20"]
        assert clang.compile_gcc_path == "/opt/llvm-20/bin/clang++"
        assert clang.compile_gcc_options == (
            "-std=gnu++20 -stdlib=libc++ -DMATRIXDEMO_ABI_V2=1 -fno-rtti"
        )

    def test_cli_end_to_end_matches_readme(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exact CLI invocation README.md's "Reproduce it yourself"
        section documents, run against the committed fixture files."""
        # The fixture's toolchain-bindings.yml deliberately names illustrative
        # paths ("/opt/gcc-14.2.0/bin/g++") that don't exist on any real
        # machine -- G34 Phase A's toolchain-identity check now probes a
        # resolved binding for real, so this stubs that probe to report an
        # identity consistent with what the fixture's own compiler_family/
        # compiler_version declare, the same way a real installation would.
        from abicheck.buildsource import toolchain_probe as tp

        def _fake_metadata(path: str) -> dict[str, str]:
            if "gcc" in path:
                return {"selected": path, "version": "gcc (Debian 14.2.0) 14.2.0"}
            return {"selected": path, "version": "clang version 20.0.0"}

        monkeypatch.setattr(tp, "_tool_identity_metadata", _fake_metadata)
        bo_gcc14 = _write_build_output(tmp_path, "linux-gcc14", ["libmatrixdemo"])
        bo_clang20 = _write_build_output(tmp_path, "linux-clang20", ["libmatrixdemo"])
        result = CliRunner().invoke(
            main,
            [
                "project",
                "plan",
                str(self._DIR / ".abicheck.yml"),
                "--build-output",
                f"linux-gcc14={bo_gcc14}",
                "--build-output",
                f"linux-clang20={bo_clang20}",
                "--toolchain-bindings",
                str(self._DIR / "toolchain-bindings.yml"),
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        by_profile = {c["profile_id"]: c for c in data["checks"]}
        assert (
            by_profile["linux-gcc14"]["compile_gcc_options"]
            == "-std=gnu++17 -stdlib=libstdc++"
        )
        assert (
            by_profile["linux-clang20"]["compile_gcc_options"]
            == "-std=gnu++20 -stdlib=libc++ -DMATRIXDEMO_ABI_V2=1 -fno-rtti"
        )


def _write_config(tmp_path: Path, raw: dict) -> Path:
    import yaml

    path = tmp_path / ".abicheck.yml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def _write_build_output(tmp_path: Path, profile: str, target_ids: list[str]) -> Path:
    d = tmp_path / f"build-{profile}"
    d.mkdir()
    (d / "build-output.json").write_text(
        json.dumps(
            {
                "schema": "abicheck.build-output/v1",
                "targets": [
                    {"id": t, "binary": f"artifacts/{t}.so"} for t in target_ids
                ],
            }
        ),
        encoding="utf-8",
    )
    return d


class TestRunPlanGenerateCli:
    def test_generate_writes_valid_json_and_exits_zero(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, _SINGLE_PROFILE_LIBRARY_RAW)
        build_dir = _write_build_output(tmp_path, "linux", ["libfoo"])
        result = CliRunner().invoke(
            main,
            [
                "project",
                "plan",
                str(config),
                "--build-output",
                f"linux={build_dir}",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert [c["check_id"] for c in data["checks"]] == [
            "libfoo@linux#release@headers"
        ]

    def test_aggregate_gate_config_stamps_the_generated_plan(
        self, tmp_path: Path
    ) -> None:
        """CLI cleanup phase two, PR 2 follow-up: CONFIG's `aggregate: gate:`
        block reaches the generated run-plan.json's own `gate` block through
        the real CLI -- the durable-config replacement for the removed
        --gate-missing-required/--gate-unexpected-target flags."""
        raw = json.loads(json.dumps(_SINGLE_PROFILE_LIBRARY_RAW))
        raw["aggregate"] = {
            "gate": {"missing_required": "warn", "unexpected_target": "fail"}
        }
        config = _write_config(tmp_path, raw)
        build_dir = _write_build_output(tmp_path, "linux", ["libfoo"])
        result = CliRunner().invoke(
            main,
            [
                "project",
                "plan",
                str(config),
                "--build-output",
                f"linux={build_dir}",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["gate"] == {"missing_required": "warn", "unexpected_target": "fail"}
        # A `gate`-carrying plan must be stamped v2 (RUN_PLAN_SCHEMA_GATE) --
        # a v1-stamped plan with `gate` present would let an old, pre-gate
        # reader's `RunPlan.from_dict()` silently ignore the block and apply
        # the wrong hard-coded default policy instead (CodeRabbit review).
        assert data["schema"] == "abicheck.run-plan/v2"

    def test_no_aggregate_gate_config_omits_gate_key(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, _SINGLE_PROFILE_LIBRARY_RAW)
        build_dir = _write_build_output(tmp_path, "linux", ["libfoo"])
        result = CliRunner().invoke(
            main,
            [
                "project",
                "plan",
                str(config),
                "--build-output",
                f"linux={build_dir}",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert "gate" not in data
        # No gate policy configured -> the plan keeps the unchanged v1
        # schema string, not the gate-bearing v2 (mirrors the assertion in
        # the sibling configured-gate test above, CodeRabbit review).
        assert data["schema"] == "abicheck.run-plan/v1"

    def test_gate_flags_removed_no_cli_alias(self, tmp_path: Path) -> None:
        """CLI cleanup phase two, PR 2 follow-up: --gate-missing-required/
        --gate-unexpected-target were removed from `project plan` with no
        deprecation alias -- the policy is durable project config now (see
        the two tests above), not a per-invocation flag."""
        config = _write_config(tmp_path, _SINGLE_PROFILE_LIBRARY_RAW)
        build_dir = _write_build_output(tmp_path, "linux", ["libfoo"])
        for flag, value in (
            ("--gate-missing-required", "warn"),
            ("--gate-unexpected-target", "fail"),
        ):
            result = CliRunner().invoke(
                main,
                [
                    "project",
                    "plan",
                    str(config),
                    "--build-output",
                    f"linux={build_dir}",
                    flag,
                    value,
                ],
            )
            assert result.exit_code == 64, result.output
            assert "No such option" in result.output

    def test_generate_exits_one_on_unresolved_explicit_profile(
        self, tmp_path: Path
    ) -> None:
        raw = json.loads(json.dumps(_LIBRARY_ONLY_RAW))
        raw["targets"]["libfoo"]["checks"][0]["profiles"] = ["linux"]
        config = _write_config(tmp_path, raw)
        result = CliRunner().invoke(main, ["project", "plan", str(config)])
        assert result.exit_code == 1
        assert "linux" in result.output

    def test_generate_exits_64_on_invalid_config(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, {"targets": {"libfoo": {"kind": "library"}}})
        result = CliRunner().invoke(main, ["project", "plan", str(config)])
        assert result.exit_code == 64

    def test_generate_exits_64_on_malformed_build_output_spec(
        self, tmp_path: Path
    ) -> None:
        config = _write_config(tmp_path, _LIBRARY_ONLY_RAW)
        result = CliRunner().invoke(
            main,
            ["project", "plan", str(config), "--build-output", "not-a-kv-pair"],
        )
        assert result.exit_code == 64

    def test_generate_exits_64_on_build_output_profile_id_mismatch(
        self, tmp_path: Path
    ) -> None:
        """A build-output.json whose own declared profile.id doesn't match
        the PROFILE key it's passed under is almost certainly a stale or
        misnamed artifact -- rejected instead of silently trusting the
        caller-provided key (Codex review)."""
        config = _write_config(tmp_path, _LIBRARY_ONLY_RAW)
        build_dir = tmp_path / "build-mismatched"
        build_dir.mkdir()
        (build_dir / "build-output.json").write_text(
            json.dumps(
                {
                    "schema": "abicheck.build-output/v1",
                    "profile": {"id": "mac"},
                    "targets": [{"id": "libfoo", "binary": "artifacts/libfoo.so"}],
                }
            ),
            encoding="utf-8",
        )
        result = CliRunner().invoke(
            main,
            [
                "project",
                "plan",
                str(config),
                "--build-output",
                f"linux={build_dir}",
            ],
        )
        assert result.exit_code == 64
        assert "mac" in result.output
        assert "linux" in result.output

    def test_generate_exits_64_on_duplicate_build_output_profile(
        self, tmp_path: Path
    ) -> None:
        """Two --build-output specs naming the same profile id used to
        silently overwrite the first with the second (dict assignment); a
        repeated profile is almost certainly a caller mistake, so it's now
        a hard usage error instead (Codex review)."""
        config = _write_config(tmp_path, _LIBRARY_ONLY_RAW)
        build_dir_1 = _write_build_output(tmp_path, "linux", ["libfoo"])
        build_dir_2 = tmp_path / "linux-again"
        build_dir_2.mkdir()
        result = CliRunner().invoke(
            main,
            [
                "project",
                "plan",
                str(config),
                "--build-output",
                f"linux={build_dir_1}",
                "--build-output",
                f"linux={build_dir_2}",
            ],
        )
        assert result.exit_code == 64
        assert "linux" in result.output
        assert "more than once" in result.output

    def test_generate_text_format_lists_checks(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, _SINGLE_PROFILE_LIBRARY_RAW)
        build_dir = _write_build_output(tmp_path, "linux", ["libfoo"])
        result = CliRunner().invoke(
            main,
            [
                "project",
                "plan",
                str(config),
                "--build-output",
                f"linux={build_dir}",
                "--format",
                "text",
            ],
        )
        assert result.exit_code == 0
        assert "libfoo@linux#release@headers" in result.output

    def test_generate_exits_64_when_build_output_dir_has_no_manifest(
        self, tmp_path: Path
    ) -> None:
        """A syntactically valid PROFILE=DIR spec whose DIR has no
        build-output.json at all (load_build_output's FileNotFoundError)."""
        config = _write_config(tmp_path, _LIBRARY_ONLY_RAW)
        empty_dir = tmp_path / "empty-build-dir"
        empty_dir.mkdir()
        result = CliRunner().invoke(
            main,
            [
                "project",
                "plan",
                str(config),
                "--build-output",
                f"linux={empty_dir}",
            ],
        )
        assert result.exit_code == 64
        assert "linux" in result.output

    def test_generate_exits_64_on_malformed_yaml(self, tmp_path: Path) -> None:
        config = tmp_path / ".abicheck.yml"
        config.write_text(
            "targets: [this is not, valid: yaml: at all", encoding="utf-8"
        )
        result = CliRunner().invoke(main, ["project", "plan", str(config)])
        assert result.exit_code == 64

    def test_generate_exits_64_when_config_is_not_a_mapping(
        self, tmp_path: Path
    ) -> None:
        config = tmp_path / ".abicheck.yml"
        config.write_text("- just\n- a\n- list\n", encoding="utf-8")
        result = CliRunner().invoke(main, ["project", "plan", str(config)])
        assert result.exit_code == 64

    # ── --allow-empty (ADR-054: fail-closed by default on zero checks) ──────

    def test_empty_run_plan_exits_one_by_default(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, {"targets": {}})
        result = CliRunner().invoke(main, ["project", "plan", str(config)])
        assert result.exit_code == 1, result.output
        assert "--allow-empty" in result.output
        # The run-plan artifact is still emitted (an empty checks: list),
        # even though the command signals failure via exit code.
        assert '"checks": []' in result.stdout

    def test_empty_run_plan_exits_zero_with_allow_empty(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, {"targets": {}})
        result = CliRunner().invoke(
            main, ["project", "plan", str(config), "--allow-empty"]
        )
        assert result.exit_code == 0, result.output
        assert '"checks": []' in result.stdout

    def test_non_empty_run_plan_ignores_allow_empty(self, tmp_path: Path) -> None:
        """--allow-empty only relaxes the zero-checks case -- a resolved,
        non-empty run-plan is unaffected either way."""
        config = _write_config(tmp_path, _SINGLE_PROFILE_LIBRARY_RAW)
        build_dir = _write_build_output(tmp_path, "linux", ["libfoo"])
        result = CliRunner().invoke(
            main,
            [
                "project",
                "plan",
                str(config),
                "--build-output",
                f"linux={build_dir}",
                "--allow-empty",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert len(data["checks"]) == 1


def _write_bindings_file(tmp_path: Path, bindings: dict) -> Path:
    import yaml

    path = tmp_path / "toolchain-bindings.yml"
    path.write_text(
        yaml.safe_dump(
            {"schema": "abicheck.toolchain-bindings/v1", "bindings": bindings}
        ),
        encoding="utf-8",
    )
    return path


class TestRunPlanGenerateCliToolchainBindings:
    """P1 toolchain-profile audit: `run-plan generate --toolchain-bindings`."""

    _RAW = {
        "targets": {
            "libfoo": {
                "kind": "library",
                "binary_pattern": "build/libfoo*.so",
                "checks": [
                    {"channel": "release", "depth": "headers", "required": True},
                ],
            },
        },
        "profiles": {
            "gcc14": {"contract": True, "compile": {"binding": "gcc14"}},
        },
        "baseline": {
            "channels": {
                "release": {"source": "github-release", "asset_pattern": "libfoo-*"},
            },
        },
    }

    def test_without_the_flag_compile_gcc_path_stays_absent(
        self, tmp_path: Path
    ) -> None:
        config = _write_config(tmp_path, self._RAW)
        build_dir = _write_build_output(tmp_path, "gcc14", ["libfoo"])
        result = CliRunner().invoke(
            main,
            [
                "project",
                "plan",
                str(config),
                "--build-output",
                f"gcc14={build_dir}",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert "compile_gcc_path" not in data["checks"][0]

    def test_resolvable_binding_populates_compile_gcc_path(
        self, tmp_path: Path
    ) -> None:
        config = _write_config(tmp_path, self._RAW)
        build_dir = _write_build_output(tmp_path, "gcc14", ["libfoo"])
        bindings = _write_bindings_file(tmp_path, {"gcc14": "/opt/gcc14/bin/g++"})
        result = CliRunner().invoke(
            main,
            [
                "project",
                "plan",
                str(config),
                "--build-output",
                f"gcc14={build_dir}",
                "--toolchain-bindings",
                str(bindings),
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["checks"][0]["compile_gcc_path"] == "/opt/gcc14/bin/g++"

    def test_unresolvable_binding_exits_one(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, self._RAW)
        build_dir = _write_build_output(tmp_path, "gcc14", ["libfoo"])
        bindings = _write_bindings_file(tmp_path, {"clang20": "/opt/clang/bin/clang++"})
        result = CliRunner().invoke(
            main,
            [
                "project",
                "plan",
                str(config),
                "--build-output",
                f"gcc14={build_dir}",
                "--toolchain-bindings",
                str(bindings),
            ],
        )
        assert result.exit_code == 1
        assert "gcc14" in result.output

    def test_family_mismatch_exits_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # G34 Phase A parity: project plan must run the same
        # toolchain-identity check project validate runs, not just binding
        # resolution -- a run-plan that silently emits the wrong compiler's
        # path is worse than one that fails to generate at all.
        from abicheck.buildsource import toolchain_probe as tp

        monkeypatch.setattr(
            tp,
            "_tool_identity_metadata",
            lambda path: {"selected": path, "version": "clang version 18.0.0"},
        )
        raw = json.loads(json.dumps(self._RAW))
        raw["profiles"]["gcc14"]["compile"]["compiler_family"] = "gcc"
        config = _write_config(tmp_path, raw)
        build_dir = _write_build_output(tmp_path, "gcc14", ["libfoo"])
        bindings = _write_bindings_file(tmp_path, {"gcc14": "/opt/clang/bin/clang++"})
        result = CliRunner().invoke(
            main,
            [
                "project",
                "plan",
                str(config),
                "--build-output",
                f"gcc14={build_dir}",
                "--toolchain-bindings",
                str(bindings),
            ],
        )
        assert result.exit_code == 1
        assert "compiler_family" in result.output

    def test_malformed_bindings_file_exits_64(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, self._RAW)
        build_dir = _write_build_output(tmp_path, "gcc14", ["libfoo"])
        bindings = tmp_path / "bad-bindings.yml"
        bindings.write_text("schema: wrong-schema\n", encoding="utf-8")
        result = CliRunner().invoke(
            main,
            [
                "project",
                "plan",
                str(config),
                "--build-output",
                f"gcc14={build_dir}",
                "--toolchain-bindings",
                str(bindings),
            ],
        )
        assert result.exit_code == 64

    def test_unused_profiles_mismatch_does_not_abort_an_otherwise_valid_plan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: check_profile_toolchain_identity previously probed
        # EVERY declared profile, not just the ones the generated plan
        # actually resolved a check for. A bindings file may legitimately be
        # shared across runners (e.g. one committed file naming both a Linux
        # and a macOS toolchain); a non-contract, unreferenced profile's
        # binding can name a platform-specific compiler that's simply
        # unavailable/mismatched on the current host, and that must not
        # abort an otherwise-valid plan that never uses it (Codex review,
        # fresh evidence).
        from abicheck.buildsource import toolchain_probe as tp

        def _fake_metadata(path: str) -> dict[str, str]:
            if path == "/opt/clang/bin/clang++":
                return {"selected": path, "version": "clang version 18.0.0"}
            return {"selected": path, "version": "gcc (GCC) 14.2.0"}

        monkeypatch.setattr(tp, "_tool_identity_metadata", _fake_metadata)
        raw = json.loads(json.dumps(self._RAW))
        raw["profiles"]["gcc14"]["compile"]["compiler_family"] = "gcc"
        # A non-contract, unreferenced profile (contract: false): never
        # selected by the implicit "every contract profile" sweep, so the
        # generated plan never resolves a check for it --
        # its declared compiler_family (gcc) disagrees with the resolved
        # binding's real family (clang) via _fake_metadata above.
        raw["profiles"]["macclang"] = {
            "contract": False,
            "compile": {"binding": "macclang", "compiler_family": "gcc"},
        }
        config = _write_config(tmp_path, raw)
        build_dir = _write_build_output(tmp_path, "gcc14", ["libfoo"])
        bindings = _write_bindings_file(
            tmp_path,
            {"gcc14": "/opt/gcc/bin/gcc", "macclang": "/opt/clang/bin/clang++"},
        )
        result = CliRunner().invoke(
            main,
            [
                "project",
                "plan",
                str(config),
                "--build-output",
                f"gcc14={build_dir}",
                "--toolchain-bindings",
                str(bindings),
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert len(data["checks"]) == 1
        assert data["checks"][0]["profile_id"] == "gcc14"


# NB: `abicheck run-plan to-aggregate-manifest` (the standalone CLI command)
# is not public CLI surface anymore (ADR-054): it was a pure intermediate-
# format conversion. Its logic is `to_aggregate_manifest()`, unit-tested
# directly above (TestToAggregateManifest); the CLI-level entry point is now
# `aggregate --run-plan`, covered in tests/test_aggregate.py::TestAggregateCLI.


@pytest.mark.parametrize(
    "kind,binary_pattern",
    [(RUN_PLAN_KIND_TARGET, "x"), (RUN_PLAN_KIND_BUNDLE, "")],
)
def test_check_to_dict_omits_kind_inappropriate_fields(
    kind: str, binary_pattern: str
) -> None:
    check = RunPlanCheck(kind=kind, binary_pattern=binary_pattern, bundle_members=["a"])
    d = check.to_dict()
    if kind == RUN_PLAN_KIND_BUNDLE:
        assert "target_kind" not in d
        assert "binary_pattern" not in d
        assert d["bundle_members"] == ["a"]
    else:
        assert "bundle_members" not in d
        assert d["binary_pattern"] == "x"


class TestSchedulingProjection:
    """G34 Phase C: each cell carries the runner it must be scheduled on and
    the dependency source it provisions with, both derived from its own
    profile — the two axes `check-project.yml` previously fixed for every
    cell in a run."""

    _RAW = {
        "targets": {
            "libfoo": {
                "kind": "library",
                "binary_pattern": "build/libfoo*.so",
                "checks": [
                    {"channel": "release", "depth": "headers", "required": True},
                ],
            },
        },
        "profiles": {
            "linux-gcc14": {
                "contract": True,
                "os": "linux",
                "dependency_source": "conda-forge-gcc14",
            },
            "windows-msvc": {"contract": True, "os": "windows"},
            "plain": {"contract": True},
        },
        "baseline": {
            "channels": {
                "release": {"source": "github-release", "asset_pattern": "libfoo-*"},
            },
        },
    }

    def _plan(self) -> dict[str, object]:
        plan, report = generate_run_plan(
            _parsed(self._RAW),
            {p: _bo("libfoo") for p in ("linux-gcc14", "windows-msvc", "plain")},
        )
        assert report.ok
        return {c.profile_id: c for c in plan.checks}

    def test_os_selects_the_runner(self) -> None:
        by_profile = self._plan()
        assert by_profile["linux-gcc14"].runs_on == "ubuntu-latest"
        assert by_profile["windows-msvc"].runs_on == "windows-latest"

    def test_a_profile_without_os_keeps_todays_runner(self) -> None:
        """Every profile written before this phase is this one."""
        assert self._plan()["plain"].runs_on == "ubuntu-latest"

    def test_runs_on_is_always_serialized(self) -> None:
        """Unlike every other optional field here: `check-project.yml` reads
        it as `matrix.runs_on`, and a matrix entry missing the key resolves
        `runs-on:` to the empty string, scheduling nothing."""
        d = self._plan()["plain"].to_dict()
        assert d["runs_on"] == "ubuntu-latest"

    def test_dependency_source_projects_per_cell(self) -> None:
        by_profile = self._plan()
        assert by_profile["linux-gcc14"].dependency_source == "conda-forge-gcc14"
        assert (
            by_profile["linux-gcc14"].to_dict()["dependency_source"]
            == "conda-forge-gcc14"
        )

    def test_an_undeclared_dependency_source_stays_empty(self) -> None:
        """Empty leaves the caller's workflow-level default standing, rather
        than this module picking one for a project that never asked."""
        check = self._plan()["windows-msvc"]
        assert check.dependency_source == ""
        assert "dependency_source" not in check.to_dict()

    def test_both_fields_round_trip(self) -> None:
        check = self._plan()["linux-gcc14"]
        assert RunPlanCheck.from_dict(check.to_dict()) == check

    def test_a_plan_from_an_older_abicheck_still_resolves_a_runner(self) -> None:
        """`from_dict` defaults a missing `runs_on` rather than emptying it —
        the same case `check-project.yml`'s own `|| 'ubuntu-latest'` covers on
        its side."""
        assert RunPlanCheck.from_dict({"check_id": "x"}).runs_on == "ubuntu-latest"

    def test_bundle_cells_are_scheduled_too(self) -> None:
        raw = {
            "targets": {
                "libpvxs": {
                    "kind": "library",
                    "binary_pattern": "lib/libpvxs.so*",
                    "bundle": "pvxs-release",
                },
            },
            "bundles": {
                "pvxs-release": {
                    "targets": ["libpvxs"],
                    "checks": [
                        {"channel": "release", "depth": "binary", "required": True},
                    ],
                },
            },
            "profiles": {
                "linux-gcc14": {
                    "contract": True,
                    "os": "linux",
                    "dependency_source": "conda-forge-clang20",
                },
            },
            "baseline": {
                "channels": {
                    "release": {
                        "source": "github-release",
                        "asset_pattern": "pvxs-*",
                    },
                },
            },
        }
        plan, report = generate_run_plan(_parsed(raw), {"linux-gcc14": _bo("libpvxs")})
        assert report.ok
        [check] = plan.checks
        assert check.runs_on == "ubuntu-latest"
        assert check.dependency_source == "conda-forge-clang20"

    def test_an_unknown_profile_falls_back_to_the_defaults(self) -> None:
        """Matches how every other `*_for_profile` helper here treats a
        profile it cannot find: the cell is generated either way, and a
        missing profile is a separate, already-reported error rather than
        this helper's to raise on."""
        assert _scheduling_fields_for_profile(_parsed(self._RAW), "nope") == (
            "ubuntu-latest",
            "",
        )

    def test_an_unroutable_os_raises_rather_than_defaulting(self) -> None:
        """Generation refuses rather than quietly scheduling a non-Linux
        profile on a Linux runner. `project validate` reports the same
        condition first; reaching this means validation was skipped."""
        raw = {
            **self._RAW,
            "profiles": {"odd": {"contract": True, "os": "freebsd"}},
        }
        with pytest.raises(ValueError, match="does not name a platform"):
            generate_run_plan(_parsed(raw), {"odd": _bo("libfoo")})
