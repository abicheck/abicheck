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

"""Tests for :mod:`abicheck.artifact_plan` — Phase 1 (Milestone A) of the
duplication-and-convergence plan's ``ResolvedArtifactPlan`` primitive."""

from __future__ import annotations

import pytest

from abicheck.artifact_plan import ResolvedArtifactPlan


def test_normal_exit_runs_cleanups_once():
    calls: list[str] = []
    with ResolvedArtifactPlan() as plan:
        plan.pending_cleanups.append(lambda: calls.append("a"))
        plan.pending_cleanups.append(lambda: calls.append("b"))
    assert calls == ["a", "b"]


def test_exception_during_with_body_still_runs_cleanups():
    calls: list[str] = []
    with pytest.raises(ValueError, match="boom"):
        with ResolvedArtifactPlan() as plan:
            plan.pending_cleanups.append(lambda: calls.append("a"))
            raise ValueError("boom")
    assert calls == ["a"]


def test_cleanup_registered_after_construction_via_add_cleanup_still_runs():
    calls: list[str] = []
    plan = ResolvedArtifactPlan()
    plan.pending_cleanups.append(lambda: calls.append("seeded"))
    # Discovered only once "execution" (not just "resolution") has run --
    # the exact case add_cleanup exists for.
    plan.add_cleanup(lambda: calls.append("discovered-later"))
    plan.run_cleanups()
    assert calls == ["seeded", "discovered-later"]


def test_idempotent_double_exit_does_not_double_run():
    calls: list[str] = []
    plan = ResolvedArtifactPlan()
    plan.pending_cleanups.append(lambda: calls.append("a"))
    plan.run_cleanups()
    plan.run_cleanups()  # explicit second call
    with plan:  # __enter__/__exit__ on an already-closed plan
        pass
    assert calls == ["a"]


def test_partial_allocation_failure_runs_what_was_collected_so_far():
    """Mirrors the plan's own named failure mode: an allocating body that
    raises partway through must still drain every cleanup it had already
    registered before the raise, even though the caller never gets an
    object back to call __exit__ on itself -- the *body* is responsible
    for calling run_cleanups() in its own except/finally when it cannot
    rely on a `with` block around itself (e.g. it isn't the one that
    constructed the plan)."""
    calls: list[str] = []
    plan = ResolvedArtifactPlan()

    def _allocate_then_fail() -> None:
        plan.pending_cleanups.append(lambda: calls.append("first"))
        plan.pending_cleanups.append(lambda: calls.append("second"))
        raise RuntimeError("partial failure")

    with pytest.raises(RuntimeError, match="partial failure"):
        try:
            _allocate_then_fail()
        except RuntimeError:
            plan.run_cleanups()
            raise

    assert calls == ["first", "second"]


def test_one_failing_cleanup_does_not_skip_the_rest():
    calls: list[str] = []

    def _bad_cleanup() -> None:
        raise OSError("cleanup itself failed")

    with ResolvedArtifactPlan() as plan:
        plan.pending_cleanups.append(lambda: calls.append("before"))
        plan.add_cleanup(_bad_cleanup)
        plan.add_cleanup(lambda: calls.append("after"))
    assert calls == ["before", "after"]


def test_no_cleanups_registered_is_a_no_op():
    with ResolvedArtifactPlan() as plan:
        assert plan.pending_cleanups == []
    # __exit__ must not raise even though nothing was ever registered.


def test_enter_returns_self():
    plan = ResolvedArtifactPlan()
    with plan as entered:
        assert entered is plan


def test_cleanup_added_via_add_cleanup_after_drain_still_runs() -> None:
    """Codex review: an earlier revision tracked closure as a single bool, so
    every drain after the first was an unconditional no-op -- a cleanup
    registered via add_cleanup() on an already-drained plan was silently
    never run. The next run_cleanups() call (here, a second `with` re-entry)
    must still pick it up."""
    calls: list[str] = []
    plan = ResolvedArtifactPlan()
    plan.add_cleanup(lambda: calls.append("first"))
    plan.run_cleanups()
    assert calls == ["first"]

    plan.add_cleanup(lambda: calls.append("late"))
    with plan:
        pass
    assert calls == ["first", "late"]


def test_cleanup_appended_to_pending_cleanups_after_drain_still_runs() -> None:
    """Same invariant as above, via the other registration path (direct
    `pending_cleanups.append(...)` rather than `add_cleanup()`) -- Codex
    review asked for both to be covered."""
    calls: list[str] = []
    plan = ResolvedArtifactPlan()
    plan.pending_cleanups.append(lambda: calls.append("first"))
    plan.run_cleanups()
    assert calls == ["first"]

    plan.pending_cleanups.append(lambda: calls.append("late"))
    plan.run_cleanups()
    assert calls == ["first", "late"]


def test_run_cleanups_with_nothing_new_after_a_drain_is_a_true_no_op() -> None:
    """The idempotent case must still hold: calling run_cleanups() again with
    no new registrations re-runs nothing."""
    calls: list[str] = []
    plan = ResolvedArtifactPlan()
    plan.add_cleanup(lambda: calls.append("a"))
    plan.run_cleanups()
    plan.run_cleanups()
    plan.run_cleanups()
    assert calls == ["a"]
