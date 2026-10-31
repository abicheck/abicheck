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

"""Packaging an operand does not change the compile context (ADR-091 D4).

The compile-context inputs were retired to ``set:`` lines (ADR-091 D7). The
parity claim survives the retirement in its new form: a directory/package
operand forwards the same ``--set compile.*`` lines a single pair does, and
the retired input names are refused on the release shape exactly as on the
single-pair shape. Every assertion compares against the single-pair run for
the identical inputs, never against a hand-written constant.
"""

from __future__ import annotations

import pytest
from test_action_compile_set_forwarding import RETIRED_COMPILE_INPUTS, _validate
from test_action_run_sh_set_input import _run_compare, _set_pairs

_COMPILE_SET = 'compile.lang="c"\ncompile.compiler=/usr/bin/g++\n'


def _release_operand(tmp_path):
    """A real directory: a package-style operand, not a file."""
    release = tmp_path / "release-operand"
    release.mkdir(exist_ok=True)
    return release


class TestCompareReleaseStyleOperandCompileContext:
    def test_set_lines_reach_a_release_operand_like_a_single_pair(
        self, tmp_path
    ) -> None:
        single = _set_pairs(_run_compare(tmp_path / "single", _COMPILE_SET))
        release_dir = _release_operand(tmp_path / "release")
        release = _set_pairs(
            _run_compare(
                tmp_path / "release-run",
                _COMPILE_SET,
                INPUT_OLD_LIBRARY=str(release_dir),
                INPUT_NEW_LIBRARY=str(release_dir),
            )
        )
        assert single == ['compile.lang="c"', "compile.compiler=/usr/bin/g++"]
        assert release == single

    def test_release_operand_with_no_compile_set_forwards_no_compile_set(
        self, tmp_path
    ) -> None:
        release_dir = _release_operand(tmp_path)
        pairs = _set_pairs(
            _run_compare(
                tmp_path / "run",
                "",
                INPUT_OLD_LIBRARY=str(release_dir),
                INPUT_NEW_LIBRARY=str(release_dir),
            )
        )
        assert [p for p in pairs if p.startswith("compile.")] == []

    @pytest.mark.parametrize(("name", "value", "replacement"), RETIRED_COMPILE_INPUTS)
    def test_retired_compile_input_is_refused_on_a_release_operand(
        self, tmp_path, name: str, value: str, replacement: str
    ) -> None:
        """The refusal does not depend on the operand shape: the validator
        names the replacement for a directory operand as for a file."""
        release_dir = _release_operand(tmp_path)
        env_name = "INPUT_" + name.upper().replace("-", "_")
        result = _validate(
            INPUT_OLD_LIBRARY=str(release_dir),
            INPUT_NEW_LIBRARY=str(release_dir),
            **{env_name: value},
        )
        combined = result.stdout + result.stderr
        assert result.returncode != 0, combined
        assert name in combined, combined
        assert f"set: {replacement}=" in combined, combined
