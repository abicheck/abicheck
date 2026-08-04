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
from abicheck.model import (
    AbiSnapshot,
    Function,
    RecordType,
    ScopeOrigin,
    TypeField,
    Visibility,
)
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


class TestExplicitScopeReachesTheGateBeforeItComputes:
    """ADR-049 §4.3: an explicit consumer/required-symbol contract is the
    strongest in-contract evidence there is — so it has to be applied
    *before* the scoped gate reads the finding set.

    Promoting afterwards (which is where the aggregate stamping runs) meant
    the scoped exit scored the weaker `UNKNOWN_UNRESOLVED` the export path
    had reached, while the very same run rendered the finding as
    `IN_CONTRACT` with a `BREAKING` decision. Driven end to end, since the
    bug is an ordering between two call sites rather than a wrong value in
    either.
    """

    @staticmethod
    def _changed_signature_pair(tmp_path: Path) -> tuple[Path, Path]:
        """A required symbol that still *exists*, but changed.

        Deliberately not a removal: a removed required symbol is a *missing
        entrypoint*, which floors the scoped exit at 4 through a separate
        path and would mask whichever way the gate went.
        """
        from abicheck.model import Param

        def snap(version: str, ret: str) -> AbiSnapshot:
            return AbiSnapshot(
                library="libfoo.so.1",
                version=version,
                from_headers=True,
                functions=[
                    Function(
                        name="pub_b",
                        mangled="_Z5pub_bi",
                        return_type=ret,
                        params=[Param(name="x", type="int")],
                        visibility=Visibility.PUBLIC,
                    )
                ],
            )

        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        old_p.write_text(snapshot_to_json(snap("1.0", "int")), encoding="utf-8")
        new_p.write_text(snapshot_to_json(snap("2.0", "long")), encoding="utf-8")
        return old_p, new_p

    def _run(self, tmp_path: Path, *extra: str) -> int:
        old_p, new_p = self._changed_signature_pair(tmp_path)
        return (
            CliRunner()
            .invoke(
                main,
                [
                    "compare",
                    str(old_p),
                    str(new_p),
                    "--required-symbol",
                    "_Z5pub_bi",
                    "--exit-code-scheme",
                    "severity",
                    *extra,
                ],
            )
            .exit_code
        )

    def test_a_required_symbol_still_gates_under_an_unresolvable_domain(
        self, tmp_path: Path
    ) -> None:
        """`exports` cannot resolve this pair, but the user explicitly
        declared the symbol part of the contract — which outranks the
        missing export evidence."""
        assert (
            self._run(tmp_path, "--contract-evaluation", "--contract", "exports") == 4
        )

    @pytest.mark.parametrize(
        "extra",
        [
            pytest.param((), id="no-contract-evaluation"),
            pytest.param(("--contract-evaluation", "--contract", "all"), id="all"),
        ],
    )
    def test_it_matches_the_runs_that_never_needed_the_promotion(
        self, tmp_path: Path, extra: tuple[str, ...]
    ) -> None:
        """The two baselines the scoped exit must agree with: the
        un-opted-in run, and the domain that keeps the finding in contract
        on its own."""
        assert self._run(tmp_path, *extra) == 4


class TestEveryRendererTellsTheSameStory:
    """A renderer that reads the unfiltered set contradicts the verdict
    printed at the top of its own output (Codex review of this PR)."""

    def test_leaf_markdown_does_not_file_it_under_a_verdict_section(self) -> None:
        """Leaf mode groups purely by `ChangeKind` and returns before the
        full-mode partition, so it rendered `## Breaking Type Changes`
        beside a `NO_CHANGE` verdict."""
        from abicheck.reporter_markdown import to_markdown

        result = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="public",
        )
        leaf = to_markdown(result, report_mode="leaf")
        assert result.verdict is Verdict.NO_CHANGE
        assert "Breaking Type Changes" not in leaf
        assert "Not Evaluated (Contract)" in leaf
        # Still disclosed, not dropped.
        assert "type_size_changed" in leaf

    def test_leaf_markdown_keeps_a_scored_finding_in_its_verdict_section(
        self,
    ) -> None:
        """The control: the partition only moves excluded findings."""
        from abicheck.reporter_markdown import to_markdown

        result = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="all",
        )
        leaf = to_markdown(result, report_mode="leaf")
        assert "Breaking Type Changes" in leaf
        assert "Not Evaluated (Contract)" not in leaf

    @pytest.mark.parametrize("report_mode", ["full", "leaf"])
    def test_the_severity_table_does_not_claim_an_exit_it_will_not_produce(
        self, report_mode: str
    ) -> None:
        """`Exit Impact` is a claim about the gate, so it has to be
        classified over the set the gate scores — not over every change."""
        from abicheck.reporter_markdown import to_markdown
        from abicheck.severity import SeverityConfig, compute_exit_code

        config = SeverityConfig()
        result = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="public",
        )
        assert compute_exit_code(result.changes, config) == 0
        text = to_markdown(result, report_mode=report_mode, severity_config=config)
        assert "causes non-zero exit" not in text


