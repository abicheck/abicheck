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

"""The contract's exclusion of a dependency's unreachable internals.

A header-only dependency compiled into a library contributes every template
instance, hidden friend, inline variable and member typedef the library's own
code happened to instantiate. They change whenever the dependency's codegen
differs between two builds (``-mavx2`` against ``-mavx512f``), and they bury
the changes the library made. The workflow layer decides, per side, which of
those entities are dependency internals no public declaration of the library
reaches (:func:`abicheck.workflows.dump.dependency_internals.dependency_internals`,
for DWARF-only and header-derived snapshots alike); this rule applies that
decision to the findings.

The rule runs after the domain decision and after the toolchain rule. A
finding on a symbol the caller declared public (``--public-symbol``, a
post-manifest commitment) keeps its decision; the caller passes that test as
*is_declared_public*. The reason code ``public_root_membership`` is not that
test: the header domain also stamps it on everything its closure reaches, and
the closure runs through a dependency's headers, which is what this rule
narrows. The excluded finding is still recorded, with reason
``dependency_internal_unreachable``, so ``scope_reasons`` in the disposition
audit counts it.

Which findings a side's :class:`DependencyInternals` can move:

* a record's **presence** (``TYPE_ADDED``/``TYPE_REMOVED``, and a
  ``TAG_TYPE_RENAMED`` whose old and new names are both internal). A size or
  layout change on the same type is not presence evidence and keeps its own
  rule. An ``INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API`` raised on such a record
  restates that record's own finding through the dependency's own
  declarations (its implicit members), so it follows the record, judged on
  either side;
* a **typedef**'s presence or target (``TYPEDEF_REMOVED``,
  ``TYPEDEF_BASE_CHANGED``). A typedef has no layout of its own; its target is
  all it states;
* any **function or variable** finding on a symbol the dependency declares and
  the library's binary does not export, and an ``OVERLOAD_SET_REROUTED`` whose
  overload set is made only of such functions on both sides.

An added entity is judged on the new side, a removed one on the old side, and
a modified one must be internal on both.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from ..contract_relevance_types import (
    NON_ENTITY_RELEVANCE,
    ContractAssurance,
    ContractRelevance,
)
from ..model.change_catalog.assembled import REGISTRY
from ..model.change_catalog.dimensions import ChangeEntity, ChangeOperation
from ..model.change_catalog.kinds import ChangeKind

__all__ = [
    "DEPENDENCY_INTERNAL_UNREACHABLE_REASON",
    "NO_DEPENDENCY_INTERNALS",
    "DependencyInternals",
    "exclude_unreachable_dependency_findings",
]

#: The reason code an excluded finding carries (a key of
#: ``contract_relevance_types.CONTRACT_REASON_CODES``).
DEPENDENCY_INTERNAL_UNREACHABLE_REASON = "dependency_internal_unreachable"


@dataclass(frozen=True)
class DependencyInternals:
    """One side's dependency entities no public declaration of the library
    reaches, keyed the way findings name them.

    ``types`` and ``typedefs`` are bare names (a finding's ``symbol``);
    ``symbols`` are the mangled names of functions and variables;
    ``overload_stems`` are the qualified names, template arguments stripped,
    under which every function of the side is one of ``symbols``.
    """

    types: frozenset[str] = frozenset()
    typedefs: frozenset[str] = frozenset()
    symbols: frozenset[str] = frozenset()
    overload_stems: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.types or self.typedefs or self.symbols or self.overload_stems)


NO_DEPENDENCY_INTERNALS = DependencyInternals()

#: Kinds judged on record names, with the side(s) each one is judged on.
_TYPE_PRESENCE_KINDS: dict[ChangeKind, ChangeOperation] = {
    ChangeKind.TYPE_ADDED: ChangeOperation.ADDED,
    ChangeKind.TYPE_REMOVED: ChangeOperation.REMOVED,
    ChangeKind.TAG_TYPE_RENAMED: ChangeOperation.MODIFIED,
}
#: Raised on a record by another finding about it (``caused_by_type``).
_RECORD_DERIVED_KINDS = frozenset({ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API})
_TYPEDEF_KINDS: dict[ChangeKind, ChangeOperation] = {
    ChangeKind.TYPEDEF_REMOVED: ChangeOperation.REMOVED,
    ChangeKind.TYPEDEF_BASE_CHANGED: ChangeOperation.MODIFIED,
}
_DECLARATION_ENTITIES = frozenset({ChangeEntity.FUNCTION, ChangeEntity.VARIABLE})


def exclude_unreachable_dependency_findings(
    changes: Sequence[Any],
    decisions: Sequence[Any],
    *,
    old: DependencyInternals,
    new: DependencyInternals,
    is_declared_public: Callable[[Any], bool] | None = None,
) -> list[Any]:
    """*decisions* with every finding on an unreachable dependency internal
    made out of contract.

    *changes* and *decisions* are parallel sequences, as in
    :func:`~abicheck.policy.contract_toolchain_origin.exclude_toolchain_runtime_findings`.
    *old*/*new* are each side's
    :func:`~abicheck.workflows.dump.dependency_internals.dependency_internals`.
    A decision already out of contract is returned as is, so the toolchain
    rule's reason is never overwritten, and so is a finding
    *is_declared_public* accepts.
    """
    if not old and not new:
        return list(decisions)
    out: list[Any] = []
    for change, decision in zip(changes, decisions, strict=True):
        if (
            decision.relevance is not NON_ENTITY_RELEVANCE
            and decision.relevance is not ContractRelevance.PROVEN_OUT_OF_CONTRACT
            and _is_unreachable_dependency_finding(change, old, new)
            and not (is_declared_public is not None and is_declared_public(change))
        ):
            decision = replace(
                decision,
                relevance=ContractRelevance.PROVEN_OUT_OF_CONTRACT,
                reason_code=DEPENDENCY_INTERNAL_UNREACHABLE_REASON,
                assurance=ContractAssurance.COMPLETE,
            )
        out.append(decision)
    return out


def _on_sides(
    operation: ChangeOperation,
    old_name: str,
    new_name: str,
    old_names: frozenset[str],
    new_names: frozenset[str],
) -> bool:
    if operation is ChangeOperation.ADDED:
        return new_name in new_names
    if operation is ChangeOperation.REMOVED:
        return old_name in old_names
    return old_name in old_names and new_name in new_names


def _is_unreachable_dependency_finding(
    change: Any, old: DependencyInternals, new: DependencyInternals
) -> bool:
    kind = getattr(change, "kind", None)
    symbol = getattr(change, "symbol", None) or ""
    if not isinstance(kind, ChangeKind) or not symbol:
        return False
    if kind in _TYPE_PRESENCE_KINDS:
        # A rename names its new spelling in ``new_value``; the other presence
        # kinds name one entity.
        renamed = getattr(change, "new_value", None) or symbol
        new_name = renamed if kind is ChangeKind.TAG_TYPE_RENAMED else symbol
        return _on_sides(
            _TYPE_PRESENCE_KINDS[kind], symbol, new_name, old.types, new.types
        )
    if kind in _RECORD_DERIVED_KINDS:
        return symbol in old.types or symbol in new.types
    if kind in _TYPEDEF_KINDS:
        return _on_sides(
            _TYPEDEF_KINDS[kind], symbol, symbol, old.typedefs, new.typedefs
        )
    if kind is ChangeKind.OVERLOAD_SET_REROUTED:
        return symbol in old.overload_stems and symbol in new.overload_stems
    meta = REGISTRY.get(kind.value)
    if meta is None or meta.entity not in _DECLARATION_ENTITIES:
        return False
    operation = meta.operation or ChangeOperation.MODIFIED
    return _on_sides(operation, symbol, symbol, old.symbols, new.symbols)
