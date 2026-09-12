# Copyright 2026 Nikolay Petrov
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

"""`cancel-in-progress` must actually be able to cancel something.

Bug class: a concurrency group keyed off a value that is *unique per run*
makes `cancel-in-progress: true` a no-op — every run lands in its own
group, so a superseded run is never cancelled and instead occupies a runner
(or a queue slot) until it finishes on its own. Every workflow here used
`${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}`,
which is correct for a pull request and silently inert for a `push`: a push
event carries no `pull_request.number`, so it fell through to `run_id`,
which differs for every single push.

The test is not a string comparison against the expression this repository
currently writes — that would pass for any future spelling that reintroduces
the same defect with different text. It evaluates each workflow's real group
expression against synthesized event contexts and asserts the behavioural
property directly: two successive events for the *same* logical unit (the
same pull request, or the same branch ref) must share a group, and two
*different* units must not. Every workflow in the tree that opts into
cancellation is checked, so a new workflow copying the old spelling fails
here rather than at the next congested merge queue.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from _gha_expressions import render
from _workflow_files import read_repo_text, workflow_paths

# Events whose runs can legitimately supersede one another. A schedule or a
# workflow_dispatch run is deliberately allowed (and expected) to key off
# run_id: two manual runs are two distinct requests, not an update of one.
_SUPERSEDABLE = ("pull_request", "push")


def _context(
    event: str,
    *,
    pr: int | None = None,
    ref: str = "refs/heads/main",
    run_id: str = "1",
) -> dict[str, Any]:
    return {
        "github.workflow": "W",
        "github.event_name": event,
        "github.ref": ref,
        "github.ref_name": ref.rsplit("/", 1)[-1],
        "github.run_id": run_id,
        "github.head_ref": f"pr-{pr}" if pr is not None else "",
        "github.event.pull_request.number": str(pr) if pr is not None else "",
        "github.event.number": str(pr) if pr is not None else "",
        "github.sha": f"sha-{run_id}",
    }


def _render(group: str, ctx: dict[str, Any]) -> str:
    """Render a concurrency-group expression.

    A thin alias for the shared renderer: the expression semantics live in
    `_gha_expressions` so this module and
    `test_workflow_coverage_consumers.py` cannot drift apart on what `||`,
    `&&` or an unmodelled context field mean. They were a private copy here
    until the second guard needed them.
    """
    return render(group, ctx)


def _cancelling_workflows() -> list[tuple[str, str, list[str]]]:
    found = []
    for path in workflow_paths():
        doc = yaml.safe_load(read_repo_text(path))
        concurrency = (doc or {}).get("concurrency")
        if not isinstance(concurrency, dict):
            continue
        if concurrency.get("cancel-in-progress") is not True:
            continue
        # PyYAML parses the bare `on:` key as the boolean True.
        triggers = doc.get("on", doc.get(True)) or {}
        events = list(triggers) if isinstance(triggers, dict) else [triggers]
        relevant = [e for e in _SUPERSEDABLE if e in events]
        if relevant:
            found.append((path.name, str(concurrency["group"]), relevant))
    return found


_CASES = _cancelling_workflows()


def test_the_survey_found_workflows_to_check() -> None:
    """Guards the test itself: a parsing change that silently matched
    nothing would make every assertion below vacuous."""
    assert len(_CASES) >= 5, [c[0] for c in _CASES]


@pytest.mark.parametrize("name,group,events", _CASES, ids=[c[0] for c in _CASES])
def test_superseding_events_share_a_concurrency_group(
    name: str, group: str, events: list[str]
) -> None:
    """The property `cancel-in-progress` depends on: an update to the same
    unit lands in the same group. Two runs differ in `run_id` and `sha`,
    which is exactly what the broken spelling keyed off."""
    for event in events:
        if event == "pull_request":
            a = _context(event, pr=42, ref="refs/pull/42/merge", run_id="100")
            b = _context(event, pr=42, ref="refs/pull/42/merge", run_id="200")
        else:
            a = _context(event, ref="refs/heads/main", run_id="100")
            b = _context(event, ref="refs/heads/main", run_id="200")
        assert _render(group, a) == _render(group, b), (
            f"{name}: two {event} runs of the same unit land in different "
            f"concurrency groups, so cancel-in-progress can never cancel "
            f"the superseded one (group expression: {group})"
        )


@pytest.mark.parametrize("name,group,events", _CASES, ids=[c[0] for c in _CASES])
def test_unrelated_units_do_not_share_a_concurrency_group(
    name: str, group: str, events: list[str]
) -> None:
    """The complement — without it, the literal group `"x"` would pass the
    test above while cancelling every unrelated run in the repository."""
    if "pull_request" in events:
        one = _render(group, _context("pull_request", pr=1, ref="refs/pull/1/merge"))
        two = _render(group, _context("pull_request", pr=2, ref="refs/pull/2/merge"))
        assert one != two, f"{name}: two different PRs would cancel each other"
    if "push" in events:
        main = _render(group, _context("push", ref="refs/heads/main"))
        other = _render(group, _context("push", ref="refs/heads/topic"))
        assert main != other, f"{name}: two different branches would cancel each other"
    if set(events) >= {"push", "pull_request"}:
        assert _render(group, _context("push", ref="refs/heads/main")) != _render(
            group, _context("pull_request", pr=7, ref="refs/pull/7/merge")
        ), f"{name}: a push run and a PR run would cancel each other"
