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


class TestUsageErrorsFailBeforeTheFixtureIsBuilt:
    """A bad flag must not cost a fixture build first.

    `_summarize`'s refusal is the invariant backstop; this is the usage
    error, and the difference is minutes of compilation.
    """

    @pytest.mark.parametrize("repeat", [0, -1])
    def test_a_non_positive_repeat_is_rejected_at_parse_time(
        self, repeat: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built: list[Any] = []
        monkeypatch.setattr(bench, "_prepare_fixture", lambda a: built.append(a))
        with pytest.raises(SystemExit) as excinfo:
            bench.main(["--root", str(tmp_path), "--repeat", str(repeat)])
        # argparse's own usage-error exit, not a bare message.
        assert excinfo.value.code == 2
        assert built == [], "the fixture was built before the flag was rejected"

    def test_a_repeat_of_one_is_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-vacuity: the boundary value must pass the guard.

        Stopped immediately after the guard by a sentinel, so this asserts
        the parse-time check alone and runs no benchmark.
        """

        class _Stop(Exception):
            pass

        def _boom(_args: Any) -> None:
            raise _Stop

        monkeypatch.setattr(bench, "_prepare_fixture", _boom)
        with pytest.raises(_Stop):
            bench.main(["--root", str(tmp_path), "--repeat", "1"])


class TestAReceiptAlwaysContainsMeasurements:
    def test_no_runs_is_refused_rather_than_summarised(self) -> None:
        """The invariant backstop, independent of which flag emptied it."""
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


class TestTheVocabularyThresholdGuard:
    """A benchmark that claims to cross a threshold must prove it did.

    **Bug class.** A performance fixture whose whole purpose is to exercise
    a size-dependent path, with nothing checking that the path was reached.
    If the fixture drifts below the threshold -- a knob defaulted back, a
    header trimmed, a cheaper compiler inlining differently -- the benchmark
    keeps running, keeps reporting numbers, and quietly measures the happy
    path instead. It would then pass identically against the very defect it
    exists to catch, which is the same failure shape as a matrix test with
    no oracle.

    **General invariant**: the guard is a *decision* over the run's own
    reported figure, so it must move in both directions -- pass when the
    figure clears the requirement, fail when it does not, and refuse to
    answer at all when it has no figure to read. Asserted in all three
    directions plus the disabled case, rather than only the passing one; a
    guard that always passes and a guard that always fails each satisfy a
    single-direction test.

    The first version of this guard read the counters from the trace
    record's top level, but ``memory_trace.counts`` nests them under
    ``"counts"``. It therefore found nothing, reported 0 bytes and would
    have failed *every* run -- caught only by exercising the passing
    direction, which is why that direction is tested here too.
    """

    @staticmethod
    def _trace(tmp_path: Path, retained: int) -> Path:
        path = tmp_path / "trace.jsonl"
        path.write_text(
            json.dumps(
                {
                    "event": "release.spelling_cache",
                    "kind": "counts",
                    "counts": {
                        "match": {"hits": 90, "misses": 10, "bypasses": 0},
                        "patterns": {"retained_bytes": retained},
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def test_it_passes_when_the_threshold_is_cleared(self, tmp_path) -> None:
        bench._require_the_vocabulary_threshold_was_crossed(
            self._trace(tmp_path, 9_000_000), 8 * 1024 * 1024
        )

    def test_it_fails_when_the_threshold_is_not_cleared(self, tmp_path) -> None:
        with pytest.raises(SystemExit) as excinfo:
            bench._require_the_vocabulary_threshold_was_crossed(
                self._trace(tmp_path, 3_590_595), 8 * 1024 * 1024
            )
        message = str(excinfo.value)
        assert "3,590,595" in message, "the guard must report what it measured"
        assert "did NOT cross" in message

    def test_it_refuses_to_answer_without_a_trace(self) -> None:
        """No figure to read is not the same as a figure that passed."""
        with pytest.raises(SystemExit):
            bench._require_the_vocabulary_threshold_was_crossed(None, 1)

    def test_it_is_disabled_at_zero(self) -> None:
        """Opt-in: a run that makes no threshold claim needs no trace."""
        bench._require_the_vocabulary_threshold_was_crossed(None, 0)

    def test_a_trace_with_no_cache_counters_does_not_pass(self, tmp_path) -> None:
        """Absent counters must read as 'not proven', never as 'fine'.

        The exact shape the nesting bug produced: nothing found, so the
        figure is 0. That must fail a non-zero requirement rather than being
        treated as an unmeasured-and-therefore-acceptable run.
        """
        path = tmp_path / "trace.jsonl"
        path.write_text(
            json.dumps({"event": "release.member", "kind": "phase"}) + "\n",
            encoding="utf-8",
        )
        with pytest.raises(SystemExit):
            bench._require_the_vocabulary_threshold_was_crossed(path, 1)


class TestTheFixtureManifestCoversEveryShapeKnob:
    """``--keep`` must not reuse a tree built to a different shape.

    ``vocabulary_scale`` changes the compiled headers. If it were left out
    of the manifest, a ``--keep`` run would silently reuse a tree built at a
    different vocabulary size -- and then report a threshold-crossing
    measurement taken on a fixture that never crossed it, which is the
    guard above defeated by the layer beneath it.
    """

    def test_vocabulary_scale_is_part_of_the_reuse_key(self, tmp_path) -> None:
        for side in ("old", "new"):
            (tmp_path / side / "lib").mkdir(parents=True)
            (tmp_path / side / "lib" / "libmember0.so").write_bytes(b"")
        wanted = {
            "members": 1,
            "apis": 2,
            "records": 1,
            "vocabulary_scale": 400,
        }
        (tmp_path / bench.FIXTURE_MANIFEST).write_text(
            json.dumps(wanted), encoding="utf-8"
        )
        assert bench._fixture_matches(tmp_path, wanted) is True
        assert bench._fixture_matches(tmp_path, {**wanted, "vocabulary_scale": 0}) is (
            False
        ), "a differently-scaled vocabulary was accepted as a matching fixture"


class TestControlledEnvironmentIsAFunctionOfTheArguments:
    """A variant's environment may not depend on what the harness inherited.

    The bug class, not the one reported input: the receipt records the
    *arguments*, so any variable the harness controls that could instead come
    from the ambient environment makes the receipt describe a run that did not
    happen. Reported as an inherited ``ABICHECK_RELEASE_JOB_MEM_GIB`` silently
    driving a sweep's unconstrained arm (every worker count in the sweep then
    attributed to the wrong setting); the identical mechanism makes an
    ambient ``ABICHECK_CACHE_DIR`` turn a run labelled cold into a warm one,
    and an ambient trace path attach a tracing cost to an untraced timing.

    So the invariant is stated over *every* controlled variable and the whole
    small argument domain, not over the one variable and the one call that
    was reported, with the expectation derived independently of the
    implementation's own mapping.
    """

    #: Ambient values for every controlled variable, so each case starts
    #: already polluted -- an implementation that merely fails to *set* a
    #: variable would pass against an empty base.
    AMBIENT = {name: f"ambient-{name}" for name in bench.CONTROLLED_ENV_VARS}

    def _expected(
        self,
        *,
        env_extra: dict[str, str] | None,
        job_mem_gib: float | None,
        trace: Path | None,
        tracemalloc: bool,
        cache_dir: Path | None,
    ) -> dict[str, str | None]:
        """What each controlled variable must be, derived from the arguments.

        Deliberately a second statement of the rule rather than a call into
        the helper's own ``settings`` mapping: an oracle that folds through
        the code under test cannot catch that code choosing the wrong source.
        """
        stated: dict[str, str | None] = {name: None for name in self.AMBIENT}
        if job_mem_gib is not None:
            stated["ABICHECK_RELEASE_JOB_MEM_GIB"] = str(job_mem_gib)
        if cache_dir is not None:
            stated["ABICHECK_CACHE_DIR"] = str(cache_dir)
        if trace is not None:
            stated[bench.ENV_TRACE_PATH] = str(trace)
            if tracemalloc:
                stated[bench.ENV_TRACEMALLOC] = "1"
        for name, value in (env_extra or {}).items():
            if stated.get(name) is None:
                stated[name] = value
        return stated

    def _cases(self) -> list[dict[str, Any]]:
        cases: list[dict[str, Any]] = []
        for job_mem_gib in (None, 2.0):
            for cache_dir in (None, Path("/tmp/cold")):
                for trace in (None, Path("/tmp/t.jsonl")):
                    for tracemalloc in (False, True):
                        for env_extra in (
                            None,
                            {},
                            {"ABICHECK_RELEASE_JOB_MEM_GIB": "7"},
                            {"ABICHECK_CACHE_DIR": "/stated"},
                            {bench.ENV_TRACE_PATH: "/stated.jsonl"},
                            {"UNRELATED_TO_THIS_HARNESS": "kept"},
                        ):
                            cases.append(
                                {
                                    "job_mem_gib": job_mem_gib,
                                    "cache_dir": cache_dir,
                                    "trace": trace,
                                    "tracemalloc": tracemalloc,
                                    "env_extra": env_extra,
                                }
                            )
        return cases

    def test_the_domain_is_not_vacuous(self) -> None:
        """Guard the oracle itself: the cases must disagree with each other.

        An oracle accidentally reduced to a constant -- or a case list that
        never varies a controlled variable -- would make the sweep below pass
        while asserting nothing.
        """
        cases = self._cases()
        assert len(cases) == 2 * 2 * 2 * 2 * 6
        distinct = {tuple(sorted(self._expected(**case).items())) for case in cases}
        assert len(distinct) > 1
        # Every controlled variable must be both set and unset somewhere in
        # the domain, or its own rule is untested.
        for name in bench.CONTROLLED_ENV_VARS:
            values = {self._expected(**case)[name] for case in cases}
            assert None in values, name
            assert values - {None}, name

    def test_no_controlled_variable_is_ever_inherited(self) -> None:
        disagreements = []
        for case in self._cases():
            env = bench.variant_env(dict(self.AMBIENT), **case)
            expected = self._expected(**case)
            for name, want in expected.items():
                got = env.get(name)
                if got != want:
                    disagreements.append((case, name, want, got))
        assert not disagreements, disagreements

    def test_an_uncontrolled_variable_is_inherited_untouched(self) -> None:
        """The rule is scoped, not a blanket wipe: the child still needs PATH."""
        base = dict(self.AMBIENT)
        base["PATH"] = "/usr/bin"
        for case in self._cases():
            env = bench.variant_env(base, **case)
            assert env["PATH"] == "/usr/bin"
            if (case["env_extra"] or {}).get("UNRELATED_TO_THIS_HARNESS"):
                assert env["UNRELATED_TO_THIS_HARNESS"] == "kept"

    def test_the_callers_mapping_is_not_mutated(self) -> None:
        base = dict(self.AMBIENT)
        before = dict(base)
        bench.variant_env(
            base,
            env_extra={"ABICHECK_CACHE_DIR": "/x"},
            job_mem_gib=1.0,
            trace=None,
            tracemalloc=False,
            cache_dir=None,
        )
        assert base == before
