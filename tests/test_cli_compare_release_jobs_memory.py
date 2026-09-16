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

"""R3 (CLI-audit): the release fan-out's ``--jobs 0`` auto default sized
purely off ``os.cpu_count()`` -- on a very-high-core-count host (a real
224-core CI runner measured 56.5 GB RSS) or a cpu-count-vs-memory-mismatched
container, that can wildly oversubscribe available RAM. The auto default
must now also clamp to a memory-derived worker cap
(:func:`abicheck.cli_compare_release_pairwise._release_jobs_mem_cap`,
mirroring ``buildsource/source_replay.py``'s identical L4 pattern) -- an
*explicit* ``--jobs N`` is never clamped.
"""

from __future__ import annotations

import abicheck.process_resources as process_resources
from abicheck import cli_compare_release_pairwise as release_pairwise
from abicheck.workflows import release_jobs


class TestReleaseJobMemBudget:
    def test_default_budget(self, monkeypatch) -> None:
        monkeypatch.delenv("ABICHECK_RELEASE_JOB_MEM_GIB", raising=False)
        assert release_pairwise._release_job_mem_budget_gib() == 1.0

    def test_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("ABICHECK_RELEASE_JOB_MEM_GIB", "2.5")
        assert release_pairwise._release_job_mem_budget_gib() == 2.5

    def test_invalid_env_falls_back_to_default(self, monkeypatch) -> None:
        monkeypatch.setenv("ABICHECK_RELEASE_JOB_MEM_GIB", "not-a-number")
        assert release_pairwise._release_job_mem_budget_gib() == 1.0


class TestReleaseJobsMemCap:
    def test_none_when_ram_unreadable(self, monkeypatch) -> None:
        monkeypatch.setattr(process_resources, "available_mem_gib", lambda: None)
        assert release_pairwise._release_jobs_mem_cap() is None

    def test_admits_against_committable_memory_not_all_of_it(self, monkeypatch) -> None:
        """The cap divides *committable* RAM by the budget, not all of it.

        This is the contract that replaced a plain ``available / budget``:
        at 6.0 GiB available and a 1.0 GiB budget the old answer was 6 --
        a 100% commitment, with nothing left for the parent-retained and
        shared-context state resident in this same process.
        """
        monkeypatch.delenv("ABICHECK_RELEASE_JOB_MEM_GIB", raising=False)
        monkeypatch.delenv("ABICHECK_RELEASE_MEM_UTILIZATION", raising=False)
        monkeypatch.delenv("ABICHECK_RELEASE_MEM_RESERVE_GIB", raising=False)
        monkeypatch.setattr(process_resources, "available_mem_gib", lambda: 6.0)
        # (6.0 * 0.85 - 1.0) / 1.0 -> 4.1 -> 4
        assert release_pairwise._release_jobs_mem_cap() == 4

    def test_floors_at_one_worker(self, monkeypatch) -> None:
        monkeypatch.setenv("ABICHECK_RELEASE_JOB_MEM_GIB", "10")
        monkeypatch.setattr(process_resources, "available_mem_gib", lambda: 0.5)
        assert release_pairwise._release_jobs_mem_cap() == 1


