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

"""The L2 perf lane's base-sha handling, executed against a hostile value.

`performance.yml`'s `l2-cli-perf` job resolves the PR's base commit into a shell
command. GitHub substitutes a `${{ }}` expression *textually* before bash parses
the line, so an interpolated value is shell source, not data -- and the whole
lesson of #705 -> #758 is that asserting the workflow's *text* proves nothing
about behaviour under a hostile value. So this module runs the real step body
with `BASE_SHA` set to a command-substitution payload and requires the payload
not to execute, observed through a sentinel file the payload would create. The
body is executed verbatim, not paraphrased; under `set -e` it stops at the
`git worktree add` line under test (the payload is not a commit), which is why
this costs well under a second rather than a venv and a pip install.

`base.sha` is a 40-hex commit id today, so the payload is not reachable in
production. That is precisely why the guard needs an executing test: the reason
the line looked safe is a property of today's value, not of the code, and the
next value spliced in the same way may not share it.
"""

from __future__ import annotations

import re

import pytest
from _workflow_exec import (
    find_run_step,
    have_bash,
    make_workspace,
    outside_is_intact,
    require_bash,
    run_step,
)

pytestmark = pytest.mark.skipif(
    not have_bash(), reason="needs a real bash (the runner's own shell)"
)

_STEP = "Measure base and gate head against it"
_WORKFLOW = "performance.yml"


def _step() -> dict:
    return find_run_step(_WORKFLOW, "l2-cli-perf", _STEP)


class TestBaseShaCannotExecuteCommands:
    def test_a_command_substitution_payload_does_not_run(self, tmp_path):
        require_bash()
        workspace = make_workspace(tmp_path)
        marker = tmp_path / "PAYLOAD_RAN"
        payload = f'$(touch "{marker}")'

        result = run_step(
            _step(),
            workspace=workspace,
            env={"BASE_SHA": payload},
        )

        # The step is expected to FAIL (the payload is not a commit), but it must
        # fail as a bad git argument, not by executing anything.
        assert not marker.exists(), (
            f"the payload executed: rc={result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
        assert outside_is_intact(tmp_path)

    @pytest.mark.parametrize(
        "payload",
        [
            '$(touch "{marker}")',
            '`touch "{marker}"`',
            '; touch "{marker}"',
            '" ; touch "{marker}" ; echo "',
            '$(echo x) && touch "{marker}"',
            '\n touch "{marker}"\n',
        ],
    )
    def test_no_shell_metacharacter_shape_executes(self, tmp_path, payload):
        # Several independently-chosen shapes, not the one that happened to be
        # tried first: command substitution in both spellings, a statement
        # separator, a quote-breaking value, a conjunction, and a newline (which
        # a single-line quoting fix would not stop).
        require_bash()
        workspace = make_workspace(tmp_path)
        marker = tmp_path / "PAYLOAD_RAN"
        result = run_step(
            _step(),
            workspace=workspace,
            env={"BASE_SHA": payload.format(marker=marker)},
        )
        assert not marker.exists(), (
            f"{payload!r} executed: rc={result.returncode}\n{result.stderr}"
        )
        assert outside_is_intact(tmp_path)

    def test_the_test_can_actually_detect_execution(self, tmp_path):
        # Vacuity guard on the whole module: if the payload could never run
        # under this harness, every assertion above would pass against an
        # interpolated (vulnerable) step too. So run the vulnerable shape
        # deliberately and require the marker to appear.
        require_bash()
        workspace = make_workspace(tmp_path)
        marker = tmp_path / "PAYLOAD_RAN"
        vulnerable = {
            "name": "interpolated (the shape this guard forbids)",
            # What a `${{ }}` expansion produces: the value as shell source.
            "run": f'set -euo pipefail\necho start\ngit worktree add base_tree "$(touch "{marker}")"\n',
        }
        run_step(vulnerable, workspace=workspace, env={})
        assert marker.exists(), "the harness cannot observe execution at all"


class TestTheStepReadsTheShaAsData:
    def test_the_body_does_not_interpolate_the_base_sha(self):
        # The cheap text guard is kept alongside the executing one, not instead
        # of it: it names the regression precisely when it happens.
        body = _step()["run"]
        assert "github.event.pull_request.base.sha" not in body
        assert '"$BASE_SHA"' in body

    def test_the_sha_is_declared_as_step_env(self):
        env = _step().get("env") or {}
        assert "BASE_SHA" in env, env
        assert re.search(
            r"github\.event\.pull_request\.base\.sha", str(env["BASE_SHA"])
        )
