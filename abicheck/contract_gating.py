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

"""ADR-049 D1: which findings compatibility policy and the change gate see.

:mod:`abicheck.contract_relevance_types` answers this for a
:class:`~abicheck.contract_relevance_types.ContractRelevance` *value*; this
module answers it for a *finding object*, which is what every consumer
actually holds. Two callers need that question answered and must agree on the
answer, or a finding could score in one and not the other:

- ``checker._compute_verdict_for`` -- the compatibility decision;
- ``severity.compute_exit_code``/``compute_gate_decision`` -- the change gate.

The rule (ADR-049 D1): a finding whose contract relevance is
``IN_CONTRACT`` or ``NOT_APPLICABLE`` is evaluated and gates normally.
``PROVEN_OUT_OF_CONTRACT``, ``UNKNOWN_UNPROVEN`` and ``UNKNOWN_UNRESOLVED``
are *not evaluated* by compatibility policy and contribute ``0`` to the
change gate. They are never deleted, never re-kinded, and stay fully visible
in the report -- an excluded finding is an audited one, not a hidden one, and
the orthogonal contract-coverage ledger
(:mod:`abicheck.contract_coverage_ledger`) independently contributes its own
exit ``1`` for the unresolved case.

**An unstamped finding is evaluated.** ``contract_relevance is None`` means
the run never opted into ``--contract-evaluation`` at all (it is off by
default), so there is no contract decision to defer to and the legacy
behaviour -- every finding scores -- is the correct and only answer. That
default is what keeps this module bit-for-bit inert for every pre-existing
invocation, and it is also why the helpers below read the attribute with
``getattr(..., None)``: ``severity.py``'s gate functions accept any object
with a ``.kind`` (``HasKind``), including lightweight stubs that carry no
contract fields at all.

Leaf module by construction: it imports only the reserved Phase 0 vocabulary,
so ``severity.py`` (which nearly everything imports) can depend on it without
growing the import graph.
"""

from __future__ import annotations

from .contract_relevance_types import (
    CompatibilityEvaluationStatus,
    ContractRelevance,
    evaluation_status_for,
)


def contract_relevance_of(change: object) -> ContractRelevance | None:
    """Return *change*'s stamped contract relevance, or ``None`` if unstamped."""
    relevance = getattr(change, "contract_relevance", None)
    return relevance if isinstance(relevance, ContractRelevance) else None


def evaluation_status_of(change: object) -> CompatibilityEvaluationStatus | None:
    """Return *change*'s compatibility-evaluation status, or ``None``.

    Prefers the status the pipeline actually stamped
    (``Change.compatibility_evaluation_status``) over re-deriving one from the
    relevance: a stamped value is what the run scored on, and a re-derivation
    that disagreed with it would be exactly the split this module exists to
    prevent. Falls back to deriving it from a stamped relevance so a finding
    that carries only the older field is still answered correctly.
    """
    status = getattr(change, "compatibility_evaluation_status", None)
    if isinstance(status, CompatibilityEvaluationStatus):
        return status
    relevance = contract_relevance_of(change)
    return None if relevance is None else evaluation_status_for(relevance)


def is_evaluated(change: object) -> bool:
    """Whether compatibility policy and the change gate score *change*.

    ``True`` for an unstamped finding -- see this module's docstring for why
    that default is the legacy-preserving one, not a guess.
    """
    return (
        evaluation_status_of(change) is not CompatibilityEvaluationStatus.NOT_EVALUATED
    )


#: Deliberately only the three predicates above. Each consumer needs the
#: *filter* in its own element type -- ``list[Change]`` for ``checker.py`` and
#: ``report_model.py``, ``list[HasKind]`` for ``severity.py`` -- so a shared
#: ``list[object]`` helper here would be widened at every call site and cast
#: back. The predicate is the part that must not be duplicated; the
#: comprehension around it is one line and correctly typed where it lives.
