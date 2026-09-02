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

"""Executable regression tests for `verify-merge-checks.yml`'s embedded
`actions/github-script` poll/select/decide logic (Codex review, eight
rounds on this one step: https://github.com/abicheck/abicheck/pull/1009).

`tests/test_required_checks_governance.py`'s `TestVerifyMergeChecksWorkflow`
deliberately only checks the workflow's YAML text, trigger shape, and
required-name membership -- it says outright that it doesn't execute the
polling logic. That gap is exactly why several real bugs in that logic (a
required check judged against the wrong reference point, a rerun selected
by a `started_at` field that can't tell pre- from post-merge, a same-second
timestamp read as proof of "before", a clean snapshot accepted without
confirming it holds) went unnoticed by every other test in this repository
while each round's fix was itself only checked with a throwaway,
uncommitted smoke script -- leaving each fix's own regression undetectable
by anything that runs in CI. This file is that missing coverage: it runs
the *real* script text extracted from the workflow file (never a
hand-copied reimplementation, which could silently drift from what
actually ships) through `tests/verify_merge_checks_harness.mjs`, a Node
harness that mocks `actions/github-script`'s `context`/`core`/`github`
globals and a fake, script-advanced clock, against a scripted sequence of
`checks.listForRef` responses -- one per poll attempt.

Rounds six through eight all found bugs in the same mechanism: an
early-exit "clean streak" heuristic that tried to declare success as soon
as a clean read had been observed enough consecutive times to plausibly
rule out a not-yet-indexed pre-merge rerun. Each fix closed one gap in that
heuristic and opened an adjacent one (a streak length that confirmed
"clean" across far less wall-clock time than the poll budget it was meant
to span; a global deadline that could still cut the streak's confirmation
short; a corrected streak length that counted *observations* instead of
the *elapsed span* between the first and last, undercounting by one poll)
because the streak count was approximating "has the full poll budget
elapsed since this looked clean" without actually measuring elapsed time
against the budget -- and once patched to close the observation/span gap,
it left no budget left over for the very case the workflow exists to
handle (a check still `queued`/`in_progress` at push time that completes
moments later). The design was replaced rather than patched a fourth time:
the loop no longer exits early on a clean read at all, only on a decisive
failure or the full `MAX_WAIT_MS` budget elapsing -- see the workflow's own
inline comment on the loop for the reasoning. `TestNoEarlyExitOnClean`
below is the regression coverage for that redesign.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "verify-merge-checks.yml"
HARNESS_PATH = Path(__file__).resolve().parent / "verify_merge_checks_harness.mjs"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is required to execute the workflow's actions/github-script step",
)

MERGE_SHA = "merge0000000000000000000000000000000000"
HEAD_SHA = "headsha00000000000000000000000000000000"
MERGED_AT = "2026-01-01T00:00:00Z"

# A run for a required check that isn't the one this scenario is testing:
# unambiguously good and pre-merge, so it never contributes a finding of
# its own -- every scenario below is about exactly one check's lineage.
_BASELINE_RUN = {
    "id": 0,
    "status": "completed",
    "conclusion": "success",
    "completed_at": "2025-12-31T23:59:00Z",
    "started_at": "2025-12-31T23:58:00Z",
}


def _extract_script() -> str:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["verify-merge-checks"]["steps"]
    (script_step,) = [s for s in steps if "script" in s.get("with", {})]
    return script_step["with"]["script"]


def _required_check_names(script: str) -> list[str]:
    """Reads the real `REQUIRED_CHECKS` array out of the script text itself
    (never hand-copied here) so this file can't silently drift out of sync
    with `tests/test_required_checks_governance.py`'s own copy or the
    workflow's actual list."""
    match = re.search(r"const REQUIRED_CHECKS = \[(.*?)\];", script, re.DOTALL)
    assert match, "could not find REQUIRED_CHECKS in verify-merge-checks.yml's script"
    return re.findall(r"'([^']+)'", match.group(1))


