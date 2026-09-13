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

"""Contract tests for the full-CLI L2 perf harness, split from its gate tests.

Split out of ``test_l2_cli_perf_gate.py`` once that file crossed the
architecture gate's 1200-line test-file cap -- a mechanical extraction, not a
redesign. What lives here is the set of claims about *what the harness asserts*
rather than *how it gates*: that a fixture arm really built what it says it
built, that a "no compiler ran" contract covers every observed native
invocation, that a recorded threshold names the right origin, and that a
duration argument cannot be configured into silently measuring nothing.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
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


def ast_dump(node) -> str:
    import ast

    return ast.dump(node)


def ast_unparse(node) -> str:
    import ast

    return ast.unparse(node)


def _run(native_invocations: dict[str, int]) -> object:
    return receipt_mod.CommandRun(
        argv=["x"],
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


class TestSharedHeaderContextIsReallyShared:
    """A "shared context" arm that gives every library its own copy shares nothing.

    The first version of the multi-library fixture wrote a byte-identical
    ``detail/core.h`` per library, so both arms were identical in substance while
    the receipt reported ``header_contexts: 1`` from the *flag*. Any conclusion
    drawn from comparing the arms was then about nothing. Two halves are asserted
    here: the builder really produces one physical dependency root, and the
    receipt counts resolved paths rather than reading the intent flag back.
    """

    def _fake(self, tmp_path, *, libs: int, shared: bool):
        new = []
        shared_root = tmp_path / "shared" / "include"
        for index in range(libs):
            inc = tmp_path / f"lib{index}" / "include"
            (inc / "detail").mkdir(parents=True, exist_ok=True)
            extra: tuple[Path, ...] = ()
            if shared and libs > 1:
                (shared_root / "detail").mkdir(parents=True, exist_ok=True)
                (shared_root / "detail" / "core.h").write_text("x", encoding="utf-8")
                extra = (shared_root,)
            else:
                (inc / "detail" / "core.h").write_text("x", encoding="utf-8")
            new.append(
                fixtures.BuiltLibrary(
                    so=inc / "libx.so",
                    headers=[],
                    include_dir=inc,
                    extra_includes=extra,
                )
            )
        spec = fixtures.FixtureSpec(
            shape="simple",
            headers=1,
            libraries=libs,
            change="unchanged",
            distinct_contexts=not shared,
        )
        return fixtures.BuiltFixture(spec=spec, old=list(new), new=new)

    @pytest.mark.parametrize("libs", [2, 3, 5])
    def test_a_shared_arm_resolves_to_exactly_one_root(self, tmp_path, libs):
        fixture = self._fake(tmp_path, libs=libs, shared=True)
        assert len(harness._resolved_dependency_roots(fixture)) == 1

    @pytest.mark.parametrize("libs", [2, 3, 5])
    def test_a_distinct_arm_resolves_to_one_root_per_library(self, tmp_path, libs):
        fixture = self._fake(tmp_path, libs=libs, shared=False)
        assert len(harness._resolved_dependency_roots(fixture)) == libs

    def test_the_count_is_not_read_off_the_intent_flag(self, tmp_path):
        # A fixture whose flag says "shared" but whose files are per-library must
        # still count N -- this is the exact state the bug was in.
        fixture = self._fake(tmp_path, libs=4, shared=False)
        lying = fixtures.BuiltFixture(
            spec=fixtures.FixtureSpec(
                shape="simple",
                headers=1,
                libraries=4,
                change="unchanged",
                distinct_contexts=False,
            ),
            old=fixture.old,
            new=fixture.new,
        )
        assert len(harness._resolved_dependency_roots(lying)) == 4

    @pytest.mark.integration
    def test_the_builder_really_writes_one_shared_file(self, tmp_path):
        if shutil.which("g++") is None:
            pytest.skip("g++ unavailable")
        spec = fixtures.FixtureSpec(
            shape="simple",
            headers=1,
            libraries=3,
            change="unchanged",
            distinct_contexts=False,
        )
        built = fixtures.build(spec, tmp_path)
        roots = harness._resolved_dependency_roots(built)
        assert len(roots) == 1, roots
        for lib in built.new:
            assert lib.extra_includes, "the shared root must reach the harness"


class TestForbiddenMeansNoCompilerAtAll:
    """A stored-operand path claims *no compiler ran*, not "no AST extraction".

    Checking only the ``header_extraction`` bucket let every other observed
    native invocation through: a regression that starts spawning ``clang++ -M``
    (``include_pass``), ``g++ --version`` (``probe``) or anything unclassified
    while loading two stored snapshots would pass the one scenario whose entire
    subject is that it spawns nothing. Stated over every bucket rather than
    against the one that was reported, so a future bucket is covered too.
    """

    @pytest.mark.parametrize("kind", list(receipt_mod.INVOCATION_KINDS))
    def test_any_single_invocation_in_any_bucket_fails(self, kind):
        counts = {k: 0 for k in receipt_mod.INVOCATION_KINDS}
        counts[kind] = 1
        problems = harness._check_extraction(_run(counts), "forbidden", one_side=2)
        assert len(problems) == 1, (kind, problems)
        assert kind in problems[0], problems[0]

    def test_an_all_zero_observation_still_passes(self):
        counts = {k: 0 for k in receipt_mod.INVOCATION_KINDS}
        assert harness._check_extraction(_run(counts), "forbidden", one_side=2) == []

    def test_the_message_names_every_nonzero_bucket(self):
        counts = {k: 0 for k in receipt_mod.INVOCATION_KINDS}
        counts["include_pass"] = 3
        counts["probe"] = 1
        problems = harness._check_extraction(_run(counts), "forbidden", one_side=2)
        assert "include_pass=3" in problems[0]
        assert "probe=1" in problems[0]

    def test_a_non_forbidden_contract_is_unaffected_by_other_buckets(self):
        # Vacuity guard in the other direction: a live side legitimately runs
        # include passes and probes, so widening `forbidden` must not have
        # widened `one_side`/`both_sides` too.
        counts = {"header_extraction": 2, "include_pass": 9, "probe": 20, "other": 1}
        assert harness._check_extraction(_run(counts), "one_side", one_side=2) == []


class TestThresholdSourceTracksTheStatement:
    """``source`` must say whether the caller stated a number, not compare values.

    Two wrong answers came from deriving it from ``args.regress_tolerance``'s
    *value*: the CI lane's own ``--regress-min-delta-seconds 0.6`` read as
    ``"default"`` because only the tolerance was consulted, and an explicitly
    passed default-equal tolerance read as ``"default"`` too.
    """

    def _threshold(self, argv):
        # Through main()'s own parser, not a reconstructed Namespace, so the test
        # cannot pass against a parser whose defaults changed.
        args = harness.parse_args([*argv, "--scenario", "nothing-matches-this"])
        return args

    def test_neither_flag_given_is_default(self):
        args = self._threshold([])
        assert args.regress_tolerance is None
        assert args.regress_min_delta_seconds is None

    @pytest.mark.parametrize(
        "argv",
        [
            ["--regress-tolerance", "0.3"],
            ["--regress-min-delta-seconds", "0.6"],
            ["--regress-tolerance", "0.3", "--regress-min-delta-seconds", "0.6"],
            ["--regress-tolerance", "0"],
        ],
    )
    def test_either_flag_given_is_recorded_as_stated(self, argv):
        args = self._threshold(argv)
        stated = (
            args.regress_tolerance is not None
            or args.regress_min_delta_seconds is not None
        )
        assert stated, argv

    def test_an_explicitly_stated_default_value_is_not_reported_as_default(self):
        # The case a value comparison gets wrong: 0.3 is the module default.
        args = self._threshold(
            ["--regress-tolerance", str(harness.DEFAULT_REGRESS_TOLERANCE)]
        )
        assert args.regress_tolerance == harness.DEFAULT_REGRESS_TOLERANCE
        assert args.regress_tolerance is not None


class TestDurationArgumentsRejectZero:
    """Zero is a silently-broken configuration for both duration options."""

    @pytest.mark.parametrize("flag", ["--timeout-seconds", "--rss-interval-seconds"])
    def test_zero_is_a_usage_error(self, flag):
        with pytest.raises(SystemExit):
            harness.parse_args([flag, "0"])

    @pytest.mark.parametrize("flag", ["--timeout-seconds", "--rss-interval-seconds"])
    @pytest.mark.parametrize("bad", ["-1", "nan", "inf"])
    def test_negative_and_non_finite_are_usage_errors(self, flag, bad):
        with pytest.raises(SystemExit):
            harness.parse_args([flag, bad])

    @pytest.mark.parametrize("flag", ["--timeout-seconds", "--rss-interval-seconds"])
    def test_a_small_positive_value_is_accepted(self, flag):
        assert harness.parse_args([flag, "0.001"]) is not None

    def test_a_regression_floor_of_zero_is_still_accepted(self):
        # The two families differ on exactly this value: zero is meaningful for a
        # regression floor (pure percentage tolerance) and not for a duration.
        assert harness.parse_args(["--regress-min-delta-seconds", "0"]) is not None


class TestMeasuredSubprocessesGetANeutralCwd:
    """`python -m abicheck` launched from a checkout imports *that* checkout.

    `-m` puts the current directory first on `sys.path`, so a measured CLI run
    with the harness's own cwd picks up the source tree it was launched from
    rather than the installed package. The PR-vs-base CI lane is where that
    bites: it deliberately runs HEAD's harness against BASE's editable install,
    so a head-rooted cwd made the "base" measurement execute HEAD's product and
    collapsed the regression comparison into head-versus-head.
    """

    def test_the_shadowing_mechanism_is_real(self, tmp_path):
        # The oracle is the interpreter, not this module's reasoning about it:
        # without it, "cwd shadows the install" is an assumption and the fix is
        # unverified. A package named `abicheck` in the cwd wins over the
        # installed one.
        import subprocess
        import sys

        pkg = tmp_path / "abicheck"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "__main__.py").write_text("print('SHADOW')\n", encoding="utf-8")
        shadowed = subprocess.run(
            [sys.executable, "-m", "abicheck"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert "SHADOW" in shadowed.stdout, shadowed

        neutral = tmp_path / "neutral"
        neutral.mkdir()
        installed = subprocess.run(
            [sys.executable, "-m", "abicheck", "--version"],
            cwd=neutral,
            capture_output=True,
            text=True,
            check=False,
        )
        assert "SHADOW" not in installed.stdout
        assert installed.returncode == 0, installed.stderr

    @staticmethod
    def _run_measured_calls():
        """Every `run_measured(...)` call the harness makes, wherever it lives.

        Searched over the whole module rather than one function, so the executor
        moving (it has: out of a `run_scenario` closure and into
        `_StepExecutor.__call__`) cannot quietly make these assertions vacuous.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(harness))
        return [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "run_measured"
        ]

    def test_every_measured_run_is_given_an_explicit_cwd(self):
        # Asserted on the call rather than on a comment: the defect is the
        # *absence* of a cwd argument, which no output can reveal (both
        # configurations produce a valid-looking receipt).
        calls = self._run_measured_calls()
        assert calls, "the harness must execute the measured subprocesses somewhere"
        for call in calls:
            assert "cwd" in {kw.arg for kw in call.keywords}, ast_dump(call)

    def test_the_cwd_is_not_the_harness_own_directory(self):
        # The fix is only a fix if the directory handed over is a per-scenario
        # work directory; passing `cwd=Path.cwd()` would satisfy the previous
        # assertion and change nothing.
        for call in self._run_measured_calls():
            cwd = next(kw.value for kw in call.keywords if kw.arg == "cwd")
            # `self.work` (the executor's own per-scenario directory) or a bare
            # `work`; never a call such as `Path.cwd()`.
            rendered = ast_unparse(cwd)
            assert rendered in ("self.work", "work"), rendered


