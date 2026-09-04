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

- a run that did not opt into `--contract` is bit-for-bit what it
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


class TestTheGateContributionIsAlwaysTheAppliedNumber:
    """`gate_contribution` is defined as what actually gated. Two ways it
    drifted from that (Codex review), both about a *second* gate replacing
    the first after the per-finding number was already written."""

    @staticmethod
    def _uncovered_break_pair(tmp_path: Path) -> tuple[Path, Path]:
        """A public removal that a required-symbol contract does not cover."""
        common = {"library": "libfoo.so.1", "from_headers": True}
        old = AbiSnapshot(
            version="1.0",
            functions=[_fn("keep", "_Z4keepv"), _fn("other", "_Z5otherv")],
            **common,
        )
        new = AbiSnapshot(version="2.0", functions=[_fn("keep", "_Z4keepv")], **common)
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        old_p.write_text(snapshot_to_json(old), encoding="utf-8")
        new_p.write_text(snapshot_to_json(new), encoding="utf-8")
        return old_p, new_p

    def test_a_scoped_out_finding_contributes_zero(self, tmp_path: Path) -> None:
        """Under `--required-symbol` the scoped gate is what the process
        exits on, so a finding outside that contract contributes nothing —
        publishing `4` beside an exit of `0` was the bug."""
        old_p, new_p = self._uncovered_break_pair(tmp_path)
        out = tmp_path / "report.json"
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--required-symbol",
                "_Z4keepv",
                "--contract",
                "all",
                "--format",
                "json",
                "-o",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        report = json.loads(out.read_text(encoding="utf-8"))
        removal = next(c for c in report["changes"] if c["kind"] == "func_removed")
        assert removal["gate_contribution"] == 0
        # The compatibility axis is untouched: the removal is still breaking,
        # and `full_verdict` still says so. Only the gate claim changed.
        assert removal["compatibility_decision"] == "BREAKING"
        assert report["full_verdict"] == "BREAKING"

    def test_an_unscoped_run_keeps_the_full_contribution(self, tmp_path: Path) -> None:
        """The control: without scoping there is no second gate, so the
        full-library number is the applied one."""
        old_p, new_p = self._uncovered_break_pair(tmp_path)
        out = tmp_path / "report.json"
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--contract",
                "all",
                "--format",
                "json",
                "-o",
                str(out),
            ],
        )
        assert result.exit_code == 4, result.output
        report = json.loads(out.read_text(encoding="utf-8"))
        removal = next(c for c in report["changes"] if c["kind"] == "func_removed")
        assert removal["gate_contribution"] == 4

    def test_every_representation_of_the_finding_agrees(self, tmp_path: Path) -> None:
        """`--report-mode root-cause` serializes the same finding twice, into
        `changes[]` and into `root_causes[].findings` — and after the JSON
        round trip those are independent dicts, so zeroing one left the same
        finding reading `0` in one place and `4` in another within a single
        document (Codex review, reproduced)."""
        old_p, new_p = self._uncovered_break_pair(tmp_path)
        out = tmp_path / "report.json"
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--required-symbol",
                "_Z4keepv",
                "--contract",
                "all",
                "--report-mode",
                "root-cause",
                "--format",
                "json",
                "-o",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        report = json.loads(out.read_text(encoding="utf-8"))

        def _contributions(node: object) -> list[int]:
            if isinstance(node, dict):
                found = (
                    [node["gate_contribution"]]
                    if node.get("kind") == "func_removed"
                    and "gate_contribution" in node
                    else []
                )
                for value in node.values():
                    found += _contributions(value)
                return found
            if isinstance(node, list):
                return [n for item in node for n in _contributions(item)]
            return []

        seen = _contributions(report)
        # Both representations must be present, or the test would pass
        # vacuously on a report that simply stopped carrying one of them.
        assert len(seen) >= 2, report
        assert set(seen) == {0}, seen


