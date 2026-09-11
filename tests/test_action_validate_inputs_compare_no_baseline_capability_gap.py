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

"""``action/validate-inputs.sh``'s since/changed-path/budget-on-audit-only
rejection -- originally added for legacy `mode: scan`'s own audit-only
shape (Codex review, PR #1210), and now real, first-class validation of
`mode: compare`'s audit-only shape (old-library and abi-baseline both
omitted) since ADR-068's Action-input-lifecycle amendment retired
`mode: scan` outright. `run.sh` keeps its own copy of this exact check,
covered by ``tests/test_action_run_sh_compare_no_baseline_capability_gap.py``.

Split into its own file (rather than added to `test_action_validate_inputs.py`
directly) because that file is at its `architecture/debt.yaml` no-growth
baseline -- the established "move responsibility to a new module instead of
growing a bounded file" pattern this repo's own `AGENTS.md` documents,
mirroring ``test_action_validate_inputs_injection.py``'s own split.
"""

from __future__ import annotations

import pytest
from test_action_validate_inputs import _run_validate


class TestCompareNoBaselineCapabilityGapFailsPreflight:
    @pytest.mark.parametrize(
        ("env", "expected"),
        [
            ({"INPUT_SINCE": "origin/main"}, "since"),
            ({"INPUT_CHANGED_PATH": "src/foo.c"}, "changed-path"),
            ({"INPUT_BUDGET": "15m"}, "budget"),
        ],
    )
    def test_audit_only_rejects_with_a_message_naming_the_input(
        self, env: dict[str, str], expected: str
    ) -> None:
        result = _run_validate(
            {"INPUT_MODE": "compare", "INPUT_NEW_LIBRARY": "new.so", **env}
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "::error::" in result.stdout
        assert f"does not support {expected}" in result.stdout
        assert "old-library" in result.stdout

    @pytest.mark.parametrize(
        "env", [{"INPUT_SINCE": "origin/main"}, {"INPUT_BUDGET": "15m"}]
    )
    def test_two_sided_compare_is_unaffected(self, env: dict[str, str]) -> None:
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_OLD_LIBRARY": "baseline.so",
                **env,
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::error::" not in result.stdout

    def test_abi_baseline_also_counts_as_having_a_baseline(self) -> None:
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_SINCE": "origin/main",
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::error::" not in result.stdout

    def test_plain_audit_only_compare_unaffected(self) -> None:
        result = _run_validate({"INPUT_MODE": "compare", "INPUT_NEW_LIBRARY": "new.so"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::error::" not in result.stdout