class TestTheUnchangedControlRejectsEveryManufacturedFinding:
    """On an identical pair, any finding claiming a *difference* is a false positive.

    Checking only the two families the break fixture produces was too narrow to
    be a control: a regression emitting a parameter, visibility or platform
    finding left the scenario reading as successful. The allowed set is derived
    from the product's own RISK/QUALITY partition, not hand-listed, because a
    risk observation that holds on both sides is a true statement about the
    fixture -- which the first strict version proved by failing on the real
    `private_header_leak` the generated fixture legitimately has.
    """

    def test_an_identical_pair_with_no_findings_passes(self):
        assert (
            harness._validate_unchanged({"verdict": "COMPATIBLE", "changes": []}) == []
        )

    @pytest.mark.parametrize(
        "kind",
        ["func_removed", "type_size_changed", "param_added", "elf_soname_changed"],
    )
    def test_any_difference_asserting_finding_fails(self, kind):
        problems = harness._validate_unchanged(
            {"verdict": "COMPATIBLE", "changes": [{"kind": kind}]}
        )
        assert problems, kind
        assert "false positive" in problems[0]

    def test_a_risk_observation_that_holds_on_both_sides_is_accepted(self):
        # Derived from the real partition, so this is not a hand-blessed
        # exception for one kind.
        from abicheck.checker_policy import RISK_KINDS

        risk = sorted(k.value for k in RISK_KINDS)[:5]
        assert risk, "the partition must be non-empty or this asserts nothing"
        for kind in risk:
            assert (
                harness._validate_unchanged(
                    {"verdict": "COMPATIBLE_WITH_RISK", "changes": [{"kind": kind}]}
                )
                == []
            ), kind

    @pytest.mark.parametrize("verdict", ["BREAKING", "SOURCE_BREAK", "API_BREAK"])
    def test_a_difference_asserting_verdict_fails_on_its_own(self, verdict):
        problems = harness._validate_unchanged({"verdict": verdict, "changes": []})
        assert problems and "identical pair" in problems[0]

    def test_the_allowed_set_is_not_accidentally_everything(self):
        # Vacuity guard on the derivation: if `_surface_state_kinds()` ever
        # returned every kind, every assertion above would pass while the
        # control asserted nothing.
        from abicheck.checker_policy import BREAKING_KINDS

        allowed = harness._surface_state_kinds()
        assert allowed
        assert not allowed & {k.value for k in BREAKING_KINDS}