class TestPromotionNeverLowersAVerdict:
    """The recomputation `appcompat`'s promotion triggers must combine with
    the standing verdict, not replace it (Codex review)."""

    @staticmethod
    def _stamped(kind: ChangeKind, symbol: str) -> Change:
        change = Change(kind, symbol, "x")
        change.contract_relevance = ContractRelevance.IN_CONTRACT
        change.compatibility_evaluation_status = CompatibilityEvaluationStatus.EVALUATED
        return change

    def test_a_redundant_findings_contribution_is_not_dropped(self) -> None:
        """`compare()` scores `kept + verdict_redundant`, and that set is not
        recoverable from the `DiffResult` — `redundant_changes` also carries
        `opaque_filtered`, which is deliberately excluded from the verdict.
        Recomputing from `changes` alone therefore lowered BREAKING to
        COMPATIBLE on an unrelated promotion."""
        from abicheck.checker_types import DiffResult
        from abicheck.contract_scoped_promotion import recompute_verdict_after_promotion

        result = DiffResult(
            old_version="1",
            new_version="2",
            library="lib",
            changes=[self._stamped(ChangeKind.ENUM_MEMBER_ADDED, "E")],
            verdict=Verdict.BREAKING,
            redundant_changes=[Change(ChangeKind.FUNC_REMOVED, "gone", "removed")],
        )
        recompute_verdict_after_promotion(result, policy="strict_abi")
        assert result.verdict is Verdict.BREAKING

    def test_it_still_raises_a_verdict_the_promotion_earns(self) -> None:
        """The control: monotone means it may only go up, not that it is
        frozen — the promotion's whole purpose is to make a proven-in-contract
        finding score."""
        from abicheck.checker_types import DiffResult
        from abicheck.contract_scoped_promotion import recompute_verdict_after_promotion

        result = DiffResult(
            old_version="1",
            new_version="2",
            library="lib",
            changes=[self._stamped(ChangeKind.FUNC_REMOVED, "pub")],
            verdict=Verdict.NO_CHANGE,
        )
        recompute_verdict_after_promotion(result, policy="strict_abi")
        assert result.verdict is Verdict.BREAKING


