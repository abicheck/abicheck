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

"""Cross-flow exit-code integrity (C7).

`policy.exit_decision_precedence` is the one owner of every `compare` exit
decision -- the single-pair path, the directory/package release, a supplied
consumer's scoped code, and `check-target`'s gate-mode composition. These
tests drive that owner's interface directly and prove, through the public
CLI, that the process status and the persisted `exit` block are the same
number: a frontend translates the decision, it never re-derives it.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from click.testing import CliRunner

from abicheck.checker import compare
from abicheck.checker_policy import Verdict
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.policy.effective_gate import EffectiveGate
from abicheck.policy.exit_decision import resolve_exit_decision
from abicheck.policy.exit_decision_precedence import (
    GATE_MODES,
    compare_assurance_notice_base_exit,
    resolve_compare_exit_decision_with_abort_axes,
    resolve_gate_mode_exit_code,
    resolve_release_exit_decision_with_assurance,
    resolve_scoped_exit_code,
)
from abicheck.policy.release_assurance import (
    MemberAssurance,
    ReleaseAssuranceDecision,
)
from abicheck.policy.severity import SeverityConfig, SeverityLevel, legacy_exit_code
from abicheck.serialization import snapshot_to_json


def _fn(name: str) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type="void",
        params=[],
        visibility=Visibility.PUBLIC,
    )


@pytest.mark.parametrize(
    ("verdict", "code"),
    [
        (Verdict.BREAKING, 4),
        (Verdict.API_BREAK, 2),
        (Verdict.COMPATIBLE_WITH_RISK, 0),
        (Verdict.COMPATIBLE, 0),
        (Verdict.NO_CHANGE, 0),
    ],
)
def test_legacy_exit_code_contract(verdict: Verdict, code: int) -> None:
    assert legacy_exit_code(verdict) == code


def test_legacy_exit_code_total_over_all_verdicts() -> None:
    # Every Verdict must map (no KeyError / silent 0 for an unmapped member).
    expected = {
        Verdict.BREAKING: 4,
        Verdict.API_BREAK: 2,
        Verdict.COMPATIBLE_WITH_RISK: 0,
        Verdict.COMPATIBLE: 0,
        Verdict.NO_CHANGE: 0,
    }
    for v in Verdict:
        assert legacy_exit_code(v) == expected[v]


def _exit_code_of(callable_, *args, **kwargs) -> int:
    """Run a function that may sys.exit; return the code (0 if it returns)."""
    try:
        callable_(*args, **kwargs)
    except SystemExit as exc:  # noqa: PT012 — capturing the exit code is the point
        return int(exc.code or 0)
    return 0


def _release_code(*args: Any, **kwargs: Any) -> int:
    return resolve_release_exit_decision_with_assurance(*args, **kwargs).code


# ── single-pair path, through the public CLI ─────────────────────────────────

_OLD = ("a", "b")
#: NEW function sets and the verdict each produces against `_OLD`.
_PAIR_CASES = {
    "removal": (("a",), Verdict.BREAKING),
    "addition": (("a", "b", "c"), Verdict.COMPATIBLE),
    "identical": (("a", "b"), Verdict.NO_CHANGE),
}


def _snapshot(path: Path, version: str, names: tuple[str, ...]) -> Path:
    path.write_text(
        snapshot_to_json(
            AbiSnapshot(
                library="libfoo.so.1",
                version=version,
                functions=[_fn(n) for n in names],
            )
        ),
        encoding="utf-8",
    )
    return path


def _run_compare(tmp_path: Path, new_names: tuple[str, ...], *extra: str):
    old = _snapshot(tmp_path / "old.json", "1", _OLD)
    new = _snapshot(tmp_path / "new.json", "2", new_names)
    report = tmp_path / "report.json"
    result = CliRunner().invoke(
        main, ["compare", str(old), str(new), "-o", f"json={report}", *extra]
    )
    return result, json.loads(report.read_text(encoding="utf-8"))


class TestPairExitThroughCli:
    """`abicheck compare OLD NEW`: the process status, the persisted `exit`
    block and the policy owner's own decision are one number."""

    @pytest.mark.parametrize("case", sorted(_PAIR_CASES))
    @pytest.mark.parametrize("preset", [None, "default", "info-only"])
    def test_process_exit_is_the_report_exit_block(
        self, tmp_path: Path, case: str, preset: str | None
    ) -> None:
        new_names, verdict = _PAIR_CASES[case]
        extra = ("--severity-preset", str(preset)) if preset else ()
        cli, report = _run_compare(tmp_path, new_names, *extra)
        assert report["verdict"] == verdict.value
        assert cli.exit_code == report["exit"]["code"]
        if preset is None:
            # The legacy scheme is the verdict mapping, independently stated.
            assert cli.exit_code == legacy_exit_code(verdict)
        elif preset == "info-only":
            # Nothing is error-level under info-only: no finding can gate.
            assert cli.exit_code == 0

    @pytest.mark.parametrize("case", sorted(_PAIR_CASES))
    def test_process_exit_is_the_policy_owners_decision(
        self, tmp_path: Path, case: str
    ) -> None:
        new_names, _verdict = _PAIR_CASES[case]
        cli, _report = _run_compare(tmp_path, new_names)
        old = AbiSnapshot(
            library="libfoo.so.1", version="1", functions=[_fn(n) for n in _OLD]
        )
        new = AbiSnapshot(
            library="libfoo.so.1", version="2", functions=[_fn(n) for n in new_names]
        )
        result = compare(old, new, scope_to_public_surface=False)
        decision = resolve_compare_exit_decision_with_abort_axes(
            result, EffectiveGate.from_severity(None)
        )
        assert cli.exit_code == decision.code

    @pytest.mark.parametrize(
        ("required", "scoped_code"),
        [("a", 0), ("b", 4)],
        ids=["unaffected", "affected"],
    )
    def test_a_consumer_never_replaces_the_full_library_exit(
        self, tmp_path: Path, required: str, scoped_code: int
    ) -> None:
        """Workstream D-S1: the scoped code is reported beside the
        full-library decision. An unaffected consumer of a breaking library
        still exits 4; an affected one reports its own 4 without narrowing."""
        cli, report = _run_compare(
            tmp_path,
            _PAIR_CASES["removal"][0],
            "--set",
            f'consumers.required_symbols=["{required}"]',
        )
        assert report["consumer_scope"]["exit_code"] == scoped_code
        assert cli.exit_code == report["exit"]["code"] == 4


