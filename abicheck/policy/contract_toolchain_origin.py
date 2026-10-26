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

"""The contract's exclusion of C++ runtime / toolchain symbols.

A finding on a symbol the C++ runtime or the toolchain emitted (a ``std::``,
``__gnu_cxx::`` or ``__cxxabiv1::`` internal, a function-local static or guard of
a standard template, a libgcc/libc helper) is not a library entity under any
contract domain. Left to the header or export domain, a removed one reads
``UNKNOWN_UNRESOLVED`` (no header declares it) and gates the run: the field
report's C-API check exited 1 on six of them.

The rule is applied after the domain decision, so a symbol the contract places
in its own surface keeps its decision, and any symbol the caller declared public
keeps gating (``public_root_membership``). The excluded finding is still
recorded, with reason ``toolchain_runtime_origin``. Its ``NOT_APPLICABLE``
findings (cross-source hygiene) are left alone.

The symbol set comes from :func:`abicheck.buildsource.export_accounting.toolchain_runtime_symbol_names`,
the origin classification the symbol-hygiene audit reports.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import replace
from typing import Any

from ..contract_relevance_types import (
    NON_ENTITY_RELEVANCE,
    ContractAssurance,
    ContractRelevance,
)
from ..model.change_catalog.assembled import REGISTRY
from ..model.change_catalog.dimensions import ContractSubject
from ..model.change_catalog.kinds import ChangeKind

__all__ = [
    "DEPENDENCY_EDGE_KINDS",
    "DEPENDENCY_EDGE_KIND_SLUGS",
    "TOOLCHAIN_RUNTIME_ORIGIN_REASON",
    "exclude_toolchain_runtime_findings",
]

#: The reason code an excluded finding carries (a key of
#: ``contract_relevance_types.CONTRACT_REASON_CODES``).
TOOLCHAIN_RUNTIME_ORIGIN_REASON = "toolchain_runtime_origin"

_PUBLIC_ROOT_REASON = "public_root_membership"

#: Kinds whose subject is an edge from this library to another one (an
#: imported symbol, a ``DT_NEEDED`` entry, a required version node, a resolved
#: provider), not a declaration this library makes: the catalog's
#: ``contract_subject`` is ``dependency_edge`` (ADR-049 amendment). Like every
#: non-declaration kind they are ``NOT_APPLICABLE`` to the contract: policy
#: scores them exactly as before, and they never reach contract coverage. Left
#: to the header domain, an import read ``UNKNOWN_UNRESOLVED`` and raised exit
#: 1 on every toolchain drift (the SVS field report).
DEPENDENCY_EDGE_KIND_SLUGS: frozenset[str] = REGISTRY.contract_subject_kinds(
    ContractSubject.DEPENDENCY_EDGE
)
DEPENDENCY_EDGE_KINDS: frozenset[ChangeKind] = frozenset(
    ChangeKind(k) for k in DEPENDENCY_EDGE_KIND_SLUGS
)

#: Kinds whose detector has already attributed the symbol to an external
#: dependency (``export_existence_wording.ACCOUNT_EXTERNAL_DEP``).
_EXTERNAL_ORIGIN_KINDS: frozenset[ChangeKind] = frozenset(
    {ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED}
)


def exclude_toolchain_runtime_findings(
    changes: Sequence[Any],
    decisions: Sequence[Any],
    toolchain_symbols: Collection[str],
    *,
    is_runtime_name: Callable[[str], bool] | None = None,
) -> list[Any]:
    """*decisions* with every runtime-owned, unexcluded finding made out of contract.

    *changes* and *decisions* are parallel sequences of a finding and its
    :class:`~abicheck.contract_evaluation.ContractEvaluationDecision`. Returns
    the decisions in order; a decision that is not replaced is returned as is.

    A finding is runtime-owned when its symbol is in *toolchain_symbols* (the
    snapshots' own ELF tables), when *is_runtime_name* says so for a symbol no
    table lists, or when its kind already attributes the symbol to an external
    dependency.
    """
    out: list[Any] = []
    for change, decision in zip(changes, decisions, strict=True):
        if (
            decision.relevance is not NON_ENTITY_RELEVANCE
            and decision.reason_code != _PUBLIC_ROOT_REASON
        ):
            reason = _exclusion_reason(change, toolchain_symbols, is_runtime_name)
            if reason is not None:
                decision = replace(
                    decision,
                    relevance=ContractRelevance.PROVEN_OUT_OF_CONTRACT,
                    reason_code=reason,
                    assurance=ContractAssurance.COMPLETE,
                )
        out.append(decision)
    return out


def _exclusion_reason(
    change: Any,
    toolchain_symbols: Collection[str],
    is_runtime_name: Callable[[str], bool] | None,
) -> str | None:
    kind = getattr(change, "kind", None)
    symbol = change.symbol or ""
    if (
        kind in _EXTERNAL_ORIGIN_KINDS
        or (symbol and symbol in toolchain_symbols)
        or (is_runtime_name is not None and is_runtime_name(symbol))
    ):
        return TOOLCHAIN_RUNTIME_ORIGIN_REASON
    return None
