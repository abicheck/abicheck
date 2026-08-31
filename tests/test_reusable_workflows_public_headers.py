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

"""``check-project.yml``'s per-cell ``header`` override -- split out of
``test_reusable_workflows.py`` (a debt.yaml ``no_growth``-tracked module) to
keep this addition's tests together without pushing that file over its
recorded baseline.

Mirrors ``test_reusable_workflows.py``'s own ``ast-frontend`` per-cell-
override tests exactly, for the identical ``matrix.header || inputs.header``
precedence (``run_plan.RunPlanCheck.header``, ``project_targets.
TargetSpec.public_headers``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
CHECK_PROJECT = WORKFLOWS_DIR / "check-project.yml"


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return job["steps"]


class TestCheckProjectPerCellHeaderOverride:
    def test_run_check_target_prefers_per_cell_header(self) -> None:
        """A target's `public_headers:` (`run_plan.RunPlanCheck.header`,
        ADR-047's own worked example) drives that cell's `--header`, falling
        back to the workflow-global `header` input when the target declares
        none -- mirroring `ast-frontend`'s identical per-cell-first
        precedence exactly. Without this, `public_headers:` was declared,
        validated, and round-tripped through the config schema but never
        actually reached a `compare`/`scan` invocation."""
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        assert run_step["with"]["header"] == (
            "${{ matrix.kind != 'bundle' && matrix.header || inputs.header }}"
        )

    def test_per_cell_header_is_not_forwarded_to_bundle_cells(self) -> None:
        """`run_plan.RunPlanCheck.header` is never set for a `kind: bundle`
        cell (per-bundle-member header staging doesn't exist yet -- see
        `BUNDLE_CHECK_DEPTHS`'s own docstring in `project_targets.py`), so
        this is a no-op today -- but the explicit guard, and its fallback to
        the workflow-global input rather than the empty string, keep a
        bundle cell's behaviour pinned rather than silently depending on the
        field always being empty (mirroring the identical `ast-frontend`
        guard's own reasoning)."""
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        expr = run_step["with"]["header"]
        assert "matrix.kind != 'bundle'" in expr
        assert expr.endswith("|| inputs.header }}")