class TestAnExcludedFindingCannotLaunderItselfBack:
    """A finding that does not score must not reach the gate *indirectly*.

    Three ways it nearly did (Codex review of this PR), all the same shape:
    something downstream still read the unfiltered change list. Excluding a
    finding from policy is only meaningful if everything that *derives* from
    the finding set is excluded with it.
    """

    @staticmethod
    def _pair_with_soname(soname: str = "libfoo.so.1"):
        """The unreached-public-type pair, plus an unchanged ELF SONAME."""
        from abicheck.elf_metadata import ElfMetadata

        old, new = _unreached_public_type_pair()
        old.elf = ElfMetadata(soname=soname)
        new.elf = ElfMetadata(soname=soname)
        return old, new

    def test_it_does_not_derive_a_soname_advisory(self) -> None:
        """The SONAME policy *creates a finding* from the presence of
        breaking ones, and the advisory it creates is `NOT_APPLICABLE` --
        therefore evaluated. Running it over unclassified findings let an
        excluded layout change manufacture an evaluated
        `soname_bump_recommended`, moving `NO_CHANGE` to `COMPATIBLE` (and,
        under a policy that escalates the advisory, into the gate).
        """
        result = _compare(
            self._pair_with_soname(),
            contract_evaluation=True,
            contract_mode="public",
        )
        kinds = [c.kind for c in result.changes]
        assert ChangeKind.SONAME_BUMP_RECOMMENDED not in kinds
        assert result.verdict is Verdict.NO_CHANGE

    def test_the_advisory_is_still_derived_when_the_finding_scores(self) -> None:
        """The control: this is a filter on excluded findings, not a
        disabling of the SONAME policy under `--contract-evaluation`."""
        result = _compare(
            self._pair_with_soname(),
            contract_evaluation=True,
            contract_mode="all",
        )
        kinds = [c.kind for c in result.changes]
        assert ChangeKind.SONAME_BUMP_RECOMMENDED in kinds
        assert result.verdict is Verdict.BREAKING

    def test_the_compatibility_percentages_exclude_it_too(self) -> None:
        """`build_summary`'s percentages are the compatibility axis. Counting
        an excluded finding there reported `verdict: NO_CHANGE` and
        `breaking: 0` beside `binary_compatibility_pct: 0.0` and
        `affected_pct: 100.0` in the same JSON document.
        """
        from abicheck.report_summary import build_summary

        result = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="public",
        )
        summary = build_summary(result)
        assert result.verdict is Verdict.NO_CHANGE
        assert summary.breaking == 0
        assert summary.binary_compatibility_pct == 100.0
        assert summary.affected_pct == 0.0
        # `total_changes` is a count of what the report shows, not of what
        # scored -- the excluded finding is still displayed.
        assert summary.total_changes == 1

    def test_the_html_percentages_exclude_it_too(self) -> None:
        """The HTML renderer computes its own metrics rather than reading
        `build_summary`, so it needs the same filter or the two disagree."""
        from abicheck.html_report import generate_html_report

        result = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="public",
        )
        html = generate_html_report(result)
        # The page's verdict banner and its compatibility percentage have to
        # tell the same story: a NO_CHANGE banner beside "0.0% binary
        # compatibility (1 breaking change)" was the bug.
        assert "NO_CHANGE" in html
        assert "100.0%" in html
        assert "(0 breaking change(s))" in html


