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

"""Tests for the full-CLI L2 perf harness (``scripts/check_l2_cli_perf.py``).

Split by what each claim actually needs to be believable:

* **Threshold, coverage and validation logic** -- constructed inputs. A
  ``sleep``-driven "regression" would test the clock, be slow, and be flaky.
* **Native-process observation and real L2 execution** -- genuine subprocesses
  (``integration``-marked). An invocation spy and a compiler-free-path
  assertion cannot be proven by a mock: a mock would confirm the harness calls
  what the test told it to call, which is the one thing never in doubt.

The negative controls are the centre of gravity here. A perf harness that only
detects "slower" rewards the worst regression available -- getting faster by
silently doing less -- so each control below states the *wrong* behaviour and
requires the harness to reject it.
"""

from __future__ import annotations

import importlib.util
import json
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

GateThreshold = harness.GateThreshold

_HAVE_CXX = shutil.which("g++") is not None
_LINUX = sys.platform.startswith("linux")
requires_toolchain = pytest.mark.skipif(
    not (_HAVE_CXX and _LINUX), reason="needs g++ on a Linux/ELF host"
)


# ── invocation classification ─────────────────────────────────────────────────
class TestInvocationClassification:
    """Version probes must never be counted as header extraction.

    Every argv below was captured from a real observed invocation on this
    fixture, not written from the product's source: the whole value of observing
    from outside is lost if the classifier is calibrated against an assumption.
    """

    @pytest.mark.parametrize(
        ("tool", "argv", "expected"),
        [
            ("castxml", "--version", "probe"),
            ("castxml", "-dumpmachine", "probe"),
            ("g++", "--version", "probe"),
            ("clang++", "--version", "probe"),
            ("g++", "-E -dM -x c++ -", "probe"),
            ("g++", "-E -x c++ -v -", "probe"),
            ("g++", "-E -dM -v /share/castxml/empty.cpp", "probe"),
            (
                "castxml",
                "--castxml-output=1 --castxml-cc-gnu /x/g++ -I /inc -o /t.xml /t.hpp",
                "header_extraction",
            ),
            (
                "clang++",
                "-I /inc -isystem /usr/include -x c++ -fsyntax-only "
                "-ferror-limit=0 -Xclang -ast-dump=json /t.hpp",
                "header_extraction",
            ),
            ("clang++", "-M -x c++ -I/inc /inc/shape.h", "include_pass"),
            ("g++", "-shared -fPIC -g -O0 -o lib.so impl.cpp", "other"),
        ],
    )
    def test_real_observed_argv_is_classified_correctly(self, tool, argv, expected):
        assert receipt_mod.classify_invocation(tool, argv) == expected

    def test_a_version_probe_is_a_probe_for_every_spied_tool(self):
        # Generalized rather than per-tool: the hazard is tool-independent, and
        # a per-tool list would silently not cover a tool added later.
        for tool in receipt_mod.SPIED_TOOLS:
            assert receipt_mod.classify_invocation(tool, "--version") == "probe"

    def test_extraction_and_probe_are_never_the_same_bucket(self):
        # The specific conflation that would break every extraction-count
        # assertion: a cold run makes three castxml calls and only one is a parse.
        castxml_calls = [
            "--version",
            "-dumpmachine",
            "--castxml-output=1 -o /t.xml /t.hpp",
        ]
        kinds = [receipt_mod.classify_invocation("castxml", a) for a in castxml_calls]
        assert kinds.count("header_extraction") == 1
        assert kinds.count("probe") == 2


# ── extraction contracts ──────────────────────────────────────────────────────
def _run(extraction_counts: dict[str, int]) -> object:
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
        native_invocations=extraction_counts,
    )


