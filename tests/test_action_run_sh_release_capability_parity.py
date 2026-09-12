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

"""The composite Action never asserts a CLI restriction the CLI no longer has.

The bug class (`tests/regressions/manifest_tool_surface.py`'s
`cli_surface.capability_guard_diverged_from_pipeline`, whose *adapter* half
this module executes): `action/run.sh` and `action/validate-inputs.sh` are
adapters over the `abicheck` CLI, and each carried a guard rejecting or
silently dropping an input "because the per-library release fan-out cannot
thread/enforce it". Both claims stopped being true when the fan-out moved
onto `service.run_compare` -- `cli_resolve.resolve_directory_compile_context`
threads the both-sides compile context to every pair's header dump, and
`cli_compare_options._resolve_depth_for_set_inputs` rejects no rung of the
public ladder -- and nothing failed when they did, because the adapter's
tests pinned the adapter's own prose rather than measuring the CLI. A
directory/package (release) comparison through the Action was therefore
strictly less capable than the same comparison run through the CLI
directly, silently, for as long as nobody re-read both files side by side.

So the oracle here is never a pinned constant and never this adapter's own
table: it is **the other operand shape**. Whatever `run.sh` does for a
single-pair operand under a given input, it must do for a directory/package
operand under that same input -- unless the CLI genuinely still refuses the
combination, in which case the Action must fail loud rather than drop it
(the `--depth build`/`source` and inline `--sources`/`--build-info`/
`--compile-db` residue below, which `cli_resolve.
_reject_evidence_flags_for_set_inputs` really does still reject).

Shaped so a future systematic check for this whole class can absorb it:
every case is `(input, operand shape) -> the single-pair answer`, enumerated
over the input families rather than written one fixed input at a time, and
the CLI-side residue is stated as data (`_CLI_REJECTED_DEPTH_RUNGS`,
`_CLI_REJECTED_EVIDENCE_INPUTS`) rather than as prose in a docstring.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from test_action_run_sh_compare_build_source import (
    _bash_executable,  # noqa: F401  (re-exported for the harness below)
    _run_compare_raw,
)

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
VALIDATE_SH = Path(__file__).resolve().parents[1] / "action" / "validate-inputs.sh"


#: Every L2 compile-context Action input, with a value that is a real
#: override rather than the documented no-op default ("c++" for lang,
#: "auto" for ast-frontend -- both of which resolve to exactly what
#: leaving the input unset resolves to, and are covered separately in
#: `tests/test_action_compile_context_parity.py`). The guard this module
#: closes named this exact family, so the family is what gets enumerated.
_COMPILE_CONTEXT_INPUTS: tuple[tuple[str, str], ...] = (
    ("INPUT_LANG", "c"),
    ("INPUT_AST_FRONTEND", "clang"),
    ("INPUT_GCC_PATH", "/opt/gcc-14/bin/g++"),
    ("INPUT_GCC_PREFIX", "aarch64-linux-gnu-"),
    ("INPUT_GCC_OPTIONS", "-DFOO=1"),
    ("INPUT_SYSROOT", "/opt/sysroot"),
    ("INPUT_NOSTDINC", "true"),
)

#: Independently-chosen release-style operand spellings, on both sides:
#: `_is_release_style_operand` accepts a directory, a recognized package
#: extension, and (elsewhere) a magic-byte-detected extensionless package,
#: and the compare branch tests old-library and new-library alike. A guard
#: keyed on the operand shape has to be wrong for every one of these or
#: none, so all of them are measured rather than one representative.
_RELEASE_OPERAND_SHAPES: tuple[str, ...] = ("dir-new", "dir-old", "rpm-new", "whl-new")

#: The depth rungs the CLI still refuses for a set input, and why -- they
#: need inline build/source evidence the fan-out does not collect
#: (`cli_resolve._reject_evidence_flags_for_set_inputs`). Everything not
#: listed here must reach the CLI verbatim.
_CLI_REJECTED_DEPTH_RUNGS: tuple[str, ...] = ("build", "source")
_FORWARDED_DEPTH_RUNGS: tuple[str, ...] = ("binary", "headers")

#: The inline evidence inputs the CLI still refuses for a set input.
_CLI_REJECTED_EVIDENCE_INPUTS: tuple[tuple[str, str], ...] = (
    ("INPUT_SOURCES", "/src"),
    ("INPUT_BUILD_INFO", "/build"),
    ("INPUT_COMPILE_DB", "/compile_commands.json"),
)


def _operand_env(shape: str, tmp_path: Path) -> dict[str, str]:
    """Env overriding one operand with a release-style spelling (or, for
    ``"single"``, leaving both as the harness's default .json snapshots)."""
    if shape == "single":
        return {}
    if shape == "dir-new":
        target = tmp_path / "new-bundle"
        target.mkdir(exist_ok=True)
        return {"INPUT_NEW_LIBRARY": str(target)}
    if shape == "dir-old":
        target = tmp_path / "old-bundle"
        target.mkdir(exist_ok=True)
        return {"INPUT_OLD_LIBRARY": str(target)}
    if shape == "rpm-new":
        target = tmp_path / "libfoo.rpm"
        target.write_text("", encoding="utf-8")
        return {"INPUT_NEW_LIBRARY": str(target)}
    if shape == "whl-new":
        target = tmp_path / "foo-1.0.whl"
        target.write_text("", encoding="utf-8")
        return {"INPUT_NEW_LIBRARY": str(target)}
    raise AssertionError(f"unknown operand shape {shape!r}")


def _captured_config(tmp_path: Path) -> dict[str, Any]:
    """The `--config` document the fake CLI stub snapshotted, or `{}`."""
    captured_config = tmp_path / "captured_config.json"
    if not captured_config.is_file():
        return {}
    with open(captured_config, encoding="utf-8") as f:
        doc: dict[str, Any] = json.load(f)
    return doc


def _compare_result(
    env_extra: dict[str, str], tmp_path: Path
) -> tuple[str, dict[str, Any]]:
    """Run the real run.sh and return ``(command line, --config document)``.

    Asserts success -- every case here is one the CLI serves, so a
    non-zero exit is itself the regression.
    """
    result, captured = _run_compare_raw(env_extra, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert captured.is_file(), "abicheck stub was never invoked"
    return captured.read_text(encoding="utf-8").strip(), _captured_config(tmp_path)


def _run_validate(env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    return subprocess.run(
        [_bash_executable(), str(VALIDATE_SH)],
        capture_output=True,
        text=True,
        env={**base_env, "INPUT_MODE": "compare", **env_extra},
        check=False,
    )


class TestReleaseOperandGetsTheSinglePairCompileContext:
    """Every compile-context input, on every release-style operand shape,
    must produce exactly the ``compile:`` block the single-pair shape
    produces for the same input -- 7 x 4 measured pairs, with the
    single-pair run as the oracle."""

    @pytest.mark.parametrize("var,value", _COMPILE_CONTEXT_INPUTS)
    @pytest.mark.parametrize("shape", _RELEASE_OPERAND_SHAPES)
    def test_matches_the_single_pair_overlay(
        self, tmp_path: Path, shape: str, var: str, value: str
    ) -> None:
        single_dir = tmp_path / "single"
        single_dir.mkdir()
        _, single_doc = _compare_result({var: value}, single_dir)
        assert single_doc.get("compile"), (
            f"{var}={value} must reach the single-pair compile: overlay for "
            "this case to mean anything"
        )

        release_dir = tmp_path / "release"
        release_dir.mkdir()
        _, release_doc = _compare_result(
            {var: value, **_operand_env(shape, release_dir)}, release_dir
        )
        assert release_doc.get("compile") == single_doc.get("compile")

    @pytest.mark.parametrize("var,value", _COMPILE_CONTEXT_INPUTS)
    def test_validate_inputs_agrees_with_run_sh(
        self, tmp_path: Path, var: str, value: str
    ) -> None:
        """The fail-fast validator is an adapter over the same CLI, so it
        may not reject what run.sh forwards (action/AGENTS.md: "Keep
        validate-inputs.sh and run.sh in sync") -- the divergence that
        version of this guard would produce is a workflow failing before
        dependency install for a comparison the run itself would serve."""
        pkg = tmp_path / "libfoo.rpm"
        pkg.write_text("", encoding="utf-8")
        result = _run_validate(
            {
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_NEW_LIBRARY": str(pkg),
                var: value,
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr


class TestReleaseOperandDepthLadderMatchesSinglePair:
    """Each rung of the public ladder, measured against the single-pair
    answer for the same rung, in several spellings -- and the rungs the CLI
    genuinely still refuses, which must fail loud rather than be dropped."""

    @pytest.mark.parametrize("rung", _FORWARDED_DEPTH_RUNGS)
    @pytest.mark.parametrize("shape", _RELEASE_OPERAND_SHAPES)
    def test_forwarded_rung_matches_single_pair(
        self, tmp_path: Path, shape: str, rung: str
    ) -> None:
        release_dir = tmp_path / "release"
        release_dir.mkdir()
        cmd, _ = _compare_result(
            {"INPUT_DEPTH": rung, **_operand_env(shape, release_dir)}, release_dir
        )
        assert f"--depth {rung}" in cmd

    @pytest.mark.parametrize("rung", _FORWARDED_DEPTH_RUNGS)
    @pytest.mark.parametrize("spelling", ("upper", "title"))
    def test_case_variants_reach_the_cli_lowercased(
        self, tmp_path: Path, rung: str, spelling: str
    ) -> None:
        """INPUT_DEPTH is a raw, unvalidated Action input and the CLI's own
        ``DepthParam.convert()`` is case-insensitive, so no spelling may be
        the one that silently drops the rung."""
        typed = rung.upper() if spelling == "upper" else rung.capitalize()
        new_dir = tmp_path / "new-bundle"
        new_dir.mkdir()
        cmd, _ = _compare_result(
            {"INPUT_DEPTH": typed, "INPUT_NEW_LIBRARY": str(new_dir)}, tmp_path
        )
        assert f"--depth {rung}" in cmd

    @pytest.mark.parametrize("rung", _CLI_REJECTED_DEPTH_RUNGS)
    @pytest.mark.parametrize("shape", _RELEASE_OPERAND_SHAPES)
    def test_cli_rejected_rung_fails_loud(
        self, tmp_path: Path, shape: str, rung: str
    ) -> None:
        result, captured = _run_compare_raw(
            {"INPUT_DEPTH": rung, **_operand_env(shape, tmp_path)}, tmp_path
        )
        assert result.returncode != 0
        assert "::error::" in result.stdout
        assert not captured.is_file(), "abicheck must never be invoked"

    @pytest.mark.parametrize("var,value", _CLI_REJECTED_EVIDENCE_INPUTS)
    @pytest.mark.parametrize("shape", _RELEASE_OPERAND_SHAPES)
    def test_cli_rejected_evidence_input_fails_loud(
        self, tmp_path: Path, shape: str, var: str, value: str
    ) -> None:
        result, captured = _run_compare_raw(
            {var: value, **_operand_env(shape, tmp_path)}, tmp_path
        )
        assert result.returncode != 0
        assert "::error::" in result.stdout
        assert not captured.is_file(), "abicheck must never be invoked"


class TestReleaseOperandConfigOverlayStaysSingular:
    """Allowing the compile-context inputs on this shape made a second
    ``--config`` producer reachable where one already was
    (`add_release_topology_config_flags`). The command line must still
    carry exactly one ``--config``, and none of the three contributing
    documents may be lost to the merge."""

    @pytest.mark.parametrize("shape", _RELEASE_OPERAND_SHAPES)
    def test_compile_context_and_build_config_yield_one_config(
        self, tmp_path: Path, shape: str
    ) -> None:
        build_config = tmp_path / "project.yml"
        build_config.write_text(
            json.dumps({"severity": {"abi_breaking": "error"}}), encoding="utf-8"
        )
        cmd, doc = _compare_result(
            {
                "INPUT_BUILD_CONFIG": str(build_config),
                "INPUT_SYSROOT": "/opt/sysroot",
                **_operand_env(shape, tmp_path),
            },
            tmp_path,
        )
        assert cmd.split().count("--config") == 1
        assert doc["compile"]["sysroot"] == "/opt/sysroot"
        assert doc["severity"] == {"abi_breaking": "error"}

    @pytest.mark.parametrize("shape", _RELEASE_OPERAND_SHAPES)
    def test_compile_context_and_release_topology_yield_one_config(
        self, tmp_path: Path, shape: str
    ) -> None:
        cmd, doc = _compare_result(
            {
                "INPUT_SYSROOT": "/opt/sysroot",
                "INPUT_DSO_ONLY": "true",
                **_operand_env(shape, tmp_path),
            },
            tmp_path,
        )
        assert cmd.split().count("--config") == 1
        assert doc["compile"]["sysroot"] == "/opt/sysroot"
        assert doc["release"]["dso_only"] is True

    @pytest.mark.parametrize("shape", _RELEASE_OPERAND_SHAPES)
    def test_all_three_config_sources_survive_the_merge(
        self, tmp_path: Path, shape: str
    ) -> None:
        """build-config + compile context + release topology at once: the
        combination that reaches both merge steps in sequence."""
        build_config = tmp_path / "project.yml"
        build_config.write_text(
            json.dumps({"severity": {"abi_breaking": "error"}}), encoding="utf-8"
        )
        cmd, doc = _compare_result(
            {
                "INPUT_BUILD_CONFIG": str(build_config),
                "INPUT_GCC_OPTIONS": "-DFOO=1",
                "INPUT_FAIL_ON_REMOVED_LIBRARY": "true",
                **_operand_env(shape, tmp_path),
            },
            tmp_path,
        )
        assert cmd.split().count("--config") == 1
        assert doc["compile"]["options"] == ["-DFOO=1"]
        assert doc["severity"] == {"abi_breaking": "error"}
        assert doc["gate"]["fail_on_removed_library"] is True
