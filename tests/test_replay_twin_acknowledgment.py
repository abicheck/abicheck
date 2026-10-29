"""A source-replay twin is one observation with the finding it folded into.

The L4 replay restates a header-tier finding (``compare.source_replay_twins``).
The dedup step drops the replay copy and keeps its partner; the dropped copy
stays on the ledger as ``deduplicated``. An acknowledgment that covers the pair
is one observation (ADR-067 D5): it lands on the surviving finding, and the
dropped copy is never acknowledged on its own.

Acknowledgment records must name a ``change_kind``, so a record covers one kind
(and its ``_elf_only`` variant). Two copies of one replay row are therefore
covered by one record only when they share a kind, which is the
``INLINE_FUNCTION_REMOVED`` row: that pair must count once, not raise.

Every row is enumerated from the twin table (``REPLAY_TWINS``,
``DISTINCT_FACETS``, ``REPLAY_ONLY``), never hand-picked. The expected survivor
is read off the table's ``owner`` field and the verdict sets, not from the
implementation's helpers. No toolchain is needed: the changes are constructed,
and the real pipeline runs only its dedup step.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.compare.source_replay_twins import (
    DISTINCT_FACETS,
    REPLAY_ONLY,
    REPLAY_TWINS,
    SOURCE_REPLAY_EVIDENCE,
    ReplayTwin,
    replay_twin_pairs,
)
from abicheck.model.change import Change
from abicheck.model.change_catalog.assembled import (
    API_BREAK_KINDS,
    BREAKING_KINDS,
    RISK_KINDS,
)
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.snapshot import AbiSnapshot
from abicheck.policy.acknowledgment import AcknowledgmentList
from abicheck.policy.dedup_survivors import (
    dedup_survivor,
    deduplicate_recording_survivors,
)
from abicheck.policy.disposition_acknowledgment import resolve_acknowledgments
from abicheck.policy.disposition_ledger import Disposition, DispositionLedger
from abicheck.policy.finding_disposition.pipeline import DispositionPipeline
from abicheck.policy.finding_disposition.run_state import PipelineContext
from abicheck.post_processing import DeduplicateCrossDetector
from abicheck.workflows.snapshot_factory import absent_baseline

_NAME = "demo_under_test"
_NEW = AbiSnapshot(library="lib", version="2.0")


def _rank(kind: ChangeKind) -> int:
    """Verdict rank read straight off the verdict sets (4 breaking .. 1 compatible)."""
    if kind in BREAKING_KINDS:
        return 4
    if kind in API_BREAK_KINDS:
        return 3
    if kind in RISK_KINDS:
        return 2
    return 1


def _expected_survivor(twin: ReplayTwin, header: ChangeKind) -> ChangeKind:
    """The kept copy, from the table row alone: the owner, else the stronger verdict."""
    if twin.owner == "replay":
        return twin.replay
    return twin.replay if _rank(twin.replay) > _rank(header) else header


def _pair(replay_kind: ChangeKind, header_kind: ChangeKind) -> list[Change]:
    header = Change(
        kind=header_kind,
        symbol=_NAME,
        description=f"header {_NAME}",
        old_value=_NAME,
        new_value="2",
    )
    replay = Change(
        kind=replay_kind,
        symbol=_NAME,
        description=f"replay {_NAME}",
        old_value=_NAME,
        new_value="2",
        evidence_category=SOURCE_REPLAY_EVIDENCE,
    )
    return [header, replay]


def _record_for(tmp_path: Path, kind: ChangeKind) -> AcknowledgmentList:
    """A ``symbol`` + ``change_kind`` record: the only shape the loader accepts."""
    path = tmp_path / "ack.yml"
    path.write_text(
        "version: 1\nacknowledgments:\n"
        f"  - symbol: {_NAME}\n    change_kind: {kind.value}\n"
        "    reason: 'deliberate'\n"
    )
    return AcknowledgmentList.load(path)


def _run_dedup(changes: list[Change]) -> tuple[list[Change], DispositionLedger]:
    """The real dedup step, recorded the way the pipeline records it."""
    ledger = DispositionLedger()
    ctx = DispositionPipeline([DeduplicateCrossDetector()]).run(
        list(changes),
        None,
        _NEW,
        ledger=ledger,
        absent_baseline=absent_baseline("lib"),
    )
    return list(ctx.kept), ledger


def _keep_on_ledger(ledger: DispositionLedger, kept: list[Change]) -> None:
    """Survivors are labelled when the ledger closes; label them as gating here."""
    for change in kept:
        ledger.record(change, Disposition.GATING, application_point="finalize")


def _paired_cases() -> list:
    """(twin, header kind, record kind) for every paired row and each kind it names."""
    cases = []
    for twin in REPLAY_TWINS:
        for header in sorted(twin.header, key=lambda k: k.name):
            for kind in sorted({header, twin.replay}, key=lambda k: k.name):
                cases.append(
                    pytest.param(
                        twin,
                        header,
                        kind,
                        id=f"{twin.replay.name}<-{header.name}:{kind.name}",
                    )
                )
    return cases


def _distinct_cases() -> list:
    return [
        pytest.param(facet.replay, header, id=f"{facet.replay.name}|{header.name}")
        for facet in DISTINCT_FACETS
        for header in sorted(facet.header, key=lambda k: k.name)
    ]


@pytest.mark.parametrize(("twin", "header", "kind"), _paired_cases())
def test_paired_twin_is_one_observation_acknowledged_through_its_survivor(
    tmp_path: Path, twin: ReplayTwin, header: ChangeKind, kind: ChangeKind
) -> None:
    changes = _pair(twin.replay, header)
    kept, ledger = _run_dedup(changes)
    assert [c.kind for c in kept] == [_expected_survivor(twin, header)]
    survivor = kept[0]
    dropped = next(c for c in changes if c is not survivor)
    _keep_on_ledger(ledger, kept)

    # A record that covers one copy, or both copies of a same-kind pair, must
    # neither raise nor acknowledge the dropped copy: the survivor carries it.
    resolve_acknowledgments(ledger, _record_for(tmp_path, kind), strict=True)

    survivor_record = ledger.record_for(survivor)
    twin_record = ledger.record_for(dropped)
    assert survivor_record is not None and twin_record is not None
    assert survivor_record.acknowledged_by is not None
    assert twin_record.disposition is Disposition.DEDUPLICATED
    assert twin_record.acknowledged_by is None


def test_chained_twins_resolve_to_the_finding_that_is_kept(tmp_path: Path) -> None:
    """A header copy dropped by a later row still names the kept survivor.

    Row 1 pairs the replay INLINE copy with the header INLINE copy (equal verdict,
    header kept). Row 2 (UNINSTANTIATED_TEMPLATE_REMOVED, replay-owned) then drops
    that header copy in favour of the replay template copy. The two INLINE copies
    must therefore both resolve to the template copy, the one kept. A record that
    names their shared kind covers both, and that is one observation.
    """
    header = Change(
        kind=ChangeKind.INLINE_FUNCTION_REMOVED, symbol=_NAME, description="header"
    )
    replay_inline = Change(
        kind=ChangeKind.INLINE_FUNCTION_REMOVED,
        symbol=_NAME,
        description="replay inline",
        evidence_category=SOURCE_REPLAY_EVIDENCE,
    )
    replay_template = Change(
        kind=ChangeKind.UNINSTANTIATED_TEMPLATE_REMOVED,
        symbol=_NAME,
        description="replay template",
        evidence_category=SOURCE_REPLAY_EVIDENCE,
    )
    changes = [header, replay_inline, replay_template]

    pairs = replay_twin_pairs(changes)
    assert len(pairs) == 2
    assert all(survivor is replay_template for _, survivor in pairs)

    kept, ledger = _run_dedup(changes)
    assert kept == [replay_template]
    _keep_on_ledger(ledger, kept)
    resolve_acknowledgments(
        ledger,
        _record_for(tmp_path, ChangeKind.INLINE_FUNCTION_REMOVED),
        strict=True,
    )

    survivor_record = ledger.record_for(replay_template)
    assert survivor_record is not None and survivor_record.acknowledged_by is not None
    for twin_change in (header, replay_inline):
        record = ledger.record_for(twin_change)
        assert record is not None
        assert record.disposition is Disposition.DEDUPLICATED
        assert record.acknowledged_by is None


@pytest.mark.parametrize(("replay", "header"), _distinct_cases())
def test_distinct_facets_are_never_folded_each_copy_is_its_own_observation(
    tmp_path: Path, replay: ChangeKind, header: ChangeKind
) -> None:
    """A facet pair is two findings: each copy keeps its own record, neither folds.

    The facet's two kinds differ, so no single ``change_kind`` record can cover
    both (the strict "matches 2 changes" raise has nothing to fire on). What
    must hold is that the pair is not folded: both survive, neither is
    deduplicated, and each record acknowledges only its own finding.
    """
    changes = _pair(replay, header)
    kept, ledger = _run_dedup(changes)
    assert sorted(c.kind.name for c in kept) == sorted([replay.name, header.name])
    _keep_on_ledger(ledger, kept)

    for kind in (replay, header):
        resolve_acknowledgments(ledger, _record_for(tmp_path, kind), strict=True)

    by_kind = {c.kind: c for c in kept}
    for kind, change in by_kind.items():
        record = ledger.record_for(change)
        assert record is not None
        assert record.disposition is Disposition.GATING
        assert record.acknowledged_by is not None


@pytest.mark.parametrize(
    "kind", sorted(REPLAY_ONLY, key=lambda k: k.name), ids=lambda k: k.name
)
def test_replay_only_kind_is_never_folded_into_another_finding(
    tmp_path: Path, kind: ChangeKind
) -> None:
    """A replay-only kind has no header partner: it is its own observation."""
    changes = _pair(kind, ChangeKind.FUNC_ADDED)
    kept, ledger = _run_dedup(changes)
    assert sorted(c.kind.name for c in kept) == sorted([kind.name, "FUNC_ADDED"])
    _keep_on_ledger(ledger, kept)

    resolve_acknowledgments(ledger, _record_for(tmp_path, kind), strict=True)

    replay_copy = next(c for c in kept if c.kind is kind)
    other = next(c for c in kept if c.kind is ChangeKind.FUNC_ADDED)
    replay_record = ledger.record_for(replay_copy)
    other_record = ledger.record_for(other)
    assert replay_record is not None and other_record is not None
    assert replay_record.acknowledged_by is not None
    assert other_record.acknowledged_by is None


@pytest.mark.parametrize(("twin", "header", "kind"), _paired_cases())
def test_pairing_names_exactly_the_copy_it_drops(
    twin: ReplayTwin, header: ChangeKind, kind: ChangeKind
) -> None:
    """The pair names the dropped copy and the kept one, and nothing else."""
    changes = _pair(twin.replay, header)
    pairs = replay_twin_pairs(changes, (twin,))
    assert len(pairs) == 1
    dropped, survivor = pairs[0]
    assert survivor.kind is _expected_survivor(twin, header)
    assert dropped is not survivor
    assert {id(c) for c in changes} == {id(dropped), id(survivor)}


def test_names_that_do_not_match_never_pair() -> None:
    twin = next(
        t for t in REPLAY_TWINS if t.replay is ChangeKind.CONSTEXPR_VALUE_CHANGED
    )
    header_kind = sorted(twin.header, key=lambda k: k.name)[0]
    changes = _pair(twin.replay, header_kind)
    changes[1].symbol = _NAME + "_other"
    assert replay_twin_pairs(changes, (twin,)) == []


def _first_paired_twin() -> tuple[ReplayTwin, ChangeKind]:
    twin = next(
        t for t in REPLAY_TWINS if t.replay is ChangeKind.CONSTEXPR_VALUE_CHANGED
    )
    return twin, sorted(twin.header, key=lambda k: k.name)[0]


def test_dropped_twin_names_its_survivor_only_once_the_survivor_is_recorded() -> None:
    """Before the survivor has a ledger record the twin is its own observation."""
    twin, header_kind = _first_paired_twin()
    changes = _pair(twin.replay, header_kind)
    kept, ledger = _run_dedup(changes)
    (survivor,) = kept
    dropped = next(c for c in changes if c is not survivor)

    assert dedup_survivor(ledger, dropped) is None

    _keep_on_ledger(ledger, kept)
    linked = dedup_survivor(ledger, dropped)
    assert linked is not None
    assert linked[1] is survivor


def test_dedup_without_a_ledger_keeps_the_same_survivor() -> None:
    """With no ledger the pair is still collapsed onto the kind the table names."""
    twin, header_kind = _first_paired_twin()
    changes = _pair(twin.replay, header_kind)
    ctx = PipelineContext(old=None, new=_NEW, absent_baseline=absent_baseline("lib"))

    kept = deduplicate_recording_survivors(list(changes), ctx)

    assert [c.kind for c in kept] == [_expected_survivor(twin, header_kind)]
    assert ctx.disposition_ledger is None
