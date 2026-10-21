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

"""ADR-067 P1: the ``unused_rules`` review gate, evaluated.

A configured rule that matched no finding in a run is almost always a stale
waiver: the code it named has moved, or the symbol was renamed. Reporting that
(the ``unused`` status on every audit row) is the audit's job. *Failing* a run
over it is a policy decision, and it is opt-in, mirroring the additions review
gate (:mod:`abicheck.policy.acknowledgment_gate`, ADR-067 D6):

* ``unused_rules: allow`` (default) -- report only; the exit code never moves;
* ``unused_rules: warn`` -- the same, and the report lists the unused rules as
  a review item;
* ``unused_rules: block`` -- ``gate_contribution`` is ``1`` when any configured
  rule has status ``unused``. Folded into the exit code with ``max()``, so it
  can raise a clean ``0`` to ``1`` and can never lower ``2``/``4``/...

``--fail-on-unused-rules`` on the CLI is ``block`` (it can only tighten).

Only status ``unused`` triggers. ``not_evaluated`` (a detector did not run),
``expired``, ``shadowed_only`` and ``active`` never do: each of those states
says something other than "this rule was checked and matched nothing".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..model.unused_rules_flag import fail_on_unused_rules_requested
from .rule_inventory import UNUSED_TRIGGER_STATUS, RuleAuditRow

if TYPE_CHECKING:  # pragma: no cover - typing only
    from datetime import date

    from ..checker_types import DiffResult

from .policy_file_top_level import UNUSED_RULES_MODES

_MODE_RANK = {mode: rank for rank, mode in enumerate(UNUSED_RULES_MODES)}


def strictest_mode(modes: Iterable[str]) -> str:
    """The strictest of *modes* (``allow`` when there are none)."""
    return max(modes, key=lambda m: _MODE_RANK.get(m, 0), default="allow")


def resolve_unused_rules_mode(result: object) -> str:
    """The run's effective mode: the policy document's, escalated by the CLI flag.

    ``--fail-on-unused-rules`` (see :mod:`abicheck.model.unused_rules_flag`)
    forces ``block``. It only tightens: the flag never relaxes a policy file's
    ``block``.
    """
    if fail_on_unused_rules_requested():
        return "block"
    policy_file = getattr(result, "policy_file", None)
    return str(getattr(policy_file, "unused_rules", None) or "allow")


@dataclass(frozen=True)
class UnusedRulesReview:
    """The evaluated ``unused_rules`` gate for one comparison (or one release)."""

    mode: str
    #: ``kind``/``rule_id``/``source_file``/``entry_index`` of each unused rule.
    unused: tuple[dict[str, object], ...]
    gate_contribution: int

    def to_dict(self) -> dict[str, object]:
        return {
            "policy": self.mode,
            "unused": [dict(entry) for entry in self.unused],
            "gate_contribution": self.gate_contribution,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> UnusedRulesReview:
        return cls(
            mode=str(d.get("policy", "allow")),
            unused=tuple(dict(e) for e in d.get("unused") or ()),
            gate_contribution=int(d.get("gate_contribution", 0) or 0),
        )


def unused_rules_review(
    rows: Iterable[RuleAuditRow], *, mode: str
) -> UnusedRulesReview:
    """D-style gate over already-computed audit rows (no re-derivation here)."""
    unused: tuple[dict[str, object], ...] = tuple(
        {
            "kind": row.kind,
            "rule_id": row.rule_id,
            "source_file": row.source_file,
            "entry_index": row.entry_index,
        }
        for row in rows
        if row.status == UNUSED_TRIGGER_STATUS
    )
    contribution = 1 if (mode == "block" and unused) else 0
    return UnusedRulesReview(mode=mode, unused=unused, gate_contribution=contribution)


def unused_rules_exit_contribution(
    result: DiffResult, *, today: date | None = None
) -> int:
    """The ``0``/``1`` exit axis for *result*'s own configured rules.

    Computed from the result, not persisted on it, because it is a pure function
    of the ledger and the policy. ``0`` whenever the mode is not ``block``, so
    every run that never opted in keeps its exit code.
    """
    from .disposition_close import ledger_for
    from .disposition_rules import configured_rule_rows

    mode = resolve_unused_rules_mode(result)
    if mode != "block":
        return 0
    ledger = ledger_for(result, today=today)
    rows = configured_rule_rows(result, ledger=ledger, today=today)
    return unused_rules_review(rows, mode=mode).gate_contribution