class TestScopedExitCode:
    """`resolve_scoped_exit_code`: a consumer's own code, under the run's scheme."""

    @pytest.mark.parametrize("verdict", list(Verdict))
    def test_legacy_scheme_is_the_verdict_mapping(self, verdict: Verdict) -> None:
        result = compare(
            AbiSnapshot(library="l", version="1"), AbiSnapshot(library="l", version="2")
        )
        code = resolve_scoped_exit_code(verdict, [], result, None, "strict_abi", None)
        assert code == legacy_exit_code(verdict)

    @pytest.mark.parametrize("missing", [False, True])
    @pytest.mark.parametrize("abi_level", [SeverityLevel.ERROR, SeverityLevel.WARNING])
    def test_severity_scheme_floors_only_a_missing_error_level_contract(
        self, missing: bool, abi_level: SeverityLevel
    ) -> None:
        # No relevant change, so only the missing-contract floor can raise it:
        # 4 exactly when the contract is missing and ABI breaks are errors.
        result = compare(
            AbiSnapshot(library="l", version="1"), AbiSnapshot(library="l", version="2")
        )
        severity = SeverityConfig(abi_breaking=abi_level)
        code = resolve_scoped_exit_code(
            Verdict.BREAKING,
            [],
            result,
            severity,
            "strict_abi",
            None,
            has_missing_contract=missing,
        )
        assert code == (4 if missing and abi_level is SeverityLevel.ERROR else 0)


