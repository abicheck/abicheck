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

"""ADR-067 D6: the additions review gate, evaluated.

Mirrors :mod:`abicheck.policy.contract_coverage_exit`'s own shape (a small,
pure, orthogonal ``0``/``1`` axis, foldable into the exit code with
``max()``): a project may configure whether an unacknowledged public
addition should ``allow`` (default — "no existing run changes", D6's own
wording), ``warn``, or ``block``. This never reclassifies the addition into
a break and never touches its own ``ChangeKind``/verdict — it only decides
whether the *absence* of an acknowledgment on it should gate the run.

A leaf: imports only ``checker_policy.ADDITION_KINDS`` and
``finding_identity.report_canonical_finding_id`` (both dependency-free from
this module's own perspective, the same way ``contract_coverage_exit.py``
imports only the coverage ledger it projects), so this can be called from
policy, report, or CLI code without acquiring a new cross-layer edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .acknowledgment_policy import AcknowledgmentPolicy

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..checker_types import Change, DiffResult
    from .acknowledgment import AcknowledgmentList


@dataclass(frozen=True)
class UnacknowledgedAddition:
    """One public addition the review gate found with no acknowledgment."""

    kind: str
    symbol: str | None
    finding_id: str | None

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "symbol": self.symbol, "finding_id": self.finding_id}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UnacknowledgedAddition:
        return cls(
            kind=str(d["kind"]),
            symbol=d.get("symbol"),
            finding_id=d.get("finding_id"),
        )


@dataclass(frozen=True)
class AdditionsReviewResult:
    """D6's evaluated additions-review gate for one comparison.

    ``gate_contribution`` is ``0``/``1`` — never anything else — and is
    ``1`` only when the resolved policy is ``block`` *and* at least one
    unacknowledged public addition was found. Under ``allow``/``warn`` it is
    always ``0``: those two settings only change what is *reported*
    (``warn`` still lists ``unacknowledged`` — see :attr:`unacknowledged` —
    it just never contributes to the exit code), matching D6's "folds
    through the existing gate/exit precedence... as policy acceptance,
    never as a reclassification" requirement.
    """

    policy: str
    unacknowledged: tuple[UnacknowledgedAddition, ...]
    gate_contribution: int

    def to_dict(self) -> dict[str, object]:
        return {
            "policy": self.policy,
            "unacknowledged": [u.to_dict() for u in self.unacknowledged],
            "gate_contribution": self.gate_contribution,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AdditionsReviewResult:
        return cls(
            policy=str(d.get("policy", "allow")),
            unacknowledged=tuple(
                UnacknowledgedAddition.from_dict(u)
                for u in d.get("unacknowledged") or ()
            ),
            gate_contribution=int(d.get("gate_contribution", 0)),
        )


def evaluate_unacknowledged_additions(
    changes: list[Change],
    acknowledgments: AcknowledgmentList | None,
    policy: AcknowledgmentPolicy | None,
    *,
    component: str | None = None,
    baseline: str | None = None,
    release_label: str | None = None,
) -> AdditionsReviewResult:
    """D6's evaluation over *changes* — the public additions review gate.

    *changes* is the comparison's own detected/kept change list (the same
    population every other disposition-based report reads); *acknowledgments*
    is the loaded record set (``None`` when the run supplied none, which
    means every addition reads as unacknowledged); *policy* resolves the
    ``allow``/``warn``/``block`` action (``None`` falls back to the built-in
    default, ``allow``, the same "unset means allow" rule
    :class:`AcknowledgmentPolicy`'s own default expresses).

    Never mutates *changes* and never reads or writes a ``Change``'s
    ``kind``/verdict — this is purely an observation over the already-decided
    additions, exactly the "policy decides acceptance, not facts" boundary
    the root ``AGENTS.md`` states.
    """
    from ..checker_policy import ADDITION_KINDS
    from ..finding_identity import report_canonical_finding_id

    action = (policy or AcknowledgmentPolicy()).unacknowledged_additions
    unacknowledged: list[UnacknowledgedAddition] = []
    for change in changes:
        if getattr(change, "kind", None) not in ADDITION_KINDS:
            continue
        if acknowledgments is not None:
            ack = acknowledgments.evaluate(
                change,
                component=component,
                baseline=baseline,
                release_label=release_label,
            )
            if ack is not None:
                continue
        unacknowledged.append(
            UnacknowledgedAddition(
                kind=getattr(getattr(change, "kind", None), "value", None) or "",
                symbol=getattr(change, "symbol", None),
                finding_id=report_canonical_finding_id(change),
            )
        )
    contribution = 1 if (action == "block" and unacknowledged) else 0
    return AdditionsReviewResult(
        policy=action,
        unacknowledged=tuple(unacknowledged),
        gate_contribution=contribution,
    )


def evaluate_unacknowledged_additions_for_result(
    result: DiffResult,
    acknowledgments: AcknowledgmentList | None,
    policy: AcknowledgmentPolicy | None,
    *,
    component: str | None = None,
    baseline: str | None = None,
    release_label: str | None = None,
) -> AdditionsReviewResult:
    """:func:`evaluate_unacknowledged_additions` over a ``DiffResult``'s own
    kept ``changes`` — the call shape ``checker.compare()`` and a report
    projection both use, read defensively like every other ledger consumer
    in this package (a duck-typed stand-in is tolerated, per
    ``disposition_ledger.py``'s own convention)."""
    changes = list(getattr(result, "changes", None) or ())
    return evaluate_unacknowledged_additions(
        changes,
        acknowledgments,
        policy,
        component=component,
        baseline=baseline,
        release_label=release_label,
    )


def additions_review_exit_contribution(result: DiffResult) -> int:
    """The exit floor *result*'s own persisted additions-review carries.

    Reads ``result.unacknowledged_additions_review`` (an
    :class:`AdditionsReviewResult`, when ``checker.compare()`` was given
    ``acknowledgments=...``) rather than recomputing it — the same
    "read, don't re-derive" rule ``contract_coverage_exit.coverage_exit_floor``
    already follows. ``0`` whenever no review was computed for this run
    (every pre-existing caller that never passed ``acknowledgments``), so no
    existing invocation's exit code moves (D6: "the default is allow, so no
    existing run changes" generalizes to "no review at all also changes
    nothing").
    """
    review = getattr(result, "unacknowledged_additions_review", None)
    if review is None:
        return 0
    return int(getattr(review, "gate_contribution", 0) or 0)


def fold_additions_review_exit(base: int, result: DiffResult) -> int:
    """*base* raised to the additions-review floor — D6's orthogonal fold.

    ``max()``, exactly like :func:`abicheck.policy.contract_coverage_exit.
    fold_coverage_exit`: the axis can raise a clean ``0`` to ``1`` and can
    never lower a real ``2``/``4`` compatibility-gate exit.
    """
    return max(base, additions_review_exit_contribution(result))
