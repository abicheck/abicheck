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

"""Both-direction behaviour of ``analysis-assurance-complete``, the tri-state
per-job override of ``assurance.require_complete``.

Before this input was tri-state, a job could only raise the floor: ``'false'``
was a no-op, so a job could not lift a ``.abicheck.yml`` ``require_complete:
true``. These tests pin the override in both directions, on both Actions and
on both the release-style and the scalar branches of the root action.

Modelled on ``tests/test_action_release_topology_config_validation.py``'s
``TestReleaseTopologyOverlayHonorsExplicitFalseInputs``. The shape parity is
checked separately in ``tests/test_action_config_overlay_input_parity.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import _yaml_fast
import pytest
from _action_run_sh_harness import REQUIRES_POSIX_SHELL, _run_action
from _assurance_overlay_exec import _run_overlay, _written_overlay
from _workflow_exec import make_workspace
from test_action_release_topology_config import (
    _harness,
    _read_config_overlay,
    _run_bash_script,
)
from test_action_retired_inputs_contract import VALIDATE_SH, _run_script

_SCOPES = ("release", "scalar")
_CALL = "add_release_topology_config_flags\nprintf"


def _script(scope: str) -> str:
    """The real ``add_release_topology_config_flags`` harness, called for one
    branch. ``release`` is the directory/package call the release-style branch
    makes; ``scalar`` is the call the single-pair and audit-only compare
    branch makes. The replacement is asserted to hit exactly once, so a harness
    edit cannot silently turn the scalar case back into the release one."""
    base = _harness()
    assert base.count(_CALL) == 1
    if scope == "release":
        return base
    return base.replace(_CALL, "add_release_topology_config_flags scalar\nprintf")


class TestRootAssuranceOverlayBothDirections:
    @pytest.mark.parametrize("scope", _SCOPES)
    def test_false_lifts_a_discovered_true(self, tmp_path: Path, scope: str) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "assurance:\n  require_complete: true\n", encoding="utf-8"
        )
        result = _run_bash_script(
            _script(scope), {"INPUT_REQUIRE_COMPLETE": "false"}, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert doc["assurance"]["require_complete"] is False

    @pytest.mark.parametrize("scope", _SCOPES)
    def test_true_raises_a_discovered_false(self, tmp_path: Path, scope: str) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "assurance:\n  require_complete: false\n", encoding="utf-8"
        )
        result = _run_bash_script(
            _script(scope), {"INPUT_REQUIRE_COMPLETE": "true"}, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert doc["assurance"]["require_complete"] is True

    @pytest.mark.parametrize("scope", _SCOPES)
    @pytest.mark.parametrize("value", ["true", "false"])
    def test_explicit_value_with_no_config_writes_exactly_that_value(
        self, tmp_path: Path, scope: str, value: str
    ) -> None:
        result = _run_bash_script(
            _script(scope), {"INPUT_REQUIRE_COMPLETE": value}, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert doc == {"assurance": {"require_complete": value == "true"}}

    def test_omitted_scalar_synthesizes_no_overlay(self, tmp_path: Path) -> None:
        """Nothing set on the scalar branch means no overlay at all: the CLI's
        own discovery keeps whatever ``.abicheck.yml`` already says."""
        (tmp_path / ".abicheck.yml").write_text(
            "assurance:\n  require_complete: true\n", encoding="utf-8"
        )
        result = _run_bash_script(_script("scalar"), {}, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ["compare"]

    def test_omitted_release_keeps_a_discovered_assurance_true(
        self, tmp_path: Path
    ) -> None:
        """With the assurance input omitted, the release shape writes no
        overlay at all, so the discovered assurance true is left untouched.
        (The retired dso-only/fail-on-removed-library siblings used to force
        an overlay here; they now reach the CLI only through ``set:``.)"""
        (tmp_path / ".abicheck.yml").write_text(
            "assurance:\n  require_complete: true\n", encoding="utf-8"
        )
        result = _run_bash_script(_script("release"), {}, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "--config" not in result.stdout.splitlines()

    def test_scalar_scope_never_writes_the_directory_package_keys(
        self, tmp_path: Path
    ) -> None:
        """dso-only, include-private-dso and fail-on-removed-library are
        directory/package-only. The scalar branch must not carry them into the
        overlay even when they are set, or a single-pair run would start reading
        release/gate keys it was never meant to see."""
        result = _run_bash_script(
            _script("scalar"),
            {
                "INPUT_REQUIRE_COMPLETE": "true",
                "INPUT_DSO_ONLY": "true",
                "INPUT_INCLUDE_PRIVATE_DSO": "true",
                "INPUT_FAIL_ON_REMOVED_LIBRARY": "true",
            },
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert doc == {"assurance": {"require_complete": True}}


class TestCheckTargetAssuranceOverlayBothDirections:
    """The check-target "Generate assurance-overlay config" step, executed for
    real via ``_run_overlay``. The step only runs when the input is non-empty,
    so these cases pass the value the step would receive."""

    def _run(self, tmp_path: Path, value: str, discovered: str | None = None):  # type: ignore[no-untyped-def]
        workspace = make_workspace(tmp_path)
        if discovered is not None:
            (workspace / ".abicheck.yml").write_text(discovered, encoding="utf-8")
        return _run_overlay(
            workspace,
            {"BASE_CONFIG": "", "ANALYSIS_ASSURANCE_COMPLETE": value},
        )

    def test_false_lifts_a_discovered_true(self, tmp_path: Path) -> None:
        result = self._run(tmp_path, "false", "assurance:\n  require_complete: true\n")
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["assurance"]["require_complete"] is False

    def test_true_raises_a_discovered_false(self, tmp_path: Path) -> None:
        result = self._run(tmp_path, "true", "assurance:\n  require_complete: false\n")
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["assurance"]["require_complete"] is True

    @pytest.mark.parametrize(("value", "expected"), [("true", True), ("false", False)])
    def test_explicit_value_with_no_config_writes_exactly_that_value(
        self, tmp_path: Path, value: str, expected: bool
    ) -> None:
        result = self._run(tmp_path, value)
        assert result.returncode == 0, result.stderr
        assert _written_overlay(result) == {"assurance": {"require_complete": expected}}


def _combined(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


class TestRootValidatesTheTriState:
    """``action/validate-inputs.sh``: the root input is a strict tri-state."""

    @pytest.mark.parametrize("value", ["", "true", "false"])
    def test_accepted_values_pass_in_compare_mode(self, value: str) -> None:
        result = _run_script(
            VALIDATE_SH,
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": "libfoo.so",
                "INPUT_REQUIRE_COMPLETE": value,
            },
        )
        assert result.returncode == 0, _combined(result)
        assert "require-complete" not in _combined(result)

    @pytest.mark.parametrize("value", ["True", "yes", "1"])
    def test_other_values_are_refused_not_read_as_inherit(self, value: str) -> None:
        result = _run_script(
            VALIDATE_SH,
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": "libfoo.so",
                "INPUT_REQUIRE_COMPLETE": value,
            },
        )
        assert result.returncode != 0
        assert f"require-complete '{value}' is not recognized" in _combined(result)

    def test_non_compare_mode_warns_and_does_not_fail(self) -> None:
        result = _run_script(
            VALIDATE_SH,
            {
                "INPUT_MODE": "dump",
                "INPUT_NEW_LIBRARY": "libfoo.so",
                "INPUT_REQUIRE_COMPLETE": "true",
            },
        )
        assert result.returncode == 0, _combined(result)
        assert "require-complete is set but has no effect" in _combined(result)


def _argv_recording_stub(tmp_path: Path) -> tuple[Path, Path]:
    """An ``abicheck`` that records its argv and copies the ``--config`` file it
    was handed, then exits 0. The copy is taken at call time because run.sh
    removes its synthesized overlay on exit."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    argv_log = tmp_path / "argv.txt"
    config_seen = tmp_path / "config-seen.yml"
    stub = bindir / "abicheck"
    stub.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                f'printf "%s\\n" "$@" > "{argv_log}"',
                "prev=''",
                'for arg in "$@"; do',
                f'  if [[ "$prev" == "--config" ]]; then cp "$arg" "{config_seen}"; fi',
                '  prev="$arg"',
                "done",
                "exit 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bindir, config_seen


