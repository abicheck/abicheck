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

"""ADR-049 Phase 7: contract relevance decides, rather than annotates.

Phases 3-6 computed a decision and reported it beside a verdict that had
already been reached without it. The claim under test here is the ordering
itself (D9): relevance is classified *before* compatibility policy, policy
scores only the `EVALUATED` findings, and the change gate follows -- while
every detector fact stays conserved and visible (D9's "exactly one visible
outcome").

Two properties are asserted throughout rather than once, because together
they are what "authoritative but safe" means:

- a run that did not opt into `--contract-evaluation` is bit-for-bit what it
  was, since nothing carries a relevance for the new rule to act on;
- an excluded finding is *excluded from scoring*, never dropped, re-kinded,
  or turned green -- the unresolved case is reported on its own orthogonal
  coverage axis, which has its own exit code.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change
from abicheck.cli import main
from abicheck.contract_relevance_types import (
    CompatibilityEvaluationStatus,
    ContractRelevance,
)
from abicheck.model import AbiSnapshot, Function, RecordType, ScopeOrigin, Visibility
from abicheck.serialization import snapshot_to_json


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int", visibility=Visibility.PUBLIC
    )


def _unreached_public_type_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """A layout change to a public-header type nothing public reaches.

    The canonical shape the three domains disagree about: `all` makes no
    root/closure claim so the change is in contract, while `public` and
    `exports` can *prove* the type outside the declared contract. That makes
    it the pair that shows relevance deciding, rather than a pair where the
    evidence merely runs out.
    """

    def snap(size: int) -> AbiSnapshot:
        return AbiSnapshot(
            library="libfoo.so",
            version="1",
            functions=[_fn("api", "api")],
            types=[
                RecordType(
                    name="Internal",
                    kind="struct",
                    size_bits=size,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
        )

    return snap(64), snap(128)


def _removal_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """A plain public-function removal, header-derived on both sides."""
    common = {"library": "libfoo.so.1", "from_headers": True}
    return (
        AbiSnapshot(
            version="1.0",
            functions=[_fn("pub_a", "_Z5pub_av"), _fn("pub_b", "_Z5pub_bv")],
            **common,
        ),
        AbiSnapshot(version="2.0", functions=[_fn("pub_a", "_Z5pub_av")], **common),
    )


def _compare(pair, **kw):
    old, new = pair
    return compare(old, new, scope_to_public_surface=False, **kw)


class TestRelevanceRunsBeforePolicy:
    """D9's ordering, observed through its only externally visible effect."""

    def test_a_proven_out_of_contract_finding_does_not_reach_the_verdict(self) -> None:
        result = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="public",
        )
        assert result.verdict is Verdict.NO_CHANGE
        assert [c.kind for c in result.changes] == [ChangeKind.TYPE_SIZE_CHANGED]

    def test_the_same_finding_scores_when_the_domain_keeps_it_in_contract(self) -> None:
        """The control. Without it, "no verdict" could just mean "no finding"."""
        result = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="all",
        )
        assert result.verdict is Verdict.BREAKING

    def test_an_unresolved_finding_does_not_become_a_break(self) -> None:
        """ADR-049 D1: "uncertainty itself never becomes an ABI break."

        `exports` on a header-only pair has no export table to resolve
        against, so the removal is `UNKNOWN_UNRESOLVED` -- reported, and
        answered on the coverage axis, not folded into the ABI verdict.
        """
        result = compare(
            *_removal_pair(), contract_evaluation=True, contract_mode="exports"
        )
        removal = next(c for c in result.changes if c.kind is ChangeKind.FUNC_REMOVED)
        assert removal.contract_relevance is ContractRelevance.UNKNOWN_UNRESOLVED
        assert result.verdict is Verdict.NO_CHANGE

    def test_an_unresolved_finding_still_blocks_via_the_coverage_axis(self) -> None:
        """The other half of the sentence above: not a break, not green
        either. Anything else would make missing evidence the cheapest way to
        pass."""
        from abicheck.contract_coverage_exit import fold_coverage_exit

        result = compare(
            *_removal_pair(), contract_evaluation=True, contract_mode="exports"
        )
        assert fold_coverage_exit(0, result) == 1