class TestExtractionContracts:
    def test_a_stored_path_with_no_extraction_passes(self):
        assert (
            harness._check_extraction(
                _run({"header_extraction": 0}), "forbidden", one_side=2
            )
            == []
        )

    def test_a_stored_path_that_extracts_fails(self):
        # The product defect this harness exists to detect, not work around.
        problems = harness._check_extraction(
            _run({"header_extraction": 2}), "forbidden", one_side=2
        )
        assert len(problems) == 1
        assert "re-extracted" in problems[0]

    def test_a_single_extraction_on_a_forbidden_path_still_fails(self):
        # Off-by-one guard: "fewer than a full side" is not "none".
        assert harness._check_extraction(
            _run({"header_extraction": 1}), "forbidden", one_side=2
        )

    def test_one_side_accepts_exactly_one_side(self):
        assert (
            harness._check_extraction(
                _run({"header_extraction": 2}), "one_side", one_side=2
            )
            == []
        )

    def test_one_side_rejects_both_sides(self):
        problems = harness._check_extraction(
            _run({"header_extraction": 4}), "one_side", one_side=2
        )
        assert problems and "re-extracted" in problems[0]

    def test_one_side_rejects_zero(self):
        # The "faster because it stopped working" direction: a stored/live
        # comparison that extracts nothing never looked at the live side.
        problems = harness._check_extraction(
            _run({"header_extraction": 0}), "one_side", one_side=2
        )
        assert problems and "never extracted" in problems[0]

    def test_both_sides_rejects_one_side(self):
        assert harness._check_extraction(
            _run({"header_extraction": 2}), "both_sides", one_side=2
        )

    def test_a_missing_observation_is_reported_not_silently_accepted(self):
        # No spy means no proof. Returning [] here would let a --no-spy run
        # claim it had verified a compiler-free path.
        problems = harness._check_extraction(_run({}), "forbidden", one_side=2)
        assert problems and "no native-invocation observation" in problems[0]

    def test_the_forbidden_contract_needs_no_calibration(self):
        # An absolute zero is checkable without knowing what one side costs.
        assert harness._check_extraction(
            _run({"header_extraction": 3}), "forbidden", one_side=None
        )

    def test_an_uncalibrated_one_side_contract_still_catches_zero(self):
        # The "faster because it stopped working" direction needs no
        # calibration, so it is always checked.
        problems = harness._check_extraction(
            _run({"header_extraction": 0}), "one_side", one_side=None
        )
        assert problems and "never extracted" in problems[0]

    def test_an_uncalibrated_one_side_contract_does_not_check_the_upper_bound(self):
        # The soundness fix: with no independent single-side calibration the
        # count bound is NOT checked, rather than checked against a number
        # derived from the thing under test. An assertion that calibrates itself
        # reduces to `observed > observed` and can never fail, which is what the
        # first version of this harness shipped.
        assert (
            harness._check_extraction(
                _run({"header_extraction": 99}), "one_side", one_side=None
            )
            == []
        )

    def test_an_uncalibrated_both_sides_contract_still_catches_zero(self):
        problems = harness._check_extraction(
            _run({"header_extraction": 0}), "both_sides", one_side=None
        )
        assert problems and "neither operand" in problems[0]

    def test_a_calibrated_failure_message_says_where_the_number_came_from(self):
        # So a reader can tell a real calibrated bound from an unchecked one.
        problems = harness._check_extraction(
            _run({"header_extraction": 4}), "one_side", one_side=2
        )
        assert problems and "calibrated from" in problems[0]

    def test_calibration_rejects_a_setup_run_that_extracted_nothing(self):
        # Returning a fabricated 1 there would be the self-calibration bug in a
        # new place.
        assert harness._one_side_extractions({"header_extraction": 0}) is None
        assert harness._one_side_extractions({}) is None
        assert harness._one_side_extractions({"header_extraction": 2}) == 2

    def test_uncalibrated_contracts_are_reported_not_hidden(self):
        steps = [
            harness.Step("a", ["x"], extraction="one_side"),
            harness.Step("b", ["x"], extraction="both_sides"),
            harness.Step("c", ["x"], extraction="forbidden"),
            harness.Step("d", ["x"], extraction="any"),
        ]
        reported = harness.uncalibrated_contracts(steps, None)
        assert {r.split()[1].rstrip(":") for r in reported} == {"a", "b"}
        assert harness.uncalibrated_contracts(steps, 2) == []

    def test_every_contract_name_has_a_stated_meaning(self):
        # So a receipt reader never sees a bare contract label they cannot
        # interpret.
        for step_contract in ("forbidden", "one_side", "both_sides", "any"):
            assert harness.EXTRACTION_EXPECTATIONS[step_contract]