def _directory_operand_env(tmp_path: Path) -> dict[str, str]:
    """Directory operands make the compare run the release-style branch."""
    old_dir = tmp_path / "old-pkg"
    new_dir = tmp_path / "new-pkg"
    for side in (old_dir, new_dir):
        side.mkdir()
        (side / "libfoo.so").write_bytes(b"\x7fELF")
    return {
        "INPUT_MODE": "compare",
        "INPUT_OLD_LIBRARY": str(old_dir),
        "INPUT_NEW_LIBRARY": str(new_dir),
        "INPUT_FORMAT": "json",
        "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
    }


@REQUIRES_POSIX_SHELL
class TestRootOverrideReachesTheCli:
    """End to end through the whole of ``action/run.sh``: the override must
    reach the ``abicheck`` argv on both the scalar and the release-style
    branch, not only in the extracted function."""

    @pytest.mark.parametrize("shape", ["scalar", "directory"])
    def test_false_overrides_a_discovered_true_on_each_branch(
        self, tmp_path: Path, shape: str
    ) -> None:
        if shape == "scalar":
            env = {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": str(tmp_path / "libold.so"),
                "INPUT_NEW_LIBRARY": str(tmp_path / "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
            }
            for name in ("libold.so", "libnew.so"):
                (tmp_path / name).write_bytes(b"\x7fELF")
        else:
            env = _directory_operand_env(tmp_path)
        (tmp_path / ".abicheck.yml").write_text(
            "assurance:\n  require_complete: true\n", encoding="utf-8"
        )
        bindir, config_seen = _argv_recording_stub(tmp_path)

        _run_action(tmp_path, {**env, "INPUT_REQUIRE_COMPLETE": "false"}, bindir)

        argv = (tmp_path / "argv.txt").read_text(encoding="utf-8").splitlines()
        assert "--config" in argv, argv
        assert config_seen.is_file(), argv
        seen = _yaml_fast.safe_load(config_seen.read_text(encoding="utf-8"))
        assert seen["assurance"]["require_complete"] is False
