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
from abicheck.buildsource.project_targets import ProjectTargetsConfig
from abicheck.buildsource.run_plan import (
    RUN_PLAN_KIND_BUNDLE,
    RUN_PLAN_KIND_TARGET,
    RunPlan,
    RunPlanCheck,
    generate_run_plan,
    to_aggregate_manifest,
)
from abicheck.cli import main


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


class TestImplicitSweep:
    def test_profile_missing_from_build_outputs_is_a_warning_not_an_error(self) -> None:
        config = _parsed(_LIBRARY_ONLY_RAW)
        plan, report = generate_run_plan(config, {"linux": _bo("libfoo")})
        assert report.ok
        assert any("mac" in w for w in report.warnings)
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

    def test_bundle_check_missing_build_output_for_an_implicit_sweep_is_a_warning(
        self,
    ) -> None:
        config = _parsed(self._RAW)
        plan, report = generate_run_plan(config, {})
        assert report.ok
        assert not plan.checks
        assert any("linux" in w for w in report.warnings)


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
        assert manifest["aggregate_manifest_version"] == "1.0"
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
        config = _write_config(tmp_path, _LIBRARY_ONLY_RAW)
        build_dir = _write_build_output(tmp_path, "linux", ["libfoo"])
        result = CliRunner().invoke(
            main,
            [
                "run-plan",
                "generate",
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

    def test_generate_exits_one_on_unresolved_explicit_profile(
        self, tmp_path: Path
    ) -> None:
        raw = json.loads(json.dumps(_LIBRARY_ONLY_RAW))
        raw["targets"]["libfoo"]["checks"][0]["profiles"] = ["linux"]
        config = _write_config(tmp_path, raw)
        result = CliRunner().invoke(main, ["run-plan", "generate", str(config)])
        assert result.exit_code == 1
        assert "linux" in result.output

    def test_generate_exits_64_on_invalid_config(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, {"targets": {"libfoo": {"kind": "library"}}})
        result = CliRunner().invoke(main, ["run-plan", "generate", str(config)])
        assert result.exit_code == 64

    def test_generate_exits_64_on_malformed_build_output_spec(
        self, tmp_path: Path
    ) -> None:
        config = _write_config(tmp_path, _LIBRARY_ONLY_RAW)
        result = CliRunner().invoke(
            main,
            ["run-plan", "generate", str(config), "--build-output", "not-a-kv-pair"],
        )
        assert result.exit_code == 64

    def test_generate_text_format_lists_checks(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, _LIBRARY_ONLY_RAW)
        build_dir = _write_build_output(tmp_path, "linux", ["libfoo"])
        result = CliRunner().invoke(
            main,
            [
                "run-plan",
                "generate",
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
                "run-plan",
                "generate",
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
        result = CliRunner().invoke(main, ["run-plan", "generate", str(config)])
        assert result.exit_code == 64

    def test_generate_exits_64_when_config_is_not_a_mapping(
        self, tmp_path: Path
    ) -> None:
        config = tmp_path / ".abicheck.yml"
        config.write_text("- just\n- a\n- list\n", encoding="utf-8")
        result = CliRunner().invoke(main, ["run-plan", "generate", str(config)])
        assert result.exit_code == 64


class TestRunPlanToAggregateManifestCli:
    def test_projects_run_plan_json_to_manifest(self, tmp_path: Path) -> None:
        plan = RunPlan(
            head_sha="deadbeef",
            checks=[
                RunPlanCheck(check_id="libfoo@linux#release@headers", required=True)
            ],
        )
        run_plan_path = tmp_path / "run-plan.json"
        run_plan_path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")

        result = CliRunner().invoke(
            main, ["run-plan", "to-aggregate-manifest", str(run_plan_path)]
        )
        assert result.exit_code == 0, result.output
        manifest = json.loads(result.stdout)
        assert manifest["targets"] == [
            {"id": "libfoo@linux#release@headers", "required": True}
        ]

    def test_head_sha_override(self, tmp_path: Path) -> None:
        plan = RunPlan(head_sha="deadbeef", checks=[])
        run_plan_path = tmp_path / "run-plan.json"
        run_plan_path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")

        result = CliRunner().invoke(
            main,
            [
                "run-plan",
                "to-aggregate-manifest",
                str(run_plan_path),
                "--head-sha",
                "cafef00d",
            ],
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["head_sha"] == "cafef00d"

    def test_malformed_json_is_a_usage_error(self, tmp_path: Path) -> None:
        run_plan_path = tmp_path / "run-plan.json"
        run_plan_path.write_text("not json", encoding="utf-8")
        result = CliRunner().invoke(
            main, ["run-plan", "to-aggregate-manifest", str(run_plan_path)]
        )
        assert result.exit_code == 64

    def test_json_object_that_is_not_a_mapping_is_a_usage_error(
        self, tmp_path: Path
    ) -> None:
        run_plan_path = tmp_path / "run-plan.json"
        run_plan_path.write_text("[1, 2, 3]", encoding="utf-8")
        result = CliRunner().invoke(
            main, ["run-plan", "to-aggregate-manifest", str(run_plan_path)]
        )
        assert result.exit_code == 64

    def test_output_file_option_writes_to_disk(self, tmp_path: Path) -> None:
        plan = RunPlan(checks=[RunPlanCheck(check_id="a@b#c@d")])
        run_plan_path = tmp_path / "run-plan.json"
        run_plan_path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")
        out_path = tmp_path / "manifest.json"

        result = CliRunner().invoke(
            main,
            [
                "run-plan",
                "to-aggregate-manifest",
                str(run_plan_path),
                "-o",
                str(out_path),
            ],
        )
        assert result.exit_code == 0, result.output
        manifest = json.loads(out_path.read_text(encoding="utf-8"))
        assert manifest["targets"] == [{"id": "a@b#c@d", "required": True}]


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