@pytest.mark.parametrize(
    ("compat", "coverage", "assurance"),
    list(itertools.product((0, 2, 4), (0, 1), (0, 1))),
)
def test_pair_assurance_notice_base_is_the_exit_without_assurance(
    compat: int, coverage: int, assurance: int
) -> None:
    """The pair's assurance notice is worded against the exit the run would
    have had with no assurance axis -- the ordinary fold re-run without it."""
    decision = resolve_exit_decision(
        compatibility_contribution=compat,
        contract_coverage_contribution=coverage,
        analysis_assurance_contribution=assurance,
    )
    oracle = resolve_exit_decision(
        compatibility_contribution=compat, contract_coverage_contribution=coverage
    ).code
    assert compare_assurance_notice_base_exit(decision) == oracle


# ── directory/package release ────────────────────────────────────────────────


@pytest.mark.parametrize("worst", ["BREAKING", "API_BREAK", "COMPATIBLE", "NO_CHANGE"])
def test_release_exit_is_the_verdict_mapping(worst: str) -> None:
    assert _release_code(worst, False, []) == legacy_exit_code(Verdict[worst])


@pytest.mark.parametrize("case", sorted(_PAIR_CASES))
def test_compare_and_release_agree_for_each_verdict(tmp_path: Path, case: str) -> None:
    # The cross-flow guarantee: identical verdict → identical exit code.
    new_names, verdict = _PAIR_CASES[case]
    cli, _report = _run_compare(tmp_path, new_names)
    assert cli.exit_code == _release_code(verdict.name, False, [])


_RELEASE_MATRIX = list(
    itertools.product(
        ["BREAKING", "API_BREAK", "COMPATIBLE", "NO_CHANGE", "ERROR", "not_comparable"],
        [False, True],  # fail_on_removed (with one proven removal)
        [None, 0, 2, 4],  # severity_exit_code (None = legacy scheme)
        [0, 1],  # contract-coverage contribution
    )
)


@pytest.mark.parametrize(
    ("worst", "fail_on_removed", "severity", "coverage"), _RELEASE_MATRIX
)
def test_release_process_exit_is_the_policy_owners_decision(
    worst: str, fail_on_removed: bool, severity: int | None, coverage: int
) -> None:
    """`_exit_compare_release` translates the one release decision to a
    process status, over the whole reachable input matrix -- never a second
    precedence ladder."""
    from abicheck.frontends.cli.runtime import _exit_compare_release

    kwargs: dict[str, Any] = {"contract_coverage_exit_contribution": coverage}
    args = (worst, fail_on_removed, ["libgone"], severity)
    assert _exit_code_of(_exit_compare_release, *args, **kwargs) == _release_code(
        *args, **kwargs
    )


class TestReleaseContractCoverageFold:
    """CLI-audit P1 (release/package contract parity): the release's own
    aggregated contract-coverage floor (0/1) obeys the same "raises a clean
    0, never lowers a real 2/4/8" rule single-pair `compare` applies -- and
    neither masks nor is masked by the removed-library exit 8."""

    @pytest.mark.parametrize(
        ("worst", "fail_on_removed", "severity", "expected"),
        [
            ("NO_CHANGE", False, None, 1),
            ("BREAKING", False, None, 4),
            ("NO_CHANGE", True, None, 8),
            ("ERROR", False, None, 4),
            ("COMPATIBLE", False, 0, 1),
            ("COMPATIBLE", True, 0, 8),
        ],
    )
    def test_coverage_floor(
        self, worst: str, fail_on_removed: bool, severity: int | None, expected: int
    ) -> None:
        code = _release_code(
            worst,
            fail_on_removed,
            ["removed_lib"],
            severity,
            contract_coverage_exit_contribution=1,
        )
        assert code == expected

    @pytest.mark.parametrize(
        "worst", ["BREAKING", "API_BREAK", "COMPATIBLE", "NO_CHANGE"]
    )
    def test_zero_contribution_is_a_true_no_op(self, worst: str) -> None:
        code = _release_code(worst, False, [], contract_coverage_exit_contribution=0)
        assert code == legacy_exit_code(Verdict[worst])


