# SPDX-License-Identifier: Apache-2.0
"""``disposition_close.override_suppression`` (Codex review, PR #1172, third
round): ``record``/``record_suppression`` are deliberately first-write-wins,
so a caller revising an already-finalized record (``scan --against``'s
baseline path dropping a ``--crosscheck KEY=off`` finding after
``compare_snapshots()`` already recorded it as gating) needs a distinct
primitive -- a plain second ``record_suppression`` call would silently
no-op, leaving the ledger disagreeing with the finding's real, reported
disposition.
"""

from __future__ import annotations

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.policy.disposition_close import (
    override_suppressed_change,
    override_suppression,
)
from abicheck.policy.disposition_ledger import Disposition, DispositionLedger


def _change(symbol: str = "_Zfoo") -> Change:
    return Change(kind=ChangeKind.EXPORTED_NOT_PUBLIC, symbol=symbol, description="x")


class TestOverrideSuppressionRewritesAnAlreadyRecordedChange:
    def test_flips_a_gating_record_to_suppressed(self) -> None:
        ledger = DispositionLedger()
        c = _change()
        ledger.record(c, Disposition.GATING, application_point="p", from_gate=True)

        override_suppression(
            ledger, c, rule=None, application_point="scan_crosscheck_off"
        )

        record = ledger.record_for(c)
        assert record is not None
        assert record.disposition is Disposition.SUPPRESSED
        assert record.application_point == "scan_crosscheck_off"
        assert record.gate_excluded is True

    def test_a_plain_second_record_call_would_have_been_a_no_op(self) -> None:
        # Negative control proving the bug this function fixes: `record`'s
        # own first-write-wins guard silently discards a second call for the
        # same change, which is exactly why `override_suppression` cannot
        # just call `record_suppression` again.
        ledger = DispositionLedger()
        c = _change()
        ledger.record(c, Disposition.GATING, application_point="p", from_gate=True)

        ledger.record_suppression(c, rule=None, application_point="would_be_ignored")

        record = ledger.record_for(c)
        assert record is not None
        assert record.disposition is Disposition.GATING  # unchanged -- the no-op

    def test_no_op_when_the_change_was_never_recorded(self) -> None:
        ledger = DispositionLedger()
        c = _change()
        # Never call ledger.record(c, ...) -- nothing to override.
        override_suppression(
            ledger, c, rule=None, application_point="scan_crosscheck_off"
        )
        assert ledger.record_for(c) is None
        assert len(ledger) == 0

    def test_unrelated_records_are_untouched(self) -> None:
        ledger = DispositionLedger()
        target = _change("_Ztarget")
        other = _change("_Zother")
        ledger.record(target, Disposition.GATING, application_point="p", from_gate=True)
        ledger.record(other, Disposition.GATING, application_point="p", from_gate=True)

        override_suppression(
            ledger, target, rule=None, application_point="scan_crosscheck_off"
        )

        assert ledger.record_for(target).disposition is Disposition.SUPPRESSED
        assert ledger.record_for(other).disposition is Disposition.GATING

    def test_module_level_helper_is_none_safe(self) -> None:
        # Mirrors record_suppressed_change's own "no ledger, no-op" contract
        # -- a caller that never opted into the audit must not crash.
        override_suppressed_change(
            None, _change(), rule=None, application_point="scan_crosscheck_off"
        )

    def test_module_level_helper_routes_to_the_ledger(self) -> None:
        ledger = DispositionLedger()
        c = _change()
        ledger.record(c, Disposition.GATING, application_point="p", from_gate=True)

        override_suppressed_change(
            ledger, c, rule=None, application_point="scan_crosscheck_off"
        )

        assert ledger.record_for(c).disposition is Disposition.SUPPRESSED
