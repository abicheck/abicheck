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

"""ADR-091 R14 for the reusable workflows: a retired input fails the run.

``check-single.yml`` and ``check-project.yml`` keep each renamed input declared
as an empty tombstone, because a value passed under an undeclared ``with:`` key
is not a reliable failure. The guard step rejects a non-empty tombstone before
any analysis runs, and these tests execute that step for real (``_workflow_exec``)
rather than reading its text, so the rejection is observed, not assumed.
"""

from __future__ import annotations

import pytest
from _workflow_exec import (
    find_run_step,
    have_bash,
    load_workflow,
    make_workspace,
    run_step,
)

pytestmark = pytest.mark.skipif(not have_bash(), reason="needs a real bash")

GUARD = "Reject retired workflow inputs"
#: (workflow, the job whose first step is the guard)
WORKFLOWS = [("check-single.yml", "check"), ("check-project.yml", "plan")]


def _retired_inputs(workflow: str) -> dict[str, str]:
    """Retired input name -> replacement, read from the declarations."""
    on = load_workflow(workflow).get(True) or load_workflow(workflow).get("on")
    declared = on["workflow_call"]["inputs"]
    out = {}
    for name, spec in declared.items():
        description = str(spec.get("description", ""))
        if "RETIRED" in description:
            # "...renamed to `depth`. ..." -> depth
            out[name] = description.split("renamed to `", 1)[1].split("`", 1)[0]
    return out


@pytest.mark.parametrize(("workflow", "job"), WORKFLOWS)
def test_every_retired_input_is_rejected_by_the_guard(workflow: str, job: str) -> None:
    """The guard names each retired declaration, so none can be left out."""
    retired = _retired_inputs(workflow)
    assert retired, f"{workflow} declares no retired inputs"
    step = find_run_step(workflow, job, GUARD)
    for name in retired:
        var = "RETIRED_" + name.upper().replace("-", "_")
        assert var in step["env"], f"{name} is not forwarded to the guard"
        assert f"reject {name} " in step["run"], f"{name} is not checked by the guard"


@pytest.mark.parametrize(("workflow", "job"), WORKFLOWS)
def test_guard_passes_when_no_retired_input_is_set(
    tmp_path, workflow: str, job: str
) -> None:
    step = find_run_step(workflow, job, GUARD)
    result = run_step(
        step,
        workspace=make_workspace(tmp_path),
        env={
            "RETIRED_" + n.upper().replace("-", "_"): ""
            for n in _retired_inputs(workflow)
        },
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("workflow", "job", "name", "replacement"),
    [(w, j, n, r) for w, j in WORKFLOWS for n, r in _retired_inputs(w).items()],
)
def test_setting_a_retired_input_fails_and_names_its_replacement(
    tmp_path, workflow: str, job: str, name: str, replacement: str
) -> None:
    step = find_run_step(workflow, job, GUARD)
    env = {
        "RETIRED_" + n.upper().replace("-", "_"): "" for n in _retired_inputs(workflow)
    }
    env["RETIRED_" + name.upper().replace("-", "_")] = "some-value"
    result = run_step(step, workspace=make_workspace(tmp_path), env=env)
    assert result.returncode != 0
    assert f"input '{name}' is retired; set '{replacement}' instead" in result.stderr
