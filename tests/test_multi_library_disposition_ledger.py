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

"""The policy trail survives a multi-library merge (ADR-067).

`merge_results` dropped every per-library `disposition_ledger`, so report
generation rebuilt one from the merged buckets: totals recovered, but every
rule, reason, reclassification and acknowledgment match lost. A release
whose policy demonstrably acted reported an empty policy trail (Codex
review).

Bug class: a conserved audit record discarded by an aggregation step.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.policy.disposition_ledger import DispositionLedger
from abicheck.policy.disposition_merge import merge_disposition_ledgers
from abicheck.policy.disposition_types import Disposition, RuleProvenance


def _ledger(*symbols: str) -> DispositionLedger:
    ledger = DispositionLedger()
    for symbol in symbols:
        ledger.record(
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol=symbol,
                description=f"{symbol} removed",
            ),
            Disposition.SUPPRESSED,
            application_point="test",
            rule=RuleProvenance(rule_id="rule-x", source_file="policy.yml"),
        )
    return ledger


class TestLedgersAreMergedNotReset:
    def test_every_record_survives(self):
        merged = merge_disposition_ledgers(
            [("liba.so", _ledger("a1", "a2")), ("libb.so", _ledger("b1"))]
        )
        assert merged is not None
        assert [r.symbol for r in merged.records] == ["a1", "a2", "b1"]

    def test_each_record_names_its_library(self):
        merged = merge_disposition_ledgers(
            [("liba.so", _ledger("a1")), ("libb.so", _ledger("b1"))]
        )
        assert {r.symbol: r.library for r in merged.records} == {
            "a1": "liba.so",
            "b1": "libb.so",
        }

    def test_the_rule_and_reason_survive(self):
        """The half a rebuild-from-buckets fallback cannot recover, and the
        whole point of ADR-067's "every disposition keeps its rule and
        reason"."""
        merged = merge_disposition_ledgers([("liba.so", _ledger("a1"))])
        record = merged.records[0]
        assert record.disposition is Disposition.SUPPRESSED
        assert record.rule is not None
        assert record.rule.rule_id == "rule-x"
        assert record.rule.source_file == "policy.yml"

    def test_the_same_symbol_in_two_libraries_is_two_dispositions(self):
        """Not deduplicated across libraries: collapsing them would
        under-report the "100 removals, 100 suppressed by rule X" total the
        ledger exists to keep visible."""
        merged = merge_disposition_ledgers(
            [("liba.so", _ledger("shared")), ("libb.so", _ledger("shared"))]
        )
        assert len(merged.records) == 2
        assert sorted(r.library for r in merged.records) == ["liba.so", "libb.so"]

    @pytest.mark.parametrize(
        "ledgers",
        [
            [],
            [("liba.so", None)],
            [("liba.so", None), ("libb.so", None)],
        ],
    )
    def test_no_member_ledger_means_none_not_an_empty_one(self, ledgers):
        """ "Policy never ran" and "policy ran and disposed of nothing" are
        different claims; inventing the second would report a clean, complete
        policy trail the release never had."""
        assert merge_disposition_ledgers(ledgers) is None

    def test_one_member_without_a_ledger_does_not_erase_the_others(self):
        merged = merge_disposition_ledgers(
            [("liba.so", _ledger("a1")), ("libb.so", None)]
        )
        assert merged is not None
        assert [r.symbol for r in merged.records] == ["a1"]

    def test_the_record_serializes_its_library(self):
        merged = merge_disposition_ledgers([("liba.so", _ledger("a1"))])
        assert merged.records[0].to_dict()["library"] == "liba.so"

    def test_a_scalar_record_omits_the_key_entirely(self):
        """Absent, not null: an added key that is always present would change
        the shape of every pre-existing scalar comparison's audit output."""
        assert "library" not in _ledger("a1").records[0].to_dict()


class TestMergeResultsKeepsTheTrail:
    def test_through_the_real_merge(self):
        """Not through the helper alone -- the defect was the *wiring*."""
        from abicheck.checker_policy import Verdict
        from abicheck.checker_types import DiffResult
        from abicheck.compat.multi_library import merge_results

        a = DiffResult(
            old_version="1",
            new_version="2",
            library="liba.so",
            verdict=Verdict.NO_CHANGE,
            disposition_ledger=_ledger("a1"),
        )
        b = DiffResult(
            old_version="1",
            new_version="2",
            library="libb.so",
            verdict=Verdict.NO_CHANGE,
            disposition_ledger=_ledger("b1"),
        )
        merged = merge_results([a, b], label="release")
        assert merged.disposition_ledger is not None
        assert {r.library for r in merged.disposition_ledger.records} == {
            "liba.so",
            "libb.so",
        }