class TestReleaseAssuranceDecisionInput:
    """`compare --bundle-facts` carries no per-member rows, only a decided
    assurance axis; the resolver must still fold it."""

    @staticmethod
    def _decision(require_complete: bool, status: str) -> ReleaseAssuranceDecision:
        return ReleaseAssuranceDecision(
            members=(MemberAssurance(name="libfoo", status=status),),
            require_complete=require_complete,
            status=status,
            exit_contribution=int(require_complete and status != "complete"),
        )

    @pytest.mark.parametrize("require_complete", [False, True])
    @pytest.mark.parametrize("status", ["complete", "partial"])
    def test_rowless_release_folds_the_assurance_axis(
        self, require_complete: bool, status: str
    ) -> None:
        decision = self._decision(require_complete, status)
        got = resolve_release_exit_decision_with_assurance(
            "NO_CHANGE", False, [], assurance_decision=decision
        )
        assert got.analysis_assurance_contribution == decision.exit_contribution
        assert got.code == decision.exit_contribution

    @pytest.mark.parametrize(("worst", "base"), [("NO_CHANGE", 0), ("BREAKING", 4)])
    def test_the_process_exit_words_its_notice_against_the_other_axes(
        self, capsys: pytest.CaptureFixture[str], worst: str, base: int
    ) -> None:
        """The notice is worded against every axis but assurance: "floored to
        1" on a clean run, "which stands" beside a real exit 4."""
        from abicheck.frontends.cli.runtime import _exit_compare_release
        from abicheck.policy.release_assurance import release_assurance_diagnostic

        decision = self._decision(True, "partial")
        code = _exit_code_of(
            _exit_compare_release, worst, False, [], assurance_decision=decision
        )
        assert code == max(base, 1)
        expected = release_assurance_diagnostic(decision, base_exit=base)
        assert expected is not None
        assert expected in capsys.readouterr().err

    def test_member_rows_win_over_the_synthesized_ones(self) -> None:
        # Real rows say the member is complete, so no floor applies even
        # though the decision object alone would floor it.
        got = resolve_release_exit_decision_with_assurance(
            "NO_CHANGE",
            False,
            [],
            library_results=[
                {
                    "library": "libfoo",
                    "verdict": "NO_CHANGE",
                    "analysis_assurance_status": "complete",
                }
            ],
            assurance_decision=self._decision(True, "partial"),
        )
        assert got.code == 0


class TestReleaseResolverEdgeCases:
    """Shapes the collapsed `worst_verdict` scalar alone cannot decide."""

    def test_a_breaking_library_ties_with_an_unrelated_error_library(self) -> None:
        got = resolve_release_exit_decision_with_assurance(
            "ERROR",
            False,
            [],
            library_results=[{"verdict": "BREAKING"}, {"verdict": "ERROR"}],
        )
        assert got.code == 4
        assert {"compatibility_gate", "operational_error"} <= {
            r.value for r in got.reasons
        }

    def test_a_bundle_only_break_with_every_library_unchanged(self) -> None:
        got = resolve_release_exit_decision_with_assurance(
            "BREAKING",
            False,
            [],
            library_results=[{"verdict": "NO_CHANGE"}, {"verdict": "NO_CHANGE"}],
        )
        assert got.code == 4

    def test_not_comparable_dominates_and_keeps_the_operational_error(self) -> None:
        got = resolve_release_exit_decision_with_assurance(
            "not_comparable",
            False,
            [],
            library_results=[{"verdict": "not_comparable"}, {"verdict": "ERROR"}],
        )
        assert got.code == 16
        assert got.operational_error_contribution == 4

    def test_a_malformed_non_dict_library_result_is_skipped_not_raised(self) -> None:
        got = resolve_release_exit_decision_with_assurance(
            "BREAKING",
            False,
            [],
            library_results=["not-a-dict", {"verdict": "BREAKING"}],  # type: ignore[list-item]
        )
        assert got.code == 4

    def test_release_global_break_is_preserved_under_an_unrelated_error(self) -> None:
        got = resolve_release_exit_decision_with_assurance(
            "ERROR",
            False,
            [],
            library_results=[{"verdict": "NO_CHANGE"}, {"verdict": "ERROR"}],
            release_global_verdict="BREAKING",
        )
        assert got.code == 4
        assert got.compatibility_contribution == 4
        assert got.operational_error_contribution == 4
        assert "compatibility_gate" in got.reasons
        assert "operational_error" in got.reasons


