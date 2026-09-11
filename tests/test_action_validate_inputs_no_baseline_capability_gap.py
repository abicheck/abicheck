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

"""Codex review, PR #1210, round 4: `run.sh`'s own since/changed-path/
budget-on-audit-only-scan rejection (added round 2,
``tests/test_action_run_sh_scan_no_baseline_capability_gap.py``) was never
wired into ``action/validate-inputs.sh``, the earlier preflight copy that
runs *before* Python setup, the pixi/toolchain provision and the abicheck
install -- so a workflow setting one of these three on an audit-only
``mode: scan`` paid the whole provisioning cost before `run.sh` finally
rejected it. The same gap `TestScanRetiredInputsFailPreflight` in
``test_action_validate_inputs.py`` already closed for the four hard-retired
scan inputs (Codex review, PR #1186).

Split into its own file (rather than added to `test_action_validate_inputs.py`
directly) because that file is at its `architecture/debt.yaml` no-growth
baseline -- the established "move responsibility to a new module instead of
growing a bounded file" pattern this repo's own `AGENTS.md` documents,
mirroring ``test_action_validate_inputs_injection.py``'s own split.
"""

from __future__ import annotations

import pytest
from test_action_validate_inputs import _run_validate


class TestScanNoBaselineCapabilityGapFailsPreflight:
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
            {"INPUT_MODE": "scan", "INPUT_NEW_LIBRARY": "new.so", **env}
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "::error::" in result.stdout
        assert f"does not support {expected}" in result.stdout
        assert "against:" in result.stdout

    def test_forced_audit_with_a_baseline_still_rejects(self) -> None:
        # audit: true forces audit-only even with against set -- mirrors
        # run.sh's own _SCAN_HAS_BASELINE computation
        # (FORCE_AUDIT_ONLY != "true" && -n INPUT_AGAINST).
        result = _run_validate(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_AGAINST": "baseline.so",
                "INPUT_AUDIT": "true",
                "INPUT_SINCE": "origin/main",
            }
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "since" in result.stdout

    @pytest.mark.parametrize(
        "env", [{"INPUT_SINCE": "origin/main"}, {"INPUT_BUDGET": "15m"}]
    )
    def test_baseline_scan_is_unaffected(self, env: dict[str, str]) -> None:
        result = _run_validate(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_AGAINST": "baseline.so",
                **env,
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::error::" not in result.stdout

    def test_plain_audit_only_scan_unaffected(self) -> None:
        result = _run_validate({"INPUT_MODE": "scan", "INPUT_NEW_LIBRARY": "new.so"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::error::" not in result.stdout
