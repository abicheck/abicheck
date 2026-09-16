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

"""The producing run's identity must survive ``actions/report``'s own boundary.

**Bug class:** ``action.declared_input_never_forwarded`` -- a composite
Action declares an input, documents it, and its script reads the matching
``INPUT_*`` variable, but no step's ``env:`` maps one to the other. Nothing
fails: the script's fallback quietly takes over. ``actions/report`` shipped
exactly that for ``source-run-id``/``source-run-attempt``, so a
``workflow_run`` publisher's sticky-comment ordering guard recorded the
*publisher's* run coordinates instead of the *producer's* -- inverting the
very comparison the guard exists to make (a re-run of an older commit is
triggered later, so its publisher carries the larger id).

Why these tests drive ``action.yml`` rather than setting ``INPUT_*``
themselves: a test that exports ``INPUT_SOURCE_RUN_ID`` has already crossed
the broken boundary by hand and passes whether or not the mapping exists.
The whole defect lives in the metadata -> environment hop, so
:mod:`tests._composite_action_env` computes the step environment from the
real, unmodified ``action.yml`` and this module runs ``run.sh`` with
*only* that. Reverting the two ``env:`` lines turns
:meth:`TestProducerIdentityReachesTheMarker.
test_producer_coordinates_win_over_the_publishers` red (verified by
mutation), and the sibling scan below turns the whole *class* into a
failure for any Action in this repository, not just this input pair.

The general invariant is stated three ways rather than once against the
reported input: the marker carries producer coordinates for arbitrary
generated id/attempt pairs; the ordering decision those coordinates feed is
exercised across an enumerated matrix of producer-vs-recorded orderings; and
every declared input of every first-party Action must be referenced by some
step.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from abicheck.frontends.action.report_publication import (
    ExistingComment,
    decide,
    find_existing,
    read_comments,
)
from tests._composite_action_env import (
    declared_inputs,
    forwarded_input_names,
    load_action,
    step_env,
)
from tests._workflow_exec import bash_executable, require_bash

REPO_DIR = Path(__file__).resolve().parent.parent
ACTIONS_DIR = REPO_DIR / "actions"
REPORT_ACTION = ACTIONS_DIR / "report"
RENDER_STEP = "Render and publish"

#: The publisher's *own* ambient coordinates. Deliberately unlike any
#: producer value used below, and larger than one of them, so a fallback
#: cannot coincidentally look correct.
PUBLISHER_RUN_ID = "7777777"
PUBLISHER_RUN_ATTEMPT = "5"

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="runs the Action's POSIX shell script directly"
)


def _report(tmp_path: Path) -> Path:
    path = tmp_path / "compare.json"
    path.write_text(
        json.dumps(
            {
                "library": "libthing.so",
                "old_version": "1.0",
                "new_version": "1.1",
                "verdict": "BREAKING",
                "changes": [
                    {
                        "kind": "func_removed",
                        "symbol": "thing_open",
                        "severity": "breaking",
                        "description": "Function removed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _publish(
    tmp_path: Path, *, with_inputs: dict[str, str], action_dir: Path = REPORT_ACTION
) -> dict[str, str]:
    """Run the Action's script with the environment ``action.yml`` computes.

    Returns the step's ``GITHUB_OUTPUT`` map. The ambient runner variables
    are set to the *publisher's* coordinates, which is what a
    ``workflow_run`` job really has and what the script falls back to.
    """
    require_bash()
    work = tmp_path / with_inputs.get("profile", "default")
    work.mkdir(parents=True, exist_ok=True)
    (work / "runner-temp").mkdir(exist_ok=True)
    outputs = work / "outputs.txt"
    outputs.touch()

    environment = dict(os.environ)
    for inherited in ("GH_TOKEN", "GITHUB_TOKEN", "GITHUB_REPOSITORY", "GITHUB_REF"):
        environment.pop(inherited, None)
    # Everything the Action itself contributes comes from action.yml. Only
    # the runner-provided variables are set here.
    environment.update(
        {
            "RUNNER_TEMP": str(work / "runner-temp"),
            "GITHUB_OUTPUT": str(outputs),
            "GITHUB_RUN_ID": PUBLISHER_RUN_ID,
            "GITHUB_RUN_ATTEMPT": PUBLISHER_RUN_ATTEMPT,
        }
    )
    environment.update(
        step_env(
            action_dir, RENDER_STEP, with_inputs={**with_inputs, "dry-run": "true"}
        )
    )
    result = subprocess.run(
        [bash_executable(), str(action_dir / "run.sh")],
        capture_output=True,
        text=True,
        env=environment,
        cwd=work,
    )
    assert result.returncode == 0, result.stderr
    parsed: dict[str, str] = {}
    for line in outputs.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            parsed[key] = value
    return parsed


def _marker_identity(outputs: dict[str, str]) -> ExistingComment:
    body = Path(outputs["body-path"]).read_text(encoding="utf-8")
    comments = read_comments(json.dumps({"id": 1, "body": body}))
    assert comments and comments[0].identity is not None, (
        "the rendered body carries no parseable sticky marker"
    )
    return comments[0]


class TestProducerIdentityReachesTheMarker:
    """metadata -> environment -> shell -> stored marker, end to end."""

    def test_producer_coordinates_win_over_the_publishers(self, tmp_path: Path) -> None:
        outputs = _publish(
            tmp_path,
            with_inputs={
                "report": str(_report(tmp_path)),
                "profile": "linux-gcc",
                "source-run-id": "9000001",
                "source-run-attempt": "3",
            },
        )
        recorded = _marker_identity(outputs).identity
        assert recorded is not None
        assert recorded.run_id == "9000001"
        assert recorded.run_attempt == 3
        body = Path(outputs["body-path"]).read_text(encoding="utf-8")
        assert PUBLISHER_RUN_ID not in body, (
            "the publisher's own run id reached the marker: the composite "
            "Action is not forwarding source-run-id"
        )

    @pytest.mark.parametrize(
        ("run_id", "attempt"),
        [("1", "1"), ("42", "2"), ("18446744073709551615", "9"), ("9000123", "4")],
        ids=["minimal", "small", "beyond-64-bit", "realistic"],
    )
    def test_arbitrary_producer_coordinates_are_carried_verbatim(
        self, tmp_path: Path, run_id: str, attempt: str
    ) -> None:
        """Not one reported pair: the mapping must be value-transparent.

        The oracle is the input itself, which is independent of any
        formatting the renderer applies -- a marker that re-derived either
        value would disagree for at least the out-of-range case.
        """
        outputs = _publish(
            tmp_path,
            with_inputs={
                "report": str(_report(tmp_path)),
                "profile": f"p{run_id}",
                "source-run-id": run_id,
                "source-run-attempt": attempt,
            },
        )
        recorded = _marker_identity(outputs).identity
        assert recorded is not None
        assert (recorded.run_id, recorded.run_attempt) == (run_id, int(attempt))

    def test_ambient_coordinates_remain_the_documented_fallback(
        self, tmp_path: Path
    ) -> None:
        """The same-run case: no producer inputs given, so the publisher's
        own coordinates are the producer's by construction."""
        outputs = _publish(
            tmp_path,
            with_inputs={"report": str(_report(tmp_path)), "profile": "same-run"},
        )
        recorded = _marker_identity(outputs).identity
        assert recorded is not None
        assert (recorded.run_id, recorded.run_attempt) == (
            PUBLISHER_RUN_ID,
            int(PUBLISHER_RUN_ATTEMPT),
        )

    def test_post_on_never_still_publishes_nothing(self, tmp_path: Path) -> None:
        """Forwarding producer identity must not disturb `post-on: never`."""
        outputs = _publish(
            tmp_path,
            with_inputs={
                "report": str(_report(tmp_path)),
                "profile": "quiet",
                "post-on": "never",
                "source-run-id": "9000001",
            },
        )
        assert outputs["posted"] == "false"
        assert outputs["skipped-reason"] == "never"


