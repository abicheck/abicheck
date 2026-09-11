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

"""``compare --no-baseline``'s audit-only shape (ADR-068) as validated by
`action/validate-inputs.sh` -- consumer/entrypoint scoping and OLD-sided
evidence inputs, both of which need a real OLD side that an audit-only
invocation (old-library/abi-baseline both omitted) does not have.

Split out of `test_action_validate_inputs.py` for the same
`architecture/debt.yaml` `no_growth` reason `test_action_validate_inputs_
injection.py` already documents at the top of its own module docstring --
move responsibility out, never trim the file to fit.
"""

from __future__ import annotations

import pytest
from test_action_validate_inputs import VALIDATE_SH, _run_validate


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestBuildTargetScanApplicationIsGone:
    """`build-target` remains a real `dump`-mode input; only its (now
    nonexistent) `scan` application is gone, since `mode: scan` itself is
    retired outright."""

    def test_warns_outside_dump(self) -> None:
        result = _run_validate(
            {"INPUT_MODE": "compare", "INPUT_BUILD_TARGET": "//:math"}
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" in result.stdout
        assert "build-target" in result.stdout

    def test_silent_on_dump(self) -> None:
        result = _run_validate({"INPUT_MODE": "dump", "INPUT_BUILD_TARGET": "//:math"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" not in result.stdout


class TestScopedComparisonInputsRejectedOnAuditOnly:
    """The audit-only shape (old-library/abi-baseline both omitted) rejects
    used-by/used-by-manifest/required-symbol/required-symbols outright --
    consumer/entrypoint scoping needs two versions to compare, and
    compare --no-baseline has no old/new pair (abicheck/frontends/cli/
    commands/no_baseline_rulings.py). Codex review, PR #1223, round 5:
    action/run.sh forwarded these four unconditionally, reaching the CLI's
    own late rejection only after Python setup and toolchain install."""

    @pytest.mark.parametrize(
        "env_name,value",
        [
            ("INPUT_USED_BY", "app1"),
            ("INPUT_USED_BY_MANIFEST", "manifest.json"),
            ("INPUT_REQUIRED_SYMBOL", "abi_do_thing"),
            ("INPUT_REQUIRED_SYMBOLS", "symbols.txt"),
        ],
    )
    def test_each_input_alone_is_rejected_without_a_baseline(
        self, env_name: str, value: str
    ) -> None:
        result = _run_validate({"INPUT_MODE": "compare", env_name: value})
        assert result.returncode == 1
        assert "used-by" in result.stdout
        assert "required-symbol" in result.stdout

    def test_passes_once_old_library_is_set(self) -> None:
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_USED_BY": "app1",
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.parametrize(
        "env_name,value",
        [
            ("INPUT_USED_BY", "app1"),
            ("INPUT_REQUIRED_SYMBOL", "abi_do_thing"),
            ("INPUT_REQUIRED_SYMBOLS", "symbols.txt"),
        ],
    )
    def test_scoped_input_warns_on_non_compare_mode(
        self, env_name: str, value: str
    ) -> None:
        result = _run_validate({"INPUT_MODE": "dump", env_name: value})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" in result.stdout
        assert "has no effect" in result.stdout

    def test_no_scoped_inputs_set_produces_no_warnings(self) -> None:
        result = _run_validate({"INPUT_MODE": "dump"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" not in result.stdout


class TestOldSidedInputsRejectedOnAuditOnly:
    """The audit-only shape (old-library/abi-baseline both omitted) rejects
    old-header/old-include/old-version outright -- there is no OLD side for
    this evidence to describe, and the CLI's own _reject_old_sided_inputs
    (abicheck/frontends/cli/commands/no_baseline_rulings.py) rejects an
    explicitly OLD-scoped --header/--include/--version rather than silently
    dropping it. Codex review, PR #1223, round 8: action/run.sh's audit-only
    branch previously just never forwarded these three, which reads as
    "honored" when it was silently dropped."""

    @pytest.mark.parametrize(
        "env_name,value",
        [
            ("INPUT_OLD_HEADER", "old_include/"),
            ("INPUT_OLD_INCLUDE", "old_include/"),
            ("INPUT_OLD_VERSION", "1.0.0"),
        ],
    )
    def test_each_input_alone_is_rejected_without_a_baseline(
        self, env_name: str, value: str
    ) -> None:
        result = _run_validate({"INPUT_MODE": "compare", env_name: value})
        assert result.returncode == 1
        assert "does not support" in result.stdout

    def test_old_header_passes_once_old_library_is_set(self) -> None:
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_OLD_HEADER": "old_include/foo.h",
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_old_version_default_placeholder_does_not_trigger_rejection(
        self,
    ) -> None:
        # Codex review, PR #1223, round 11 (P1): old-version's Action-level
        # default is the literal placeholder 'old' (action.yml), so GitHub
        # Actions always populates INPUT_OLD_VERSION with at least 'old' --
        # never actually empty. A bare truthiness check therefore rejected
        # *every* audit-only invocation, not just ones that explicitly set
        # old-version to something else. This is the regression test: the
        # real Action-populated default must not trip the rejection.
        result = _run_validate({"INPUT_MODE": "compare", "INPUT_OLD_VERSION": "old"})
        assert result.returncode == 0, result.stdout + result.stderr
