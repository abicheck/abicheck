"""Acknowledgment matching counts a dedup twin pair as one observed change.

The dedup pass collapses a cross-tier twin (a header-tier removal and its
ELF-only copy) onto one identity and keeps the survivor; the loser stays on the
ledger as ``deduplicated``. A ``symbol:`` record that covers the pair must
therefore see one observation, not two (ADR-067 D5): acknowledging it must not
raise ``AmbiguousAcknowledgmentError``. Two findings with different identities
that one record matches are still ambiguous.

No toolchain: the changes are built directly, and the oracle for "these collapse"
is the dedup pass itself rather than the matcher's own helper.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.compare.finding_dedup import (
    dedup_identity_key,
    deduplicate_cross_detector,
)
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.policy.acknowledgment import (
    AcknowledgmentList,
    AmbiguousAcknowledgmentError,
)
from abicheck.policy.disposition_acknowledgment import resolve_acknowledgments
from abicheck.policy.disposition_ledger import Disposition, DispositionLedger

_APP = "finding_dedup"


def _acks(tmp_path: Path, body: str) -> AcknowledgmentList:
    path = tmp_path / "ack.yml"
    path.write_text("version: 1\nacknowledgments:\n" + body)
    return AcknowledgmentList.load(path)


_REMOVAL_RECORD = (
    "  - symbol: gone_fn\n    change_kind: func_removed\n    reason: 'deliberate'\n"
)


@pytest.mark.parametrize(
    ("first", "second", "collapses"),
    [
        (ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_REMOVED_ELF_ONLY, True),
        (ChangeKind.VAR_REMOVED, ChangeKind.VAR_REMOVED_ELF_ONLY, True),
        (ChangeKind.FUNC_REMOVED, ChangeKind.VAR_REMOVED, False),
        (ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_ADDED, False),
    ],
    ids=["func-twin", "var-twin", "func-vs-var", "removal-vs-addition"],
)
def test_matcher_key_agrees_with_the_dedup_pass(
    first: ChangeKind, second: ChangeKind, collapses: bool
) -> None:
    """The matcher's key equals the key the dedup pass collapses on.

    The oracle is ``deduplicate_cross_detector`` itself: a pair it folds into one
    finding must share a dedup identity, and a pair it keeps apart must not.
    """
    a = Change(kind=first, symbol="gone_fn", description="a")
    b = Change(kind=second, symbol="gone_fn", description="b")
    assert len(deduplicate_cross_detector([a, b])) == (1 if collapses else 2)
    same_identity = dedup_identity_key(a) == dedup_identity_key(b)
    assert same_identity is collapses


def test_a_kind_outside_the_dedup_categories_has_no_identity_key() -> None:
    change = Change(
        kind=ChangeKind.TYPE_FIELD_TYPE_CHANGED, symbol="S", description="field a"
    )
    assert dedup_identity_key(change) is None


def test_twin_pair_is_one_observation_and_the_survivor_carries_the_record(
    tmp_path: Path,
) -> None:
    acks = _acks(tmp_path, _REMOVAL_RECORD)
    survivor = Change(kind=ChangeKind.FUNC_REMOVED, symbol="gone_fn", description="s")
    twin = Change(
        kind=ChangeKind.FUNC_REMOVED_ELF_ONLY, symbol="gone_fn", description="t"
    )
    ledger = DispositionLedger()
    ledger.record(survivor, Disposition.GATING, application_point=_APP)
    ledger.record(twin, Disposition.DEDUPLICATED, application_point=_APP)

    resolve_acknowledgments(ledger, acks, strict=True)

    kept = ledger.record_for(survivor)
    folded = ledger.record_for(twin)
    assert kept is not None and folded is not None
    # The gating finding is acknowledged and keeps its gate and its rule.
    assert kept.disposition is Disposition.GATING
    assert kept.acknowledged_by is not None
    assert kept.acknowledgment_rule is not None
    # The folded duplicate stays on the ledger, still deduplicated, and is not a
    # second acknowledged finding.
    assert folded.disposition is Disposition.DEDUPLICATED
    assert folded.acknowledged_by is None


def test_folded_duplicate_without_a_surviving_record_is_acknowledged_itself(
    tmp_path: Path,
) -> None:
    """With no surviving finding to carry it, the duplicate is the one observation."""
    acks = _acks(tmp_path, _REMOVAL_RECORD)
    twin = Change(
        kind=ChangeKind.FUNC_REMOVED_ELF_ONLY, symbol="gone_fn", description="t"
    )
    ledger = DispositionLedger()
    ledger.record(twin, Disposition.DEDUPLICATED, application_point=_APP)

    resolve_acknowledgments(ledger, acks, strict=True)

    record = ledger.record_for(twin)
    assert record is not None
    assert record.disposition is Disposition.DEDUPLICATED
    assert record.acknowledged_by is not None


def test_genuinely_different_findings_under_one_record_still_raise(
    tmp_path: Path,
) -> None:
    """Two field-level findings of one type are two findings: strict raises."""
    acks = _acks(
        tmp_path,
        "  - symbol: S\n    change_kind: type_field_type_changed\n    reason: 'x'\n",
    )
    field_a = Change(
        kind=ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        symbol="S",
        description="field a",
        old_value="int",
        new_value="long",
    )
    field_b = Change(
        kind=ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        symbol="S",
        description="field b",
        old_value="int",
        new_value="long",
    )
    ledger = DispositionLedger()
    ledger.record(field_a, Disposition.GATING, application_point=_APP)
    ledger.record(field_b, Disposition.GATING, application_point=_APP)

    with pytest.raises(AmbiguousAcknowledgmentError, match="matches 2 changes"):
        resolve_acknowledgments(ledger, acks, strict=True)


def test_genuinely_different_findings_are_left_unresolved_when_not_strict(
    tmp_path: Path,
) -> None:
    acks = _acks(
        tmp_path,
        "  - symbol: S\n    change_kind: type_field_type_changed\n    reason: 'x'\n",
    )
    field_a = Change(
        kind=ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        symbol="S",
        description="field a",
        old_value="int",
        new_value="long",
    )
    field_b = Change(
        kind=ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        symbol="S",
        description="field b",
        old_value="int",
        new_value="long",
    )
    ledger = DispositionLedger()
    ledger.record(field_a, Disposition.GATING, application_point=_APP)
    ledger.record(field_b, Disposition.GATING, application_point=_APP)

    resolve_acknowledgments(ledger, acks, strict=False)

    for change in (field_a, field_b):
        record = ledger.record_for(change)
        assert record is not None
        assert record.acknowledged_by is None
