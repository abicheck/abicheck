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

Only status ``unused`` triggers. ``not_evaluated`` (a detector did not run),
``expired``, ``shadowed_only`` and ``active`` never do: each of those states
says something other than "this rule was checked and matched nothing".

``not_evaluated`` is still never silent once the user opted in (ADR-067 P1
addendum 2): under ``warn`` and ``block`` the review lists every
``not_evaluated`` rule with its ``missing_evidence``, and the report renders
it as a warning. It does not gate -- missing evidence narrows a conclusion, it
never manufactures a failure (ADR-063) -- so ``gate_contribution`` ignores it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from .rule_inventory import STATUS_NOT_EVALUATED, UNUSED_TRIGGER_STATUS, RuleAuditRow

if TYPE_CHECKING:  # pragma: no cover - typing only
    from datetime import date

    from ..checker_types import DiffResult

from .policy_file_top_level import UNUSED_RULES_MODES

_MODE_RANK = {mode: rank for rank, mode in enumerate(UNUSED_RULES_MODES)}


def strictest_mode(modes: Iterable[str]) -> str:
    """The strictest of *modes* (``allow`` when there are none)."""
    return max(modes, key=lambda m: _MODE_RANK.get(m, 0), default="allow")


def effective_unused_rules_mode(policy_file: object | None) -> str:
    """The mode the policy document sets (``allow`` when it sets none)."""
    return str(getattr(policy_file, "unused_rules", None) or "allow")


def unused_rules_digest_field(policy_file: object | None) -> dict[str, str]:
    """``policy.unused_rules`` for the effective-config digest.

    Present only when the mode is not ``allow``, so every run that never opted
    in keeps its digest.
    """
    mode = effective_unused_rules_mode(policy_file)
    return {} if mode == "allow" else {"policy.unused_rules": mode}


def resolve_unused_rules_mode(result: object) -> str:
    """The effective mode of *result*'s own policy document (see above)."""
    return effective_unused_rules_mode(getattr(result, "policy_file", None))


@dataclass(frozen=True)
class UnusedRulesReview:
    """The evaluated ``unused_rules`` gate for one comparison (or one release)."""

    mode: str
    #: ``kind``/``rule_id``/``source_file``/``entry_index`` of each unused rule.
    unused: tuple[dict[str, object], ...]
    gate_contribution: int
    #: Under ``warn``/``block``: each ``not_evaluated`` rule, with its
    #: ``missing_evidence``. A warning, never a gate input.
    not_evaluated: tuple[dict[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "policy": self.mode,
            "unused": [dict(entry) for entry in self.unused],
            "gate_contribution": self.gate_contribution,
        }
        if self.mode != "allow":
            out["not_evaluated"] = [dict(entry) for entry in self.not_evaluated]
        return out

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> UnusedRulesReview:
        return cls(
            mode=str(d.get("policy", "allow")),
            unused=tuple(dict(e) for e in d.get("unused") or ()),
            gate_contribution=int(d.get("gate_contribution", 0) or 0),
            not_evaluated=tuple(dict(e) for e in d.get("not_evaluated") or ()),
        )


def unused_rules_review(
    rows: Iterable[RuleAuditRow], *, mode: str
) -> UnusedRulesReview:
    """D-style gate over already-computed audit rows (no re-derivation here)."""
    rows = tuple(rows)
    unused: tuple[dict[str, object], ...] = tuple(
        _entry(row) for row in rows if row.status == UNUSED_TRIGGER_STATUS
    )
    not_evaluated: tuple[dict[str, object], ...] = (
        ()
        if mode == "allow"
        else tuple(
            {**_entry(row), "missing_evidence": row.missing_evidence}
            for row in rows
            if row.status == STATUS_NOT_EVALUATED
        )
    )
    contribution = 1 if (mode == "block" and unused) else 0
    return UnusedRulesReview(
        mode=mode,
        unused=unused,
        gate_contribution=contribution,
        not_evaluated=not_evaluated,
    )


def _entry(row: RuleAuditRow) -> dict[str, object]:
    return {
        "kind": row.kind,
        "rule_id": row.rule_id,
        "source_file": row.source_file,
        "entry_index": row.entry_index,
    }


def render_not_evaluated_rules(
    review: Mapping[str, object] | None, cap: int
) -> list[str]:
    """The Markdown warning for ``not_evaluated`` rules (empty when none)."""
    entries = cast("list[dict[str, object]]", (review or {}).get("not_evaluated") or [])
    if not entries:
        return []
    lines = [
        f"**Warning -- rules not evaluated ({(review or {}).get('policy')}):** "
        f"{len(entries)} configured rule(s) matched nothing, but the evidence "
        "that could have matched them was missing, so they were not checked "
        "(not gating)",
        "",
    ]
    for entry in entries[:cap]:
        lines.append(
            f"- `{entry.get('rule_id')}` -- missing: {entry.get('missing_evidence') or 'unknown'}"
        )
    if len(entries) > cap:
        lines.append(f"- … {len(entries) - cap} more omitted")
    lines.append("")
    return lines


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