# ── validation: the "got faster by doing less" controls ───────────────────────
def _l2_report(**overrides) -> dict:
    report = {
        "analysis_assurance": {"effective_depth": "headers", "depth_satisfied": True},
        "old_evidence_depth": "headers",
        "new_evidence_depth": "headers",
        "scope": {"public_headers_applied": True, "fell_back": False},
        "verdict": "BREAKING",
        "changes": [{"kind": "func_removed"}, {"kind": "type_size_changed"}],
    }
    report.update(overrides)
    return report


class TestFasterBecauseItStoppedWorking:
    """Each case is a *speedup* the harness must reject as a failure."""

    def test_a_clean_l2_report_validates(self):
        assert harness._validate_l2_reached(_l2_report(), sides=("old", "new")) == []

    def test_a_binary_only_fallback_is_rejected(self):
        problems = harness._validate_l2_reached(
            _l2_report(
                analysis_assurance={
                    "effective_depth": "binary",
                    "depth_satisfied": False,
                },
                old_evidence_depth="binary",
                new_evidence_depth="binary",
            ),
            sides=("old", "new"),
        )
        assert any("did not actually perform L2" in p for p in problems)

    def test_one_side_silently_dropping_to_binary_is_rejected(self):
        # The asymmetric case a single aggregate depth field would hide.
        problems = harness._validate_l2_reached(
            _l2_report(old_evidence_depth="binary"), sides=("old", "new")
        )
        assert any("old_evidence_depth" in p for p in problems)

    def test_lost_public_scoping_is_rejected(self):
        problems = harness._validate_l2_reached(
            _l2_report(scope={"public_headers_applied": False, "fell_back": False}),
            sides=("old",),
        )
        assert any("public scoping" in p for p in problems)

    def test_degraded_public_scoping_is_rejected(self):
        problems = harness._validate_l2_reached(
            _l2_report(scope={"public_headers_applied": True, "fell_back": True}),
            sides=("old",),
        )
        assert any("degraded" in p for p in problems)

    def test_a_run_that_found_nothing_is_rejected(self):
        # A detector that stopped detecting is the cheapest possible "speedup".
        problems = harness._validate_break_findings(
            _l2_report(verdict="COMPATIBLE", changes=[])
        )
        assert any("removal-family" in p for p in problems)
        assert any("layout-family" in p for p in problems)

    def test_losing_only_the_layout_finding_is_still_rejected(self):
        problems = harness._validate_break_findings(
            _l2_report(changes=[{"kind": "func_removed"}])
        )
        assert len(problems) == 1
        assert "layout-family" in problems[0]

    def test_a_breaking_verdict_alone_is_not_enough(self):
        # Why families exist instead of a verdict check: an unrelated finding can
        # carry the verdict while both real findings are gone.
        problems = harness._validate_break_findings(
            _l2_report(changes=[{"kind": "private_header_leak"}])
        )
        assert len(problems) == 2

    @pytest.mark.parametrize(
        "kind", ["func_removed", "func_removed_elf_only", "public_surface_shrank"]
    )
    def test_any_removal_spelling_satisfies_the_removal_family(self, kind):
        problems = harness._validate_break_findings(
            _l2_report(changes=[{"kind": kind}, {"kind": "type_size_changed"}])
        )
        assert problems == []

    def test_a_false_positive_on_the_unchanged_control_is_rejected(self):
        problems = harness._validate_unchanged(
            _l2_report(verdict="COMPATIBLE", changes=[{"kind": "func_removed"}])
        )
        assert any("false positive" in p for p in problems)

    def test_the_unchanged_control_passes_with_no_break_findings(self):
        assert (
            harness._validate_unchanged(
                _l2_report(
                    verdict="COMPATIBLE", changes=[{"kind": "private_header_leak"}]
                )
            )
            == []
        )


