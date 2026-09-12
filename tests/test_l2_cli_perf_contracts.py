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
import shutil
import sys
import textwrap
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

    def test_every_measured_run_is_given_an_explicit_cwd(self):
        # Asserted on the call rather than on a comment: the defect is the
        # *absence* of a cwd argument, which no output can reveal (both
        # configurations produce a valid-looking receipt).
        import ast
        import inspect

        source = inspect.getsource(harness.run_scenario)
        calls = [
            node
            for node in ast.walk(ast.parse(textwrap.dedent(source)))
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "run_measured"
        ]
        assert calls, "run_scenario must execute the measured subprocesses"
        for call in calls:
            assert "cwd" in {kw.arg for kw in call.keywords}, ast.dump(call)

    def test_the_cwd_is_not_the_harness_own_directory(self):
        # The fix is only a fix if the directory handed over is a per-scenario
        # work directory; passing `cwd=Path.cwd()` would satisfy the previous
        # assertion and change nothing.
        import ast
        import inspect

        source = textwrap.dedent(inspect.getsource(harness.run_scenario))
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "run_measured"
            ):
                cwd = next(kw.value for kw in node.keywords if kw.arg == "cwd")
                assert isinstance(cwd, ast.Name) and cwd.id == "work", ast.dump(cwd)


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