class TestTheAuditMustHaveDoneTheL2Work:
    """Audit *semantics* are satisfied by a binary-only fallback, which is faster.

    The audit report publishes no `analysis_assurance`/`scope`/`*_evidence_depth`
    block at all, so `_validate_l2_reached` cannot be applied to it; its own
    `evidence_tiers` list is what proves which tiers the run consumed.
    """

    def test_a_header_tier_passes(self):
        assert (
            harness._validate_audit_reached_l2(
                {"evidence_tiers": ["elf", "dwarf", "dwarf_advanced", "header"]}
            )
            == []
        )

    @pytest.mark.parametrize(
        "tiers",
        [
            ["elf"],
            ["elf", "dwarf"],
            ["elf", "dwarf", "dwarf_advanced"],
            [],
        ],
    )
    def test_a_binary_only_fallback_fails(self, tiers):
        # The "faster because it stopped working" direction, over every shape a
        # real fallback can take rather than the one observed.
        problems = harness._validate_audit_reached_l2({"evidence_tiers": tiers})
        assert problems, tiers
        assert "header" in problems[0]

    @pytest.mark.parametrize("value", [None, "header", 3, {"header": True}])
    def test_a_missing_or_malformed_tier_list_fails_rather_than_passing(self, value):
        # A string containing "header" must not satisfy a membership test.
        assert harness._validate_audit_reached_l2({"evidence_tiers": value})

    def test_the_audit_step_does_not_accept_a_compatibility_exit(self):
        # 2 and 4 are compatibility exits; an audit has nothing to compare
        # against, so producing one is the defect `_validate_audit` rejects --
        # and accepting it at the step level let the run pass before validation
        # ever read the report.
        spec = fixtures.FixtureSpec(
            shape="simple",
            headers=1,
            libraries=1,
            change="break",
            distinct_contexts=False,
        )
        scenario = harness.scenario_no_baseline(spec)
        built = fixtures.BuiltFixture(spec=spec, old=[], new=[_stub_library()])
        steps = scenario.steps(built, Path("/tmp/does-not-need-to-exist"))
        assert steps
        for step in steps:
            assert 2 not in step.ok_exit_codes, step
            assert 4 not in step.ok_exit_codes, step


