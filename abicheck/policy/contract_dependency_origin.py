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

"""The contract's exclusion of dependency-internal record presence changes.

A header-only dependency compiled into a library contributes every template
instance the library's own code happened to emit. Those instances are
``TYPE_ADDED``/``TYPE_REMOVED`` when the dependency's codegen differs between
two builds (``-mavx2`` against ``-mavx512f``), and they bury the changes the
library made. The workflow layer decides which record names are dependency
internals no public declaration reaches
(:func:`abicheck.workflows.dump.dwarf_dependency_types.unreachable_dependency_type_names`);
this rule applies that decision to the findings.

The rule runs after the domain decision and after the toolchain rule, so a
type the contract already places in its surface keeps its decision, and a
finding a caller declared public keeps gating (``public_root_membership``).
The excluded finding is still recorded, with reason
``dependency_internal_unreachable``, so ``scope_reasons`` in the disposition
audit counts it. Only the presence kinds are handled here; a size or layout
change on the same type is not presence evidence and is left to its own rule.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import replace
from typing import Any

from ..contract_relevance_types import (
    NON_ENTITY_RELEVANCE,
    ContractAssurance,
    ContractRelevance,
)
from ..model.change_catalog.kinds import ChangeKind

__all__ = [
    "DEPENDENCY_INTERNAL_UNREACHABLE_REASON",
    "exclude_unreachable_dependency_findings",
]

#: The reason code an excluded finding carries (a key of
#: ``contract_relevance_types.CONTRACT_REASON_CODES``).
DEPENDENCY_INTERNAL_UNREACHABLE_REASON = "dependency_internal_unreachable"

_PUBLIC_ROOT_REASON = "public_root_membership"

#: A type's presence changed. A removed type is judged on the old side's
#: names, an added one on the new side's.
_PRESENCE_KINDS: frozenset[ChangeKind] = frozenset(
    {ChangeKind.TYPE_ADDED, ChangeKind.TYPE_REMOVED}
)


def exclude_unreachable_dependency_findings(
    changes: Sequence[Any],
    decisions: Sequence[Any],
    *,
    old_names: Collection[str],
    new_names: Collection[str],
) -> list[Any]:
    """*decisions* with every unreachable dependency-internal presence finding
    made out of contract.

    *changes* and *decisions* are parallel sequences, as in
    :func:`~abicheck.policy.contract_toolchain_origin.exclude_toolchain_runtime_findings`.
    *old_names*/*new_names* are each side's
    :func:`~abicheck.workflows.dump.dwarf_dependency_types.unreachable_dependency_type_names`.
    A decision already out of contract is returned as is, so the toolchain
    rule's reason is never overwritten.
    """
    out: list[Any] = []
    for change, decision in zip(changes, decisions, strict=True):
        if (
            decision.relevance is not NON_ENTITY_RELEVANCE
            and decision.relevance is not ContractRelevance.PROVEN_OUT_OF_CONTRACT
            and decision.reason_code != _PUBLIC_ROOT_REASON
            and _is_unreachable_dependency_presence(change, old_names, new_names)
        ):
            decision = replace(
                decision,
                relevance=ContractRelevance.PROVEN_OUT_OF_CONTRACT,
                reason_code=DEPENDENCY_INTERNAL_UNREACHABLE_REASON,
                assurance=ContractAssurance.COMPLETE,
            )
        out.append(decision)
    return out


def _is_unreachable_dependency_presence(
    change: Any,
    old_names: Collection[str],
    new_names: Collection[str],
) -> bool:
    kind = getattr(change, "kind", None)
    symbol = change.symbol or ""
    if kind not in _PRESENCE_KINDS or not symbol:
        return False
    names = new_names if kind is ChangeKind.TYPE_ADDED else old_names
    return symbol in names