class TestEveryReportModeStatesTheSameGateContribution:
    """`--report-mode leaf` builds its own entry dicts rather than routing
    through `_change_to_dict`, so it can silently miss a field the other
    modes carry -- which is exactly what happened to `gate_contribution`."""

    @staticmethod
    def _reachable_type_pair():
        def snap(size: int) -> AbiSnapshot:
            return AbiSnapshot(
                library="libfoo.so",
                version="1",
                functions=[
                    Function(
                        name="api",
                        mangled="api",
                        return_type="Cfg *",
                        visibility=Visibility.PUBLIC,
                    )
                ],
                types=[
                    RecordType(
                        name="Cfg",
                        kind="struct",
                        size_bits=size,
                        fields=[TypeField(name="x", type="int", offset_bits=0)],
                        origin=ScopeOrigin.PUBLIC_HEADER,
                    )
                ],
            )

        return snap(64), snap(128)

    @pytest.mark.parametrize("report_mode", ["full", "leaf"])
    def test_a_gating_finding_states_its_real_contribution(
        self, report_mode: str
    ) -> None:
        from abicheck import reporter
        from abicheck.severity import SeverityConfig

        result = _compare(
            self._reachable_type_pair(),
            contract_evaluation=True,
            contract_mode="all",
        )
        assert result.verdict is Verdict.BREAKING
        kwargs = {"severity_config": SeverityConfig()}
        if report_mode == "leaf":
            kwargs["report_mode"] = "leaf"
        payload = json.loads(reporter.to_json(result, **kwargs))
        entries = payload.get("leaf_changes") or payload["changes"]
        type_entries = [e for e in entries if e["kind"].startswith("type_")]
        assert type_entries, entries
        for entry in type_entries:
            assert entry["compatibility_decision"] == "BREAKING"
            assert entry["gate_contribution"] == 4


class TestExplicitConsumerEvidencePromotesAFinding:
    """`--used-by`/`--required-symbol` stamping runs *after* `compare()`
    returned, so it has to carry the whole decision with it.

    ADR-049 §4.3 ranks explicit consumer/required-symbol evidence as the
    strongest public-contract proof, above header-derived membership — so it
    overrides a weaker `UNKNOWN_UNRESOLVED` the header path reached. Once
    relevance is authoritative, promoting the relevance alone is not enough:
    a stale `NOT_EVALUATED` status would keep the gate excluding a finding a
    real consumer was just proven to depend on, and a stale `None` decision
    would read as "policy declined to score a finding it did score".
    """

    @staticmethod
    def _unresolved_removal():
        from abicheck.contract_pipeline import build_contract_stage
        from abicheck.post_processing import PipelineContext

        old, new = _removal_pair()
        stage = build_contract_stage(
            old,
            new,
            scope_to_public_surface=True,
            force_public_symbols=None,
            pp_ctx=PipelineContext(old=old, new=new),
            contract_mode="exports",
        )
        change = Change(ChangeKind.FUNC_REMOVED, "pub_b", "Public function removed")
        stage.classify([change])
        return change

    def test_promotion_carries_status_and_decision(self) -> None:
        from abicheck.contract_evaluation import stamp_scoped_result_findings
        from abicheck.contract_gating import is_evaluated
        from abicheck.finding_identity import report_finding_id

        change = self._unresolved_removal()
        assert change.contract_relevance is ContractRelevance.UNKNOWN_UNRESOLVED
        assert not is_evaluated(change)

        class _Result:
            changes = [change]
            scoped_relevant_finding_ids = frozenset({report_finding_id(change)})
            scoped_only_changes = ()
            policy = "strict_abi"
            policy_file = None

        stamp_scoped_result_findings(_Result(), finding_id=report_finding_id)

        assert change.contract_relevance is ContractRelevance.IN_CONTRACT
        assert change.compatibility_evaluation_status is (
            CompatibilityEvaluationStatus.EVALUATED
        )
        assert is_evaluated(change)
        # And the decision policy would reach for it, not a stale null.
        assert change.compatibility_decision is Verdict.BREAKING

    def test_a_non_entity_finding_is_left_alone(self) -> None:
        """The documented exception: a SONAME/loader finding is in the scoped
        relevant set for reasons unrelated to a consumer referencing a symbol,
        so overriding it to IN_CONTRACT would be a false decision rather than
        a stronger one. It is already NOT_APPLICABLE, hence already
        evaluated."""
        from abicheck.contract_evaluation import (
            stamp_explicit_scope_contract_evaluation,
        )

        change = Change(ChangeKind.SONAME_CHANGED, "DT_SONAME", "soname changed")
        change.contract_relevance = ContractRelevance.NOT_APPLICABLE
        change.compatibility_evaluation_status = CompatibilityEvaluationStatus.EVALUATED
        stamp_explicit_scope_contract_evaluation(change)
        assert change.contract_relevance is ContractRelevance.NOT_APPLICABLE

    def test_a_plain_dict_entry_gets_the_status_too(self) -> None:
        """A missing-symbol/entrypoint label is a plain dict the caller
        renders directly, not a `Change` — it takes the same fields."""
        from abicheck.contract_evaluation import (
            stamp_explicit_scope_contract_evaluation,
        )

        entry: dict = {"kind": "consumer_required_symbol_missing", "symbol": "pub_b"}
        stamp_explicit_scope_contract_evaluation(entry)
        assert entry["contract_relevance"] == "IN_CONTRACT"
        assert entry["compatibility_evaluation_status"] == "EVALUATED"


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