def _stub_library() -> object:
    return fixtures.BuiltLibrary(
        so=Path("/tmp/libstub.so"),
        headers=[Path("/tmp/stub.h")],
        include_dir=Path("/tmp"),
    )


def _batch(counts: list[int]) -> list[object]:
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


class TestAStepMustProduceItsOwnOutput:
    """A declared output that this run did not write is not a measurement.

    Two halves, both needed: the path is unlinked before every invocation, and a
    missing file afterwards is a failure rather than (as before) only being
    size-checked when present. Without the pair, a repetition that exited with
    an allowed code and rendered nothing was validated against the *previous*
    repetition's file.
    """

    def _step(self, out: Path) -> object:
        return harness.Step("s", ["true"], output=out, ok_exit_codes=(0, 2, 4))

    def _ok_run(self, code: int = 0) -> object:
        run = _run({"header_extraction": 0})
        run.exit_code = code
        return run

    def test_a_missing_output_fails(self, tmp_path):
        problems = harness._step_failure(
            self._step(tmp_path / "absent.json"),
            self._ok_run(),
            timeout=10,
            one_side=None,
            check_extraction=False,
        )
        assert problems and "wrote no output" in problems[0]

    @pytest.mark.parametrize("code", [0, 2, 4])
    def test_a_missing_output_fails_for_every_allowed_exit_code(self, tmp_path, code):
        # The reported shape: an *allowed* verdict exit with no rendered report.
        assert harness._step_failure(
            self._step(tmp_path / "absent.json"),
            self._ok_run(code),
            timeout=10,
            one_side=None,
            check_extraction=False,
        )

    def test_a_present_output_passes(self, tmp_path):
        out = tmp_path / "present.json"
        out.write_text("{}", encoding="utf-8")
        assert (
            harness._step_failure(
                self._step(out),
                self._ok_run(),
                timeout=10,
                one_side=None,
                check_extraction=False,
            )
            is None
        )

    def test_a_step_declaring_no_output_is_unaffected(self, tmp_path):
        step = harness.Step("s", ["true"], ok_exit_codes=(0,))
        assert (
            harness._step_failure(
                step,
                self._ok_run(),
                timeout=10,
                one_side=None,
                check_extraction=False,
            )
            is None
        )

    def test_the_executor_unlinks_the_output_before_running(self, tmp_path):
        # Without this, "the file exists" after a run does not mean this run
        # wrote it, and the existence check above would accept a stale file.
        stale = tmp_path / "stale.json"
        stale.write_text('{"from": "a previous repetition"}', encoding="utf-8")
        executor = harness._StepExecutor(
            work=tmp_path,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            timeout=30,
            rss_interval=0.05,
            spy=None,
        )
        # A command that exits 0 and writes nothing -- the exact shape the stale
        # file used to mask.
        executor(
            harness.Step("noop", [sys.executable, "-c", ""], output=stale),
            timed=False,
        )
        assert not stale.exists(), "the stale output survived the run"


