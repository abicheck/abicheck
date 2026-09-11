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

"""Product-gaps audit §3's "real effect" half, now a **retirement** pin
(rulings.py deferred-option followup): the ``require-complete-analysis``
forwarding chain this file used to assert (``check-project.yml``'s
``matrix.analysis_assurance`` -> ``check-target``'s own
``require-complete-analysis`` input -> the nested root-Action analysis
step) is gone. The CLI's own ``compare --require-complete-analysis`` flag
it terminated at was demoted to a config-only ``.abicheck.yml``
``assurance.require_complete: true`` with no CLI or Action-input override,
so the whole chain has nothing left to forward to -- see
``actions/check-target/action.yml``'s own ``require-complete-analysis``
input docstring and ``.github/workflows/check-project.yml``'s own comment
at its former call site for the documented, tracked gap this leaves
(``checks[].analysis.assurance: complete`` is still validated at run-plan
generation time but no longer enforced by this Action chain).

Split out of ``tests/test_reusable_workflows.py`` purely to respect that
file's ``architecture/debt.yaml`` ``no_growth`` baseline -- see this repo's
own "move responsibility, don't trim to fit" convention (root
``AGENTS.md``).

Structural tests over the parsed YAML, mirroring ``test_reusable_workflows.
py``'s own precedent: a real GitHub Actions runner is needed to exercise the
nested composite `uses:` chain end to end.
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


class TestRequireCompleteAnalysisRetired:
    def test_check_project_no_longer_forwards_matrix_analysis_assurance(
        self,
    ) -> None:
        data = _load(CHECK_PROJECT)
        steps = data["jobs"]["check"]["steps"]
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        assert "require-complete-analysis" not in run_step["with"]

    def test_check_target_action_still_declares_the_retired_input(self) -> None:
        """Still declared (as a tombstone), so a workflow that sets it gets
        an explicit ::error:: instead of a silently-dropped, undeclared
        key -- see actions/check-target/validate-inputs.sh."""
        data = _load(CHECK_TARGET_ACTION)
        assert "require-complete-analysis" in data["inputs"]
        assert data["inputs"]["require-complete-analysis"]["default"] == "false"
        description = data["inputs"]["require-complete-analysis"]["description"]
        assert "RETIRED" in description

    def test_check_target_action_no_longer_forwards_to_the_root_action(self) -> None:
        """The nested root-Action analysis step has nothing left to
        forward to (its own require-complete-analysis input is retired
        too), so this cell's own conditional forward is gone outright."""
        data = _load(CHECK_TARGET_ACTION)
        analysis_step = next(
            s for s in data["runs"]["steps"] if s.get("name") == "Run analysis"
        )
        assert "require-complete-analysis" not in analysis_step["with"]
