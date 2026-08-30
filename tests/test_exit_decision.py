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

"""CLI cleanup phase two, PR G1/G2: ``exit_decision.ExitDecision``.

The pure-resolver tests (``TestResolveExitDecision``) state the primitive's
own contract as invariants -- precedence, tie-handling, the ``clean``
sentinel -- independent of any one caller. The CLI-level tests
(``TestCompareExitDecisionIntegration``) are the regression pin that the
canonical resolver reproduces exactly the exit code the pre-refactor,
three-separate-``max``-calls fold chain produced, and that the JSON report's
new ``exit`` block agrees with the real process exit.

``TestResolveScanExitDecision``/``TestResolveReleaseExitDecision`` cover
ADR-064's additive extension: pure resolvers for the three axes PR G1
deliberately left unmodeled (evidence-contract error, budget overflow,
not-comparable, and a release's mode-dependent removed-required-library
rank). Neither resolver is wired into a real call site yet -- see
``docs/contribute/adr/064-canonical-gate-algorithm-and-exit-decision.md``
and `abicheck/policy/exit_decision.py`'s own module docstring for what
remains open.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.checker import compare
from abicheck.cli import main
from abicheck.exit_decision import ExitDecision, ExitReason, resolve_exit_decision
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.policy.exit_decision import (
    resolve_release_exit_decision,
    resolve_scan_exit_decision,
)
from abicheck.reporter import to_json
from abicheck.schemas import load_compare_report_schema
from abicheck.serialization import snapshot_to_json

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised only when jsonschema absent
    jsonschema = None

_requires_jsonschema = pytest.mark.skipif(
    jsonschema is None, reason="jsonschema not installed"
)


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

    @pytest.mark.parametrize(
        ("compat", "coverage", "assurance"),
        [(0, 0, 0), (2, 0, 0), (0, 1, 0), (0, 0, 1), (4, 1, 1), (1, 1, 1)],
    )
    def test_matches_manual_max_fold(
        self, compat: int, coverage: int, assurance: int
    ) -> None:
        """`code` is exactly `max()` over the three contributions -- the
        identical value the pre-PR-G1 sequential fold chain computed.
        """
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
            "crosscheck_promotion_contribution": 0,
        }

    def test_crosscheck_promotion_is_a_real_contribution_not_a_patch(self) -> None:
        """`scan_engine._promote_published_gate`'s own axis: `code` must
        equal `max()` over *all four* contributions, `crosscheck_promotion_
        contribution` included, exactly like the other three (Codex review
        -- an earlier revision patched `code`/`reasons` directly without
        this axis, which broke this invariant for a promoted scan).
        """
        decision = resolve_exit_decision(
            compatibility_contribution=0, crosscheck_promotion_contribution=2,
        )
        assert decision.code == 2
        assert decision.reasons == (ExitReason.PROMOTED_CROSSCHECK,)
        assert decision.code == max(
            decision.compatibility_contribution,
            decision.contract_coverage_contribution,
            decision.analysis_assurance_contribution,
            decision.crosscheck_promotion_contribution,
        )

    def test_crosscheck_promotion_ties_are_named_not_dropped(self) -> None:
        """A promotion that only *ties* the existing code must still be
        named -- not silently omitted the way a hand-rolled strict `>`
        check on the caller side would drop it.
        """
        decision = resolve_exit_decision(
            compatibility_contribution=2, crosscheck_promotion_contribution=2,
        )
        assert decision.code == 2
        assert set(decision.reasons) == {
            ExitReason.COMPATIBILITY_GATE, ExitReason.PROMOTED_CROSSCHECK,
        }

    def test_crosscheck_promotion_never_lowers_a_real_break(self) -> None:
        decision = resolve_exit_decision(
            compatibility_contribution=4, crosscheck_promotion_contribution=2,
        )
        assert decision.code == 4
        assert decision.reasons == (ExitReason.COMPATIBILITY_GATE,)

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


class TestResolveScanExitDecision:
    """`resolve_scan_exit_decision` -- ADR-064's outer precedence layer for
    `scan`, ahead of the ordinary gate/coverage/assurance fold.
    """

    def test_none_of_the_three_axes_returns_none(self) -> None:
        assert resolve_scan_exit_decision() is None

    def test_evidence_contract_error_alone(self) -> None:
        decision = resolve_scan_exit_decision(evidence_contract_error=True)
        assert decision is not None
        assert decision.code == 1
        assert decision.reasons == (ExitReason.EVIDENCE_CONTRACT_ERROR,)
        assert decision.compatibility_contribution == 0
        assert decision.contract_coverage_contribution == 0
        assert decision.analysis_assurance_contribution == 0

    def test_budget_overflow_alone(self) -> None:
        decision = resolve_scan_exit_decision(budget_overflow=True)
        assert decision is not None
        assert decision.code == 5
        assert decision.reasons == (ExitReason.BUDGET_OVERFLOW,)

    def test_not_comparable_alone(self) -> None:
        decision = resolve_scan_exit_decision(not_comparable=True)
        assert decision is not None
        assert decision.code == 6
        assert decision.reasons == (ExitReason.NOT_COMPARABLE,)

    def test_budget_overflow_discards_not_comparable(self) -> None:
        """`scan_engine.run_scan_core` runs its budget check unconditionally
        after a `not_comparable` result may already be decided, and that
        check's own exception discards the already-built report entirely --
        so when both are true for the same run, budget wins, not a tie.
        """
        decision = resolve_scan_exit_decision(
            budget_overflow=True, not_comparable=True,
        )
        assert decision is not None
        assert decision.code == 5
        assert decision.reasons == (ExitReason.BUDGET_OVERFLOW,)

    def test_evidence_contract_error_dominates_the_other_two(self) -> None:
        """`_EvidenceContractError` aborts during evidence collection, before
        a baseline comparison -- and therefore before budget/not-comparable
        could ever be decided -- is even attempted.
        """
        decision = resolve_scan_exit_decision(
            evidence_contract_error=True, budget_overflow=True, not_comparable=True,
        )
        assert decision is not None
        assert decision.code == 1
        assert decision.reasons == (ExitReason.EVIDENCE_CONTRACT_ERROR,)

    def test_custom_codes_are_honored(self) -> None:
        """The `*_code` keywords default to `scan`'s own numbers, but are
        real parameters, not hard-coded -- see the resolver's own docstring
        for why the numbers stay per-command (ADR-064).
        """
        decision = resolve_scan_exit_decision(
            not_comparable=True, not_comparable_code=99,
        )
        assert decision is not None
        assert decision.code == 99


class TestResolveReleaseExitDecision:
    """`resolve_release_exit_decision` -- reproduces
    `cli_compare_release_helpers._exit_compare_release`'s exact precedence,
    including removed-required-library's mode-dependent rank.
    """

    def test_not_comparable_dominates_everything(self) -> None:
        decision = resolve_release_exit_decision(
            not_comparable=True,
            severity_scheme_active=True,
            verdict_or_severity_contribution=4,
            removed_required_library=True,
            contract_coverage_contribution=1,
        )
        assert decision.code == 16
        assert decision.reasons == (ExitReason.NOT_COMPARABLE,)

    def test_legacy_scheme_breaking_wins_over_removed_library(self) -> None:
        """The pinned regression this mirrors:
        `tests/test_compare_release.py::test_removed_and_breaking_exits_4_not_8`
        -- BREAKING (4) takes priority over removed-library (8) under the
        legacy scheme, even though 8 is numerically larger.
        """
        decision = resolve_release_exit_decision(
            not_comparable=False,
            severity_scheme_active=False,
            verdict_or_severity_contribution=4,
            removed_required_library=True,
        )
        assert decision.code == 4
        assert decision.reasons == (ExitReason.COMPATIBILITY_GATE,)

    def test_legacy_scheme_removed_library_only_when_verdict_clean(self) -> None:
        decision = resolve_release_exit_decision(
            not_comparable=False,
            severity_scheme_active=False,
            verdict_or_severity_contribution=0,
            removed_required_library=True,
        )
        assert decision.code == 8
        assert decision.reasons == (ExitReason.REMOVED_REQUIRED_LIBRARY,)

    def test_legacy_scheme_no_removed_library_falls_to_coverage(self) -> None:
        decision = resolve_release_exit_decision(
            not_comparable=False,
            severity_scheme_active=False,
            verdict_or_severity_contribution=0,
            removed_required_library=False,
            contract_coverage_contribution=1,
        )
        assert decision.code == 1
        assert decision.reasons == (ExitReason.CONTRACT_COVERAGE,)

    def test_severity_scheme_removed_library_wins_outright(self) -> None:
        """A real, pre-existing asymmetry in today's code: the severity
        branch's removed-library check runs *before* the coverage floor is
        even read, so removed-library wins even over a coverage
        contribution that would otherwise floor a clean gate to `1`.
        """
        decision = resolve_release_exit_decision(
            not_comparable=False,
            severity_scheme_active=True,
            verdict_or_severity_contribution=0,
            removed_required_library=True,
            contract_coverage_contribution=1,
        )
        assert decision.code == 8
        assert decision.reasons == (ExitReason.REMOVED_REQUIRED_LIBRARY,)

    def test_severity_scheme_no_removed_library_folds_coverage_via_max(self) -> None:
        decision = resolve_release_exit_decision(
            not_comparable=False,
            severity_scheme_active=True,
            verdict_or_severity_contribution=2,
            removed_required_library=False,
            contract_coverage_contribution=1,
        )
        assert decision.code == 2
        assert decision.reasons == (ExitReason.COMPATIBILITY_GATE,)

    def test_severity_scheme_clean_with_no_removed_library_is_clean(self) -> None:
        decision = resolve_release_exit_decision(
            not_comparable=False,
            severity_scheme_active=True,
            verdict_or_severity_contribution=0,
            removed_required_library=False,
        )
        assert decision.code == 0
        assert decision.reasons == (ExitReason.CLEAN,)

    def test_custom_codes_are_honored(self) -> None:
        decision = resolve_release_exit_decision(
            not_comparable=True,
            severity_scheme_active=False,
            verdict_or_severity_contribution=0,
            not_comparable_code=77,
        )
        assert decision.code == 77


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
            "crosscheck_promotion_contribution": 0,
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

    def test_scoped_gate_reports_the_scoped_exit_not_the_full_library_gate(
        self, tmp_path: Path,
    ) -> None:
        """Codex review: a `--required-symbol` compare's real process exit is
        the *scoped* gate (`result.scoped_exit_code`), floored/persisted by
        `cli_compare_helpers._apply_scoped_gating` before any report renders
        -- not the full-library verdict/severity gate this module would
        otherwise compute from `result.verdict`. Scoping the requirement to
        the surviving symbol alone must report a clean scoped exit even
        though the full library is BREAKING (a real removed symbol outside
        the required set), and the persisted ``exit`` block must agree with
        the real process exit code, not the informational full-library one.
        """
        old_p, new_p = _write(tmp_path, *_breaking_pair())
        res = CliRunner().invoke(
            main,
            [
                "compare", str(old_p), str(new_p),
                "--required-symbol", "_Z5pub_av",  # pub_a survives; pub_b was removed
                "--format", "json",
            ],
        )
        assert res.exit_code == 0, res.output
        report = json.loads(res.stdout[res.stdout.index("{") :])
        # full_verdict is the informational, unscoped full-library gate;
        # verdict itself is already the scoped one under --required-symbol.
        assert report["full_verdict"] == "BREAKING"
        assert report["exit"]["code"] == 0
        assert report["exit"]["reasons"] == ["clean"]
        assert report["exit"]["compatibility_contribution"] == 0
        assert report["exit"]["code"] == res.exit_code

    def test_scoped_gate_failure_names_the_scoped_reason(
        self, tmp_path: Path,
    ) -> None:
        """The inverse: requiring the *removed* symbol must fail the scoped
        gate, and the ``exit`` block must name ``scoped_gate`` -- not
        ``compatibility_gate`` -- since the full-library verdict alone never
        determined this exit.
        """
        old_p, new_p = _write(tmp_path, *_breaking_pair())
        res = CliRunner().invoke(
            main,
            [
                "compare", str(old_p), str(new_p),
                "--required-symbol", "_Z5pub_bv",  # pub_b was removed
                "--format", "json",
            ],
        )
        assert res.exit_code != 0, res.output
        report = json.loads(res.stdout[res.stdout.index("{") :])
        assert report["exit"]["code"] == res.exit_code
        assert report["exit"]["reasons"] == ["scoped_gate"]
        assert report["exit"]["compatibility_contribution"] == res.exit_code

    def test_scoped_clean_gate_does_not_mask_the_real_assurance_reason(
        self, tmp_path: Path,
    ) -> None:
        """Codex review: an earlier revision of the scoped fix read the
        already-*folded* ``result.scoped_exit_code`` as the compatibility
        contribution, so a clean scoped gate (0) floored to 1 purely by
        ``--require-complete-analysis`` still reported ``reasons:
        ["scoped_gate"]`` and ``compatibility_contribution: 1`` -- as if the
        scoped gate itself had determined the exit, when it contributed
        nothing. The *pre-fold* scoped contribution must be what's compared
        against the other axes, so a clean scoped gate correctly stays out
        of ``reasons`` when assurance alone is what floored the exit.
        """
        common = {"library": "libfoo.so.1", "elf_only_mode": True}
        fns = [_fn("pub_a", "_Z5pub_av")]
        old_p, new_p = _write(
            tmp_path,
            AbiSnapshot(version="1.0", functions=fns, **common),
            AbiSnapshot(version="2.0", functions=fns, **common),
        )
        res = CliRunner().invoke(
            main,
            [
                "compare", str(old_p), str(new_p),
                "--required-symbol", "_Z5pub_av",  # survives -- clean scoped gate
                "--format", "json",
                "--require-complete-analysis",  # elf-only pair: incomplete
            ],
        )
        assert res.exit_code == 1, res.output
        report = json.loads(res.stdout[res.stdout.index("{") :])
        assert report["exit"]["code"] == 1
        assert report["exit"]["code"] == res.exit_code
        assert report["exit"]["reasons"] == ["analysis_assurance"]
        assert report["exit"]["compatibility_contribution"] == 0
        assert report["exit"]["analysis_assurance_contribution"] == 1


class TestIncludeExitDecisionFlag:
    """Codex review: ``compat/cli.py``'s own ``-report-format json`` reuses
    this exact ``reporter.to_json`` function, but ``compat check``'s real
    process exit follows a different, ABICC-style 0/1/2 scheme
    (``_classify_compat_error_exit_code``) than the native
    ``legacy_exit_code``/``compute_exit_code`` the ``exit`` block computes --
    emitting it unconditionally would report a code that disagrees with the
    actual ``compat check`` exit for the same run. ``include_exit_decision``
    is the flag that keeps them apart; ``compat/cli.py``'s own call site
    passes ``False``, and these tests pin `to_json` itself -- the real
    function both callers share -- rather than only the CLI wrapper.
    """

    def test_default_includes_the_exit_block(self) -> None:
        old, new = _breaking_pair()
        result = compare(old, new)
        report = json.loads(to_json(result))
        assert "exit" in report
        assert report["exit"]["code"] == 4

    def test_include_exit_decision_false_omits_it(self) -> None:
        old, new = _breaking_pair()
        result = compare(old, new)
        report = json.loads(to_json(result, include_exit_decision=False))
        assert "exit" not in report
        # Every other field this function always writes stays present --
        # this flag turns off exactly one block, nothing else.
        assert report["verdict"] == "BREAKING"
        assert "changes" in report

    def test_include_exit_decision_false_also_applies_to_leaf_and_root_cause(
        self,
    ) -> None:
        old, new = _breaking_pair()
        result = compare(old, new)
        for mode in ("leaf", "root-cause"):
            report = json.loads(
                to_json(result, report_mode=mode, include_exit_decision=False)
            )
            assert "exit" not in report, mode

    @_requires_jsonschema
    def test_include_exit_decision_false_still_validates_against_schema(
        self,
    ) -> None:
        """``exit`` is schema-optional (not in ``required``) specifically so
        this -- what ``compat check``'s JSON output actually produces -- still
        validates against the same ``report_schema_version`` it stamps
        (Codex review: an earlier revision required ``exit`` unconditionally,
        so a ``compat`` report claiming schema 2.41 while omitting the block
        failed to validate against its own advertised schema).
        """
        old, new = _breaking_pair()
        result = compare(old, new)
        report = json.loads(to_json(result, include_exit_decision=False))
        assert "exit" not in report
        jsonschema.validate(report, load_compare_report_schema())


class TestPromotePublishedGateInvariant:
    """White-box regression pin for ``scan_engine._promote_published_gate``
    (CLI cleanup phase two, PR E follow-up, Codex review) -- the function
    that keeps `scan --against`'s persisted ``diff.exit`` block honest when
    a maintainer-promoted ``--crosscheck KEY=error`` finding raises the
    process exit after ``_run_baseline_compare`` already built that block.

    An earlier revision hand-patched only ``code``/``reasons`` in place,
    which (1) broke :class:`ExitDecision`'s own documented invariant that
    ``code`` equals the max of its contribution fields, since the three
    pre-existing contributions were left at their pre-promotion values, and
    (2) used a strict ``>`` check that silently dropped a promotion which
    only *tied* the block's existing code. Both are fixed by reconstructing
    the whole block through :func:`resolve_exit_decision`; these tests fail
    against that earlier revision, not just against the current one.
    """

    @staticmethod
    def _exit_block(
        *, code: int, reasons: list[str], compat: int,
        coverage: int = 0, assurance: int = 0, crosscheck: int = 0,
    ) -> dict[str, object]:
        return {
            "code": code,
            "reasons": reasons,
            "compatibility_contribution": compat,
            "contract_coverage_contribution": coverage,
            "analysis_assurance_contribution": assurance,
            "crosscheck_promotion_contribution": crosscheck,
        }

    def test_promotion_preserves_the_max_invariant(self) -> None:
        from abicheck.scan_engine import _promote_published_gate

        diff_summary: dict[str, object] = {
            "exit": self._exit_block(code=0, reasons=["clean"], compat=0),
        }
        _promote_published_gate(diff_summary, sev_exit=2)
        exit_block = diff_summary["exit"]
        assert isinstance(exit_block, dict)
        assert exit_block["code"] == 2
        assert exit_block["reasons"] == ["promoted_crosscheck"]
        assert exit_block["crosscheck_promotion_contribution"] == 2
        assert exit_block["code"] == max(
            exit_block["compatibility_contribution"],
            exit_block["contract_coverage_contribution"],
            exit_block["analysis_assurance_contribution"],
            exit_block["crosscheck_promotion_contribution"],
        )

    def test_promotion_names_a_tie_instead_of_dropping_it(self) -> None:
        from abicheck.scan_engine import _promote_published_gate

        diff_summary: dict[str, object] = {
            "exit": self._exit_block(
                code=2, reasons=["compatibility_gate"], compat=2,
            ),
        }
        _promote_published_gate(diff_summary, sev_exit=2)
        exit_block = diff_summary["exit"]
        assert isinstance(exit_block, dict)
        assert exit_block["code"] == 2
        assert set(exit_block["reasons"]) == {
            "compatibility_gate", "promoted_crosscheck",
        }
        assert exit_block["crosscheck_promotion_contribution"] == 2

    def test_severity_gate_tie_also_names_the_crosscheck(self) -> None:
        """Sibling of the previous test for the *other* persisted block --
        `diff.severity` -- which used a strict `>` guard even after the
        `diff.exit` tie fix, leaving the two blocks disagreeing about the
        same tie (Codex review, fresh evidence: the tie only became
        reachable once the call-site restructuring stopped gating this
        whole function on `sev_exit > exit_code`).
        """
        from abicheck.scan_engine import _promote_published_gate

        diff_summary: dict[str, object] = {
            "severity": {
                "exit_code": 2,
                "blocking": True,
                "blocking_categories": ["abi_breaking"],
            },
        }
        _promote_published_gate(diff_summary, sev_exit=2)
        gate = diff_summary["severity"]
        assert isinstance(gate, dict)
        assert gate["exit_code"] == 2
        assert gate["blocking"] is True
        assert set(gate["blocking_categories"]) == {
            "abi_breaking", "promoted_crosscheck",
        }

    def test_severity_gate_strictly_higher_stays_untouched(self) -> None:
        from abicheck.scan_engine import _promote_published_gate

        diff_summary: dict[str, object] = {
            "severity": {
                "exit_code": 4,
                "blocking": True,
                "blocking_categories": ["abi_breaking"],
            },
        }
        _promote_published_gate(diff_summary, sev_exit=2)
        gate = diff_summary["severity"]
        assert isinstance(gate, dict)
        assert gate["exit_code"] == 4
        assert gate["blocking_categories"] == ["abi_breaking"]

    def test_promotion_never_lowers_a_higher_existing_code(self) -> None:
        from abicheck.scan_engine import _promote_published_gate

        diff_summary: dict[str, object] = {
            "exit": self._exit_block(
                code=4, reasons=["compatibility_gate"], compat=4,
            ),
        }
        _promote_published_gate(diff_summary, sev_exit=2)
        exit_block = diff_summary["exit"]
        assert isinstance(exit_block, dict)
        assert exit_block["code"] == 4
        assert exit_block["reasons"] == ["compatibility_gate"]