class TestOrderingUsesProducerCoordinates:
    """The consequence of the marker: which result a reviewer is left with.

    The existing comment here is a body this *same* Action boundary really
    rendered, not a hand-written marker -- so the ordering guard is exercised
    against the format the fix actually produces.
    """

    @pytest.mark.parametrize(
        ("recorded", "producer", "expect_stale"),
        [
            (("9000002", "1"), ("9000001", "1"), True),
            (("9000001", "1"), ("9000002", "1"), False),
            (("9000001", "2"), ("9000001", "1"), True),
            (("9000001", "1"), ("9000001", "2"), False),
            (("9000001", "1"), ("9000001", "1"), False),
        ],
        ids=[
            "older-producer-run-is-stale",
            "newer-producer-run-publishes",
            "older-attempt-is-stale",
            "newer-attempt-publishes",
            "same-coordinates-republish",
        ],
    )
    def test_ordering_follows_the_producer_not_the_publisher(
        self,
        tmp_path: Path,
        recorded: tuple[str, str],
        producer: tuple[str, str],
        expect_stale: bool,
    ) -> None:
        existing_outputs = _publish(
            tmp_path,
            with_inputs={
                "report": str(_report(tmp_path)),
                "profile": f"prev-{recorded[0]}-{recorded[1]}",
                "source-run-id": recorded[0],
                "source-run-attempt": recorded[1],
            },
        )
        existing = _marker_identity(existing_outputs)
        current_outputs = _publish(
            tmp_path,
            with_inputs={
                "report": str(_report(tmp_path)),
                "profile": f"cur-{producer[0]}-{producer[1]}",
                "source-run-id": producer[0],
                "source-run-attempt": producer[1],
            },
        )
        current = _marker_identity(current_outputs)
        assert current.identity is not None and existing.identity is not None
        # One identity key, two producer runs: what the sticky guard sees.
        prior = ExistingComment(
            comment_id=existing.comment_id,
            body=existing.body.replace(
                existing.identity.identity, current.identity.identity
            ),
        )
        assert find_existing([prior], current.identity) is prior
        plan = decide(
            _rendered_from(current_outputs),
            identity=current.identity,
            comments=[prior],
        )
        assert (plan.skipped_reason == "stale") is expect_stale, plan


