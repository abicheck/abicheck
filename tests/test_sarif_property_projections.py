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

"""Direct checks of the per-result SARIF property projections.

``report/sarif_properties.py`` holds the optional-field projections a SARIF
result's ``properties`` bag is built from. Each projection omits a key rather
than emitting ``null``, so each test sets exactly one field and asserts the
exact key and value that appear -- the oracle is the field itself, not the
projection's own output shape.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from abicheck.checker import Change, ChangeKind
from abicheck.contract_relevance_types import ContractAssurance, ContractRelevance
from abicheck.report.sarif_properties import (
    _change_detail_properties,
    _contract_properties,
    _reachability_and_impact_properties,
)


def _change(**fields: object) -> Change:
    return Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol="f",
        description="d",
        **fields,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("field", "value", "key"),
    [
        ("old_value", "int", "oldValue"),
        ("new_value", "long", "newValue"),
        ("affected_symbols", ["g"], "affectedSymbols"),
        ("caused_by_type", "T", "causedByType"),
        ("caused_count", 2, "causedCount"),
        ("correlated_change_kind", "type_vtable_changed", "correlatedChangeKind"),
        ("symbol_binding", "weak", "symbolBinding"),
    ],
)
def test_change_detail_projects_each_populated_field(
    field: str, value: object, key: str
) -> None:
    props = _change_detail_properties(_change(**{field: value}))
    assert props == {key: value}


def test_change_detail_omits_empty_fields() -> None:
    assert _change_detail_properties(_change()) == {}
    assert _change_detail_properties(_change(caused_count=0)) == {}


@pytest.mark.parametrize(
    ("field", "value", "key"),
    [
        ("affected_public_roots", ["main"], "affectedPublicRoots"),
        ("impact_proof_path", ["a", "b"], "impactProofPath"),
        ("impact_is_direct", False, "impactIsDirect"),
    ],
)
def test_impact_projects_each_populated_field(
    field: str, value: object, key: str
) -> None:
    props = _reachability_and_impact_properties(_change(**{field: value}))
    assert props == {key: value}


def test_reachability_sub_fields_only_under_public_reachable() -> None:
    reached = _change(
        public_reachable=True,
        reachability_kind="exported",
        reachability_proof_path=["f", "g"],
    )
    assert _reachability_and_impact_properties(reached) == {
        "publicReachable": True,
        "reachabilityKind": "exported",
        "reachabilityProofPath": ["f", "g"],
    }
    # The sub-fields without public reachability are not emitted.
    hidden = _change(reachability_kind="exported", reachability_proof_path=["f"])
    assert _reachability_and_impact_properties(hidden) == {}


def test_contract_properties_carry_the_stamped_decision() -> None:
    change = _change(
        contract_relevance=ContractRelevance.IN_CONTRACT,
        contract_reason_code="R1",
        contract_assurance=ContractAssurance.PARTIAL,
    )
    result = SimpleNamespace(policy=None, policy_file=None)
    props = _contract_properties(change, ContractRelevance.IN_CONTRACT, result, None)
    assert props["contractRelevance"] == "IN_CONTRACT"
    assert props["contractReasonCode"] == "R1"
    assert props["contractAssurance"] == "partial"
    assert props["compatibilityEvaluationStatus"] == "EVALUATED"
    assert props["compatibilityDecision"] is None
    assert isinstance(props["gateContribution"], int)


def test_contract_properties_empty_without_a_contract_run() -> None:
    result = SimpleNamespace(policy=None, policy_file=None)
    assert _contract_properties(_change(), None, result, None) == {}
