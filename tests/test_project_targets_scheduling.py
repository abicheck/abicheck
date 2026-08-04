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

"""G34 Phase C — a profile schedules its own check cell.

Two axes that were previously fixed for every cell in a `check-project.yml`
run, regardless of what the profile declared: which runner it lands on
(hardcoded `ubuntu-latest`, so a `windows` profile could not be checked
natively at all) and how it provisions its own system dependencies (one
workflow-level boolean, so a GCC-profile cell and a Clang-profile cell could
not each get a matching conda environment).

The rule under test throughout: **an existing project's scheduling is
unmoved.** `os:` was a free-form, never-consulted string before this phase,
so every profile written to date resolves exactly where it already ran.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from abicheck.buildsource.project_targets import (
    DEFAULT_PROFILE_RUNNER_LABEL,
    PROFILE_DEPENDENCY_SOURCES,
    ProfileSpec,
    load_project_targets_config,
    runner_label_for_os,
    validate_project_targets,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestRunnerLabelForOs:
    """The `os:`-to-runner mapping in isolation — plain string resolution,
    testable without running a workflow."""

    @pytest.mark.parametrize(
        "os_value,expected",
        [
            pytest.param(
                "", DEFAULT_PROFILE_RUNNER_LABEL, id="unset-is-todays-default"
            ),
            pytest.param("linux", "ubuntu-latest", id="linux"),
            pytest.param("windows", "windows-latest", id="windows"),
            pytest.param("macos", "macos-latest", id="macos"),
            pytest.param("darwin", "macos-latest", id="darwin-is-macos"),
            pytest.param("Linux", "ubuntu-latest", id="case-insensitive"),
        ],
    )
    def test_platform_names(self, os_value: str, expected: str) -> None:
        assert runner_label_for_os(os_value) == expected

    @pytest.mark.parametrize(
        "label",
        ["ubuntu-24.04", "windows-2022", "macos-14", "ubuntu-latest"],
    )
    def test_a_runner_label_passes_through(self, label: str) -> None:
        """`os:` was free-form before this phase, so a project that already
        wrote an image label there keeps working — narrowing it to platform
        names only would be a breaking config change dressed up as a
        feature."""
        assert runner_label_for_os(label) == label

    @pytest.mark.parametrize("os_value", ["freebsd", "aix", "linux-x86_64", "gcc14"])
    def test_an_unroutable_os_is_not_silently_defaulted(self, os_value: str) -> None:
        """`None`, never `ubuntu-latest`. Quietly sending a non-Linux profile
        to a Linux runner produces a green cell that checked the wrong
        platform — a job reporting success having gated the wrong thing."""
        assert runner_label_for_os(os_value) is None

    def test_profile_exposes_its_own_label(self) -> None:
        assert ProfileSpec(id="p", os="windows").runner_label == "windows-latest"
        assert ProfileSpec(id="p").runner_label == DEFAULT_PROFILE_RUNNER_LABEL


class TestDependencySourceSchema:
    """`profiles.<id>.dependency_source` shape validation."""

    def _load(self, tmp_path: Path, profiles: dict) -> dict[str, ProfileSpec]:
        path = tmp_path / ".abicheck.yml"
        path.write_text(yaml.safe_dump({"profiles": profiles}))
        return load_project_targets_config(path).profiles

    def test_absent_is_empty_not_a_default(self, tmp_path: Path) -> None:
        """Empty means "inherit the caller's workflow-level default", which is
        what every profile does today — this phase does not decide for them."""
        profiles = self._load(tmp_path, {"p": {"contract": True}})
        assert profiles["p"].dependency_source == ""
        assert "dependency_source" not in profiles["p"].to_dict()

    def test_a_declared_source_round_trips(self, tmp_path: Path) -> None:
        profiles = self._load(
            tmp_path, {"p": {"dependency_source": "conda-forge-clang20"}}
        )
        assert profiles["p"].dependency_source == "conda-forge-clang20"
        assert profiles["p"].to_dict()["dependency_source"] == "conda-forge-clang20"

    @pytest.mark.parametrize("value", sorted(PROFILE_DEPENDENCY_SOURCES))
    def test_every_accepted_value_parses(self, tmp_path: Path, value: str) -> None:
        assert (
            self._load(tmp_path, {"p": {"dependency_source": value}})[
                "p"
            ].dependency_source
            == value
        )

    def test_an_unknown_value_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="dependency_source must be one of"):
            self._load(tmp_path, {"p": {"dependency_source": "apt"}})

    def test_a_non_string_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="dependency_source must be a string"):
            self._load(tmp_path, {"p": {"dependency_source": True}})


class TestActionYmlAgreesOnDependencySources:
    """The root `action.yml` owns the accepted-value list; this mirror only
    exists so a bad value fails at `project validate` time rather than inside
    a matrix cell twenty minutes later. This is what stops it drifting."""

    def test_mirror_matches_the_action(self) -> None:
        action = (REPO_ROOT / "action.yml").read_text()
        # The one place action.yml enumerates them: its own validation case.
        marker = "system|conda-forge|conda-forge-gcc14|conda-forge-clang20|none)"
        assert marker in action, "action.yml's dependency-source case changed shape"
        assert PROFILE_DEPENDENCY_SOURCES == frozenset(marker.rstrip(")").split("|"))

    def test_check_target_forwards_the_input(self) -> None:
        """A per-cell value that check-target accepts but never forwards would
        be silently inert — the failure this whole phase is about."""
        action = (REPO_ROOT / "actions" / "check-target" / "action.yml").read_text()
        assert "dependency-source:" in action
        assert "dependency-source: ${{ inputs.dependency-source }}" in action


def _resolve_dependency_source(
    tmp_path: Path, *, source: str, install_deps: str, runner_os: str
) -> str:
    """Run `action.yml`'s own "Resolve dependency-source" script.

    Extracted and executed rather than string-matched, so this pins the
    resolution *behaviour* — a future edit that keeps the same words but
    changes the branching still fails here.
    """
    action = yaml.safe_load((REPO_ROOT / "action.yml").read_text())
    step = next(
        s
        for s in action["runs"]["steps"]
        if s.get("name") == "Resolve dependency-source"
    )
    script = tmp_path / "resolve.sh"
    script.write_text(step["run"])
    out = tmp_path / "gh_output"
    out.write_text("")
    # Relative paths, run from inside tmp_path — a Windows absolute path
    # would reach a POSIX bash with its backslashes intact and break the
    # step's own `>> "$GITHUB_OUTPUT"` redirect. (That was also my first
    # theory for why this class failed on the Windows lane; it was not the
    # cause — see the skipif above for the real one — but the relative form
    # is correct on its own merits, so it stays.)
    completed = subprocess.run(
        ["bash", "resolve.sh"],
        cwd=tmp_path,
        env={
            **os.environ,
            "INPUT_DEPENDENCY_SOURCE": source,
            "INPUT_INSTALL_DEPS": install_deps,
            "RUNNER_OS": runner_os,
            "GITHUB_OUTPUT": "gh_output",
        },
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, (
        f"the resolve step exited {completed.returncode}\n"
        f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
    )
    for line in out.read_text().splitlines():
        if line.startswith("value="):
            return line.split("=", 1)[1]
    raise AssertionError("the resolve step wrote no value= output")


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "This runs the step's own bash script, and the Actions runner "
        "provides a real POSIX bash for it on every platform -- including "
        "the windows-latest cell this fix is *about*. What a Windows *test* "
        "runner cannot provide is that bash: plain 'bash' on PATH there is "
        "the System32 WSL launcher, not Git Bash, and with no WSL distro "
        "installed it prints an install hint and exits 1 before running "
        "anything. Same reason every bash-invoking test in "
        "test_reusable_workflows.py skips here; the Linux and macOS lanes "
        "cover this logic in full."
    ),
)
class TestWindowsDependencySourceDefault:
    """G34 Phase C made an `os: windows` profile schedulable for the first
    time — straight into a guaranteed failure, until this (Codex review).

    The unset default is `conda-forge`, and `action.yml` explicitly hard-fails
    every conda-forge source on Windows (pixi's native-toolchain features
    don't cover win-64), so a Windows cell that declared no
    `dependency_source:` never reached analysis at all.
    """

    def test_windows_defaults_to_system_not_conda_forge(self, tmp_path: Path) -> None:
        assert (
            _resolve_dependency_source(
                tmp_path, source="", install_deps="true", runner_os="Windows"
            )
            == "system"
        )

    @pytest.mark.parametrize("runner_os", ["Linux", "macOS"])
    def test_other_platforms_keep_the_conda_forge_default(
        self, tmp_path: Path, runner_os: str
    ) -> None:
        assert (
            _resolve_dependency_source(
                tmp_path, source="", install_deps="true", runner_os=runner_os
            )
            == "conda-forge"
        )

    def test_an_explicit_conda_forge_on_windows_is_not_rewritten(
        self, tmp_path: Path
    ) -> None:
        """Asking for something unsupported must still say so — the later
        step's hard error is the right answer there, not a silent
        substitution."""
        assert (
            _resolve_dependency_source(
                tmp_path,
                source="conda-forge-clang20",
                install_deps="true",
                runner_os="Windows",
            )
            == "conda-forge-clang20"
        )

    def test_install_deps_false_still_wins_on_windows(self, tmp_path: Path) -> None:
        """The legacy opt-out is checked before the platform default, so an
        explicit `install-deps: false` is unaffected."""
        assert (
            _resolve_dependency_source(
                tmp_path, source="", install_deps="false", runner_os="Windows"
            )
            == "none"
        )


class TestUnroutableOsIsAValidationError:
    """`os:` selects a runner now, so a value naming no schedulable platform
    is a real misconfiguration — reported before a cell exists to be
    scheduled wrongly."""

    def _validate(self, tmp_path: Path, profile: dict) -> list[str]:
        path = tmp_path / ".abicheck.yml"
        path.write_text(
            yaml.safe_dump(
                {
                    "targets": {"lib": {"kind": "library", "binary_pattern": "l.so"}},
                    "profiles": {"p": profile},
                }
            )
        )
        return validate_project_targets(load_project_targets_config(path)).errors

    def test_unroutable_os_is_reported(self, tmp_path: Path) -> None:
        issues = self._validate(tmp_path, {"os": "freebsd"})
        assert any("does not name a platform" in i for i in issues)

    @pytest.mark.parametrize("os_value", ["", "linux", "windows", "ubuntu-24.04"])
    def test_a_routable_os_is_not_reported(self, tmp_path: Path, os_value: str) -> None:
        issues = self._validate(tmp_path, {"os": os_value} if os_value else {})
        assert not any("does not name a platform" in i for i in issues)
