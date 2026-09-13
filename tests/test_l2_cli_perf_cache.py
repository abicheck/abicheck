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

"""Every claim the harness makes about a run's CACHE STATE, and nothing else.

The fourth file in this family, split out once ``test_l2_cli_perf_contracts.py``
crossed the architecture gate's 1200-line test-file cap. Thematic rather than a
trim to fit: a cache claim is the one kind of claim here that cannot be read off
a single run's output at all -- "this run was served by a warm cache" and "this
run re-extracted after its dependency changed" are statements about a *pair* of
observations, and every defect this file guards against came from collapsing a
pair (or a batch) to its most favourable member.

So the rule stated throughout: every repetition is checked individually, paired
by index, and an absent counter makes the claim unverified rather than satisfied.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


harness = _load("check_l2_cli_perf")
receipt_mod = _load("perf_receipt")
fixtures = _load("l2_cli_fixture")


def _run(
    native_invocations: dict[str, int], *, argv: list[str] | None = None
) -> object:
    return receipt_mod.CommandRun(
        argv=argv if argv is not None else ["x"],
        exit_code=0,
        wall_seconds=1.0,
        user_cpu_seconds=0.5,
        system_cpu_seconds=0.1,
        cpu_scope="waited_children_delta",
        timed_out=False,
        stdout="",
        stderr="",
        native_invocations=native_invocations,
    )


def _stub_library() -> object:
    return fixtures.BuiltLibrary(
        so=Path("/tmp/libstub.so"),
        headers=[Path("/tmp/stub.h")],
        include_dir=Path("/tmp"),
    )


def _batch(counts: list[int]) -> list[object]:
    """One `CommandRun` per repetition, so every check here is per-repetition."""
    return [_run({"header_extraction": n}) for n in counts]


class TestEveryWarmRepetitionMustShowTheCacheServing:
    """One cache hit must not certify a batch in which the others re-extracted.

    The first version reduced each batch with `min()` and compared the two
    numbers, so a single warm repetition hitting the cache validated the whole
    scenario while the rest extracted in full — the gated median could then
    describe an uncached run while the receipt reported a served cache. No
    reducer over repetitions can express "each repetition was warm", so the
    comparison is per index-aligned pair.
    """

    def test_all_warm_repetitions_served_passes(self):
        runs = {"cold": _batch([2, 2, 2]), "warm": _batch([0, 0, 0])}
        assert harness._warm_cache_problems(runs) == []

    @pytest.mark.parametrize(
        "warm,bad",
        [
            ([0, 2, 0], [1]),
            ([2, 0, 0], [0]),
            ([0, 0, 2], [2]),
            ([2, 2, 0], [0, 1]),
            ([3, 3, 3], [0, 1, 2]),
        ],
    )
    def test_any_unserved_repetition_fails_and_is_named(self, warm, bad):
        # Every position, not just the one a `min()` reduction happened to miss
        # first, and the failure names which repetitions were wrong.
        runs = {"cold": _batch([2, 2, 2]), "warm": _batch(warm)}
        problems = harness._warm_cache_problems(runs)
        assert len(problems) == len(bad), problems
        for index in bad:
            assert any(f"repetition {index}:" in p for p in problems), (index, problems)

    def test_a_min_reduction_would_have_passed_the_mixed_case(self):
        # States the defect directly, so the test cannot drift back: under the
        # old rule (min of each batch) a batch with one served repetition looked
        # identical to a fully served one.
        runs = {"cold": _batch([2, 2, 2]), "warm": _batch([0, 2, 2])}
        assert min(0, 2, 2) < min(2, 2, 2), "the old rule really did pass this"
        assert harness._warm_cache_problems(runs)

    def test_a_cold_repetition_that_extracted_nothing_fails(self):
        # The other direction: a cache root that was not actually fresh means
        # nothing in the scenario measures a cold state.
        runs = {"cold": _batch([2, 0, 2]), "warm": _batch([0, 0, 0])}
        problems = harness._warm_cache_problems(runs)
        assert any("not actually fresh" in p for p in problems), problems

    def test_unpairable_batches_fail_rather_than_being_reduced(self):
        runs = {"cold": _batch([2, 2]), "warm": _batch([0])}
        problems = harness._warm_cache_problems(runs)
        assert problems and "cannot pair" in problems[0]

    @pytest.mark.parametrize(
        "cold,warm,expected",
        [
            ([2, 2, 2], [0, 0, 0], "full"),
            ([2, 2, 2], [1, 1, 1], "partial"),
            ([2, 2, 2], [2, 2, 2], "none"),
            ([2, 2, 2], [0, 2, 0], "none"),
            ([2, 2, 2], [0, 1, 0], "partial"),
        ],
    )
    def test_the_reported_service_is_the_worst_repetition(self, cold, warm, expected):
        # Reporting the *best* repetition is the mislabelling half of the same
        # defect: a batch served once and missed twice is not a "full" cache.
        runs = {"cold": _batch(cold), "warm": _batch(warm)}
        assert harness._classify_cache_service(runs) == expected


class TestEveryInvalidationRepetitionMustReExtract:
    """The mirror image: `max()` let one re-extraction excuse stale repetitions.

    Serving stale evidence is the correctness bug this control exists to catch,
    so a repetition that served it must fail even when a sibling repetition did
    the work.
    """

    def test_all_repetitions_re_extracting_passes(self):
        scenario = harness.scenario_cache_invalidation(
            fixtures.FixtureSpec(
                shape="simple",
                headers=1,
                libraries=1,
                change="break",
                distinct_contexts=False,
            )
        )
        runs = {"after_dependency_change": _batch([2, 2, 2])}
        problems = [
            p
            for p in scenario.validate(Path("/nonexistent"), runs)
            if "snapshot" not in p
        ]
        assert problems == [], problems

    @pytest.mark.parametrize("after", [[0, 2, 2], [2, 0, 2], [2, 2, 0], [0, 0, 0]])
    def test_any_stale_repetition_fails(self, after):
        # The first three cases are exactly the ones a `max()` reduction passed:
        # one repetition re-extracted, so the batch's maximum was nonzero.
        scenario = harness.scenario_cache_invalidation(
            fixtures.FixtureSpec(
                shape="simple",
                headers=1,
                libraries=1,
                change="break",
                distinct_contexts=False,
            )
        )
        runs = {"after_dependency_change": _batch(after)}
        problems = scenario.validate(Path("/nonexistent"), runs)
        assert any("stale" in p for p in problems), problems


class TestTheInvalidationSetupStepsDeclareTheirOutputs:
    """Its `cold` and `warm` dumps are measured steps, so they are gated.

    They declared no output, so a run that exited 0 after skipping report
    serialization had its faster timing gated while validation looked only at the
    post-change snapshot.
    """

    def _steps(self) -> list[object]:
        spec = fixtures.FixtureSpec(
            shape="simple",
            headers=1,
            libraries=1,
            change="break",
            distinct_contexts=False,
        )
        return harness.scenario_cache_invalidation(spec).steps(
            fixtures.BuiltFixture(
                spec=spec, old=[_stub_library()], new=[_stub_library()]
            ),
            Path("/tmp/l2-inv-decl"),
        )

    def test_every_measured_step_declares_an_output(self):
        steps = self._steps()
        assert len(steps) == 3, [s.name for s in steps]
        for step in steps:
            assert step.declared_outputs, step.name

    def test_each_step_declares_its_own_distinct_snapshot(self):
        # Sharing one path between the three would reintroduce the stale-output
        # problem inside a single repetition.
        names = [p.name for step in self._steps() for p in step.declared_outputs]
        assert sorted(names) == [
            "inv_after.abi.json",
            "inv_cold.abi.json",
            "inv_warm.abi.json",
        ], names


class TestTheMultiLibrarySetSharesOneCacheLifecycle:
    """A workload reset before each member cannot measure reuse between members.

    Every `scenario_multi_library` step used the default `cache_mode="cold"`, so
    the shared-context arm started each library's comparison from an empty cache
    and could never reuse anything the previous library warmed -- the single thing
    that distinguishes it from the distinct-context arm (Codex review).
    """

    def test_the_set_declares_a_sequence_cache_mode(self) -> None:
        scenario = harness.scenario_multi_library(
            fixtures.FixtureSpec(shape="simple", headers=1, libraries=5, change="break")
        )
        assert scenario.cache_mode in harness._SEQUENCE_CACHE_MODES

    def test_the_cache_is_reset_once_for_the_set_not_per_member(self) -> None:
        """The behavioural claim, through the real reset predicate."""
        spec = fixtures.FixtureSpec(
            shape="simple", headers=1, libraries=5, change="break"
        )
        mode = harness.scenario_multi_library(spec).cache_mode
        step = harness.Step("compare_lib0", ["x"], extraction="both_sides")
        assert harness._needs_cold_cache(mode, step, 0)
        assert not any(
            harness._needs_cold_cache(mode, step, index) for index in range(1, 5)
        )

    def test_the_set_still_begins_cold(self) -> None:
        """Otherwise repetition 2 would be served by repetition 1's cache."""
        spec = fixtures.FixtureSpec(
            shape="simple", headers=1, libraries=2, change="break"
        )
        mode = harness.scenario_multi_library(spec).cache_mode
        assert harness._needs_cold_cache(mode, harness.Step("compare_lib0", ["x"]), 0)
