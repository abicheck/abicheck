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


@pytest.mark.parametrize(
    ("declared_gen", "evidence", "agrees"),
    [
        ("Ninja", [("cmake", "Ninja")], True),
        ("Ninja", [("cmake", "Unix Makefiles")], False),
        ("Ninja", [("cmake", "")], True),  # evidence records no backend
        ("", [("cmake", "Unix Makefiles")], True),  # declaration names none
        ("Ninja", [("cmake", "Unix Makefiles"), ("cmake", "Ninja")], True),
        (
            "Ninja",
            [("cmake", "Unix Makefiles"), ("ninja", "Ninja")],
            False,
        ),  # other kind's backend does not count
    ],
)
def test_build_system_generator_must_agree_with_evidence_backend(
    tmp_path, declared_gen, evidence, agrees
):
    ev = BuildEvidence(generators=[Generator(kind=k, generator=g) for k, g in evidence])
    root = _write(
        tmp_path / "o",
        {"id": "p", "build_system": {"name": "cmake", "generator": declared_gen}},
        attribution=ev,
    )
    mismatches = [
        e for e in validate_build_output(root).errors if "build_system.generator" in e
    ]
    assert (not mismatches) is agrees, mismatches


def _doc(root: Path, targets: list) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": BUILD_OUTPUT_SCHEMA,
        "profile": {"id": "p", "build_system": {"name": "cmake"}},
        "targets": targets,
    }
    (root / "build-output.json").write_text(json.dumps(doc))
    return root


def _ev(attribution_path: str) -> dict:
    return {
        "id": "libx",
        "evidence": {
            "kind": "source-facts",
            "path": "evidence/pack",
            "projection": "inferred",
            "attribution_path": attribution_path,
        },
    }


@pytest.mark.parametrize(
    ("targets", "setup"),
    [
        ([{"id": "libx"}], None),  # no evidence at all
        ([_ev("../escape.json")], None),  # escapes the root
        ([_ev("evidence/missing.json")], None),  # unreadable
        ([_ev("evidence/bad.json")], "not json"),  # malformed JSON
        ([_ev("evidence/bad.json")], "[1, 2]"),  # not an object
    ],
)
def test_unusable_attribution_evidence_is_not_a_build_system_conflict(
    tmp_path, targets, setup
):
    """Those are the attribution check's own findings; the build-system
    check must neither crash nor invent a disagreement from them."""
    root = tmp_path / "o"
    if setup is not None:
        (root / "evidence").mkdir(parents=True)
        (root / "evidence" / "bad.json").write_text(setup)
    _doc(root, targets)
    errors = validate_build_output(root).errors
    assert not [e for e in errors if "profile.build_system" in e], errors
