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

"""`dispose_by_suppression`: the one suppression primitive every application
point shares (the pipeline's `ApplySuppression`, late detector findings, and
`checker.compare()`'s SONAME/floor/surface-metric/pattern-verdict findings).

Tested through its interface: the partition it returns and the ledger it
records into. The oracle for "which rule decides" is `SuppressionList.evaluate`
itself — this module's job is the bookkeeping around that decision, so the
invariants below are stated over every combination of rule shape and
reachability rather than one fixture each.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.policy.disposition_ledger import Disposition, DispositionLedger
from abicheck.policy.finding_disposition import dispose_by_suppression
from abicheck.suppression import Suppression, SuppressionList

_DIAGNOSTIC_KINDS = {
    ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK,
    ChangeKind.SUPPRESSION_REACHABILITY_UNKNOWN,
}

#: Rule shapes that, against the findings below, cover all three outcomes:
#: suppressed outright, kept with a withheld-rule diagnostic, kept untouched.
_RULES = {
    "exact": Suppression(symbol="lib::internal_helper", label="exact-waiver"),
    "broad": Suppression(namespace="oneapi::dal::**::detail::**", reason="broad"),
    "needs-proof": Suppression(
        symbol="libfoo.so.1", reachability="proven-unreachable-only", reason="proof"
    ),
}


def _findings() -> list[Change]:
    return [
        Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="lib::internal_helper",
            description="removed",
        ),
        Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="oneapi::dal::kmeans::detail::train_ops_dispatcher",
            description="removed",
            public_reachable=True,
        ),
        Change(
            kind=ChangeKind.SONAME_BUMP_UNNECESSARY,
            symbol="libfoo.so.1",
            description="soname bumped unnecessarily",
        ),
        Change(kind=ChangeKind.FUNC_ADDED, symbol="untouched", description="added"),
    ]


_RULE_SUBSETS = [
    subset
    for size in range(len(_RULES) + 1)
    for subset in itertools.combinations(sorted(_RULES), size)
]


@pytest.mark.parametrize(
    "rule_names", _RULE_SUBSETS, ids=lambda s: "+".join(s) or "none"
)
def test_every_finding_lands_in_exactly_one_place_and_matches_evaluate(
    rule_names: tuple[str, ...],
) -> None:
    """Conservation: each input is kept or suppressed, never both or neither;
    the split agrees with `SuppressionList.evaluate`; each withheld rule yields
    exactly one diagnostic; and the ledger holds exactly the suppressed ones,
    under the caller's application point."""
    findings = _findings()
    suppression = SuppressionList([_RULES[n] for n in rule_names])
    oracle = {id(c): suppression.evaluate(c) for c in findings}
    ledger = DispositionLedger()

    outcome = dispose_by_suppression(
        findings, suppression, ledger=ledger, application_point="unit_point"
    )

    assert [c for c in findings if oracle[id(c)].suppressed] == outcome.suppressed
    assert [c for c in findings if not oracle[id(c)].suppressed] == outcome.kept
    expected_diagnostics = sum(
        (o.withheld_rule is not None) + (o.withheld_unknown_rule is not None)
        for o in oracle.values()
        if not o.suppressed
    )
    assert len(outcome.diagnostics) == expected_diagnostics
    assert all(d.kind in _DIAGNOSTIC_KINDS for d in outcome.diagnostics)
    assert outcome.visible == [*outcome.kept, *outcome.diagnostics]

    assert ledger.detected_total == len(outcome.suppressed)
    for change in outcome.suppressed:
        record = ledger.record_for(change)
        assert record is not None
        assert record.disposition is Disposition.SUPPRESSED
        assert record.application_point == "unit_point"
        assert change.suppression_rule == oracle[id(change)].rule_label()
    for change in outcome.kept:
        assert ledger.record_for(change) is None


def test_no_rule_set_keeps_everything_and_records_nothing() -> None:
    findings = _findings()
    ledger = DispositionLedger()
    outcome = dispose_by_suppression(
        findings, None, ledger=ledger, application_point="unit_point"
    )
    assert outcome.kept == findings
    assert outcome.suppressed == outcome.diagnostics == []
    assert len(ledger) == 0


def test_a_missing_ledger_still_suppresses() -> None:
    """A caller that did not opt into the audit gets the same partition."""
    findings = _findings()
    outcome = dispose_by_suppression(
        findings,
        SuppressionList([_RULES["exact"]]),
        ledger=None,
        application_point="unit_point",
    )
    assert outcome.suppressed == [findings[0]]
    assert findings[0].suppression_rule == "exact-waiver"


def test_withheld_broad_rule_diagnostic_names_the_finding() -> None:
    """ADR-044 D4: the broad rule's match on a public-reachable finding is
    withheld, the finding stays, and the diagnostic says which one."""
    finding = _findings()[1]
    outcome = dispose_by_suppression(
        [finding],
        SuppressionList([_RULES["broad"]]),
        ledger=None,
        application_point="unit_point",
    )
    assert outcome.kept == [finding]
    (diag,) = outcome.diagnostics
    assert diag.kind is ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK
    assert "train_ops_dispatcher" in diag.symbol


def test_unproven_reachability_rule_yields_unknown_diagnostic() -> None:
    finding = _findings()[2]
    outcome = dispose_by_suppression(
        [finding],
        SuppressionList([_RULES["needs-proof"]]),
        ledger=None,
        application_point="unit_point",
    )
    assert outcome.kept == [finding]
    (diag,) = outcome.diagnostics
    assert diag.kind is ChangeKind.SUPPRESSION_REACHABILITY_UNKNOWN
