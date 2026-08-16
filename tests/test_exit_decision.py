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

"""CLI cleanup phase two, PR G1: ``exit_decision.ExitDecision``.

The pure-resolver tests (``TestResolveExitDecision``) state the primitive's
own contract as invariants -- precedence, tie-handling, the ``clean``
sentinel -- independent of any one caller. The CLI-level tests
(``TestCompareExitDecisionIntegration``) are the regression pin that the
canonical resolver reproduces exactly the exit code the pre-refactor,
three-separate-``max``-calls fold chain produced, and that the JSON report's
new ``exit`` block agrees with the real process exit.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.exit_decision import ExitDecision, ExitReason, resolve_exit_decision
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


class TestResolveExitDecision:
    def test_clean_when_every_contribution_is_zero(self) -> None:
        decision = resolve_exit_decision(compatibility_contribution=0)
        assert decision.code == 0
        assert decision.reasons == (ExitReason.CLEAN,)

    def test_compatibility_alone_wins(self) -> None:
        decision = resolve_exit_decision(compatibility_contribution=4)
        assert decision.code == 4
        assert decision.reasons == (ExitReason.COMPATIBILITY_GATE,)

    def test_coverage_floor_raises_a_clean_zero(self) -> None:
        decision = resolve_exit_decision(
            compatibility_contribution=0, contract_coverage_contribution=1,
        )
        assert decision.code == 1
        assert decision.reasons == (ExitReason.CONTRACT_COVERAGE,)

    def test_assurance_floor_raises_a_clean_zero(self) -> None:
        decision = resolve_exit_decision(
            compatibility_contribution=0, analysis_assurance_contribution=1,
        )
        assert decision.code == 1
        assert decision.reasons == (ExitReason.ANALYSIS_ASSURANCE,)

    def test_floors_never_lower_a_real_break(self) -> None:
        """A `1` floor sitting underneath a `4` gate never wins, and does not
        appear in `reasons` -- it explains nothing about why the exit is `4`.
        """
        decision = resolve_exit_decision(
            compatibility_contribution=4,
            contract_coverage_contribution=1,
            analysis_assurance_contribution=1,
        )
        assert decision.code == 4
        assert decision.reasons == (ExitReason.COMPATIBILITY_GATE,)

    def test_tied_axes_both_appear_in_reasons(self) -> None:
        """Coverage and assurance can independently both floor to `1` -- a
        shared `1` is explainable only if both are named.
        """
        decision = resolve_exit_decision(
            compatibility_contribution=0,
            contract_coverage_contribution=1,
            analysis_assurance_contribution=1,
        )
        assert decision.code == 1
        assert set(decision.reasons) == {
            ExitReason.CONTRACT_COVERAGE, ExitReason.ANALYSIS_ASSURANCE,
        }

    def test_matches_manual_max_fold(self) -> None:
        """`code` is exactly `max()` over the three contributions -- the
        identical value the pre-PR-G1 sequential fold chain computed.
        """
        for compat, coverage, assurance in [
            (0, 0, 0), (2, 0, 0), (0, 1, 0), (0, 0, 1), (4, 1, 1), (1, 1, 1),
        ]:
            decision = resolve_exit_decision(
                compatibility_contribution=compat,
                contract_coverage_contribution=coverage,
                analysis_assurance_contribution=assurance,
            )
            assert decision.code == max(compat, coverage, assurance)

    def test_to_dict_is_json_serializable(self) -> None:
        decision = resolve_exit_decision(
            compatibility_contribution=0, contract_coverage_contribution=1,
        )
        d = decision.to_dict()
        json.dumps(d)  # must not raise
        assert d == {
            "code": 1,
            "reasons": ["contract_coverage"],
            "compatibility_contribution": 0,
            "contract_coverage_contribution": 1,
            "analysis_assurance_contribution": 0,
        }

    def test_default_contributions_are_zero(self) -> None:
        """A caller with neither `--contract` nor
        `--require-complete-analysis` in effect can omit both keyword args.
        """
        decision = resolve_exit_decision(compatibility_contribution=2)
        assert decision.contract_coverage_contribution == 0
        assert decision.analysis_assurance_contribution == 0

    def test_is_frozen(self) -> None:
        decision: ExitDecision = resolve_exit_decision(compatibility_contribution=0)
        try:
            decision.code = 4  # type: ignore[misc]
        except AttributeError:
            pass
        else:
            raise AssertionError("ExitDecision must be immutable")


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int", visibility=Visibility.PUBLIC,
    )


def _write(tmp_path: Path, old: AbiSnapshot, new: AbiSnapshot) -> tuple[Path, Path]:
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return old_p, new_p


def _compatible_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    common = {"library": "libfoo.so.1", "from_headers": True}
    fns = [_fn("pub_a", "_Z5pub_av")]
    return (
        AbiSnapshot(version="1.0", functions=fns, **common),
        AbiSnapshot(version="2.0", functions=fns, **common),
    )


def _breaking_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    common = {"library": "libfoo.so.1", "from_headers": True}
    return (
        AbiSnapshot(
            version="1.0",
            functions=[_fn("pub_a", "_Z5pub_av"), _fn("pub_b", "_Z5pub_bv")],
            **common,
        ),
        AbiSnapshot(version="2.0", functions=[_fn("pub_a", "_Z5pub_av")], **common),
    )


def _compare(tmp_path: Path, pair: tuple[AbiSnapshot, AbiSnapshot], *extra: str):
    old_p, new_p = _write(tmp_path, *pair)
    return CliRunner().invoke(main, ["compare", str(old_p), str(new_p), *extra])


class TestCompareExitDecisionIntegration:
    """The real `compare --format json` path: the persisted `exit` block
    must agree with the real process exit code, for both a clean and a
    breaking comparison, with no orthogonal axis engaged.
    """

    def test_clean_comparison_reports_a_clean_exit_block(self, tmp_path: Path) -> None:
        res = _compare(tmp_path, _compatible_pair(), "--format", "json")
        assert res.exit_code == 0, res.output
        report = json.loads(res.stdout[res.stdout.index("{") :])
        assert report["exit"] == {
            "code": 0,
            "reasons": ["clean"],
            "compatibility_contribution": 0,
            "contract_coverage_contribution": 0,
            "analysis_assurance_contribution": 0,
        }

    def test_breaking_comparison_reports_the_compatibility_gate_reason(
        self, tmp_path: Path,
    ) -> None:
        res = _compare(tmp_path, _breaking_pair(), "--format", "json")
        assert res.exit_code == 4, res.output
        report = json.loads(res.stdout[res.stdout.index("{") :])
        assert report["exit"]["code"] == 4
        assert report["exit"]["reasons"] == ["compatibility_gate"]
        assert report["exit"]["compatibility_contribution"] == 4
        assert report["exit"]["code"] == res.exit_code

    def test_require_complete_analysis_floor_matches_process_exit(
        self, tmp_path: Path,
    ) -> None:
        """The elf-only pair from `test_analysis_assurance.py`'s own
        contract has an incomplete status -- ``--require-complete-analysis``
        floors a clean compatibility exit to 1, and the persisted ``exit``
        block must name ``analysis_assurance`` as the reason.
        """
        common = {"library": "libfoo.so.1", "elf_only_mode": True}
        fns = [_fn("pub_a", "_Z5pub_av")]
        pair = (
            AbiSnapshot(version="1.0", functions=fns, **common),
            AbiSnapshot(version="2.0", functions=fns, **common),
        )
        res = _compare(
            tmp_path, pair, "--format", "json", "--require-complete-analysis",
        )
        assert res.exit_code == 1, res.output
        report = json.loads(res.stdout[res.stdout.index("{") :])
        assert report["exit"]["code"] == 1
        assert "analysis_assurance" in report["exit"]["reasons"]
        assert report["exit"]["analysis_assurance_contribution"] == 1
        assert report["exit"]["code"] == res.exit_code
        # And the top-level sibling field this duplicates must agree.
        assert (
            report["exit"]["analysis_assurance_contribution"]
            == report["analysis_assurance_exit_contribution"]
        )