class TestScanKeepsWhatItDoesNotScore:
    """`scan --against` itemizes findings from the compatibility buckets, so
    filtering those buckets removed excluded findings from its report
    entirely — not merely from its gate (Codex review).

    That is the one outcome ADR-049 D9 forbids outright: a detector fact has
    to land in exactly one *visible* outcome, and "gone" is not one of them.
    """

    @staticmethod
    def _scan(tmp_path: Path, *extra: str) -> dict:
        old, new = _removal_pair()
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        old_p.write_text(snapshot_to_json(old), encoding="utf-8")
        new_p.write_text(snapshot_to_json(new), encoding="utf-8")
        result = CliRunner().invoke(
            main,
            ["scan", str(new_p), "--against", str(old_p), "--format", "json", *extra],
        )
        # A documented nonzero exit is a `SystemExit`, not a failure --
        # anything else is a real traceback the parse below would hide.
        assert isinstance(result.exception, SystemExit | None), result.output
        payload = json.loads(result.output)
        return payload.get("report", payload)

    def test_an_excluded_finding_is_still_itemized(self, tmp_path: Path) -> None:
        report = self._scan(tmp_path, "--contract", "exports")
        diff = report["diff"]
        assert diff["breaking"] == 0
        assert diff["not_evaluated"] == 1
        entries = [f for f in diff["findings"] if f["bucket"] == "not_evaluated"]
        assert [f["kind"] for f in entries] == ["func_removed"]
        # ...with the reason it did not gate, which is what makes the row
        # actionable rather than merely present.
        assert entries[0]["contract_relevance"] == "UNKNOWN_UNRESOLVED"
        assert entries[0]["contract_reason_code"]

    def test_a_scan_row_carries_the_canonical_decision_pair(
        self, tmp_path: Path
    ) -> None:
        """ADR-049 section 6.4 is field-for-field parity, not just matching
        exit codes: a scan row that stated the relevance but not the decision
        could not be compared with `compare`'s finding for the same fact
        (Codex review). `null` is the required value for an unscored row --
        it records that policy never ran."""
        report = self._scan(tmp_path, "--contract", "exports")
        row = next(
            f for f in report["diff"]["findings"] if f["bucket"] == "not_evaluated"
        )
        assert row["compatibility_evaluation_status"] == "NOT_EVALUATED"
        assert row["compatibility_decision"] is None

    def test_an_ordinary_scan_row_states_no_decision_pair(self, tmp_path: Path) -> None:
        """The control: absent, not null. A run that never opted in has no
        contract decision at all, so the whole group stays off the row."""
        report = self._scan(tmp_path)
        row = report["diff"]["findings"][0]
        assert "compatibility_evaluation_status" not in row
        assert "compatibility_decision" not in row

    def test_an_ordinary_scan_is_unchanged(self, tmp_path: Path) -> None:
        """No opt-in means no excluded findings, so the key is absent rather
        than present-and-zero — an ordinary scan summary stays byte-identical."""
        diff = self._scan(tmp_path)["diff"]
        assert diff["breaking"] == 1
        assert "not_evaluated" not in diff


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
        argv = [
            "compare", str(old_p), str(new_p),
            "--required-symbol", "_Z5pub_bi",
            "--severity-preset", "default",
            *extra,
        ]
        return CliRunner().invoke(main, argv).exit_code

    def test_a_required_symbol_still_gates_under_an_unresolvable_domain(
        self, tmp_path: Path
    ) -> None:
        """`exports` cannot resolve this pair, but the user explicitly
        declared the symbol part of the contract — which outranks the
        missing export evidence."""
        assert (
            self._run(tmp_path, "--contract", "exports") == 4
        )

    @pytest.mark.parametrize(
        "extra",
        [
            pytest.param((), id="no-contract-evaluation"),
            pytest.param(("--contract", "all"), id="all"),
        ],
    )
    def test_it_matches_the_runs_that_never_needed_the_promotion(
        self, tmp_path: Path, extra: tuple[str, ...]
    ) -> None:
        """The two baselines the scoped exit must agree with: the
        un-opted-in run, and the domain that keeps the finding in contract
        on its own."""
        assert self._run(tmp_path, *extra) == 4

    def test_the_promotion_does_not_leave_the_full_verdict_stale(
        self, tmp_path: Path
    ) -> None:
        """The promotion mutates the same `Change` objects the full result
        holds, and `DiffResult`'s buckets are computed lazily from the
        current field state — so a `verdict` frozen before the promotion
        disagreed with a summary derived after it: `full_verdict: NO_CHANGE`
        beside `full_summary.breaking: 1` (Codex review).

        Recomputed rather than reverted: ADR-049 §4.3 ranks explicit
        consumer evidence above the export-derived conclusion, so a finding
        it proves in-contract belongs in the full verdict too.
        """
        old_p, new_p = self._changed_signature_pair(tmp_path)
        out = tmp_path / "report.json"
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--required-symbol",
                "_Z5pub_bi",
                "--contract",
                "exports",
                "--format",
                "json",
                "-o",
                str(out),
            ],
        )
        # A documented nonzero exit is a `SystemExit`, not a failure --
        # anything else is a real traceback the parse below would hide.
        assert isinstance(result.exception, SystemExit | None), result.output
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["full_verdict"] == "BREAKING"
        assert report["full_summary"]["breaking"] == 1
        # The scoped view agrees with it rather than contradicting it.
        assert report["verdict"] == "BREAKING"

    @pytest.mark.parametrize(
        "extra_flags",
        [pytest.param((), id="legacy"), pytest.param(("--severity-preset", "default"), id="severity")],
    )
    def test_a_missing_label_carries_the_whole_canonical_shape(
        self, tmp_path: Path, extra_flags: tuple[str, ...]
    ) -> None:
        """A missing required symbol has no backing `Change`, so its
        synthesized entry got neither decision nor contribution — on what is
        frequently the response's only blocking finding (Codex review). Runs
        under both the derived-legacy (no severity setting) and severity
        (`--severity-preset`) schemes -- PR G2 removed the manual pin."""
        old, new = _removal_pair()
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        old_p.write_text(snapshot_to_json(old), encoding="utf-8")
        new_p.write_text(snapshot_to_json(new), encoding="utf-8")
        out = tmp_path / "report.json"
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                # Absent from both sides, so it stays an uncovered label
                # rather than being deduped into the real removal finding.
                "--required-symbol",
                "_Z7missingv",
                "--contract",
                "exports",
                *extra_flags,
                "--format",
                "json",
                "-o",
                str(out),
            ],
        )
        assert result.exit_code == 4, result.output
        report = json.loads(out.read_text(encoding="utf-8"))
        missing = [
            c
            for c in report["changes"]
            if c["kind"].endswith("required_symbol_missing")
        ]
        assert missing, [c["kind"] for c in report["changes"]]
        for entry in missing:
            assert entry["contract_relevance"] == "IN_CONTRACT"
            assert entry["compatibility_evaluation_status"] == "EVALUATED"
            assert entry["compatibility_decision"] == "BREAKING"
            # The number that actually gated: this label is why the run
            # exited 4.
            assert entry["gate_contribution"] == result.exit_code
            # Regression (Codex review, PR #753, fresh evidence): this
            # synthetic entry bypasses _change_to_dict entirely, so it
            # never picked up canonical_finding_id (schema 2.35) the way
            # every other changes[] entry does -- exactly the entry most
            # likely to be the response's only blocking finding.
            assert entry["canonical_finding_id"]


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

    @staticmethod
    def _excluded():
        return _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="public",
        )

    @staticmethod
    def _scored():
        return _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="all",
        )

    def test_sarif_annotates_it_as_a_note_not_an_error(self) -> None:
        """SARIF classified every entry by its effective kind verdict, so a
        finding policy never scored was annotated `level: error` on a run
        that reported `NO_CHANGE` and exited clean (Codex review)."""
        from abicheck.sarif import to_sarif

        result = self._excluded()
        assert result.verdict is Verdict.NO_CHANGE
        entries = to_sarif(result)["runs"][0]["results"]
        entry = next(e for e in entries if e["ruleId"] == "type_size_changed")
        assert entry["level"] == "note"
        # Downgraded, not dropped -- and it says why.
        props = entry["properties"]
        assert props["compatibilityEvaluationStatus"] == "NOT_EVALUATED"
        assert props["contractRelevance"] == "PROVEN_OUT_OF_CONTRACT"

    def test_sarif_keeps_error_for_a_scored_finding(self) -> None:
        """The control: this is a filter on excluded findings, not a blanket
        SARIF downgrade under `--contract`."""
        from abicheck.sarif import to_sarif

        entries = to_sarif(self._scored())["runs"][0]["results"]
        entry = next(e for e in entries if e["ruleId"] == "type_size_changed")
        assert entry["level"] == "error"

    def test_junit_does_not_report_it_as_a_failure(self) -> None:
        """Same shape one renderer over: one `<failure>` beside a
        `NO_CHANGE` verdict and a clean exit (Codex review)."""
        from abicheck.junit_report import to_junit_xml

        xml = to_junit_xml(self._excluded())
        assert 'failures="0"' in xml
        # The testcase itself is still there -- excluded from failing, not
        # from the report (ADR-049 D9). JUnit names a testcase by symbol and
        # only spells the kind inside a `<failure>`, so a passing one looks
        # exactly like any other compatible finding's, which is the point.
        assert '<testcase name="Internal"' in xml
        assert 'tests="1"' in xml

    def test_junit_still_fails_on_a_scored_finding(self) -> None:
        from abicheck.junit_report import to_junit_xml

        assert 'failures="1"' in to_junit_xml(self._scored())

    def test_the_review_digest_does_not_list_it_as_impacted(self) -> None:
        """The digest prints its merge advice from the verdict and its
        impacted-symbol list from the raw change set, so it said "safe to
        merge" directly above the symbol it called impacted (Codex review)."""
        from abicheck.reporter_markdown import to_review_digest

        digest = to_review_digest(self._excluded())
        assert "safe to merge" in digest
        assert "Top impacted symbols" not in digest

    def test_the_review_digest_lists_a_scored_finding(self) -> None:
        from abicheck.reporter_markdown import to_review_digest

        digest = to_review_digest(self._scored())
        assert "Top impacted symbols" in digest
        assert "Internal" in digest

    def test_the_workflow_annotation_is_a_notice_not_an_error(self) -> None:
        """A GitHub annotation states how a finding gated. `::error` on a
        comparison whose verdict is NO_CHANGE and whose gate is clean put a
        red inline annotation on a passing PR (Codex review)."""
        from abicheck.annotations import collect_annotations

        (annotation,) = collect_annotations(self._excluded())
        assert annotation[1].startswith("::notice")
        assert "Not evaluated (contract)" in annotation[1]

    def test_the_workflow_annotation_is_an_error_for_a_scored_finding(self) -> None:
        from abicheck.annotations import collect_annotations

        (annotation,) = collect_annotations(self._scored())
        assert annotation[1].startswith("::error")

    def test_the_html_report_does_not_file_it_under_a_verdict_section(self) -> None:
        """The HTML page computes its own buckets, so the metric filter alone
        left the finding under the red "Changed Symbols (1)" heading beside a
        NO_CHANGE banner and 100% compatibility, with the relevance nowhere on
        the page (Codex review)."""
        from abicheck.html_report import generate_html_report

        page = generate_html_report(self._excluded())
        assert "Changed Symbols" not in page
        assert "Not Evaluated (Contract)" in page
        # Disclosed with the reason, which is what makes the section useful
        # rather than merely non-contradictory.
        assert "PROVEN_OUT_OF_CONTRACT" in page

    def test_the_html_report_keeps_a_scored_finding_in_its_section(self) -> None:
        from abicheck.html_report import generate_html_report

        page = generate_html_report(self._scored())
        assert "Changed Symbols" in page
        assert "Not Evaluated (Contract)" not in page

    def test_the_filtered_summary_agrees_with_the_main_one(self) -> None:
        """`--show-only` builds a second set of counters over the *displayed*
        changes, so the main summary read `breaking: 0` beside
        `filtered_summary.breaking: 1` in one document (Codex review)."""
        from abicheck.reporter import to_json

        report = json.loads(to_json(self._excluded(), show_only="breaking"))
        assert report["summary"]["breaking"] == 0
        assert report["filtered_summary"]["breaking"] == 0
        # `total_changes` stays inclusive in both: it counts what is shown.
        assert report["filtered_summary"]["total_changes"] == 1