def _poll_interval_and_max_wait_ms(script: str) -> tuple[int, int]:
    """Reads `POLL_INTERVAL_MS`/`MAX_WAIT_MS` out of the script text itself,
    the same anti-drift reasoning as `_required_check_names` -- a test that
    hardcoded these constants would silently stop matching reality the next
    time either changes. `MAX_WAIT_MS`'s value is a small arithmetic
    expression (`3 * 60 * 1000`), not a bare literal, so it's evaluated
    (restricted to digits/whitespace/`*+-/` -- asserted before `eval` ever
    sees it) rather than hand-copied as a number."""
    interval_match = re.search(r"const POLL_INTERVAL_MS = ([0-9_]+);", script)
    assert interval_match, (
        "could not find POLL_INTERVAL_MS in verify-merge-checks.yml's script"
    )
    poll_interval_ms = int(interval_match.group(1).replace("_", ""))

    wait_match = re.search(r"const MAX_WAIT_MS = ([0-9\s*+\-/]+);", script)
    assert wait_match, "could not find MAX_WAIT_MS in verify-merge-checks.yml's script"
    expr = wait_match.group(1)
    assert re.fullmatch(r"[0-9\s*+\-/]+", expr)
    max_wait_ms = eval(expr, {"__builtins__": {}}, {})  # noqa: S307 -- digits/operators only, asserted above
    return poll_interval_ms, max_wait_ms


