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

"""Compile-context forwarding for ``dump`` and ``compare`` (ADR-091 D4, D7).

The compile context reaches the CLI as ``--set compile.*`` pairs (the
``compile:`` overlay is gone). ``lang``, ``compiler`` and ``sysroot`` are live
inputs; ``frontend``, ``compile-options`` and ``nostdinc`` are RETIRED and fail
the run with their ``set:`` replacement named. These tests drive the real
``action/run.sh`` through the same argv stub the set-forwarding tests use, so
the assertions are about the command that would actually run.
"""

from __future__ import annotations

import pytest
from test_action_compile_set_forwarding import RETIRED_COMPILE_INPUTS, _run, _validate
from test_action_run_sh_set_input import _set_pairs

LIVE_COMPILE_SETS = [
    ("INPUT_COMPILER", "/opt/gcc-14/bin/g++", 'compile.compiler="/opt/gcc-14/bin/g++"'),
    ("INPUT_SYSROOT", "/opt/sysroot", 'compile.sysroot="/opt/sysroot"'),
    ("INPUT_LANG", "c", 'compile.lang="c"'),
]


def _compile_pairs(argv: list[str]) -> list[str]:
    return [value for value in _set_pairs(argv) if value.startswith("compile.")]


@pytest.mark.parametrize("mode", ["dump", "compare"])
@pytest.mark.parametrize(("env_name", "value", "expected"), LIVE_COMPILE_SETS)
def test_live_compile_input_forwards_as_a_set_pair_for_each_mode(
    tmp_path, mode: str, env_name: str, value: str, expected: str
) -> None:
    argv = _run(tmp_path, mode, **{env_name: value})
    assert expected in _compile_pairs(argv)


@pytest.mark.parametrize("mode", ["dump", "compare"])
def test_no_compile_inputs_forward_no_compile_pair(tmp_path, mode: str) -> None:
    argv = _run(tmp_path, mode)
    assert _compile_pairs(argv) == []


@pytest.mark.parametrize("mode", ["dump", "compare"])
def test_default_lang_alone_forwards_nothing(tmp_path, mode: str) -> None:
    """``c++`` is the lang default, not a user override: it forwards nothing."""
    argv = _run(tmp_path, mode, INPUT_LANG="c++")
    assert _compile_pairs(argv) == []


def test_compile_inputs_never_synthesize_a_config_overlay(tmp_path) -> None:
    argv = _run(
        tmp_path,
        INPUT_COMPILER="/usr/bin/g++",
        INPUT_SYSROOT="/opt/sysroot",
        INPUT_LANG="c",
    )
    assert "--config" not in argv


def test_build_config_is_still_passed_alongside_the_set_pairs(tmp_path) -> None:
    """The retired overlay used to fold build-config into one document. Now the
    CLI reads build-config itself, and the compile context rides beside it as
    ``--set`` pairs, so neither is stripped by the other."""
    build_config = tmp_path / "build.abicheck.yml"
    build_config.write_text("compile:\n  lang: c++\n", encoding="utf-8")
    argv = _run(
        tmp_path,
        INPUT_BUILD_CONFIG=str(build_config),
        INPUT_COMPILER="/usr/bin/g++",
    )
    assert "--config" in argv
    assert argv[argv.index("--config") + 1] == str(build_config)
    assert 'compile.compiler="/usr/bin/g++"' in _compile_pairs(argv)


@pytest.mark.parametrize(("name", "value", "replacement"), RETIRED_COMPILE_INPUTS)
def test_retired_compile_input_is_refused_with_its_replacement(
    name: str, value: str, replacement: str
) -> None:
    env_name = "INPUT_" + name.upper().replace("-", "_")
    result = _validate(**{env_name: value})
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert name in combined, combined
    assert f"set: {replacement}=" in combined, combined