class TestTheCanonicalPerFindingShape:
    """D1's four fields, and that they agree with each other."""

    @staticmethod
    def _by_symbol(result) -> dict[str, Change]:
        return {c.symbol: c for c in result.changes}

    def test_an_evaluated_finding_carries_its_decision(self) -> None:
        result = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="all",
        )
        change = self._by_symbol(result)["Internal"]
        assert change.contract_relevance is ContractRelevance.IN_CONTRACT
        assert change.compatibility_evaluation_status is (
            CompatibilityEvaluationStatus.EVALUATED
        )
        assert change.compatibility_decision is Verdict.BREAKING

    def test_a_not_evaluated_finding_carries_a_null_decision(self) -> None:
        """`None` records that policy never ran. It is deliberately not
        `COMPATIBLE`: that would be a decision, and no decision was made."""
        result = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="public",
        )
        change = self._by_symbol(result)["Internal"]
        assert change.compatibility_evaluation_status is (
            CompatibilityEvaluationStatus.NOT_EVALUATED
        )
        assert change.compatibility_decision is None

    @pytest.mark.parametrize(
        ("relevance", "expected"),
        [
            (ContractRelevance.IN_CONTRACT, CompatibilityEvaluationStatus.EVALUATED),
            (ContractRelevance.NOT_APPLICABLE, CompatibilityEvaluationStatus.EVALUATED),
            (
                ContractRelevance.PROVEN_OUT_OF_CONTRACT,
                CompatibilityEvaluationStatus.NOT_EVALUATED,
            ),
            (
                ContractRelevance.UNKNOWN_UNPROVEN,
                CompatibilityEvaluationStatus.NOT_EVALUATED,
            ),
            (
                ContractRelevance.UNKNOWN_UNRESOLVED,
                CompatibilityEvaluationStatus.NOT_EVALUATED,
            ),
        ],
    )
    def test_the_status_follows_the_relevance_for_every_value(
        self, relevance: ContractRelevance, expected: CompatibilityEvaluationStatus
    ) -> None:
        """The whole five-value mapping, so a new relevance value cannot be
        added without deciding which side of the gate it falls on."""
        from abicheck.contract_relevance_types import evaluation_status_for

        assert evaluation_status_for(relevance) is expected

    def test_an_unstamped_finding_is_gated_exactly_as_before(self) -> None:
        """The compatibility guarantee, stated at the predicate every
        consumer reads: no opt-in means no relevance, and no relevance means
        the legacy answer."""
        from abicheck.contract_gating import evaluation_status_of, is_evaluated

        change = Change(ChangeKind.FUNC_REMOVED, "pub", "removed")
        assert change.contract_relevance is None
        assert evaluation_status_of(change) is None
        assert is_evaluated(change)


class TestTheGateFollowsTheDecision:
    def test_a_not_evaluated_finding_contributes_nothing_to_the_gate(self) -> None:
        from abicheck.severity import (
            SeverityConfig,
            SeverityLevel,
            compute_exit_code,
            gate_contribution_for_change,
        )

        config = SeverityConfig(abi_breaking=SeverityLevel.ERROR)
        result = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="public",
        )
        change = next(iter(result.changes))
        assert gate_contribution_for_change(change, config) == 0
        assert compute_exit_code(result.changes, config) == 0

    def test_the_identical_finding_gates_when_it_is_in_contract(self) -> None:
        from abicheck.severity import (
            SeverityConfig,
            SeverityLevel,
            compute_exit_code,
            gate_contribution_for_change,
        )

        config = SeverityConfig(abi_breaking=SeverityLevel.ERROR)
        result = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="all",
        )
        change = next(iter(result.changes))
        assert gate_contribution_for_change(change, config) == 4
        assert compute_exit_code(result.changes, config) == 4

    def test_the_blamed_categories_agree_with_the_exit_code(self) -> None:
        """`compute_gate_decision` exists so these two cannot disagree; the
        exclusion has to be applied to both or it reintroduces exactly that
        bug."""
        from abicheck.severity import (
            SeverityConfig,
            SeverityLevel,
            compute_gate_decision,
        )

        config = SeverityConfig(abi_breaking=SeverityLevel.ERROR)
        result = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="public",
        )
        gate = compute_gate_decision(result.changes, config)
        assert gate.exit_code == 0
        assert gate.blocking_categories == ()

    def test_a_legacy_scheme_contribution_folds_to_the_legacy_exit(self) -> None:
        """The per-finding number must be the one the run exits on, under the
        scheme that has no severity config to read."""
        from abicheck.severity import gate_contribution_for_change, legacy_exit_code

        result = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="all",
        )
        contributions = [
            gate_contribution_for_change(c, None, policy=result.policy)
            for c in result.changes
        ]
        assert max(contributions) == legacy_exit_code(result.verdict)