class TestLiveRunsMustRunTheIncludeGraphPass:
    """Header-AST extraction is not the whole of the measured L2 work.

    The compare path also runs an always-on `clang -M` include/dependency pass.
    Checking only `header_extraction` let a regression that stops running it read
    as a *performance improvement*: the run is shorter, the evidence depth still
    resolves to `headers`, and the deliberate break is still found, so nothing
    else notices. The floor is one pass per live side, which is what every
    observed run does — and the count scales above that with header count
    (8 headers × 2 sides → 16, 32 → 64), so it is derived from measurement.
    """

    @staticmethod
    def _run_with(extraction: int, include: int) -> object:
        return _run({"header_extraction": extraction, "include_pass": include})

    @pytest.mark.parametrize("contract,sides", [("one_side", 1), ("both_sides", 2)])
    def test_no_include_pass_fails_even_though_extraction_succeeded(
        self, contract, sides
    ):
        problems = harness._check_extraction(
            self._run_with(2 * sides, 0), contract, one_side=2
        )
        assert problems, (contract, sides)
        assert "include-graph pass did not run" in problems[0]

    def test_one_pass_for_two_sides_fails(self):
        # The half-regression: the pass still runs, but only for one operand.
        problems = harness._check_extraction(
            self._run_with(4, 1), "both_sides", one_side=2
        )
        assert problems and "1 include" in problems[0]

    @pytest.mark.parametrize(
        "contract,sides,include", [("one_side", 1, 1), ("both_sides", 2, 2)]
    )
    def test_the_observed_floor_passes(self, contract, sides, include):
        assert (
            harness._check_extraction(
                self._run_with(2 * sides, include), contract, one_side=2
            )
            == []
        )

    @pytest.mark.parametrize("include", [2, 16, 64])
    def test_more_passes_than_the_floor_pass(self, include):
        # Not an equality: how many passes the product runs per side is its
        # business; running none is the regression. 16 and 64 are the real counts
        # observed for the 8- and 32-header extended scenarios.
        assert (
            harness._check_extraction(
                self._run_with(4, include), "both_sides", one_side=2
            )
            == []
        )

    def test_a_forbidden_path_is_unaffected(self):
        # A stored/stored run legitimately reports zero include passes, and the
        # new floor must not turn that into a failure.
        counts = {k: 0 for k in receipt_mod.INVOCATION_KINDS}
        assert harness._check_extraction(_run(counts), "forbidden", one_side=2) == []

    def test_an_unobserved_counter_does_not_fabricate_a_failure(self):
        # With --no-spy there are no counters at all; absent is not zero.
        assert (
            harness._check_extraction(
                _run({"header_extraction": 2}), "one_side", one_side=2
            )
            == []
        )


