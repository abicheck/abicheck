# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``build-output.json``'s ``profile.build_system`` (WS-A, P0.7 build-system
axis): parse/round-trip, shape validation, and agreement with the build
evidence a target's ``attribution_path`` names."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.buildsource.build_evidence import BuildEvidence, Generator
from abicheck.buildsource.build_output import (
    BUILD_OUTPUT_SCHEMA,
    BuildOutput,
    load_build_output,
    validate_build_output,
)
from abicheck.buildsource.build_output_profile import BuildOutputBuildSystem


def _write(
    root: Path, profile: dict, *, attribution: BuildEvidence | None = None
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    targets = []
    if attribution is not None:
        (root / "evidence").mkdir(exist_ok=True)
        (root / "evidence" / "build.json").write_text(json.dumps(attribution.to_dict()))
        targets.append(
            {
                "id": "libx",
                "evidence": {
                    "kind": "source-facts",
                    "path": "evidence/pack",
                    "projection": "inferred",
                    "attribution_path": "evidence/build.json",
                },
            }
        )
    doc = {"schema": BUILD_OUTPUT_SCHEMA, "profile": profile, "targets": targets}
    (root / "build-output.json").write_text(json.dumps(doc))
    return root


@pytest.mark.parametrize(
    "raw",
    [
        {"name": "cmake", "generator": "Ninja"},
        {"name": "cmake", "generator": "Unix Makefiles"},
        {"name": "bazel", "generator": ""},
        {"name": "make"},
        {"name": "msbuild", "generator": "Visual Studio 17 2022"},
    ],
)
def test_build_system_round_trips(tmp_path, raw):
    root = _write(tmp_path / "o", {"id": "p", "build_system": raw})
    parsed = load_build_output(root)
    assert parsed.profile.build_system == BuildOutputBuildSystem(
        name=raw["name"], generator=raw.get("generator", "")
    )
    again = BuildOutput.from_dict(parsed.to_dict())
    assert again.profile.build_system == parsed.profile.build_system
    assert not [e for e in validate_build_output(root).errors if "build_system" in e]


def test_absent_build_system_is_unrecorded_and_omitted(tmp_path):
    root = _write(tmp_path / "o", {"id": "p"})
    parsed = load_build_output(root)
    assert parsed.profile.build_system is None
    assert "build_system" not in parsed.to_dict()["profile"]
    assert not [e for e in validate_build_output(root).errors if "build_system" in e]


@pytest.mark.parametrize(
    "raw",
    [
        "cmake",
        ["cmake"],
        {},
        {"name": ""},
        {"generator": "Ninja"},
        {"name": 3},
        {"name": "cmake", "generator": 1},
    ],
)
def test_malformed_build_system_is_an_error_not_a_silent_drop(tmp_path, raw):
    root = _write(tmp_path / "o", {"id": "p", "build_system": raw})
    errors = validate_build_output(root).errors
    assert any("profile.build_system" in e for e in errors), errors


@pytest.mark.parametrize(
    ("declared", "evidence_kinds", "agrees"),
    [
        ("cmake", ["cmake"], True),
        ("CMake", ["cmake", "ninja"], True),
        ("cmake", ["generic"], True),  # evidence names no build system
        ("cmake", [], True),
        ("bazel", ["cmake"], False),
        ("make", ["ninja"], False),
    ],
)
def test_build_system_must_agree_with_attribution_evidence(
    tmp_path, declared, evidence_kinds, agrees
):
    evidence = BuildEvidence(generators=[Generator(kind=k) for k in evidence_kinds])
    root = _write(
        tmp_path / "o",
        {"id": "p", "build_system": {"name": declared}},
        attribution=evidence,
    )
    mismatches = [
        e
        for e in validate_build_output(root).errors
        if "disagrees with the build evidence" in e
    ]
    assert (not mismatches) is agrees, mismatches
