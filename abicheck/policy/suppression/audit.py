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

"""The suppression audit result (``SuppressionAudit``).

Moved out of ``policy/suppression/rules.py`` (a ``no_growth`` architecture
debt file) when P2 added the ``id:`` key there. ``rules.py`` re-exports the
class, so every ``from abicheck.policy.suppression.rules import
SuppressionAudit`` keeps resolving.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...model.change import Change

    pass


@dataclass
class SuppressionAudit:
    """Result of auditing suppression rules against detected changes."""

    stale_rules: list[Any]
    """Rules that matched zero changes (likely stale or misconfigured)."""
    high_risk_matches: list[tuple[Any, Change]]
    """Suppressions that matched BREAKING changes (high risk — should require reason)."""
    expired_rules: list[Any]
    """Rules past their expiry date."""
    near_expiry_rules: list[Any]
    """Rules expiring within the near-expiry window."""
    match_counts: dict[int, int]
    """Per-rule match count (rule index → number of matched changes)."""
    total_rules: int
    """Total number of suppression rules."""

    @property
    def has_issues(self) -> bool:
        """True if the audit found any issues worth reporting."""
        return bool(
            self.stale_rules
            or self.high_risk_matches
            or self.expired_rules
            or self.near_expiry_rules
        )

    def summary(self) -> str:
        """Human-readable audit summary."""
        lines = [f"Suppression audit: {self.total_rules} rules"]
        if self.stale_rules:
            # Codex review, fresh evidence: this used to also print up to 5
            # per-rule detail lines here, naming each stale rule by only its
            # first populated selector -- two rules sharing that first
            # selector (e.g. the same `symbol` but a different `change_kind`)
            # rendered identically, misdirecting a reader to the wrong rule.
            # A stable, fully-disambiguated identifier needs every matching
            # selector (see cli_compare_fold._suppression_rule_label), which
            # this module has no reason to duplicate -- callers that want
            # per-rule detail (e.g. compare --audit-suppressions's markdown/
            # JSON output) list stale_rules themselves; this summary only
            # reports the count, matching the expired/near-expiry buckets
            # below.
            lines.append(f"  ⚠ {len(self.stale_rules)} stale rule(s) matched nothing")
        if self.high_risk_matches:
            lines.append(
                f"  ⚠ {len(self.high_risk_matches)} suppression(s) matched BREAKING changes"
            )
            for _sup, change in self.high_risk_matches[:5]:
                lines.append(f"    - {change.kind.value}: {change.symbol}")
        if self.expired_rules:
            lines.append(f"  ⚠ {len(self.expired_rules)} expired rule(s)")
        if self.near_expiry_rules:
            lines.append(f"  ℹ {len(self.near_expiry_rules)} rule(s) expiring soon")
        if not self.has_issues:
            lines.append("  ✓ No issues found")
        return "\n".join(lines)
