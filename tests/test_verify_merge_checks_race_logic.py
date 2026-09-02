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
`actions/github-script` poll/select/decide logic (Codex review, seven
rounds on this one step: https://github.com/abicheck/abicheck/pull/1009).

`tests/test_required_checks_governance.py`'s `TestVerifyMergeChecksWorkflow`
deliberately only checks the workflow's YAML text, trigger shape, and
required-name membership -- it says outright that it doesn't execute the
polling logic. That gap is exactly why several real bugs in that logic (a
required check judged against the wrong reference point, a rerun selected
by a `started_at` field that can't tell pre- from post-merge, a same-second
timestamp read as proof of "before", a clean snapshot accepted without
confirming it holds, a clean-streak counter that counted total attempts
instead of consecutive ones, and a consecutive-clean-streak requirement
fixed at a small constant that confirmed "clean" across far less time than
the poll budget it was supposed to span) went unnoticed by every other test
in this repository while each round's fix was itself only checked with a
throwaway, uncommitted smoke script -- leaving each fix's own regression
undetectable by anything that runs in CI. This file is that missing
coverage: it runs the *real* script text extracted from the workflow file
(never a hand-copied reimplementation, which could silently drift from what
actually ships) through `tests/verify_merge_checks_harness.mjs`, a Node
harness that mocks `actions/github-script`'s `context`/`core`/`github`
globals and a fake, script-advanced clock, against a scripted sequence of
`checks.listForRef` responses -- one per poll attempt.
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


class TestCleanResultMustBeConfirmed:
    def test_a_clean_snapshot_that_hides_an_unindexed_rerun_is_not_accepted_on_one_read(
        self, tmp_path: Path
    ) -> None:
        """The symmetric false-pass case to the stale-failure handling
        above: the first poll can look completely clean not because the
        check is done, but because a rerun queued before the merge hasn't
        been indexed by the API *at all* yet -- not even as a visible
        `queued` entry. A single clean read must not end the audit; once
        the rerun appears (still unresolved), the audit must eventually
        fail rather than having already reported success."""
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

    def test_clean_streak_counts_consecutive_polls_not_total_attempts(
        self, tmp_path: Path
    ) -> None:
        """Direct regression test for the counting bug itself: an earlier
        pending attempt must not count toward the confirmation streak a
        later clean read needs. One pending poll followed by only a
        single clean poll must not be enough to pass -- the clean result
        must hold across MIN_CLEAN_ATTEMPTS *consecutive* polls, counted
        from the first clean one, not from the start of the loop."""
        pending_first = {"id": 1, "status": "queued"}
        clean_once = {
            "id": 1,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2025-12-31T23:59:59Z",
            "started_at": "2025-12-31T23:59:30Z",
        }
        # Only ONE clean read after the pending one: if the loop wrongly
        # counted total attempts (attempt 2 >= MIN_CLEAN_ATTEMPTS), it
        # would exit here and report success after a single clean
        # observation. It must not -- the harness's poll sequence repeats
        # `clean_once` for every attempt beyond this, so the real fix
        # (a consecutive-clean-streak counter) needs several more clean
        # polls before passing, which this assertion captures via
        # `attempts`.
        result = _run_scenario(tmp_path, [[pending_first], [clean_once]])
        assert result["failedMessage"] is None
        # The fix requires more than 2 polls total (1 pending + at least 2
        # consecutive clean) before accepting the result -- exactly 2 would
        # mean the bug (counting total attempts) is back.
        assert result["attempts"] >= 3

    def test_clean_streak_must_span_the_full_poll_budget_not_a_fixed_small_count(
        self, tmp_path: Path
    ) -> None:
        """Direct regression test for the sixth Codex round: a fixed
        `MIN_CLEAN_ATTEMPTS = 2` only confirms a clean read holds across
        one `POLL_INTERVAL_MS` gap (~20s) before exiting, even though
        `MAX_WAIT_MS` reserves a full 3 minutes for a not-yet-indexed
        pre-merge rerun to appear. Two clean polls followed by a rerun
        appearing on the third must still be caught -- a fixed 2-attempt
        streak would have already declared victory after the second poll
        and never seen it."""
        old_pass_only = {
            "id": 100,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2025-12-31T23:55:00Z",
            "started_at": "2025-12-31T23:50:00Z",
        }
        rerun_appears = {"id": 105, "status": "queued"}
        result = _run_scenario(
            tmp_path,
            [[old_pass_only], [old_pass_only], [old_pass_only, rerun_appears]],
        )
        assert result["failedMessage"] is not None
        assert "run 105" in result["failedMessage"]
        # Confirming "clean" must take at least as many polls as the
        # rerun's own appearance -- the bug this guards against exited
        # after exactly 2 attempts, before ever polling a third time.
        assert result["attempts"] > 2

    def test_deadline_cannot_bypass_an_unconfirmed_clean_streak(
        self, tmp_path: Path
    ) -> None:
        """Direct regression test for the seventh Codex round: the global
        `deadline` check sits right after the clean-streak bookkeeping and,
        before this fix, broke out of the loop unconditionally once reached
        -- even when the *very first* clean read landed exactly at the
        deadline (because several earlier, genuinely-pending polls had
        already consumed the budget). `pending` was empty from that one
        unconfirmed read, so `problems` ended up empty and the audit
        reported success without ever confirming the clean state held for
        the required number of consecutive polls. Exactly enough pending
        polls to exhaust the poll budget, followed by one lone clean read
        landing on the deadline, must still be reported as unresolved."""
        script = _extract_script()
        poll_interval_ms, max_wait_ms = _poll_interval_and_max_wait_ms(script)
        # The number of pending polls needed so that the deadline is first
        # reached exactly when the *next* (clean) poll's own check runs --
        # see the module-level poll-loop timing this mirrors.
        pending_polls_to_exhaust_budget = -(-max_wait_ms // poll_interval_ms)

        stuck_rerun = {"id": 1, "status": "queued"}
        clean_run = {
            "id": 1,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2025-12-31T23:59:59Z",
            "started_at": "2025-12-31T23:59:00Z",
        }
        poll_sequence = [[stuck_rerun]] * pending_polls_to_exhaust_budget + [
            [clean_run]
        ]
        result = _run_scenario(tmp_path, poll_sequence)

        assert result["failedMessage"] is not None
        assert "poll budget expired" in result["failedMessage"]
        assert result["attempts"] == pending_polls_to_exhaust_budget + 1