class TestTheReleaseRecommendationDoesNotOverclaim:
    """`recommend_release` is documented as automation-grade advice, so a
    `NO_CHANGE` verdict reached over evidence that did not close must not
    serialize as a confident "no bump required" (Codex review)."""

    def test_an_unresolved_run_is_review_not_actionable(self) -> None:
        from abicheck.semver import (
            ReleaseRecommendationState,
            SonameAction,
            recommend_release,
        )

        result = compare(
            *_removal_pair(), contract_evaluation=True, contract_mode="exports"
        )
        assert result.verdict is Verdict.NO_CHANGE
        rec = recommend_release(result)
        assert rec.state is ReleaseRecommendationState.REVIEW
        assert rec.soname is SonameAction.NOT_DETERMINED
        assert "could not resolve" in rec.rationale

    def test_a_proven_exclusion_stays_actionable(self) -> None:
        """The distinction ADR-049 draws, and the one the coverage exit draws
        too: proven-out-of-contract is a *determination*, so "no bump" really
        is well-founded. Only the unknown cases are non-actionable."""
        from abicheck.semver import ReleaseRecommendationState, recommend_release

        result = _compare(
            _unreached_public_type_pair(),
            contract_evaluation=True,
            contract_mode="public",
        )
        assert result.verdict is Verdict.NO_CHANGE
        rec = recommend_release(result)
        assert rec.state is ReleaseRecommendationState.ACTIONABLE

    def test_a_run_without_the_opt_in_is_unchanged(self) -> None:
        from abicheck.semver import ReleaseRecommendationState, recommend_release

        old, _ = _removal_pair()
        rec = recommend_release(compare(old, old))
        assert rec.state is ReleaseRecommendationState.ACTIONABLE
        assert "No ABI or API changes detected" in rec.rationale

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
        disabling of the SONAME policy under `--contract`."""
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
        from abicheck.contract_gating import is_evaluated
        from abicheck.contract_scoped_promotion import stamp_scoped_result_findings
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
        from abicheck.contract_scoped_promotion import (
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
        from abicheck.contract_scoped_promotion import (
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
                "--contract",
                "exports",
            ],
        )
        # A documented nonzero exit is a `SystemExit`, not a failure --
        # anything else is a real traceback the parse below would hide.
        assert isinstance(result.exception, SystemExit | None), result.output
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


class TestForcedPublicSymbolsWidenTheCommittedRootAllowlist:
    """Codex review, fresh evidence: `--post-manifest` and `--public-symbol`
    combined with header scoping (`_run_allowlist` in `post_processing.py`)
    keeps a `Change` on the forced symbol as a widening overlay -- but
    `build_contract_stage`'s own `committed_roots` (threaded into
    `directly_referenced_stdlib_type_spellings` to scope which declarations
    may seed a stdlib direct-reference root) never consulted
    `force_public_symbols` at all. An uncommitted-but-forced-public
    `api(vector<int>)` was rejected as a root, so a `std::vector` layout
    break under it classified `UNKNOWN_UNRESOLVED` (gate: `NO_CHANGE`) even
    though the identical comparison without the manifest is `BREAKING`."""

    @staticmethod
    def _pair() -> tuple[AbiSnapshot, AbiSnapshot]:
        from abicheck.model import Param

        rec_name = "basic_string<char, std::char_traits<char>, std::allocator<char> >"

        def snap(has_cxx11: bool) -> AbiSnapshot:
            qname = ("std::__cxx11::" if has_cxx11 else "std::") + rec_name
            return AbiSnapshot(
                library="libfoo.so",
                version="1",
                functions=[
                    Function(
                        name="stable",
                        mangled="stable",
                        return_type="void",
                        visibility=Visibility.PUBLIC,
                        origin=ScopeOrigin.PUBLIC_HEADER,
                    ),
                    Function(
                        name="api",
                        mangled="api",
                        return_type="void",
                        params=[Param(name="s", type=rec_name)],
                        visibility=Visibility.PUBLIC,
                        origin=ScopeOrigin.PUBLIC_HEADER,
                    ),
                ],
                types=[
                    RecordType(
                        name=rec_name,
                        qualified_name=qname,
                        kind="class",
                        size_bits=40 if has_cxx11 else 32,
                    )
                ],
            )

        return snap(False), snap(True)

    def test_an_uncommitted_but_forced_public_root_still_confirms_the_break(
        self,
    ) -> None:
        old, new = self._pair()
        # `stable` is the only committed export; `api` (which names the
        # stdlib type) is uncommitted but explicitly forced public.
        result = compare(
            old,
            new,
            scope_to_public_surface=True,
            contract_evaluation=True,
            contract_mode="public",
            public_surface_allowlist={"stable"},
            force_public_symbols={"api"},
        )
        change = next(
            c for c in result.changes if c.kind is ChangeKind.TYPE_SIZE_CHANGED
        )
        assert change.contract_relevance is ContractRelevance.IN_CONTRACT
        assert change.compatibility_decision is Verdict.BREAKING
        assert result.verdict is Verdict.BREAKING

    def test_without_the_forced_symbol_it_stays_unresolved(self) -> None:
        """The control: dropping `force_public_symbols` reverts to the
        pre-fix, manifest-only behavior -- confirming the widening, not a
        change to the manifest's own baseline scoping."""
        old, new = self._pair()
        result = compare(
            old,
            new,
            scope_to_public_surface=True,
            contract_evaluation=True,
            contract_mode="public",
            public_surface_allowlist={"stable"},
        )
        change = next(
            c for c in result.changes if c.kind is ChangeKind.TYPE_SIZE_CHANGED
        )
        assert change.contract_relevance is ContractRelevance.UNKNOWN_UNRESOLVED
        assert result.verdict is Verdict.NO_CHANGE

    def test_without_header_scoping_the_widening_overlay_is_not_applied(
        self,
    ) -> None:
        """Mirrors `_run_allowlist`'s own gate exactly: the CLI already warns
        `--public-symbol` is ignored under `--no-scope-public-headers`, so
        `committed_roots` must not be widened when `scope_to_public_surface`
        is False either -- applying it unconditionally would contradict that
        warning."""
        old, new = self._pair()
        result = compare(
            old,
            new,
            scope_to_public_surface=False,
            contract_evaluation=True,
            contract_mode="public",
            public_surface_allowlist={"stable"},
            force_public_symbols={"api"},
        )
        change = next(
            c for c in result.changes if c.kind is ChangeKind.TYPE_SIZE_CHANGED
        )
        assert change.contract_relevance is ContractRelevance.UNKNOWN_UNRESOLVED
