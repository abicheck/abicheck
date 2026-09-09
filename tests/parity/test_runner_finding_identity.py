# SPDX-License-Identifier: Apache-2.0
"""``tests/parity/runner.py``'s :class:`~.runner.Finding`/:class:`~.runner.RunOutcome`
own multiplicity contract (Codex review, PR #1172, round 16, fresh evidence).

Before this fix, ``Finding`` dropped every finding down to
``(kind, identity, severity, evidence_refs)`` and ``RunOutcome.severities``/
``.gate_contributions`` keyed on the bare ``(kind, identity)`` pair. A
cross-source check like ``private_header_leak`` can legitimately emit
several findings for one function -- one per leaked type, same ``kind`` and
same ``symbol`` -- so two *distinct* findings collapsed onto the identical
:class:`~.runner.Finding` value and the identical dict key. A partial
capability loss (one of the two rows missing on one side, the other
present) then hashed the same on both sides and ``assert_full_parity()``
read it as full parity instead of catching the loss.

These are primitive-level tests directly against ``runner.py``'s own
building blocks (no real ``compare``/``scan`` CLI invocation needed to
prove the identity primitive itself is right), per this repo's
"Primitive-level property tests" convention (root ``AGENTS.md``): a
reusable identity/dedup primitive earns its own standalone tests stating
its contract, decoupled from any one caller's fixture.
"""

from __future__ import annotations

from .runner import Finding, _outcome_from_findings


def _raw_finding(
    *, kind: str, symbol: str, new_value: str, finding_id: str, **extra: object
) -> dict[str, object]:
    return {
        "kind": kind,
        "symbol": symbol,
        "new_value": new_value,
        "finding_id": finding_id,
        **extra,
    }


class TestFindingMultiplicity:
    def test_two_same_kind_same_symbol_findings_stay_distinct(self) -> None:
        """Two ``private_header_leak``-shaped findings on the identical
        function, differing only in the leaked type (hence a different
        ``finding_id`` -- ``report_finding_id`` folds in ``new_value``),
        must not collapse into one :class:`Finding`."""
        raw = [
            _raw_finding(
                kind="private_header_leak",
                symbol="_Z3fnv",
                new_value="detail::Impl",
                finding_id="a" * 16,
                severity="risk",
            ),
            _raw_finding(
                kind="private_header_leak",
                symbol="_Z3fnv",
                new_value="detail::OtherImpl",
                finding_id="b" * 16,
                severity="risk",
            ),
        ]
        result = type("Result", (), {"exit_code": 0})()
        outcome = _outcome_from_findings(
            result, "COMPATIBLE_WITH_RISK", raw, evidence_key=None
        )

        assert len(outcome.findings) == 2, (
            "two findings with the same (kind, symbol) but different "
            "finding_id collapsed into one -- multiplicity lost"
        )
        assert len(outcome.severities) == 2, (
            "the second finding's severity entry overwrote the first's in "
            "the (kind, identity, finding_id)-keyed dict"
        )
        assert len(outcome.gate_contributions) == 2

    def test_a_genuine_duplicate_still_collapses(self) -> None:
        """Two raw dicts that really are the identical finding (same kind,
        symbol, and finding_id) must still dedupe -- this fix must not turn
        every finding into its own row regardless of identity."""
        raw = [
            _raw_finding(
                kind="exported_not_public",
                symbol="_Z3fnv",
                new_value="",
                finding_id="c" * 16,
                severity="risk",
            ),
            _raw_finding(
                kind="exported_not_public",
                symbol="_Z3fnv",
                new_value="",
                finding_id="c" * 16,
                severity="risk",
            ),
        ]
        result = type("Result", (), {"exit_code": 0})()
        outcome = _outcome_from_findings(
            result, "COMPATIBLE_WITH_RISK", raw, evidence_key=None
        )

        assert len(outcome.findings) == 1
        assert len(outcome.severities) == 1

    def test_finding_dataclass_hashes_on_finding_id_too(self) -> None:
        """Direct check on the dataclass itself: two ``Finding`` values that
        agree on kind/identity/severity/evidence_refs but differ only in
        ``finding_id`` are unequal (and thus coexist in a set)."""
        a = Finding(kind="k", identity="sym", severity="risk", finding_id="a" * 16)
        b = Finding(kind="k", identity="sym", severity="risk", finding_id="b" * 16)
        assert a != b
        assert len({a, b}) == 2
