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

"""The memory benchmark must not be able to report a number it did not measure.

A benchmark's only product is trust in its numbers, so its failure modes are
not crashes -- they are *plausible receipts*. Three of them, each found by
review rather than by the harness failing:

* a receipt that reports success while containing no runs at all,
* a peak of ``0`` standing in for a figure that was never readable, which is
  the unavailable-is-not-zero rule ``abicheck.workflows.memory_trace``
  states for its own probes and which this harness was contradicting,
* a run that never compared anything (failed extraction, malformed report)
  recorded as a fast, small -- i.e. *improved* -- measurement.

The script is loaded by path because ``scripts/`` is not an importable
package; that is also why it must have no import-time side effects, which
the first test pins directly.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench_release_memory.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("_bench_harness", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bench = _load()


def _args(**overrides: Any) -> argparse.Namespace:
    base = {
        "tracemalloc": False,
        "label": "",
        "members": 6,
        "apis": 300,
        "records": 20,
        "cold": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "variant": "json",
        "exit_code": 4,
        "verdict": "BREAKING",
        "findings": 3,
        "members": 6,
        "seconds": 12.0,
        "parent_peak_rss_bytes": 1024,
        "tree_peak_rss_bytes": 2048,
        "tree_peak_pss_bytes": 1536,
        "cgroup_peak_bytes": 4096,
        "stderr_tail": "",
    }
    row.update(overrides)
    return row


class TestImportIsFree:
    def test_importing_the_script_does_not_touch_sys_path(self) -> None:
        """It is imported by these tests, so import must be a no-op.

        The original version mutated ``sys.path`` at import time, which
        changes process-wide import resolution for whoever imports it.
        """
        before = list(sys.path)
        _load()
        assert sys.path == before


class TestAReceiptAlwaysContainsMeasurements:
    def test_no_runs_is_refused_rather_than_summarised(self) -> None:
        """``--repeat 0`` used to write a cheerful, empty receipt."""
        with pytest.raises(SystemExit) as excinfo:
            bench._summarize(_args(), [])
        assert "no runs were measured" in str(excinfo.value)

    def test_one_run_summarises_normally(self) -> None:
        """The complement, so the refusal above is not vacuous."""
        summary = bench._summarize(_args(), [_row()])
        assert summary["by_variant"]["json"]["median_parent_peak_mib"] == pytest.approx(
            1024 / (1024 * 1024)
        )
        assert summary["by_variant"]["json"]["exit_codes"] == [4]

    def test_a_tracemalloc_run_publishes_no_timing(self) -> None:
        """Perturbed timing is withheld, not reported for comparison."""
        summary = bench._summarize(_args(tracemalloc=True), [_row()])
        assert summary["by_variant"]["json"]["median_seconds"] is None
        assert summary["by_variant"]["json"]["median_parent_peak_mib"] is not None


class TestUnavailableIsNotZero:
    """The rule `memory_trace` states for its probes, applied to the sampler."""

    def test_a_peak_never_read_stays_none(self) -> None:
        sampler = bench._Sampler(pid=1)
        assert sampler.parent_peak is None
        assert sampler.tree_rss_peak is None
        assert sampler.tree_pss_peak is None
        assert sampler.cgroup_peak is None

    @pytest.mark.parametrize(
        ("current", "reading", "expected"),
        [
            (None, None, None),
            (None, 5, 5),
            (5, None, 5),
            (5, 7, 7),
            (7, 5, 7),
            # 0 is a real reading, not an absent one -- the distinction the
            # whole fix rests on.
            (None, 0, 0),
            (0, None, 0),
        ],
    )
    def test_the_fold_keeps_absent_and_zero_apart(
        self, current: int | None, reading: int | None, expected: int | None
    ) -> None:
        assert bench._Sampler._peak(current, reading) == expected

    def test_an_unmeasured_run_is_refused(self) -> None:
        """A row with no memory in it is not a memory measurement."""
        with pytest.raises(SystemExit) as excinfo:
            bench._require_a_measured_comparison(_row(parent_peak_rss_bytes=None))
        assert "never read" in str(excinfo.value)

    def test_mib_renders_an_absent_peak_as_none(self) -> None:
        assert bench._mib(None) is None
        assert bench._mib(1024 * 1024) == 1.0


class TestARunMustHaveCompared:
    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            ({"exit_code": 0}, "expected exit 4"),
            ({"verdict": "COMPATIBLE"}, "not BREAKING"),
            ({"findings": 0}, "findings"),
            ({"findings": None}, "findings"),
        ],
    )
    def test_a_run_that_did_not_compare_is_refused(
        self, overrides: dict[str, Any], expected: str
    ) -> None:
        """A failed extraction is fast and small -- i.e. it looks like a win."""
        with pytest.raises(SystemExit) as excinfo:
            bench._require_a_measured_comparison(_row(**overrides))
        assert expected in str(excinfo.value)

    def test_a_real_breaking_run_is_accepted(self) -> None:
        """Non-vacuity: the guard must let the expected outcome through."""
        bench._require_a_measured_comparison(_row())


class TestFixtureIdentity:
    def test_a_fixture_built_with_other_parameters_is_not_reused(
        self, tmp_path: Path
    ) -> None:
        wanted = {"members": 2, "apis": 5, "records": 1}
        for side in ("old", "new"):
            (tmp_path / side / "lib").mkdir(parents=True)
            for i in range(2):
                (tmp_path / side / "lib" / f"libmember{i}.so").write_bytes(b"")
        (tmp_path / bench.FIXTURE_MANIFEST).write_text(
            json.dumps(wanted), encoding="utf-8"
        )
        assert bench._fixture_matches(tmp_path, wanted) is True
        assert bench._fixture_matches(tmp_path, {**wanted, "members": 3}) is False
        assert bench._fixture_matches(tmp_path, {**wanted, "apis": 6}) is False

    def test_an_incomplete_fixture_is_not_reused(self, tmp_path: Path) -> None:
        """A build interrupted part-way leaves a manifest and too few members."""
        wanted = {"members": 2, "apis": 5, "records": 1}
        for side in ("old", "new"):
            (tmp_path / side / "lib").mkdir(parents=True)
        (tmp_path / "old" / "lib" / "libmember0.so").write_bytes(b"")
        (tmp_path / bench.FIXTURE_MANIFEST).write_text(
            json.dumps(wanted), encoding="utf-8"
        )
        assert bench._fixture_matches(tmp_path, wanted) is False

    def test_a_missing_or_malformed_manifest_is_not_reused(
        self, tmp_path: Path
    ) -> None:
        wanted = {"members": 1, "apis": 5, "records": 1}
        for side in ("old", "new"):
            (tmp_path / side / "lib").mkdir(parents=True)
            (tmp_path / side / "lib" / "libmember0.so").write_bytes(b"")
        assert bench._fixture_matches(tmp_path, wanted) is False
        (tmp_path / bench.FIXTURE_MANIFEST).write_text("{not json", encoding="utf-8")
        assert bench._fixture_matches(tmp_path, wanted) is False