class TestEveryDeclaredExportIsClearedAndRequired:
    """One invocation writing two files must have both checked, not just the first.

    The two-formats scenario's single `compare` writes a JSON *and* a Markdown
    export. With only the JSON declared, a repetition that wrote fresh JSON and
    silently omitted Markdown left the previous repetition's Markdown in place for
    the validator to accept, and the incomplete render's faster timing stayed in
    the median.
    """

    def test_declared_outputs_covers_both_fields(self, tmp_path):
        step = harness.Step(
            "s",
            ["true"],
            output=tmp_path / "a.json",
            extra_outputs=(tmp_path / "b.md",),
        )
        assert step.declared_outputs == (tmp_path / "a.json", tmp_path / "b.md")

    def test_declared_outputs_is_empty_when_nothing_is_declared(self):
        assert harness.Step("s", ["true"]).declared_outputs == ()

    def test_a_missing_extra_output_fails(self, tmp_path):
        json_out = tmp_path / "a.json"
        json_out.write_text("{}", encoding="utf-8")
        run = _run({"header_extraction": 0})
        run.exit_code = 0
        problems = harness._step_failure(
            harness.Step(
                "s",
                ["true"],
                output=json_out,
                extra_outputs=(tmp_path / "absent.md",),
            ),
            run,
            timeout=10,
            one_side=None,
            check_extraction=False,
        )
        assert problems and "absent.md" in problems[0]

    def test_the_executor_clears_every_declared_export(self, tmp_path):
        stale_json = tmp_path / "a.json"
        stale_md = tmp_path / "b.md"
        for path in (stale_json, stale_md):
            path.write_text("from a previous repetition", encoding="utf-8")
        executor = harness._StepExecutor(
            work=tmp_path,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            timeout=30,
            rss_interval=0.05,
            spy=None,
        )
        executor(
            harness.Step(
                "noop",
                [sys.executable, "-c", ""],
                output=stale_json,
                extra_outputs=(stale_md,),
            ),
            timed=False,
        )
        assert not stale_json.exists()
        assert not stale_md.exists(), "the extra export survived the run"

    def test_the_two_formats_scenario_declares_both_of_its_exports(self):
        # The real scenario, not a constructed Step: the defect was in what that
        # scenario declared, so asserting on a hand-built Step would miss it.
        spec = fixtures.FixtureSpec(
            shape="simple",
            headers=1,
            libraries=1,
            change="break",
            distinct_contexts=False,
        )
        steps = harness.scenario_two_formats(spec).steps(
            fixtures.BuiltFixture(
                spec=spec, old=[_stub_library()], new=[_stub_library()]
            ),
            Path("/tmp/l2-two-formats-decl"),
        )
        timed = [s for s in steps if s.scope == "full_cli"]
        assert timed, "the scenario must have a measured step"
        for step in timed:
            names = {p.name for p in step.declared_outputs}
            assert names == {"fmt.json", "fmt.md"}, names