# ── check-target gate mode ───────────────────────────────────────────────────

#: ADR-047 §7 stated as a table: operational errors fail every mode; only
#: `local` passes a real exit through.
_GATE_MODE_ORACLE = {
    ("local", False): lambda real: real,
    ("deferred", False): lambda real: 0,
    ("advisory", False): lambda real: 0,
    ("local", True): lambda real: 1,
    ("deferred", True): lambda real: 1,
    ("advisory", True): lambda real: 1,
}


@pytest.mark.parametrize(
    ("gate_mode", "operational", "real"),
    list(itertools.product(GATE_MODES, (False, True), (0, 1, 2, 4, 7, 8, 16))),
)
def test_gate_mode_exit_code(gate_mode: str, operational: bool, real: int) -> None:
    expected = _GATE_MODE_ORACLE[(gate_mode, operational)](real)
    got = resolve_gate_mode_exit_code(
        gate_mode, real_exit_code=real, operational_error=operational
    )
    assert got == expected


def test_gate_mode_rejects_an_unknown_mode() -> None:
    with pytest.raises(ValueError, match="gate_mode must be one of"):
        resolve_gate_mode_exit_code("bogus", real_exit_code=0, operational_error=False)


def _release_global_verdict(bundle: Any, matrix: Any) -> str:
    from abicheck.policy.release_outcome import (
        fold_release_outcome,
        release_global_verdicts,
    )

    return fold_release_outcome(
        (), release_global_verdicts(bundle, matrix)
    ).global_verdict


class TestReleaseGlobalVerdict:
    """`policy.release_outcome.fold_release_outcome(...).global_verdict` -- the
    uncollapsed bundle/probe-matrix verdict `resolve_release_exit_decision_
    for_report` needs independently of `worst_verdict` (Codex review, fresh
    evidence, second round)."""

    def test_no_bundle_or_matrix_is_no_change(self) -> None:
        assert _release_global_verdict(None, None) == "NO_CHANGE"

    def test_bundle_verdict_alone(self) -> None:
        from abicheck.checker_policy import Verdict

        bundle = cast(Any, SimpleNamespace(bundle_verdict=Verdict.BREAKING))
        assert _release_global_verdict(bundle, None) == "BREAKING"

    def test_matrix_verdict_alone(self) -> None:
        from abicheck.checker_policy import Verdict

        matrix = cast(Any, SimpleNamespace(verdict=Verdict.API_BREAK))
        assert _release_global_verdict(None, matrix) == "API_BREAK"

    def test_the_worse_of_bundle_and_matrix_wins(self) -> None:
        from abicheck.checker_policy import Verdict

        bundle = cast(Any, SimpleNamespace(bundle_verdict=Verdict.COMPATIBLE))
        matrix = cast(Any, SimpleNamespace(verdict=Verdict.BREAKING))
        assert _release_global_verdict(bundle, matrix) == "BREAKING"