class TestNoBaselineAuditSemantics:
    """``--no-baseline`` must audit, never manufacture a compatibility verdict."""

    def _audit(self, **overrides) -> dict:
        report = {
            "no_baseline": True,
            "verdict": None,
            "run_outcome": {"compatibility": None},
            "audit_report_schema_version": "1.5",
        }
        report.update(overrides)
        return report

    def test_a_real_audit_report_validates(self):
        assert harness._validate_audit(self._audit()) == []

    def test_a_fabricated_verdict_is_rejected(self):
        problems = harness._validate_audit(self._audit(verdict="COMPATIBLE"))
        assert any("must not" in p and "verdict" in p for p in problems)

    def test_a_fabricated_compatibility_outcome_is_rejected(self):
        problems = harness._validate_audit(
            self._audit(run_outcome={"compatibility": "COMPATIBLE"})
        )
        assert any("run_outcome.compatibility" in p for p in problems)

    def test_a_non_audit_report_is_rejected(self):
        problems = harness._validate_audit(
            self._audit(audit_report_schema_version=None)
        )
        assert any("not an audit report" in p for p in problems)


# ── gating and coverage logic ─────────────────────────────────────────────────
def _scenario(ident: str, **steps: float) -> dict:
    return {
        "id": ident,
        "status": "ok",
        "steps": [
            {"name": name, "scope": "full_cli", "gated": True, "wall_seconds": value}
            for name, value in steps.items()
        ],
    }


class TestGating:
    def test_only_full_cli_steps_are_gated(self):
        # The nested startup/resolution windows live inside the gated number;
        # gating them too would charge one slowdown three times.
        scenario = {
            "id": "s",
            "status": "ok",
            "steps": [
                {
                    "name": "startup",
                    "scope": "startup_only",
                    "gated": False,
                    "wall_seconds": 0.6,
                },
                {
                    "name": "compare",
                    "scope": "full_cli",
                    "gated": True,
                    "wall_seconds": 1.2,
                },
            ],
        }
        assert harness.gated_points([scenario]) == {("s", "compare"): 1.2}

    def test_a_regression_beyond_both_floors_is_flagged(self):
        failures = harness.check_regressions(
            {("s", "compare"): 3.0},
            {("s", "compare"): 1.0},
            GateThreshold(0.3, 0.5),
        )
        assert len(failures) == 1
        assert "+200%" in failures[0]

    def test_the_absolute_floor_absorbs_startup_jitter(self):
        # 1.0 -> 1.4s is +40%, past the relative tolerance, but inside the 0.5s
        # floor that exists because every full-CLI sample carries ~0.5s of
        # interpreter startup.
        assert (
            harness.check_regressions(
                {("s", "compare"): 1.4},
                {("s", "compare"): 1.0},
                GateThreshold(0.3, 0.5),
            )
            == []
        )

    def test_a_large_regression_still_fails_despite_the_floor(self):
        assert harness.check_regressions(
            {("s", "compare"): 10.0}, {("s", "compare"): 1.0}, GateThreshold(0.3, 0.5)
        )

    def test_a_missing_baseline_point_is_not_a_failure(self):
        assert (
            harness.check_regressions(
                {("new", "compare"): 9.0},
                {("old", "compare"): 1.0},
                GateThreshold(0.3, 0.5),
            )
            == []
        )

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.0, -1.0, None])
    def test_a_non_gateable_baseline_is_skipped_not_silently_passed(self, bad):
        # Paired with the coverage check below: skipping here is only safe
        # because "nothing was gated" is itself a failure.
        assert (
            harness.check_regressions(
                {("s", "compare"): 99.0},
                {("s", "compare"): bad},
                GateThreshold(0.3, 0.5),
            )
            == []
        )

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.0, -1.0])
    def test_a_non_gateable_measurement_is_not_collected_as_a_point(self, bad):
        assert harness.gated_points([_scenario("s", compare=bad)]) == {}

    def test_the_failure_message_names_the_threshold_that_judged_it(self):
        failures = harness.check_regressions(
            {("s", "compare"): 5.0},
            {("s", "compare"): 1.0},
            GateThreshold(0.25, 0.1, source="explicit"),
        )
        assert "tolerance=0.25" in failures[0]
        assert "source=explicit" in failures[0]


