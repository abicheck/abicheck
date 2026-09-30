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

"""Contract tests for the `set-up-abi-compatibility-ci` evaluation grader.

The grader is only evidence if it (a) passes a known-good setup in full and
(b) fails each known-bad setup on exactly the check that names its defect.
`reference/` holds hand-written good workflows; every mutation below is one
real failure mode from the skill's `references/pitfalls.md`, applied to a good
workflow, so a check that accepts everything (or rejects everything) fails
here rather than silently scoring runs.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
EVAL = REPO / "skills-src" / "evaluation" / "agents" / "ci-setup"

_spec = importlib.util.spec_from_file_location("ci_setup_grader", EVAL / "grader.py")
assert _spec is not None and _spec.loader is not None
grader = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("ci_setup_grader", grader)
_spec.loader.exec_module(grader)

CORPUS = grader.load_scenarios()
SCENARIO_IDS = [s["id"] for s in CORPUS["scenarios"]]
REFERENCE_IDS = sorted(p.name for p in (EVAL / "reference").iterdir() if p.is_dir())


def _workspace(tmp_path: Path, scenario_id: str) -> Path:
    scenario = next(s for s in CORPUS["scenarios"] if s["id"] == scenario_id)
    work = tmp_path / "ws"
    shutil.copytree(EVAL / "fixtures" / scenario["fixture"], work)
    wf = work / ".github" / "workflows"
    # A reference replaces any pre-existing ABI workflow; the unrelated build
    # workflow the fixture ships stays, as it would in a real repository.
    for path in wf.glob("abi*.yml"):
        path.unlink()
    # The whole reference tree: a solution may include `.abicheck.yml`.
    shutil.copytree(EVAL / "reference" / scenario_id, work, dirs_exist_ok=True)
    return work


def _edit(work: Path, name: str, fn: Callable[[str], str]) -> None:
    path = work / ".github" / "workflows" / name
    new = fn(path.read_text(encoding="utf-8"))
    path.write_text(new, encoding="utf-8")


def _failed(result: dict) -> set[str]:
    return {c["check"] for c in result["checks"] if not c["passed"]}


# --------------------------------------------------------------------------
# Corpus integrity
# --------------------------------------------------------------------------


def test_every_check_named_by_the_corpus_exists():
    named = set(CORPUS["common_checks"]) | set(CORPUS["critical"])
    for scenario in CORPUS["scenarios"]:
        named |= set((scenario.get("checks") or {}).keys())
    assert named - set(grader.CHECKS) == set()


def test_every_scenario_fixture_exists_and_is_a_github_repository():
    for scenario in CORPUS["scenarios"]:
        fixture = EVAL / "fixtures" / scenario["fixture"]
        assert fixture.is_dir(), scenario["id"]
        assert (fixture / ".github" / "workflows").is_dir(), scenario["id"]


def test_fixture_workflows_parse():
    for path in (EVAL / "fixtures").glob("*/.github/workflows/*.yml"):
        assert isinstance(yaml.safe_load(path.read_text(encoding="utf-8")), dict), path


# --------------------------------------------------------------------------
# Known-good: every reference passes every check
# --------------------------------------------------------------------------


@pytest.mark.parametrize("scenario_id", REFERENCE_IDS)
def test_reference_setup_passes_every_check(tmp_path: Path, scenario_id: str):
    result = grader.grade(
        _workspace(tmp_path, scenario_id),
        scenario_id,
        "Bootstrap: backfill the latest release.",
        CORPUS,
    )
    assert _failed(result) == set(), result["checks"]
    assert result["success"]


# --------------------------------------------------------------------------
# Known-bad: an untouched fixture, and one mutation per failure mode
# --------------------------------------------------------------------------


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_doing_nothing_is_never_a_success(tmp_path: Path, scenario_id: str):
    scenario = next(s for s in CORPUS["scenarios"] if s["id"] == scenario_id)
    work = tmp_path / "ws"
    shutil.copytree(EVAL / "fixtures" / scenario["fixture"], work)
    result = grader.grade(work, scenario_id, "", CORPUS)
    assert not result["success"]


def _sub(old: str, new: str) -> Callable[[str], str]:
    def apply(text: str) -> str:
        assert old in text, f"mutation anchor {old!r} not found"
        return text.replace(old, new)

    return apply


MUTATIONS = [
    # (scenario, file, mutation, the check that must now fail)
    (
        "cmake-c-releases",
        "abi-check.yml",
        _sub("abicheck/abicheck@v0.6.0", "abicheck/abicheck@main"),
        "pinned_abicheck",
    ),
    (
        "cmake-c-releases",
        "abi-check.yml",
        _sub("  pull_request:\n", "  pull_request_target:\n  pull_request:\n"),
        "no_pull_request_target",
    ),
    (
        "cmake-c-releases",
        "abi-check.yml",
        _sub(
            "      contents: read\n      pull-requests: write",
            "      contents: write\n      pull-requests: write",
        ),
        "contents_write_not_on_pr",
    ),
    ("cmake-c-releases", "abi-check.yml", _sub("          lang: c\n", ""), "lang"),
    (
        "cmake-c-releases",
        "abi-check.yml",
        _sub("new-header: include/", "new-header: src/"),
        "headers_public",
    ),
    (
        "cmake-c-releases",
        "abi-check.yml",
        _sub("          new-header: include/\n", ""),
        "headers_public",
    ),
    (
        "cmake-c-releases",
        "abi-baseline.yml",
        _sub("libgeom.abicheck.json", "abi-baseline.json"),
        "baseline_release",
    ),
    (
        "cmake-c-releases",
        "abi-baseline.yml",
        _sub("gh release upload", "echo"),
        "baseline_release",
    ),
    (
        "cmake-c-releases",
        "abi-baseline.yml",
        _sub(
            "  workflow_dispatch:\n    inputs:\n      tag:\n        description: Existing release tag to attach a snapshot to\n        required: true\n",
            "",
        ),
        "release_bootstrap",
    ),
    (
        "cmake-c-releases",
        "abi-check.yml",
        _sub(
            "          new-library: build/libgeom.so\n",
            "          new-library: build/libgeom.so\n          mode: scan\n",
        ),
        "no_retired_inputs",
    ),
    (
        "cmake-c-releases",
        "abi-check.yml",
        _sub(
            "      - uses: abicheck/abicheck@v0.6.0\n",
            "      - uses: abicheck/abicheck@v0.6.0\n        continue-on-error: true\n",
        ),
        "no_continue_on_error",
    ),
    (
        "cmake-c-releases",
        "abi-check.yml",
        (
            lambda t: t.replace("permissions:\n  contents: read\n", "", 1).replace(
                "    permissions:\n      contents: read\n      pull-requests: write\n",
                "",
            )
        ),
        "permissions_declared",
    ),
    (
        "cmake-c-releases",
        "abi-check.yml",
        _sub("RelWithDebInfo", "Release"),
        "debug_info",
    ),
    (
        "cmake-cpp-no-releases",
        "abi-check.yml",
        _sub(" -DBUILD_SHARED_LIBS=ON", ""),
        "shared_build",
    ),
    (
        "cmake-cpp-no-releases",
        "abi-check.yml",
        _sub(
            "          new-header: new/include\n",
            "          new-header: new/include\n          lang: c\n",
        ),
        "lang",
    ),
    (
        "cmake-cpp-no-releases",
        "abi-check.yml",
        _sub("old-library: old/build/libtinyjson.so", "abi-baseline: latest-release"),
        "baseline_without_releases",
    ),
    (
        "cmake-cpp-no-releases",
        "abi-check.yml",
        _sub(
            "old-library: old/build/libtinyjson.so",
            "old-library: new/build/libtinyjson.so",
        ),
        "not_self_compare",
    ),
    (
        "make-two-libs",
        "abi-check.yml",
        _sub(
            "          - lib: libbeta\n            so: libbeta.so.1\n            headers: include/beta\n",
            "",
        ),
        "covers_libs",
    ),
    (
        "make-two-libs",
        "abi-check.yml",
        _sub("old-library: old/build/${{ matrix.so }}", "abi-baseline: latest-release"),
        "no_ambiguous_latest_release",
    ),
    (
        "make-two-libs",
        "abi-check.yml",
        _sub("            headers: include/beta\n", "            headers: src\n"),
        "headers_public",
    ),
]


@pytest.mark.parametrize(
    ("scenario_id", "name", "mutate", "expected"),
    MUTATIONS,
    ids=[f"{m[0]}:{m[3]}:{i}" for i, m in enumerate(MUTATIONS)],
)
def test_each_failure_mode_is_caught_by_its_own_check(
    tmp_path, scenario_id, name, mutate, expected
):
    work = _workspace(tmp_path, scenario_id)
    _edit(work, name, mutate)
    result = grader.grade(work, scenario_id, "", CORPUS)
    assert expected in _failed(result), result["checks"]


def test_broken_fixture_fails_exactly_the_repair_checks(tmp_path: Path):
    """The shipped broken workflow (`mode: scan`, `@main`,
    `continue-on-error`, `header: .`, a `{}` baseline) must fail each of the
    checks it violates, so a run that leaves it untouched can never pass."""
    scenario = next(
        s for s in CORPUS["scenarios"] if s["id"] == "cmake-cpp-broken-check"
    )
    work = tmp_path / "ws"
    shutil.copytree(EVAL / "fixtures" / scenario["fixture"], work)
    failed = _failed(grader.grade(work, "cmake-cpp-broken-check", "", CORPUS))
    assert {
        "pinned_abicheck",
        "no_continue_on_error",
        "no_retired_inputs",
        "abicheck_workflow_on_pr",
        "headers_public",
    } <= failed


def test_matrix_expansion_resolves_include_rows_and_axes():
    job = grader.Job(
        "j", {"strategy": {"matrix": {"include": [{"lib": "a"}, {"lib": "b"}]}}}, None
    )  # type: ignore[arg-type]
    step = grader.Step(
        {
            "uses": "abicheck/abicheck@v0.6.0",
            "with": {"new-library": "build/${{ matrix.lib }}.so"},
        },
        job,
    )
    assert step.expand(step.inputs["new-library"]) == ["build/a.so", "build/b.so"]
    job2 = grader.Job(
        "j", {"strategy": {"matrix": {"lib": ["x", "y"], "cc": ["gcc"]}}}, None
    )  # type: ignore[arg-type]
    assert sorted(r["lib"] for r in job2.matrix_rows()) == ["x", "y"]


def test_hand_rolled_release_dump_is_recognised_but_apt_castxml_is_not(tmp_path: Path):
    """A release job that calls `abicheck dump -o x.abicheck.json` in a `run:`
    step still publishes a baseline (not the grader's call to forbid), but
    installing distribution CastXML is its own failure: abicheck refuses the
    old versions Ubuntu ships."""
    work = _workspace(tmp_path, "cmake-c-releases")
    _edit(
        work,
        "abi-baseline.yml",
        lambda t: (
            t.split("      - uses: abicheck/abicheck@v0.6.0")[0]
            + (
                "      - run: sudo apt-get install -y castxml && pip install abicheck==0.6.0\n"
                "      - run: |\n"
                "          abicheck dump build/libgeom.so -H include \\\n"
                "            -o libgeom.abicheck.json\n"
                '      - run: gh release upload "$TAG" libgeom.abicheck.json --clobber\n'
                "        env:\n          GH_TOKEN: ${{ github.token }}\n"
            )
        ),
    )
    failed = _failed(grader.grade(work, "cmake-c-releases", "", CORPUS))
    assert "baseline_release" not in failed
    assert "release_bootstrap" not in failed
    assert "toolchain_via_action" in failed


CLI_TAG_REBUILD = """name: abi
on:
  pull_request:
permissions:
  contents: read
jobs:
  abi:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v6
        with:
          fetch-depth: 0
      - run: pip install abicheck==0.6.0 castxml
      - run: |
          tag=$(git tag --list 'v*' --sort=-v:refname | head -n1)
          git worktree add ../base "$tag"
          cmake -S ../base -B ../base/build -DCMAKE_BUILD_TYPE=RelWithDebInfo && cmake --build ../base/build
          cmake -S . -B build -DCMAKE_BUILD_TYPE=RelWithDebInfo && cmake --build build
      - run: |
          printf 'compile:\\n  lang: c\\n' > cfg.yml
          abicheck compare ../base/build/libgeom.so build/libgeom.so \\
            --header old=../base/include --header new=include --config cfg.yml
"""


def _cli_workspace(tmp_path: Path, text: str) -> Path:
    work = tmp_path / "ws"
    shutil.copytree(EVAL / "fixtures" / "cmake-c-releases", work)
    (work / ".github" / "workflows" / "abi.yml").write_text(text, encoding="utf-8")
    return work


def test_a_correct_cli_based_setup_passes_like_an_action_based_one(tmp_path: Path):
    """The grader judges the integration, not which front end it uses: a
    direct CLI call rebuilding the last release tag is a valid setup."""
    result = grader.grade(
        _cli_workspace(tmp_path, CLI_TAG_REBUILD), "cmake-c-releases", "", CORPUS
    )
    assert _failed(result) == set(), result["checks"]


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (
            "pip install abicheck==0.6.0 castxml",
            "pipx install abicheck",
            "pinned_abicheck",
        ),
        (
            "pip install abicheck==0.6.0 castxml",
            "pip install abicheck==0.6.0",
            "toolchain_via_action",
        ),
        ("lang: c", "std: c11", "lang"),
        ("--header new=include", "--header new=src", "headers_public"),
        (
            "../base/build/libgeom.so build/libgeom.so",
            "build/libgeom.so build/libgeom.so",
            "not_self_compare",
        ),
    ],
)
def test_cli_setup_failure_modes_are_caught(
    tmp_path: Path, old: str, new: str, expected: str
):
    assert old in CLI_TAG_REBUILD
    result = grader.grade(
        _cli_workspace(tmp_path, CLI_TAG_REBUILD.replace(old, new)),
        "cmake-c-releases",
        "",
        CORPUS,
    )
    assert expected in _failed(result), result["checks"]


def test_a_pin_to_an_annotated_tag_object_is_caught(tmp_path: Path):
    work = _workspace(tmp_path, "cmake-c-releases")
    _edit(
        work,
        "abi-baseline.yml",
        _sub(
            "abicheck/abicheck@v0.6.0",
            "abicheck/abicheck@2ff668d3907e9368b51ea6240768c1bb3f36f8db",
        ),
    )
    assert "pins_are_commits" in _failed(
        grader.grade(work, "cmake-c-releases", "", CORPUS)
    )
    _edit(
        work,
        "abi-baseline.yml",
        _sub(
            "2ff668d3907e9368b51ea6240768c1bb3f36f8db",
            "a1f78b640ba29960251f7043c17ae98a647ee135",
        ),
    )
    assert "pins_are_commits" not in _failed(
        grader.grade(work, "cmake-c-releases", "", CORPUS)
    )


def _write(work: Path, rel: str, text: str) -> None:
    (work / rel).write_text(text, encoding="utf-8")


NEW_MUTATIONS = [
    # runtime floors
    (
        "cmake-c-rhel8-floor",
        ".abicheck.yml",
        lambda t: t.replace('"2.28"', '"2.34"'),
        "runtime_floors_declared",
    ),
    (
        "cmake-c-rhel8-floor",
        ".abicheck.yml",
        lambda t: "policy: {}\n",
        "runtime_floors_declared",
    ),
    (
        "cmake-c-rhel8-floor",
        ".github/workflows/abi-check.yml",
        lambda t: t.replace("runs-on: ubuntu-24.04", "runs-on: ubuntu-latest"),
        "runner_pinned",
    ),
    # source depth
    (
        "cmake-cpp-inline-heavy",
        ".github/workflows/abi-check.yml",
        lambda t: t.replace("          depth: source\n", ""),
        "source_depth_complete",
    ),
    (
        "cmake-cpp-inline-heavy",
        ".github/workflows/abi-check.yml",
        lambda t: t.replace("          dependency-source: conda-forge-clang20\n", ""),
        "source_depth_complete",
    ),
    (
        "cmake-cpp-inline-heavy",
        ".github/workflows/abi-check.yml",
        lambda t: t.replace(
            "          sources: .\n          compile-db: build/compile_commands.json\n",
            "",
        ),
        "source_depth_complete",
    ),
    (
        "cmake-cpp-inline-heavy",
        ".github/workflows/abi-baseline.yml",
        lambda t: t.replace(
            "          depth: source\n          sources: .\n          compile-db: build/compile_commands.json\n",
            "",
        ),
        "baseline_has_source_evidence",
    ),
    # build systems
    (
        "autotools-c",
        ".github/workflows/abi-check.yml",
        lambda t: t.replace("/.libs/", "/"),
        "library_path_plausible",
    ),
    (
        "bazel-cpp",
        ".github/workflows/abi-check.yml",
        lambda t: t.replace("bazel-bin/libkv.so", "libkv.so"),
        "library_path_plausible",
    ),
    (
        "cross-aarch64",
        ".github/workflows/abi-check.yml",
        lambda t: t.replace("          gcc-prefix: aarch64-linux-gnu-\n", ""),
        "cross_toolchain",
    ),
]


@pytest.mark.parametrize(
    ("scenario_id", "rel", "mutate", "expected"),
    NEW_MUTATIONS,
    ids=[f"{m[0]}:{m[3]}:{i}" for i, m in enumerate(NEW_MUTATIONS)],
)
def test_depth_toolchain_floor_failure_modes_are_caught(
    tmp_path, scenario_id, rel, mutate, expected
):
    work = _workspace(tmp_path, scenario_id)
    before = (work / rel).read_text(encoding="utf-8")
    after = mutate(before)
    assert after != before, "mutation did not apply"
    _write(work, rel, after)
    assert expected in _failed(grader.grade(work, scenario_id, "", CORPUS))


def test_cross_toolchain_accepts_a_native_arm_runner(tmp_path: Path):
    work = _workspace(tmp_path, "cross-aarch64")
    rel = ".github/workflows/abi-check.yml"
    text = (work / rel).read_text(encoding="utf-8")
    text = text.replace("          gcc-prefix: aarch64-linux-gnu-\n", "").replace(
        "runs-on: ubuntu-24.04", "runs-on: ubuntu-24.04-arm"
    )
    _write(work, rel, text)
    assert "cross_toolchain" not in _failed(
        grader.grade(work, "cross-aarch64", "", CORPUS)
    )


def test_a_floor_declared_below_the_promise_is_accepted(tmp_path: Path):
    """Declaring glibc 2.17 when the promise is 2.28 is stricter, not weaker."""
    work = _workspace(tmp_path, "cmake-c-rhel8-floor")
    _write(work, ".abicheck.yml", 'deployment:\n  runtime_floors:\n    GLIBC: "2.17"\n')
    assert "runtime_floors_declared" not in _failed(
        grader.grade(work, "cmake-c-rhel8-floor", "", CORPUS)
    )