def _run_scenario(
    tmp_path: Path,
    poll_sequence: list[list[dict[str, Any]]],
    required_check_name: str = "ai-readiness",
) -> dict[str, Any]:
    """Runs the real embedded script against a scripted poll sequence.

    *poll_sequence* is one list of check-run dicts per poll attempt (the
    last entry repeats for any attempt beyond the sequence's length, so a
    scenario that must run out the poll budget need not enumerate every
    attempt). Each check-run dict may omit `name`; it defaults to
    *required_check_name* so callers don't have to repeat it.
    """
    script = _extract_script()
    script_path = tmp_path / "script.js"
    script_path.write_text(script, encoding="utf-8")

    other_names = [n for n in _required_check_names(script) if n != required_check_name]
    filled_sequence = [
        [{"name": required_check_name, **run} for run in attempt]
        + [{"name": name, **_BASELINE_RUN} for name in other_names]
        for attempt in poll_sequence
    ]

    scenario = {
        "scriptPath": str(script_path),
        "sha": MERGE_SHA,
        "prs": [
            {
                "number": 1234,
                "merged_at": MERGED_AT,
                "merge_commit_sha": MERGE_SHA,
                "base": {"ref": "main"},
                "head": {"sha": HEAD_SHA},
            }
        ],
        "pollSequence": filled_sequence,
    }
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(scenario), encoding="utf-8")

    proc = subprocess.run(
        ["node", str(HARNESS_PATH), str(scenario_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"harness failed (exit {proc.returncode}):\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    result = json.loads(proc.stdout)
    assert "error" not in result, f"script raised: {result.get('error')}"
    return result


class TestGenuinelyPreMergeChecksPass:
    def test_a_check_already_completed_before_merge_passes(
        self, tmp_path: Path
    ) -> None:
        """Baseline: one run, completed with success strictly before the
        merge, observed identically across the required confirmation
        polls. General invariant under test: a required check whose only
        evidence is unambiguously pre-merge and green passes."""
        run = {
            "id": 1,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2025-12-31T23:59:59Z",
            "started_at": "2025-12-31T23:59:00Z",
        }
        result = _run_scenario(tmp_path, [[run], [run]])
        assert result["failedMessage"] is None

    def test_a_check_still_queued_at_merge_time_then_completes_before_merge_passes(
        self, tmp_path: Path
    ) -> None:
        """The original real-world failure shape (PRs #991/#993/#997): a
        required check hasn't reached `completed` yet at the instant this
        workflow's `push` handler queries, purely because the workflow's
        own query raced ahead of the still-running required matrix. Once
        it does complete with a pre-merge `completed_at`, the audit must
        pass -- not treat the earlier `queued` observation as a permanent
        failure."""
        queued = {"id": 1, "status": "queued"}
        done = {
            "id": 1,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2025-12-31T23:59:59Z",
            "started_at": "2025-12-31T23:59:30Z",
        }
        result = _run_scenario(tmp_path, [[queued], [done], [done]])
        assert result["failedMessage"] is None

    def test_a_stale_snapshot_showing_only_an_old_failure_is_superseded_by_a_later_good_rerun(
        self, tmp_path: Path
    ) -> None:
        """A required check was rerun and fixed *before* the merge, but
        this workflow's first `checks.listForRef` poll only shows the
        earlier, already-superseded failed attempt (ordinary API
        read-after-write lag). The audit must not report a hard failure on
        the strength of a run a later, higher-id rerun has already
        superseded -- it must keep polling until the newer attempt is
        visible."""
        old_failed = {
            "id": 90,
            "status": "completed",
            "conclusion": "failure",
            "completed_at": "2025-12-31T23:55:00Z",
            "started_at": "2025-12-31T23:50:00Z",
        }
        new_good = {
            "id": 95,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2025-12-31T23:58:00Z",
            "started_at": "2025-12-31T23:56:00Z",
        }
        result = _run_scenario(tmp_path, [[old_failed], [old_failed, new_good]])
        assert result["failedMessage"] is None


class TestPostMergeCompletionAlwaysFails:
    def test_a_check_that_completes_after_the_merge_fails_even_with_a_good_conclusion(
        self, tmp_path: Path
    ) -> None:
        """Negative control for the whole audit: a required check that is
        demonstrably still running when the merge happens -- here, one
        that only finishes a second after it -- must be reported as a
        real finding regardless of its eventual conclusion. This is the
        exact #782-style gap the workflow exists to catch; a regression
        here would make the entire audit inert."""
        run = {
            "id": 1,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2026-01-01T00:00:01Z",
            "started_at": "2025-12-31T23:59:00Z",
        }
        result = _run_scenario(tmp_path, [[run]])
        assert result["failedMessage"] is not None
        assert "after merge time" in result["failedMessage"]

    def test_a_same_second_completion_is_not_proven_pre_merge_and_fails(
        self, tmp_path: Path
    ) -> None:
        """GitHub's REST timestamps are whole-second strings: a check that
        genuinely finishes a fraction of a second after the merge can
        parse to the exact same value as `merged_at`. Equality doesn't
        prove the check finished first, so it must fail conservatively
        rather than pass on the benefit of the doubt (the asymmetric
        principle this audit is built on: a false failure here is
        acceptable, a false pass is not)."""
        run = {
            "id": 1,
            "status": "completed",
            "conclusion": "success",
            "completed_at": MERGED_AT,
            "started_at": "2025-12-31T23:59:00Z",
        }
        result = _run_scenario(tmp_path, [[run]])
        assert result["failedMessage"] is not None

    def test_a_genuinely_broken_check_that_never_recovers_eventually_fails(
        self, tmp_path: Path
    ) -> None:
        """The true negative case this audit exists for: a required check
        that failed before the merge and never gets a superseding rerun.
        Must not be treated as "maybe superseded, keep waiting forever" --
        it has to run out the poll budget and report the failure."""
        broken = {
            "id": 1,
            "status": "completed",
            "conclusion": "failure",
            "completed_at": "2025-12-31T23:55:00Z",
            "started_at": "2025-12-31T23:50:00Z",
        }
        result = _run_scenario(tmp_path, [[broken]])
        assert result["failedMessage"] is not None
        assert "conclusion=failure" in result["failedMessage"]


class TestRerunSelectionRanksByIdNotStartedAt:
    def test_a_rerun_queued_before_the_merge_that_never_finishes_is_not_masked_by_an_older_pass(
        self, tmp_path: Path
    ) -> None:
        """The concrete counter-example that sank the started_at-based
        exclusion entirely: an older run (id 100) already passed
        pre-merge, but a rerun (id 105, the check's true current attempt)
        was queued before the merge and never completes. Selecting by
        `id` must prefer the rerun over the older pass, and the audit
        must eventually fail rather than silently reporting the stale
        pass."""
        old_pass = {
            "id": 100,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2025-12-31T23:55:00Z",
            "started_at": "2025-12-31T23:50:00Z",
        }
        stuck_rerun = {"id": 105, "status": "queued"}
        result = _run_scenario(tmp_path, [[old_pass, stuck_rerun]])
        assert result["failedMessage"] is not None
        assert "status=queued on run 105" in result["failedMessage"]

    def test_a_rerun_queued_pre_merge_that_finishes_post_merge_still_fails(
        self, tmp_path: Path
    ) -> None:
        """A rerun genuinely queued before the merge can start executing
        *after* it (ordinary runner backlog) -- its own `started_at` then
        reads as post-merge, identical to a rerun that was both created
        and started after the merge. Selection must still pick it (by
        `id`, not `started_at`) over the older pass, and its own
        post-merge `completed_at` must fail the audit."""
        old_pass = {
            "id": 100,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2025-12-31T23:55:00Z",
            "started_at": "2025-12-31T23:50:00Z",
        }
        late_rerun = {
            "id": 105,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2026-01-01T00:00:30Z",
            "started_at": "2026-01-01T00:00:20Z",
        }
        result = _run_scenario(tmp_path, [[old_pass, late_rerun]])
        assert result["failedMessage"] is not None
        assert "run 105" in result["failedMessage"]


def _expected_full_budget_attempts(script: str) -> int:
    """The exact number of poll attempts the redesigned loop takes to reach
    `deadline` when nothing is ever decisively `failed`: one attempt at
    t=0, then a sleep-and-poll every `POLL_INTERVAL_MS` until elapsed time
    reaches `MAX_WAIT_MS`. Derived from the script's own constants (never
    hand-copied) so this can't silently drift from the real timing."""
    poll_interval_ms, max_wait_ms = _poll_interval_and_max_wait_ms(script)
    sleeps_to_exhaust_budget = -(-max_wait_ms // poll_interval_ms)  # ceil
    return sleeps_to_exhaust_budget + 1


class TestNoEarlyExitOnClean:
    """Regression coverage for the loop redesign that replaced the
    clean-streak heuristic (rounds six through eight) entirely: the loop
    never exits early on a clean read, only on a decisive failure or the
    full poll budget elapsing. This is a strictly simpler invariant than
    the streak-based one it replaced, and it structurally cannot reproduce
    any of that heuristic's three bugs, because there is no early-exit path
    left for a bug to hide in."""

    def test_a_genuinely_clean_merge_always_polls_the_full_budget_before_passing(
        self, tmp_path: Path
    ) -> None:
        """A required check that is clean from the very first poll must
        still pass -- but only once the loop has actually spent the full
        `MAX_WAIT_MS` budget confirming it, not merely observed it once or
        twice. This is the direct behavioral assertion that no early-exit
        heuristic remains: `attempts` must equal exactly the number needed
        to reach the deadline, not stop short of it."""
        script = _extract_script()
        clean_run = {
            "id": 1,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2025-12-31T23:55:00Z",
            "started_at": "2025-12-31T23:50:00Z",
        }
        result = _run_scenario(tmp_path, [[clean_run]])
        assert result["failedMessage"] is None
        assert result["attempts"] == _expected_full_budget_attempts(script)

    def test_a_rerun_that_appears_only_on_the_final_poll_before_deadline_still_fails(
        self, tmp_path: Path
    ) -> None:
        """The general form of rounds six through eight: however late in
        the poll budget a not-yet-indexed pre-merge rerun surfaces --
        including on the very last poll before the deadline -- it must
        still be caught. A streak-based heuristic could always be beaten
        by delaying the rerun's appearance past whatever streak length it
        required; polling to the full budget on every run closes that
        class of gap structurally rather than by tuning a constant."""
        script = _extract_script()
        expected_attempts = _expected_full_budget_attempts(script)
        old_pass_only = {
            "id": 100,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2025-12-31T23:55:00Z",
            "started_at": "2025-12-31T23:50:00Z",
        }
        rerun_appears = {"id": 105, "status": "queued"}
        poll_sequence = [[old_pass_only]] * (expected_attempts - 1) + [
            [old_pass_only, rerun_appears]
        ]
        result = _run_scenario(tmp_path, poll_sequence)
        assert result["failedMessage"] is not None
        assert "run 105" in result["failedMessage"]
        assert result["attempts"] == expected_attempts

    def test_a_check_that_resolves_cleanly_on_the_final_poll_before_deadline_passes(
        self, tmp_path: Path
    ) -> None:
        """The positive counterpart, and the original real-world shape
        (PRs #991/#993/#997) pushed to its limit: a required check that
        stays `queued` for almost the *entire* poll budget and only
        completes, cleanly, on the very last poll before the deadline must
        still pass. A streak-based heuristic requiring several consecutive
        clean reads could never accept this (there's only one clean read
        available before the deadline); polling to the full budget and
        checking only the final state accepts it correctly."""
        script = _extract_script()
        expected_attempts = _expected_full_budget_attempts(script)
        queued = {"id": 1, "status": "queued"}
        done = {
            "id": 1,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2025-12-31T23:59:59Z",
            "started_at": "2025-12-31T23:59:30Z",
        }
        poll_sequence = [[queued]] * (expected_attempts - 1) + [[done]]
        result = _run_scenario(tmp_path, poll_sequence)
        assert result["failedMessage"] is None
        assert result["attempts"] == expected_attempts

    def test_a_clean_snapshot_that_hides_an_unindexed_rerun_is_not_accepted_on_one_read(
        self, tmp_path: Path
    ) -> None:
        """The first poll can look completely clean not because the check
        is done, but because a rerun queued before the merge hasn't been
        indexed by the API *at all* yet -- not even as a visible `queued`
        entry. A single clean read must not end the audit; once the rerun
        appears (and stays unresolved for the rest of the budget), the
        audit must fail rather than having already reported success."""
        old_pass_only = {
            "id": 100,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2025-12-31T23:55:00Z",
            "started_at": "2025-12-31T23:50:00Z",
        }
        rerun_appears = {"id": 105, "status": "queued"}
        result = _run_scenario(
            tmp_path, [[old_pass_only], [old_pass_only, rerun_appears]]
        )
        assert result["failedMessage"] is not None
        assert "run 105" in result["failedMessage"]