class TestRequiredCoverage:
    def test_a_full_run_claims_coverage(self):
        measured = [_scenario(f"{shape}[p]") for shape in harness.REQUIRED_PR_SHAPES]
        assert (
            harness.required_coverage_failures(measured, harness.REQUIRED_PR_SHAPES)
            == []
        )

    def test_a_missing_required_shape_fails(self):
        # "Не называй чистым pass run, где обязательный case не измерен": five
        # green scenarios are not a pass when the sixth never ran.
        measured = [
            _scenario(f"{shape}[p]") for shape in harness.REQUIRED_PR_SHAPES[:-1]
        ]
        failures = harness.required_coverage_failures(
            measured, harness.REQUIRED_PR_SHAPES
        )
        assert len(failures) == 1
        assert harness.REQUIRED_PR_SHAPES[-1] in failures[0]

    def test_a_failed_scenario_does_not_count_as_coverage(self):
        measured = [_scenario(f"{shape}[p]") for shape in harness.REQUIRED_PR_SHAPES]
        measured[0]["status"] = "failed"
        assert harness.required_coverage_failures(measured, harness.REQUIRED_PR_SHAPES)

    def test_coverage_is_matched_by_shape_not_by_exact_profile(self):
        measured = [
            _scenario(f"{shape}[templates-h8-l2-break-distinct]")
            for shape in harness.REQUIRED_PR_SHAPES
        ]
        assert (
            harness.required_coverage_failures(measured, harness.REQUIRED_PR_SHAPES)
            == []
        )

    def test_the_pr_suite_really_contains_every_required_shape(self):
        # Guards the registry against the test above passing vacuously: the
        # constructed ids prove the predicate, this proves the real suite.
        shapes = {s.id.split("[", 1)[0] for s in harness.pr_suite()}
        assert set(harness.REQUIRED_PR_SHAPES) <= shapes


class TestCacheServiceClassification:
    """ "Warm" must be proven by counters, never by run order."""

    def _runs(self, cold: int, warm: int) -> dict:
        return {
            "cold": [_run({"header_extraction": cold})],
            "warm": [_run({"header_extraction": warm})],
        }

    def test_a_fully_served_warm_run_is_full(self):
        assert harness._classify_cache_service(self._runs(2, 0)) == "full"

    def test_a_partially_served_warm_run_is_partial(self):
        assert harness._classify_cache_service(self._runs(3, 1)) == "partial"

    def test_an_unserved_second_run_is_not_warm(self):
        # The claim this replaces: "it ran second, so it was warm". A second run
        # served by nothing extracts just as much, and on a small fixture the
        # wall times are indistinguishable.
        assert harness._classify_cache_service(self._runs(2, 2)) == "none"

    def test_a_slower_second_run_is_also_not_warm(self):
        assert harness._classify_cache_service(self._runs(2, 3)) == "none"

    def test_missing_runs_report_unknown_not_a_cache_claim(self):
        assert harness._classify_cache_service({"cold": [_run({})]}) == "unknown"


