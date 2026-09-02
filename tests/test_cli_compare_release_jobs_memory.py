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

    def test_divides_available_by_budget(self, monkeypatch) -> None:
        monkeypatch.delenv("ABICHECK_RELEASE_JOB_MEM_GIB", raising=False)
        monkeypatch.setattr(process_resources, "available_mem_gib", lambda: 6.0)
        assert release_pairwise._release_jobs_mem_cap() == 6

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
        monkeypatch.setattr(release_pairwise, "_release_jobs_mem_cap", lambda: 2)
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
        monkeypatch.setattr(release_pairwise, "_release_jobs_mem_cap", lambda: 1)
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