class TestEveryRepetitionIsValidated:
    """Validating once after the loop inspects only the final repetition's output.

    An earlier repetition that emitted degraded evidence or wrong findings while
    still writing a file and exiting with an allowed code kept its faster timing
    in the median whenever the last repetition happened to be correct. The
    repetition loop is the outer one precisely so per-repetition validation is
    possible: each pass writes its outputs before the next begins.
    """

    def _steps(self) -> list[object]:
        return [harness.Step("s", [sys.executable, "-c", ""], ok_exit_codes=(0,))]

    def _execute(self, step, *, timed: bool):
        run = _run({"header_extraction": 0})
        run.exit_code = 0
        return run

    def _scenario(self) -> object:
        spec = fixtures.FixtureSpec(
            shape="simple",
            headers=1,
            libraries=1,
            change="break",
            distinct_contexts=False,
        )
        return harness.Scenario(
            id="x",
            description="",
            spec=spec,
            prepare=None,
            steps=lambda f, w: self._steps(),
            validate=lambda w, r: [],
        )

    def test_validation_runs_once_per_repetition(self, tmp_path):
        seen: list[int] = []
        abort = harness._run_measured_steps(
            self._steps(),
            execute=self._execute,
            repeat=3,
            runs={},
            scenario=self._scenario(),
            fixture=None,
            cache_root=tmp_path,
            timeout=10,
            one_side=None,
            check_extraction=False,
            validate_repetition=lambda index: seen.append(index) or [],
        )
        assert abort is None
        assert seen == [0, 1, 2], seen

    @pytest.mark.parametrize("bad", [0, 1, 2])
    def test_a_degraded_repetition_aborts_even_if_a_later_one_is_fine(
        self, tmp_path, bad
    ):
        # Every position, including the last-but-one and the first — the defect
        # was that only the final repetition's output was ever inspected.
        def validate(index: int) -> list[str]:
            return (
                ["effective_depth='binary', expected 'headers'"] if index == bad else []
            )

        abort = harness._run_measured_steps(
            self._steps(),
            execute=self._execute,
            repeat=3,
            runs={},
            scenario=self._scenario(),
            fixture=None,
            cache_root=tmp_path,
            timeout=10,
            one_side=None,
            check_extraction=False,
            validate_repetition=validate,
        )
        assert abort is not None
        assert f"repetition {bad}:" in abort[0], abort

    def test_the_failing_repetition_is_named(self, tmp_path):
        abort = harness._run_measured_steps(
            self._steps(),
            execute=self._execute,
            repeat=2,
            runs={},
            scenario=self._scenario(),
            fixture=None,
            cache_root=tmp_path,
            timeout=10,
            one_side=None,
            check_extraction=False,
            validate_repetition=lambda index: ["bad evidence"] if index == 1 else [],
        )
        assert abort == ["repetition 1: bad evidence"], abort

    def test_no_callback_keeps_the_previous_behaviour(self, tmp_path):
        # The parameter is optional, so a caller that does not validate per
        # repetition still runs (every existing unit test drives it this way).
        assert (
            harness._run_measured_steps(
                self._steps(),
                execute=self._execute,
                repeat=2,
                runs={},
                scenario=self._scenario(),
                fixture=None,
                cache_root=tmp_path,
                timeout=10,
                one_side=None,
                check_extraction=False,
            )
            is None
        )