class TestCompareArgvGrammar:
    """The harness must build the CLI's *current* export grammar.

    Added after the grammar changed under this branch (ADR-068 slices 7m/7n
    replaced `--format F -o PATH` with a repeatable `-o FORMAT=DESTINATION`) and
    every scenario silently built invocations the CLI no longer accepts. A
    logic-level guard here fails in milliseconds; without it the first signal is
    a real subprocess exiting 64 minutes into a lane.
    """

    def test_a_single_export_uses_the_format_equals_destination_form(self):
        argv = harness._compare_argv(
            "old.json", "new.so", exports={"json": Path("r.json")}
        )
        assert "-o" in argv
        assert "json=r.json" in argv
        assert "--format" not in argv

    def test_two_exports_produce_two_o_flags_in_one_invocation(self):
        # The whole point of the two-format scenario: one comparison, two
        # artifacts. Two invocations would measure something else.
        argv = harness._compare_argv(
            "old.json",
            "new.so",
            exports={"json": Path("r.json"), "markdown": Path("r.md")},
        )
        assert argv.count("-o") == 2
        assert "json=r.json" in argv and "markdown=r.md" in argv

    def test_depth_headers_is_always_requested(self):
        argv = harness._compare_argv("o", "n", exports={"json": Path("r.json")})
        assert argv[argv.index("--depth") + 1] == "headers"

    def test_no_baseline_omits_the_old_operand(self):
        argv = harness._compare_argv(
            None, "new.so", exports={"json": Path("r.json")}, no_baseline=True
        )
        assert "--no-baseline" in argv
        assert argv.count("new.so") == 1

    def test_a_baseline_comparison_without_an_old_operand_is_a_usage_error(self):
        # Raise, never silently build a one-operand compare that would be
        # interpreted as something else.
        with pytest.raises(ValueError):
            harness._compare_argv(None, "new.so", exports={"json": Path("r.json")})


class TestDryRunArgv:
    def test_the_output_flag_is_stripped(self):
        # compare rejects --dry-run together with -o (exit 64); the first version
        # of this harness tripped exactly that.
        argv = harness._dry_run_argv(
            ["abicheck", "compare", "a", "b", "-o", "/out.json"]
        )
        assert "-o" not in argv and "/out.json" not in argv
        assert argv[-1] == "--dry-run"

    def test_the_long_spelling_is_stripped_too(self):
        argv = harness._dry_run_argv(
            ["compare", "a", "--output", "/out.json", "--depth", "headers"]
        )
        assert "/out.json" not in argv
        assert ["--depth", "headers"] == argv[-3:-1]

    def test_other_arguments_survive_unchanged(self):
        argv = harness._dry_run_argv(["compare", "a", "b", "--header", "old=x.h"])
        assert argv == ["compare", "a", "b", "--header", "old=x.h", "--dry-run"]


