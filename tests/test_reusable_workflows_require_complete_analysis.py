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

"""Product-gaps audit §3's "real effect" half: a declared ``checks[].
analysis.assurance: complete`` must actually reach the
``--require-complete-analysis`` gate, not just pass ``project
validate``/``project plan``'s truthfulness check (covered by
``tests/test_project_targets_check_identity.py``, the CheckSpec/RunPlanCheck
half of this chain). Split out of ``tests/test_reusable_workflows.py``
purely to respect that file's ``architecture/debt.yaml`` ``no_growth``
baseline -- see this repo's own "move responsibility, don't trim to fit"
convention (root ``AGENTS.md``).

Structural tests over the parsed YAML, mirroring ``test_reusable_workflows.
py``'s own precedent: a real GitHub Actions runner is needed to exercise the
nested composite `uses:` chain end to end, so these assert the wiring
(``matrix.analysis_assurance`` -> ``check-target``'s own
``require-complete-analysis`` input -> the nested root-Action analysis
step) rather than running it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK_PROJECT = _REPO_ROOT / ".github" / "workflows" / "check-project.yml"
CHECK_TARGET_ACTION = _REPO_ROOT / "actions" / "check-target" / "action.yml"


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class TestRequireCompleteAnalysisForwarding:
    def test_check_project_forwards_matrix_analysis_assurance(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = data["jobs"]["check"]["steps"]
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        assert run_step["with"]["require-complete-analysis"] == (
            "${{ matrix.analysis_assurance == 'complete' }}"
        )

    def test_check_target_action_declares_the_input(self) -> None:
        data = _load(CHECK_TARGET_ACTION)
        assert "require-complete-analysis" in data["inputs"]
        assert data["inputs"]["require-complete-analysis"]["default"] == "false"

    def test_check_target_action_gates_forwarding_on_kind_not_bundle(self) -> None:
        """A bundle check's operand is a directory -- the root Action
        rejects require-complete-analysis outright for a directory/package
        compare, so this cell's own gate must exclude kind: bundle rather
        than forwarding the raw input straight through."""
        data = _load(CHECK_TARGET_ACTION)
        analysis_step = next(
            s for s in data["runs"]["steps"] if s.get("name") == "Run analysis"
        )
        assert analysis_step["with"]["require-complete-analysis"] == (
            "${{ inputs.kind != 'bundle' && "
            "inputs.require-complete-analysis == 'true' }}"
        )

    def test_check_target_action_does_not_forward_for_other_inputs(self) -> None:
        """Negative control: an input the module docstring says is
        deliberately gated (kind: bundle) does not leak through as 'true'
        just because it's syntactically present -- confirms the assertion
        above is checking the real conditional expression, not a
        loosely-matching substring."""
        data = _load(CHECK_TARGET_ACTION)
        analysis_step = next(
            s for s in data["runs"]["steps"] if s.get("name") == "Run analysis"
        )
        expr = analysis_step["with"]["require-complete-analysis"]
        assert "kind != 'bundle'" in expr
        assert "kind == 'bundle'" not in expr
