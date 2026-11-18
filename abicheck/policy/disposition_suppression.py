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

"""The suppression recording primitive (ADR-067 D3), moved out of the ledger.

:func:`record_suppressed_change` is the one call every suppression application
point makes. It lives here, and not on :class:`DispositionLedger`, so the ledger
module stays under its size record. The ledger re-exports the name, so every
existing import keeps working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ..model.change import Change
from .rule_inventory import RuleRef

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .suppression.rules import Suppression


class SuppressionSink(Protocol):
    """The ledger surface the suppression recording needs (the ledger satisfies it)."""

    def record_suppression(
        self,
        change: Change,
        *,
        rule: Suppression | None,
        application_point: str,
        source_file: str | None = None,
        dedupe_key: str | None = None,
        entry_index: int | None = None,
        shadowed_refs: tuple[RuleRef, ...] = (),
    ) -> None: ...


def record_suppressed_change(
    ledger: SuppressionSink | None,
    change: Change,
    *,
    rule: Suppression | None,
    application_point: str,
    suppression: object | None = None,
    dedupe_key: str | None = None,
) -> None:
    """The single call every suppression application point makes.

    A module-level function rather than only a method so the five call sites
    — three of which hold no ``PipelineContext`` and one of which lives in
    ``appcompat`` with a differently-shaped input — share *one* recording
    path, ledger-absence handling included: a ``None`` *ledger* is a caller
    that never opted into the audit, which is skipped, never faked.

    *suppression* is the ``SuppressionList`` the rule came from; its
    ``source_path`` is what puts D3's suppression-document path in the audit.
    """
    if ledger is None:
        return
    source_file, entry_index, shadowed = _suppression_position(
        suppression, change, rule
    )
    ledger.record_suppression(
        change,
        rule=rule,
        application_point=application_point,
        source_file=source_file,
        dedupe_key=dedupe_key,
        entry_index=entry_index,
        shadowed_refs=shadowed,
    )


def _suppression_position(
    suppression: object | None, change: Change, rule: Suppression | None
) -> tuple[str | None, int | None, tuple[RuleRef, ...]]:
    """*rule*'s document, its index there, and which other rules also matched.

    P1 (ADR-067 D3): the winning rule is first-match, so every other rule that
    matches *change* is *shadowed*. Computed here, at the one recording
    primitive, because the list is in hand and the winner is known. Positions
    come from :func:`~abicheck.policy.suppression.merge.rule_origin`, so a
    rule merged from one of several ``--suppress`` files keeps that file and
    its index in it. A rule not in the list (a synthetic decision, or a caller
    that passed no list) gets the list's own ``source_path`` and no position.
    """
    from .rule_inventory import KIND_SUPPRESSION
    from .suppression.merge import rule_origin

    source_path = getattr(suppression, "source_path", None)
    fallback = str(source_path) if source_path is not None else None
    rules = getattr(suppression, "rules", None) if suppression is not None else None
    if rule is None or not rules:
        return fallback, None, ()
    winner = next((i for i, r in enumerate(rules) if r is rule), None)
    if winner is None:
        return fallback, None, ()
    shadowed = tuple(
        RuleRef(KIND_SUPPRESSION, *rule_origin(suppression, i))
        for i, other in enumerate(rules)
        if i != winner and other.matches(change)
    )
    return (*rule_origin(suppression, winner), shadowed)
