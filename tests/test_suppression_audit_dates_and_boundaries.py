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

"""`SuppressionList.audit` and `Suppression.matches` decide at the caller's
date, and the near-expiry window has exact, inclusive edges.

Every date-bearing check takes an explicit *today*. These tests pin it to a
date in 2031 against rules that expire in December 2030, so a check that
silently fell back to the real current date (still before that expiry) would
answer differently. The near-expiry window is checked exhaustively over a
small domain of day offsets against a stated inequality, not the
implementation's own cutoff arithmetic.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.evidence_status import CrossSourceEvolution
from abicheck.policy.classification import (
    excluded_from_verdict_as_persistent_hygiene,
)
from abicheck.policy_file import PolicyFile
from abicheck.suppression import Suppression, SuppressionList

#: Later than every real run of this suite; rules below expire before it.
_TODAY = date(2031, 1, 1)
_EXPIRED_AT_TODAY = date(2030, 12, 1)


def _change(kind: ChangeKind = ChangeKind.FUNC_REMOVED, symbol: str = "foo") -> Change:
    return Change(kind=kind, symbol=symbol, description="x")


class TestNearExpiryWindow:
    @pytest.mark.parametrize("window", [0, 1, 7, 30])
    def test_window_is_inclusive_on_both_edges(self, window: int) -> None:
        offsets = range(-2, window + 3)
        rules = [
            Suppression(symbol=f"s{d}", reason="r", expires=_TODAY + timedelta(days=d))
            for d in offsets
        ]
        audit = SuppressionList(rules).audit([], _TODAY, near_expiry_days=window)

        near = {s.symbol for s in audit.near_expiry_rules}
        # Oracle: a rule is near expiry when it is not yet expired (expires on
        # or after today) and expires no later than today + window.
        assert near == {f"s{d}" for d in offsets if 0 <= d <= window}

    def test_default_window_is_thirty_days(self) -> None:
        rules = [
            Suppression(symbol=f"s{d}", reason="r", expires=_TODAY + timedelta(days=d))
            for d in (30, 31)
        ]
        audit = SuppressionList(rules).audit([], _TODAY)
        assert [s.symbol for s in audit.near_expiry_rules] == ["s30"]

    def test_zero_window_is_accepted(self) -> None:
        audit = SuppressionList([]).audit([], _TODAY, near_expiry_days=0)
        assert audit.total_rules == 0

    @pytest.mark.parametrize("window", [-1, -30])
    def test_negative_window_is_rejected_with_its_reason(self, window: int) -> None:
        with pytest.raises(ValueError) as excinfo:
            SuppressionList([]).audit([], _TODAY, near_expiry_days=window)
        assert str(excinfo.value) == "near_expiry_days must be non-negative"


class TestAuditDecidesAtTheCallersDate:
    """A rule that expired before *today* but not before the real date."""

    def _audit(self) -> tuple[Suppression, object]:
        rule = Suppression(symbol="foo", reason="r", expires=_EXPIRED_AT_TODAY)
        return rule, SuppressionList([rule]).audit([_change()], _TODAY)

    def test_an_expired_rule_matches_nothing(self) -> None:
        _rule, audit = self._audit()
        assert audit.match_counts == {0: 0}
        assert audit.high_risk_matches == []

    def test_an_expired_rule_is_expired_not_stale_or_near_expiry(self) -> None:
        rule, audit = self._audit()
        assert audit.expired_rules == [rule]
        assert audit.stale_rules == []
        assert audit.near_expiry_rules == []

    def test_matches_uses_the_given_date(self) -> None:
        rule = Suppression(symbol="foo", reason="r", expires=_EXPIRED_AT_TODAY)
        assert not rule.matches(_change(), today=_TODAY)
        assert rule.matches(_change(), today=_EXPIRED_AT_TODAY - timedelta(days=1))


class TestAuditHighRiskClassification:
    def test_breaking_kinds_override_replaces_the_default_set(self) -> None:
        rule = Suppression(symbol="foo", reason="r")
        changes = [_change(ChangeKind.FUNC_REMOVED)]

        default = SuppressionList([rule]).audit(changes, _TODAY)
        overridden = SuppressionList([rule]).audit(
            changes, _TODAY, breaking_kinds=frozenset()
        )

        assert [c.kind for _s, c in default.high_risk_matches] == [
            ChangeKind.FUNC_REMOVED
        ]
        assert overridden.high_risk_matches == []

    def test_policy_file_resolves_at_the_callers_date(self, tmp_path: Path) -> None:
        """A `reclassify:` promotion that expired before *today* no longer
        makes the matched finding high risk."""
        policy = tmp_path / "policy.yaml"
        policy.write_text(
            "reclassify:\n"
            "  - kind: func_added\n"
            "    symbol: foo\n"
            "    to: break\n"
            f"    expires: {_EXPIRED_AT_TODAY.isoformat()}\n",
            encoding="utf-8",
        )
        pf = PolicyFile.load(policy)
        rule = Suppression(symbol="foo", reason="r")
        changes = [_change(ChangeKind.FUNC_ADDED)]

        after = SuppressionList([rule]).audit(changes, _TODAY, policy_file=pf)
        before = SuppressionList([rule]).audit(
            changes, _EXPIRED_AT_TODAY - timedelta(days=1), policy_file=pf
        )

        assert after.high_risk_matches == []
        assert [c.kind for _s, c in before.high_risk_matches] == [ChangeKind.FUNC_ADDED]


class TestPersistentHygieneUsesTheCompatibleSet:
    def test_a_compatible_only_kind_is_not_excluded(self) -> None:
        """A persistent finding whose kind is only in the compatible set
        resolves to COMPATIBLE, not COMPATIBLE_WITH_RISK, so it is not
        excluded -- the category has to be decided against all four sets."""
        change = _change(ChangeKind.FUNC_ADDED)
        change.cross_source_evolution = CrossSourceEvolution.PERSISTENT
        empty: frozenset[ChangeKind] = frozenset()

        assert not excluded_from_verdict_as_persistent_hygiene(
            change, empty, empty, frozenset({ChangeKind.FUNC_ADDED}), empty
        )

    def test_a_risk_kind_is_excluded(self) -> None:
        change = _change(ChangeKind.FUNC_ADDED)
        change.cross_source_evolution = CrossSourceEvolution.PERSISTENT
        empty: frozenset[ChangeKind] = frozenset()

        assert excluded_from_verdict_as_persistent_hygiene(
            change, empty, empty, empty, frozenset({ChangeKind.FUNC_ADDED})
        )


def test_a_finding_id_rule_matches_at_the_callers_date() -> None:
    """The `finding_id` selector path decides expiry at *today* too."""
    from abicheck.finding_identity import report_canonical_finding_id

    change = _change()
    rule = Suppression(
        finding_id=report_canonical_finding_id(change),
        reason="r",
        expires=_EXPIRED_AT_TODAY,
    )
    other = _change(symbol="bar")

    assert rule.matches(change, today=_EXPIRED_AT_TODAY - timedelta(days=1))
    assert not rule.matches(change, today=_TODAY)
    assert not rule.matches(other, today=_EXPIRED_AT_TODAY - timedelta(days=1))


def test_a_finding_id_rule_still_checks_its_other_selectors() -> None:
    """`finding_id` narrows the rule's other selectors; it does not replace
    them, so the change itself is still matched against `symbol`."""
    from abicheck.finding_identity import report_canonical_finding_id

    change = _change()
    finding_id = report_canonical_finding_id(change)

    assert Suppression(finding_id=finding_id, symbol="foo", reason="r").matches(
        change, today=_TODAY
    )
    assert not Suppression(finding_id=finding_id, symbol="bar", reason="r").matches(
        change, today=_TODAY
    )