class TestNothingIsLost:
    """D9: every detector fact lands in exactly one visible outcome."""

    def test_an_excluded_finding_stays_in_the_change_list(self) -> None:
        result = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="public",
        )
        assert len(result.changes) == 1
        assert result.not_evaluated == result.changes

    def test_its_kind_is_never_rewritten(self) -> None:
        """ "A detector fact never disappears and its ChangeKind is never
        rewritten merely to obtain a desired gate result" (ADR-049 plan §1)."""
        scored = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="all",
        )
        unscored = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="public",
        )
        assert [c.kind for c in scored.changes] == [c.kind for c in unscored.changes]
        assert [c.description for c in scored.changes] == [
            c.description for c in unscored.changes
        ]

    def test_the_summary_counts_agree_with_the_verdict(self) -> None:
        """The four compatibility buckets are over the evaluated findings, so
        a `NO_CHANGE` verdict cannot sit beside "1 breaking change"."""
        result = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="public",
        )
        assert result.verdict is Verdict.NO_CHANGE
        assert result.breaking == []
        assert len(result.not_evaluated) == 1

    def test_the_markdown_report_discloses_what_it_did_not_score(self) -> None:
        from abicheck.reporter_markdown import to_markdown

        result = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="public",
        )
        text = to_markdown(result)
        assert "Not Evaluated (Contract)" in text
        assert "PROVEN_OUT_OF_CONTRACT" in text
        assert "type_size_changed" in text
        # ...and it reconciles the headline table with the section below it.
        assert "| Not evaluated (contract) | 1 |" in text

    def test_the_json_report_carries_the_canonical_shape(self, tmp_path: Path) -> None:
        old, new = _removal_pair()
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        old_p.write_text(snapshot_to_json(old), encoding="utf-8")
        new_p.write_text(snapshot_to_json(new), encoding="utf-8")
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--format",
                "json",
                "--contract-evaluation",
                "--contract",
                "exports",
            ],
        )
        report = json.loads(result.output)
        removal = next(c for c in report["changes"] if c["kind"] == "func_removed")
        assert removal["contract_relevance"] == "UNKNOWN_UNRESOLVED"
        assert removal["compatibility_evaluation_status"] == "NOT_EVALUATED"
        assert removal["compatibility_decision"] is None
        assert removal["gate_contribution"] == 0


class TestTheDefaultPathIsUntouched:
    """Every pre-existing invocation. The reorder is opt-in or it is a
    regression."""

    @pytest.mark.parametrize("pair", [_removal_pair(), _unreached_public_type_pair()])
    def test_no_finding_is_excluded_without_the_opt_in(self, pair) -> None:
        result = _compare(pair)
        assert result.not_evaluated == []
        assert all(c.contract_relevance is None for c in result.changes)
        assert all(c.compatibility_decision is None for c in result.changes)

    def test_the_verdict_is_unchanged_without_the_opt_in(self) -> None:
        assert _compare(_unreached_public_type_pair()).verdict is Verdict.BREAKING
        assert compare(*_removal_pair()).verdict is Verdict.BREAKING


class TestTheStageClassifiesEveryFinding:
    """The stage runs before the verdict now, so findings appended by a later
    step (and the audit ledgers, which never reach `kept` at all) have to be
    picked up deliberately rather than by being last."""

    def test_findings_added_by_a_later_step_are_classified(self) -> None:
        """`--surface-metrics` and `--pattern-verdicts` append after the first
        classification pass and then recompute the verdict."""
        result = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="all",
            surface_metrics=True,
            pattern_verdicts=True,
        )
        assert result.changes
        assert all(c.contract_relevance is not None for c in result.changes)

    def test_the_audit_ledgers_are_classified_too(self) -> None:
        """A finding public-surface scoping demoted is exactly the
        false-positive-reduction case the evaluator exists to measure, so its
        ledger entry has to carry a decision as well."""
        old, new = _unreached_public_type_pair()
        result = compare(
            old,
            new,
            scope_to_public_surface=True,
            contract_evaluation=True,
            contract_mode="public",
        )
        assert result.out_of_surface_changes
        assert all(
            c.contract_relevance is not None for c in result.out_of_surface_changes
        )

    def test_classification_is_idempotent(self) -> None:
        """`_compute_verdict_for` calls it on every recomputation, so a
        second pass must not duplicate a finding in the decision receipt."""
        from abicheck.contract_pipeline import build_contract_stage
        from abicheck.post_processing import PipelineContext

        old, new = _unreached_public_type_pair()
        stage = build_contract_stage(
            old,
            new,
            scope_to_public_surface=True,
            force_public_symbols=None,
            pp_ctx=PipelineContext(old=old, new=new),
        )
        changes = [Change(ChangeKind.FUNC_REMOVED, "pub_b", "removed")]
        stage.classify(changes)
        stage.classify(changes)
        stage.classify(changes)
        assert len(stage.changes) == 1
