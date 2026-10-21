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

"""The ADR-084 binary-gate marker on a finding: its rule id, and the test for it.

A leaf with no imports. :mod:`~abicheck.policy.binary_gated_contract` owns the
rule, but it imports :mod:`~abicheck.policy.rule_provenance`, which reaches back
into the verdict classifiers. Those classifiers must read the marker, so it
lives here, where importing it closes no cycle.
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = [
    "BINARY_GATE_RULE_ID",
    "binary_gate_assurance_notes",
    "is_binary_gate_demoted",
]

#: The rule id a demoted finding carries (``modulation_rule`` and the ADR-067
#: ledger's ``rule.rule_id``).
BINARY_GATE_RULE_ID = "binary_gated_api_break"


def is_binary_gate_demoted(change: object) -> bool:
    """Whether the ADR-084 gate demoted *change* (read off its rule id, not its verdict)."""
    return getattr(change, "modulation_rule", None) == BINARY_GATE_RULE_ID


def binary_gate_assurance_notes(changes: Iterable[object]) -> list[str]:
    """The analysis-assurance note stating that the gate narrowed this run.

    A demotion is a conclusion the evidence could not support, not a clean
    result, so the report says so (``tests/test_family_f1_evidence_ablation.py``
    oracle (a)). Empty when nothing was demoted.
    """
    count = sum(1 for change in changes if is_binary_gate_demoted(change))
    if not count:
        return []
    return [
        f"source compatibility unverified: {count} API-break finding(s) reported as "
        "compatible-with-risk because neither headers nor sources observed the entity"
    ]