def _rendered_from(outputs: dict[str, str]):
    """Re-render for :func:`decide` from the body the Action wrote."""
    from abicheck.frontends.action.report_publication import RenderedReport

    body = Path(outputs["body-path"]).read_text(encoding="utf-8")
    return RenderedReport(body=body, has_content=True, truncated=False)


class TestNoDeclaredInputIsUnforwarded:
    """The sibling scan: the bug *class*, over every first-party Action.

    An input a caller can set that no step ever references is either dead
    metadata or -- as here -- a silently broken wire. Either way the Action's
    documented contract does not match what it does.
    """

    @pytest.mark.parametrize(
        "action_dir",
        sorted(p for p in ACTIONS_DIR.iterdir() if (p / "action.yml").is_file()),
        ids=lambda p: p.name,
    )
    def test_every_declared_input_is_referenced_by_some_step(
        self, action_dir: Path
    ) -> None:
        action = load_action(action_dir)
        unforwarded = sorted(
            set(declared_inputs(action)) - forwarded_input_names(action)
        )
        assert unforwarded == [], (
            f"{action_dir.name}: declared but never referenced by any step: "
            f"{unforwarded}"
        )

    def test_the_scan_detects_a_removed_mapping(self) -> None:
        """Vacuity guard: the scan above passes on a correct tree, so prove
        it can fail. Removing the two `env:` lines under test is exactly the
        state this repository shipped."""
        action = load_action(REPORT_ACTION)
        step = next(s for s in action["runs"]["steps"] if s.get("name") == RENDER_STEP)
        for key in ("INPUT_SOURCE_RUN_ID", "INPUT_SOURCE_RUN_ATTEMPT"):
            assert key in step["env"], f"{key} is not mapped by {RENDER_STEP}"
            step["env"].pop(key)
        unforwarded = set(declared_inputs(action)) - forwarded_input_names(action)
        assert unforwarded == {"source-run-id", "source-run-attempt"}

    def test_the_environment_really_comes_from_the_metadata(self) -> None:
        """Second vacuity guard, for the runner harness rather than the scan:
        with the mapping gone, the computed step environment carries no
        producer coordinates at all -- which is what makes the end-to-end
        tests above fail rather than silently assert a hand-set value."""
        env = step_env(
            REPORT_ACTION,
            RENDER_STEP,
            with_inputs={"report": "r.json", "source-run-id": "9000001"},
        )
        assert env["INPUT_SOURCE_RUN_ID"] == "9000001"
        assert "INPUT_REPORT" in env and env["INPUT_REPORT"] == "r.json"
