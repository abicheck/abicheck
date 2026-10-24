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


"""ADR-061 D9 taxonomy: suppression-disposition audit ChangeKind entries.

Records produced by applying a suppression rule, not by reading an artifact:
an informational audit record that a rule hid changes without graph coverage.
Kept apart from ``source.py`` (already at its line cap) and never changes a
verdict or exit code.
"""

from __future__ import annotations

from .registry import (
    ChangeEntity,
    ChangeKindMeta,
    ChangeOperation,
    ContractSubject,
    Verdict,
)

_C = Verdict.COMPATIBLE
_E = ChangeKindMeta
_ENT = ChangeEntity
_OP = ChangeOperation
_CS = ContractSubject

DISPOSITION_ENTRIES: list[ChangeKindMeta] = [
    _E(
        "graph_coverage_insufficient_for_suppression",
        _C,
        impact="Informational audit record: a suppression rule using the default "
        "`unreachable-only` gate hid changes whose reachability graph coverage "
        "never established (unknown, not proven unreachable). One record per rule "
        "carries the count and the suppressed change ids. Never changes the verdict "
        "or exit code.",
        entity=_ENT.ANALYSIS,
        contract_subject=_CS.ANALYSIS,
        operation=_OP.MODIFIED,
    ),
]