class TestCompareReleaseLibrariesMemoryClamp:
    """End-to-end proof against the real ``_compare_release_libraries``
    entry point, not just the cap primitives above."""

    def _common_kwargs(self, matched_keys, old_map, new_map):
        return dict(
            matched_keys=matched_keys,
            old_map=old_map,
            new_map=new_map,
            old_debug_dir=None,
            new_debug_dir=None,
            resolve_debug_info=lambda *a, **k: None,
            old_h=[],
            new_h=[],
            old_inc=[],
            new_inc=[],
            old_version="1.0",
            new_version="1.0",
            lang="c++",
            suppress=None,
            policy="strict_abi",
            policy_file_path=None,
            output_dir=None,
        )

    def test_auto_jobs_are_reduced_to_fit_memory(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr("os.cpu_count", lambda: 64)
        # The memory probe itself, not the wrapper: worker sizing moved to
        # `workflows.release_jobs.resolve_release_worker_count`, so patching
        # a CLI-side wrapper the code no longer calls would leave this test
        # asserting against the real host's memory.
        monkeypatch.setattr(
            release_jobs, "release_jobs_mem_cap", lambda depth=None, **kw: 2
        )
        captured_jobs: list[int] = []

        def _fake_sequential(matched_keys, common_args):
            return []

        def _fake_parallel(matched_keys, common_args, old_map, max_workers):
            captured_jobs.append(max_workers)
            return []

        monkeypatch.setattr(
            release_pairwise, "_compare_release_sequential", _fake_sequential
        )
        monkeypatch.setattr(
            release_pairwise, "_compare_release_parallel", _fake_parallel
        )

        release_pairwise._compare_release_libraries(
            **self._common_kwargs(
                ["a", "b"], {"a": None, "b": None}, {"a": None, "b": None}
            ),
            jobs=0,
        )
        assert captured_jobs == [2]
        assert "reduced 64 -> 2" in capsys.readouterr().err

    def test_explicit_jobs_are_never_clamped(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(
            release_jobs, "release_jobs_mem_cap", lambda depth=None, **kw: 1
        )
        captured_jobs: list[int] = []

        def _fake_parallel(matched_keys, common_args, old_map, max_workers):
            captured_jobs.append(max_workers)
            return []

        monkeypatch.setattr(
            release_pairwise, "_compare_release_parallel", _fake_parallel
        )

        release_pairwise._compare_release_libraries(
            **self._common_kwargs(
                ["a", "b"], {"a": None, "b": None}, {"a": None, "b": None}
            ),
            jobs=8,
        )
        assert captured_jobs == [8]
        assert "reduced" not in capsys.readouterr().err


class TestReleaseAdmissionReserve:
    """The reserve's own invariants, stated independently of any one host.

    These are deliberately *not* assertions against the shipped constants'
    arithmetic -- a test that recomputes ``avail * utilization - reserve``
    and compares it to the implementation compares the module with itself
    and passes against any utilization, including 1.0 (which is the absent
    reserve this change exists to remove). Each of these instead states a
    property the admission must hold for *every* input, and is checked over
    a swept domain with an explicit non-vacuity guard.
    """

    _AVAILS = (0.25, 0.5, 1.0, 2.0, 6.0, 13.17, 32.0, 64.0, 256.0, 1024.0)
    _DEPTHS = (None, "binary", "headers", "build", "source")

    @staticmethod
    def _plain_cap(avail: float, budget: float) -> int:
        """The pre-change rule, written out here rather than imported.

        An independent second derivation, per AGENTS.md's matrix-test rule:
        importing the module's own helper would make every comparison below
        a tautology.
        """
        return max(1, int(avail / budget))

    def test_never_admits_more_than_the_unreserved_rule_did(self, monkeypatch) -> None:
        """Conservative in one direction only -- this is the safety property.

        A reserve that could *raise* the admission on some input would be a
        memory regression hiding inside a memory fix.
        """
        monkeypatch.delenv("ABICHECK_RELEASE_JOB_MEM_GIB", raising=False)
        monkeypatch.delenv("ABICHECK_RELEASE_MEM_UTILIZATION", raising=False)
        monkeypatch.delenv("ABICHECK_RELEASE_MEM_RESERVE_GIB", raising=False)
        strictly_lower = 0
        violations = []
        for avail in self._AVAILS:
            monkeypatch.setattr(
                process_resources, "available_mem_gib", lambda a=avail: a
            )
            for depth in self._DEPTHS:
                budget = release_jobs.release_job_mem_budget_gib(depth)
                got = release_jobs.release_jobs_mem_cap(depth)
                plain = self._plain_cap(avail, budget)
                if got > plain:
                    violations.append((avail, depth, got, plain))
                if got < plain:
                    strictly_lower += 1
        assert not violations, f"admission rose above the unreserved rule: {violations}"
        # Non-vacuity: the sweep must actually contain cases the reserve
        # changed, or the assertion above is true for a no-op.
        assert strictly_lower > 0

    def test_always_admits_at_least_one_worker(self, monkeypatch) -> None:
        """A host too small for one budget runs one member, never zero.

        Zero workers would turn an under-provisioned host into a run that
        silently compares nothing -- which ADR-065 treats as never a clean
        pass, and which a cap is not allowed to manufacture.
        """
        monkeypatch.delenv("ABICHECK_RELEASE_MEM_UTILIZATION", raising=False)
        monkeypatch.setenv("ABICHECK_RELEASE_JOB_MEM_GIB", "64")
        for avail in (0.0, 0.01, 0.25, 1.0):
            monkeypatch.setattr(
                process_resources, "available_mem_gib", lambda a=avail: a
            )
            assert release_jobs.release_jobs_mem_cap("headers") == 1

    def test_still_skips_the_clamp_when_ram_is_unreadable(self, monkeypatch) -> None:
        """Negative control: the reserve must not turn an unprobed host into
        a clamped one. ``None`` means "skip the clamp", not "assume nothing
        is available"."""
        monkeypatch.setattr(process_resources, "available_mem_gib", lambda: None)
        for depth in self._DEPTHS:
            assert release_jobs.release_jobs_mem_cap(depth) is None

    def test_admission_is_monotonic_in_available_memory(self, monkeypatch) -> None:
        """More memory never admits fewer workers."""
        monkeypatch.delenv("ABICHECK_RELEASE_JOB_MEM_GIB", raising=False)
        monkeypatch.delenv("ABICHECK_RELEASE_MEM_UTILIZATION", raising=False)
        monkeypatch.delenv("ABICHECK_RELEASE_MEM_RESERVE_GIB", raising=False)
        for depth in self._DEPTHS:
            caps = []
            for avail in self._AVAILS:
                monkeypatch.setattr(
                    process_resources, "available_mem_gib", lambda a=avail: a
                )
                caps.append(release_jobs.release_jobs_mem_cap(depth))
            assert caps == sorted(caps), f"{depth}: non-monotonic {caps}"
            # Non-vacuity: a constant sequence would also be sorted.
            assert caps[-1] > caps[0]

    def test_scales_up_rather_than_pinning_one_worker(self, monkeypatch) -> None:
        """A large host is not clamped to a single worker.

        The failure mode this guards against is a reserve large enough to
        act as a blanket single-worker policy regardless of host size.
        """
        monkeypatch.delenv("ABICHECK_RELEASE_JOB_MEM_GIB", raising=False)
        monkeypatch.delenv("ABICHECK_RELEASE_MEM_UTILIZATION", raising=False)
        monkeypatch.delenv("ABICHECK_RELEASE_MEM_RESERVE_GIB", raising=False)
        monkeypatch.setattr(process_resources, "available_mem_gib", lambda: 32.0)
        # A six-member bundle at header depth still fans out fully on a
        # host that can actually hold it.
        assert release_jobs.release_jobs_mem_cap("headers") >= 6

    def test_binary_depth_admission_is_unchanged_on_a_cpu_bound_host(
        self, monkeypatch
    ) -> None:
        """The common path keeps its worker count.

        At the binary-depth budget the reserve is far below the CPU-derived
        default on any host that was not already memory-clamped, so an
        ordinary ``compare OLD_DIR NEW_DIR`` is sized exactly as before.
        """
        monkeypatch.delenv("ABICHECK_RELEASE_JOB_MEM_GIB", raising=False)
        monkeypatch.delenv("ABICHECK_RELEASE_MEM_UTILIZATION", raising=False)
        monkeypatch.delenv("ABICHECK_RELEASE_MEM_RESERVE_GIB", raising=False)
        monkeypatch.setattr(process_resources, "available_mem_gib", lambda: 13.17)
        import os as _os

        monkeypatch.setattr(_os, "cpu_count", lambda: 4)
        effective, clamped_from, _ = release_jobs.resolve_release_worker_count(
            0, depth="binary"
        )
        assert (effective, clamped_from) == (4, None)

    def test_explicit_overrides_are_honoured_and_bounded(self, monkeypatch) -> None:
        """Both knobs move the answer, and a nonsense value falls back."""
        monkeypatch.delenv("ABICHECK_RELEASE_JOB_MEM_GIB", raising=False)
        monkeypatch.setattr(process_resources, "available_mem_gib", lambda: 12.0)

        monkeypatch.setenv("ABICHECK_RELEASE_MEM_UTILIZATION", "1.0")
        monkeypatch.setenv("ABICHECK_RELEASE_MEM_RESERVE_GIB", "0")
        # Fully committing with no reserve reproduces the pre-change rule.
        assert release_jobs.release_jobs_mem_cap("headers") == self._plain_cap(
            12.0, 4.0
        )

        monkeypatch.setenv("ABICHECK_RELEASE_MEM_UTILIZATION", "0.5")
        assert release_jobs.release_jobs_mem_cap("headers") == 1

        # Out-of-range and unparsable both fall back to the default rather
        # than inverting the clamp.
        for bad in ("0", "-1", "1.5", "not-a-number"):
            monkeypatch.setenv("ABICHECK_RELEASE_MEM_UTILIZATION", bad)
            monkeypatch.setenv("ABICHECK_RELEASE_MEM_RESERVE_GIB", "1.0")
            assert release_jobs.release_jobs_mem_cap("headers") == 2

    def test_an_unparsable_reserve_falls_back_to_the_default(self, monkeypatch) -> None:
        """The reserve override has its own fallback, and its own test.

        The utilization case above cannot stand in for this one: they are
        two independent `try`/`except ValueError` bodies, and a sweep that
        only ever feeds a bad value to one of them leaves the other's
        fallback unexecuted. Codecov caught exactly that on the first push
        of this change -- the branch was written and never run, which is
        the state in which a fallback is indistinguishable from a crash.
        """
        monkeypatch.delenv("ABICHECK_RELEASE_JOB_MEM_GIB", raising=False)
        monkeypatch.delenv("ABICHECK_RELEASE_MEM_UTILIZATION", raising=False)
        # 19.0 GiB is chosen, not arbitrary: it is a probe value where the
        # default reserve and a zero reserve give *different* worker counts
        # (3 vs 4). At 12.0 GiB both truncate to 2, so a test written there
        # executes the fallback without checking what it returns -- verified
        # by mutating the fallback to `return 0.0`, which such a test passes.
        monkeypatch.setattr(process_resources, "available_mem_gib", lambda: 19.0)
        for bad in ("not-a-number", "", "   ", "1.0.0"):
            monkeypatch.setenv("ABICHECK_RELEASE_MEM_RESERVE_GIB", bad)
            # Falls back to the 1.0 GiB default: (19.0 * 0.85 - 1.0) / 4.0 -> 3,
            # where a dropped reserve would give 4.
            assert release_jobs.release_jobs_mem_cap("headers") == 3

    def test_a_negative_reserve_is_floored_rather_than_raising_admission(
        self, monkeypatch
    ) -> None:
        """A negative reserve would *add* committable memory.

        That is the one direction this whole change forbids, and the floor
        that prevents it needs its own assertion rather than being implied
        by the `max(0.0, ...)` in the source.
        """
        monkeypatch.delenv("ABICHECK_RELEASE_JOB_MEM_GIB", raising=False)
        monkeypatch.delenv("ABICHECK_RELEASE_MEM_UTILIZATION", raising=False)
        monkeypatch.setattr(process_resources, "available_mem_gib", lambda: 19.0)

        monkeypatch.setenv("ABICHECK_RELEASE_MEM_RESERVE_GIB", "-100")
        floored = release_jobs.release_jobs_mem_cap("headers")
        monkeypatch.setenv("ABICHECK_RELEASE_MEM_RESERVE_GIB", "0")
        no_reserve = release_jobs.release_jobs_mem_cap("headers")
        assert floored == no_reserve
        # And still no more than the rule this replaces, which is the point.
        assert floored <= self._plain_cap(19.0, 4.0)
