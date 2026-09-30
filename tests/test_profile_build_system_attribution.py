# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""WS-A per-profile attribution: build-output.json ``profile.build_system``
travels into the run-plan cell for that profile and into the check-target
report envelope (``profile_build_system``, report schema 5.11), so a cell's
findings name the build lane that produced them."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import jsonschema
import pytest
from test_action_check_target import (
    _BASE_IDENTITY,
    RUN_SH,
    _run_finalize,
    _write_compare_report,
)

from abicheck.buildsource.build_output import BuildOutput, BuildOutputTarget
from abicheck.buildsource.build_output_profile import (
    BuildOutputBuildSystem,
    BuildOutputProfile,
)
from abicheck.buildsource.check_report_build_system import stamp_profile_build_system
from abicheck.buildsource.project_targets import ProjectTargetsConfig
from abicheck.buildsource.run_plan import RunPlanCheck, generate_run_plan
from abicheck.schemas.documents import load_compare_report_schema

_SYSTEMS: list[BuildOutputBuildSystem | None] = [
    None,
    BuildOutputBuildSystem("cmake", "Ninja"),
    BuildOutputBuildSystem("cmake", "Unix Makefiles"),
    BuildOutputBuildSystem("bazel", ""),
    BuildOutputBuildSystem("make", ""),
]


def _config(profiles: list[str]) -> ProjectTargetsConfig:
    return ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "build/libfoo*.so",
                    "checks": [
                        {"channel": "release", "depth": "headers", "required": True}
                    ],
                }
            },
            "profiles": {p: {"contract": True} for p in profiles},
            "baseline": {
                "channels": {
                    "release": {"source": "github-release", "asset_pattern": "libfoo-*"}
                }
            },
        }
    )


def _bo(profile_id: str, bs: BuildOutputBuildSystem | None) -> BuildOutput:
    return BuildOutput(
        profile=BuildOutputProfile(id=profile_id, build_system=bs),
        targets=[BuildOutputTarget(id="libfoo", binary="artifacts/libfoo.so")],
    )


def test_each_cell_carries_its_own_profiles_build_system():
    """Every pair of profiles, every combination of declared build systems:
    a cell's identity is its own profile's, never a sibling's."""
    for a, b in itertools.product(_SYSTEMS, repeat=2):
        plan, report = generate_run_plan(
            _config(["p1", "p2"]), {"p1": _bo("p1", a), "p2": _bo("p2", b)}
        )
        assert not report.errors, report.errors
        by_profile = {c.profile_id: c for c in plan.checks}
        for pid, bs in (("p1", a), ("p2", b)):
            cell = by_profile[pid]
            assert (cell.build_system, cell.build_generator) == (
                (bs.name, bs.generator) if bs else ("", "")
            )
            d = cell.to_dict()
            if bs is None:
                assert "build_system" not in d and "build_generator" not in d
            else:
                assert d["build_system"] == bs.name
            assert RunPlanCheck.from_dict(d) == cell


@pytest.mark.parametrize(
    ("name", "generator"), [("cmake", "Ninja"), ("bazel", ""), (" make ", " ")]
)
def test_stamp_sets_block_and_validates_against_schema(name, generator):
    report = {"profile_id": "p"}
    stamp_profile_build_system(report, name=name, generator=generator)
    assert report["profile_build_system"] == {
        "name": name.strip(),
        "generator": generator.strip(),
    }
    sub = load_compare_report_schema()["properties"]["profile_build_system"]
    jsonschema.validate(report["profile_build_system"], sub)


@pytest.mark.parametrize("name", ["", "  "])
def test_unrecorded_build_system_is_absent_not_empty(name):
    report: dict = {}
    stamp_profile_build_system(report, name=name, generator="Ninja")
    assert "profile_build_system" not in report


pytestmark_run_sh = pytest.mark.skipif(not RUN_SH.is_file(), reason="run.sh missing")


@pytestmark_run_sh
@pytest.mark.parametrize(
    ("resolve", "expect_analysis"),
    [("resolved", True), ("ambiguous", False)],
)
def test_real_run_sh_stamps_every_envelope_mode(
    tmp_path: Path, resolve, expect_analysis
):
    env = {
        **_BASE_IDENTITY,
        "INPUT_BUILD_SYSTEM": "cmake",
        "INPUT_BUILD_GENERATOR": "Ninja",
        "RESOLVE_RAN": "true",
        "RESOLVE_OUTCOME": resolve,
        "RESOLVE_MESSAGE": "x",
    }
    if expect_analysis:
        report_path = tmp_path / "analysis.json"
        _write_compare_report(report_path, verdict="BREAKING", exit_code=4)
        env.update({"ANALYSIS_RAN": "true", "ANALYSIS_REPORT_PATH": str(report_path)})
    _, outputs = _run_finalize(env, tmp_path)
    report = json.loads((tmp_path / outputs["report-path"]).read_text())
    assert report["profile_build_system"] == {"name": "cmake", "generator": "Ninja"}


@pytestmark_run_sh
def test_real_run_sh_without_build_system_omits_block(tmp_path: Path):
    report_path = tmp_path / "analysis.json"
    _write_compare_report(report_path, verdict="BREAKING", exit_code=4)
    _, outputs = _run_finalize(
        {
            **_BASE_IDENTITY,
            "RESOLVE_RAN": "true",
            "RESOLVE_OUTCOME": "resolved",
            "ANALYSIS_RAN": "true",
            "ANALYSIS_REPORT_PATH": str(report_path),
        },
        tmp_path,
    )
    report = json.loads((tmp_path / outputs["report-path"]).read_text())
    assert "profile_build_system" not in report
