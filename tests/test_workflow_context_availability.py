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

"""No workflow may name a context where GitHub does not provide one.

A context used in a position that does not offer it is not a runtime error
in one job -- it is a *validation* error for the whole file, so GitHub
rejects the workflow before any job starts. The run then completes in about
a second with `failure`, zero jobs, and a title taken from the file's path
rather than its `name:`, because the file never parsed. Every gate in that
workflow is silently offline, which for `ci.yml` is the entire required set.

Observed here rather than theorised: PR #1294 set `TMPDIR: ${{ runner.temp }}`
in `jobs.unit-tests.env`, and run 34779572374 did exactly the above. `runner`
is available to a *step*'s `env`/`if`/`run`, and not at `jobs.<id>.env` --
which reads as an arbitrary distinction and is easy to get wrong, since the
same expression one indentation level down is correct. No local check catches
it: the file is valid YAML, `ruff` does not read it, and the repository's own
workflow tests parse it rather than validate it.

Scoped deliberately narrowly. This asserts the *one* rule that has actually
bitten, over the positions a plain YAML parse can identify with certainty --
job-level `env`, `if`, `name`, `timeout-minutes`, `continue-on-error` and
`strategy` -- rather than attempting a general re-implementation of GitHub's
context-availability table, which would be a second, unverifiable copy of a
specification this repository does not own. `actionlint` would subsume it; it
is not available in this environment, and a test that only runs where a tool
happens to be installed is not a gate.
"""

from __future__ import annotations

import re

import pytest
import yaml
from _workflow_files import read_repo_text, workflow_paths

#: Contexts GitHub does not resolve at the *job* level. `runner` describes the
#: machine a step runs on, which does not exist until a job is dispatched, so
#: no `jobs.<id>.*` key outside a step may name it. `steps`, `job` and `env`
#: are unavailable there for the same reason: they describe state that only
#: exists once the job is running.
_JOB_LEVEL_FORBIDDEN = ("runner", "steps", "job", "env")

#: The `jobs.<id>.*` keys whose values GitHub evaluates as expressions and
#: that are not themselves step definitions.
_JOB_LEVEL_EXPRESSION_KEYS = (
    "env",
    "if",
    "name",
    "timeout-minutes",
    "continue-on-error",
    "strategy",
)


def _expressions(value: object) -> list[str]:
    """Every `${{ ... }}` body anywhere inside *value*, recursively."""
    if isinstance(value, str):
        return re.findall(r"\$\{\{(.*?)\}\}", value, flags=re.DOTALL)
    if isinstance(value, dict):
        return [e for v in value.values() for e in _expressions(v)]
    if isinstance(value, list):
        return [e for v in value for e in _expressions(v)]
    return []


def _job_level_violations(document: dict) -> list[str]:
    found: list[str] = []
    for job_name, job in (document.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        for key in _JOB_LEVEL_EXPRESSION_KEYS:
            if key not in job:
                continue
            for expression in _expressions(job[key]):
                for context in _JOB_LEVEL_FORBIDDEN:
                    if re.search(rf"\b{context}\s*\.", expression):
                        found.append(
                            f"jobs.{job_name}.{key} uses the '{context}' "
                            f"context: ${{{{{expression}}}}}"
                        )
    return found


@pytest.mark.parametrize("path", workflow_paths(), ids=lambda p: p.name)
def test_no_job_level_key_uses_a_step_only_context(path) -> None:
    document = yaml.safe_load(read_repo_text(path))
    if not isinstance(document, dict):  # pragma: no cover - a non-mapping workflow
        pytest.skip(f"{path.name} is not a mapping")
    violations = _job_level_violations(document)
    assert violations == [], (
        f"{path.name} would be REJECTED by GitHub before any job starts, "
        "taking every gate in it offline. Move the value into a step (an "
        '`env:` on the step, or `echo "NAME=$VALUE" >> "$GITHUB_ENV"` in '
        f"a `run:`), where the context is available: {violations}"
    )


def test_the_check_catches_the_shape_that_broke_ci() -> None:
    """Vacuity guard, against the exact document that was rejected.

    Without this, the sweep above passes on a clean tree whether or not the
    rule is implemented at all -- which is the state the repository was in
    when the real one shipped.
    """

    rejected = yaml.safe_load(
        "jobs:\n"
        "  unit-tests:\n"
        "    env:\n"
        "      TMPDIR: ${{ runner.temp }}\n"
        "    steps:\n"
        "      - run: true\n"
    )
    assert _job_level_violations(rejected) == [
        "jobs.unit-tests.env uses the 'runner' context: ${{ runner.temp }}"
    ]


def test_the_same_expression_inside_a_step_is_accepted() -> None:
    """And the rule must not reject the correct spelling one level down."""

    accepted = yaml.safe_load(
        "jobs:\n"
        "  unit-tests:\n"
        "    steps:\n"
        "      - run: echo hi\n"
        "        env:\n"
        "          TMPDIR: ${{ runner.temp }}\n"
        "      - if: runner.os == 'Linux'\n"
        "        run: echo linux\n"
    )
    assert _job_level_violations(accepted) == []
