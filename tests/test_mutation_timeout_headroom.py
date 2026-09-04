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

"""P2 review finding, split out of ``test_mutation_score_gate.py`` (which
already carries several such ``_extra``-style splits -- ``test_mutation_run_
scoping.py``, ``test_mutation_per_module_scoping.py`` -- for the identical
file-size reason) purely to stay under that file's AI-readiness no-growth
debt baseline.

Finding: the mutation.yml PR job's ``timeout-minutes`` and
``check_mutation_score.py``'s ``MUTMUT_RUN_TIMEOUT_SECONDS`` must not simply
MATCH. A GitHub Actions job timeout kills the WHOLE job -- including the
result-export/artifact-upload steps that run after ``mutmut run`` -- the
instant it elapses, with no chance for a receipt to be written. Setting the
two equal meant a long-but-legitimate ``mutmut run`` was terminated by the
OUTER job limit before its own inner subprocess timeout could ever fire and
be handled cleanly, so neither path produced a receipt. Fixed by raising the
job timeout strictly above the subprocess timeout with real headroom.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_GATE_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "check_mutation_score.py"
)
_spec = importlib.util.spec_from_file_location("check_mutation_score", _GATE_PATH)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def test_mutmut_subprocess_timeout_leaves_headroom_below_the_job_ceiling() -> None:
    yaml = pytest.importorskip("yaml")

    workflow_path = (
        Path(__file__).resolve().parent.parent
        / ".github"
        / "workflows"
        / "mutation.yml"
    )
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    mutmut_job = workflow["jobs"]["mutmut"]
    job_timeout_seconds = int(mutmut_job["timeout-minutes"]) * 60

    assert gate.MUTMUT_RUN_TIMEOUT_SECONDS < job_timeout_seconds, (
        f"subprocess timeout ({gate.MUTMUT_RUN_TIMEOUT_SECONDS}s) must leave "
        f"headroom below the job timeout ({job_timeout_seconds}s), not equal "
        "or exceed it"
    )
    headroom_seconds = job_timeout_seconds - gate.MUTMUT_RUN_TIMEOUT_SECONDS
    assert headroom_seconds >= 20 * 60, (
        f"only {headroom_seconds}s of headroom -- checkout/deps/export/"
        "upload need real room to complete after the inner timeout fires"
    )
