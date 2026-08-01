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

"""`scan --against`'s finding projection, at the level of one finding.

`tests/test_scan_compare_parity.py` drives these helpers end to end and pins
their output against `compare`'s. That is the assertion that matters, but it
only ever reaches the *fully stamped* shape: a real
`compare --contract-evaluation` run either stamps all four contract fields or
none. The partial shapes are reachable in practice -- a decision with no
resolved assurance, or one that consulted no provider and so cites nothing --
and this file covers each independently (ADR-049 Phase 5 §6.4).
"""

from __future__ import annotations

from types import SimpleNamespace

from abicheck.checker_policy import ChangeKind
from abicheck.cli_scan_baseline import _baseline_finding_dicts
from abicheck.contract_relevance_types import ContractAssurance, ContractRelevance


def _change(**kwargs):
    base = {
        "kind": ChangeKind.FUNC_REMOVED,
        "symbol": "_Z3barv",
        "description": "Public function removed: bar",
        "source_location": None,
        "old_value": None,
        "new_value": None,
        "contract_relevance": None,
        "contract_reason_code": None,
        "contract_assurance": None,
        "contract_evidence_refs": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


class TestContractFields:
    def test_an_unstamped_finding_carries_no_contract_key_at_all(self):
        """Absent, not `None`: `reporter._add_contract_evaluation_fields`
        omits the keys entirely for a run without `--contract-evaluation`,
        and a consumer must be able to tell "not evaluated" from
        "evaluated, no answer"."""
        (entry,) = _baseline_finding_dicts([_change()], "breaking")
        assert not [k for k in entry if k.startswith("contract_")]
        # The canonical identity is unconditional, though -- it is what makes
        # the finding joinable to `compare`'s, evaluated or not.
        assert entry["finding_id"]

    def test_a_fully_stamped_finding_carries_all_four(self):
        (entry,) = _baseline_finding_dicts(
            [
                _change(
                    contract_relevance=ContractRelevance.IN_CONTRACT,
                    contract_reason_code="public_root_membership",
                    contract_assurance=ContractAssurance.COMPLETE,
                    contract_evidence_refs=("public_header:old",),
                )
            ],
            "breaking",
        )
        assert entry["contract_relevance"] == "IN_CONTRACT"
        assert entry["contract_reason_code"] == "public_root_membership"
        assert entry["contract_assurance"] == "complete"
        assert entry["contract_evidence_refs"] == ["public_header:old"]

    def test_a_decision_with_no_resolved_assurance_omits_only_that_key(self):
        (entry,) = _baseline_finding_dicts(
            [
                _change(
                    contract_relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
                    contract_reason_code="required_evidence_incomplete",
                    contract_evidence_refs=(),
                )
            ],
            "risk",
        )
        assert "contract_assurance" not in entry
        assert entry["contract_relevance"] == "UNKNOWN_UNRESOLVED"
        # An empty list is the real answer for a finding whose relevance
        # follows from its kind alone -- emitted, not omitted, so it stays
        # distinguishable from an unstamped finding.
        assert entry["contract_evidence_refs"] == []

    def test_absent_evidence_refs_omit_the_key(self):
        (entry,) = _baseline_finding_dicts(
            [
                _change(
                    contract_relevance=ContractRelevance.NOT_APPLICABLE,
                    contract_reason_code="non_entity_finding",
                    contract_assurance=ContractAssurance.COMPLETE,
                )
            ],
            "risk",
        )
        assert "contract_evidence_refs" not in entry

    def test_the_suppressed_bucket_still_names_its_rule(self):
        (entry,) = _baseline_finding_dicts(
            [_change(suppression_rule="intentional")], "suppressed"
        )
        assert entry["suppression_rule"] == "intentional"
        assert entry["bucket"] == "suppressed"

    def test_two_findings_differing_only_in_detail_get_distinct_ids(self):
        """`finding_id` has to discriminate, or the parity join would collapse
        two findings into one and report the other as missing."""
        entries = _baseline_finding_dicts(
            [
                _change(description="Public function removed: bar"),
                _change(description="Public function removed: baz"),
            ],
            "breaking",
        )
        assert entries[0]["finding_id"] != entries[1]["finding_id"]

    def test_a_duck_typed_stub_projects_without_a_real_enum(self):
        """`_baseline_finding_dicts`' whole contract is that it stays safe
        against the lightweight fakes the scan tests and sibling modules
        build (its own docstring), so the contract fields must read their
        values the same tolerant way `kind` already does (CodeRabbit
        review)."""
        (entry,) = _baseline_finding_dicts(
            [
                _change(
                    kind="func_removed",
                    contract_relevance="IN_CONTRACT",
                    contract_reason_code="public_root_membership",
                    contract_assurance="complete",
                )
            ],
            "breaking",
        )
        assert entry["kind"] == "func_removed"
        assert entry["contract_relevance"] == "IN_CONTRACT"
        assert entry["contract_assurance"] == "complete"