# ── real subprocess / real compiler ───────────────────────────────────────────
@pytest.mark.integration
class TestRealL2Execution:
    """Claims that cannot be proven by a mock, proven by real processes."""

    @requires_toolchain
    def test_the_fixture_builds_and_exposes_the_expected_declarations(self, tmp_path):
        built = fixtures.build(fixtures.FixtureSpec(), tmp_path)
        assert built.new[0].so.exists()
        assert built.new[0].headers
        text = built.new[0].headers[0].read_text()
        # The break is in the header, not only in the source.
        assert "shape_count" not in text
        assert "shape_count" in built.old[0].headers[0].read_text()

    @requires_toolchain
    def test_a_stored_stored_comparison_runs_no_compiler_at_all(self, tmp_path):
        # The central observational claim of the stored-operand scenarios, made
        # against real processes: a mock could only confirm the harness passed
        # the arguments the test handed it.
        result = harness.run_scenario(
            harness.scenario_compare_stored_stored(fixtures.FixtureSpec()),
            repeat=1,
            timeout=900,
            rss_interval=0.05,
        )
        assert result["status"] == "ok", result["validation_problems"]
        compare = next(s for s in result["steps"] if s["name"] == "compare")
        assert compare["native_invocations"]["header_extraction"] == 0
        assert compare["native_invocations"]["include_pass"] == 0
        # And it really did the L2 comparison despite running no compiler.
        assert result["validation"] == "passed"

    @requires_toolchain
    def test_a_stored_live_comparison_extracts_exactly_one_side(self, tmp_path):
        result = harness.run_scenario(
            harness.scenario_compare_stored_live(fixtures.FixtureSpec()),
            repeat=1,
            timeout=900,
            rss_interval=0.05,
        )
        assert result["status"] == "ok", result["validation_problems"]
        compare = next(s for s in result["steps"] if s["name"] == "compare")
        live_only = compare["native_invocations"]["header_extraction"]
        setup = next(s for s in result["steps"] if s["name"] == "prep_dump_old")
        one_side = setup["native_invocations"]["header_extraction"]
        assert live_only == one_side, (
            "a stored/live comparison must cost one side's extraction, not two"
        )
        # And the harness calibrated from that same setup dump rather than from
        # the measured step itself, so its own assertion was not self-referential.
        assert result["one_side_extraction_calibration"] == one_side
        assert result["uncalibrated_contracts"] == []

    @requires_toolchain
    def test_an_unexpected_extraction_on_a_forbidden_path_fails_the_scenario(
        self, tmp_path
    ):
        # Negative control for the above, executed for real: a live comparison
        # declared compiler-free must be rejected, not measured.
        scenario = harness.scenario_compare_live_live(fixtures.FixtureSpec())
        real_steps = scenario.steps

        def steps(fixture, work):
            out = [s for s in real_steps(fixture, work) if s.scope == "full_cli"]
            for step in out:
                step.extraction = "forbidden"
            return out

        scenario.steps = steps
        result = harness.run_scenario(
            scenario, repeat=1, timeout=900, rss_interval=0.05
        )
        assert result["status"] == "failed"
        assert any("re-extracted" in p for p in result["validation_problems"])

    @requires_toolchain
    def test_a_binary_only_run_is_rejected_even_though_it_is_faster(self, tmp_path):
        # The headline negative control, end to end: strip the headers, get a
        # genuinely faster run, and require the harness to fail it.
        spec = fixtures.FixtureSpec()

        def steps(fixture, work):
            out = work / "binary_only.json"
            return [
                harness.Step(
                    "compare",
                    harness._compare_argv(
                        str(fixture.old[0].so),
                        str(fixture.new[0].so),
                        exports={"json": out},
                    ),
                    extraction="any",
                    ok_exit_codes=(0, 2, 4),
                    output=out,
                )
            ]

        scenario = harness.Scenario(
            id="binary_only_control",
            description="headers stripped: faster, and wrong",
            spec=spec,
            prepare=None,
            steps=steps,
            validate=lambda work, runs: harness._validate_l2_reached(
                harness._load_report(work / "binary_only.json"), sides=("old", "new")
            ),
        )
        result = harness.run_scenario(
            scenario, repeat=1, timeout=900, rss_interval=0.05
        )
        assert result["status"] == "failed"
        assert any(
            "did not actually perform L2" in p for p in result["validation_problems"]
        )

    @requires_toolchain
    def test_the_whole_pr_suite_passes_and_writes_a_valid_receipt(self, tmp_path):
        out = tmp_path / "receipt.json"
        rc = harness.main(["--repeat", "1", "--json-out", str(out)])
        assert rc == 0
        receipt = json.loads(out.read_text())
        assert receipt["schema"] == receipt_mod.RECEIPT_SCHEMA
        assert receipt["identity"]["product_sha"]
        assert receipt["effective_thresholds"]["wall_seconds"]["tolerance"]
        measured = {s["id"].split("[", 1)[0] for s in receipt["scenarios"]}
        assert set(harness.REQUIRED_PR_SHAPES) <= measured
        # Every gated step carries a real, finite, positive duration.
        for scenario in receipt["scenarios"]:
            for step in scenario["steps"]:
                if step.get("gated"):
                    assert step["wall_seconds"] > 0
                    assert step["additive"] is False

    @requires_toolchain
    def test_the_no_spy_lane_runs_and_claims_no_invocation_coverage(self, capsys):
        # Regression: --no-spy crashed with FileNotFoundError, because the
        # scenario runner reset the spy's log unconditionally while --no-spy had
        # never created the shim directory. Found by the very measurement the
        # flag exists to enable (this harness's own instrumentation overhead),
        # which could not be taken at all.
        #
        # Two claims, because the flag has two obligations: the lane must run,
        # and it must NOT let a run with no observation read as having verified
        # a compiler-free path.
        rc = harness.main(["--repeat", "1", "--no-spy"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "COVERAGE NOT CLAIMED (native invocations)" in out

    @requires_toolchain
    def test_no_spy_records_no_invocation_counts_at_all(self):
        result = harness.run_scenario(
            harness.scenario_compare_stored_stored(fixtures.FixtureSpec()),
            repeat=1,
            timeout=900,
            rss_interval=0.05,
            keep_spy=False,
        )
        assert result["status"] == "ok", result["validation_problems"]
        compare = next(s for s in result["steps"] if s["name"] == "compare")
        # Empty, not a zero-filled mapping: "we did not look" must be
        # distinguishable from "we looked and saw none", which is the whole
        # basis of the compiler-free claim.
        assert compare["native_invocations"] == {}

    @requires_toolchain
    def test_a_doctored_faster_baseline_makes_the_gate_fail(self, tmp_path):
        # Proof the gate is wired, not merely present: measure, halve the
        # baseline, and require a failure.
        base = tmp_path / "base.json"
        assert (
            harness.main(
                [
                    "--repeat",
                    "1",
                    "--scenario",
                    "stored_stored",
                    "--json-out",
                    str(base),
                ]
            )
            == 0
        )
        doctored = json.loads(base.read_text())
        for scenario in doctored["scenarios"]:
            for step in scenario["steps"]:
                if step.get("gated"):
                    step["wall_seconds"] = step["wall_seconds"] / 10.0
        faster = tmp_path / "faster.json"
        faster.write_text(json.dumps(doctored))
        assert (
            harness.main(
                [
                    "--repeat",
                    "1",
                    "--scenario",
                    "stored_stored",
                    "--baseline",
                    str(faster),
                    "--regress-min-delta-seconds",
                    "0.05",
                ]
            )
            == 1
        )

    @requires_toolchain
    def test_a_baseline_sharing_no_point_fails_rather_than_reporting_ok(self, tmp_path):
        empty = tmp_path / "empty.json"
        empty.write_text(json.dumps({"scenarios": []}))
        assert (
            harness.main(
                [
                    "--repeat",
                    "1",
                    "--scenario",
                    "stored_stored",
                    "--baseline",
                    str(empty),
                ]
            )
            == 1
        )

    @requires_toolchain
    def test_an_unreadable_baseline_fails_cleanly(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert (
            harness.main(
                ["--repeat", "1", "--scenario", "stored_stored", "--baseline", str(bad)]
            )
            == 1
        )


@pytest.mark.integration
class TestRequireToolchain:
    def test_an_unsuitable_host_skips_by_default(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "platform", "darwin")
        assert harness.main([]) == 0
        assert "SKIP" in capsys.readouterr().out

    def test_require_toolchain_turns_that_skip_into_a_failure(
        self, monkeypatch, capsys
    ):
        # A CI job claiming to cover this backend must not report a pass over a
        # run that measured nothing.
        monkeypatch.setattr(sys, "platform", "darwin")
        assert harness.main(["--require-toolchain"]) == 1
        assert "FAIL" in capsys.readouterr().out

    def test_a_missing_compiler_is_also_covered(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(fixtures, "compiler_available", lambda cxx="g++": False)
        monkeypatch.setattr(
            harness.fixtures, "compiler_available", lambda cxx="g++": False
        )
        assert harness.main(["--require-toolchain"]) == 1
        assert "no g++" in capsys.readouterr().out
