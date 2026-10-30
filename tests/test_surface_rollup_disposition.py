"""Unit: the roll-up membership relation and its disposition, no toolchain.

``public_surface_shrank`` is computed from declaration counts, so it has no
symbol a suppression can name. Its membership (which removal kinds it
summarises, ELF-only variants included) is owned by ``kind_variants``; the
disposition step in ``policy.finding_disposition.rollup`` applies it. The e2e
file proves the same thing through the CLI with real libraries.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.model import AbiSnapshot, Function, ScopeOrigin, Visibility
from abicheck.model.change import Change
from abicheck.model.change_catalog.kind_variants import (
    ROLLUP_MEMBER_KINDS,
    rollup_member_kinds,
)
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.policy.acknowledgment import AcknowledgmentList
from abicheck.policy.disposition_acknowledgment import resolve_acknowledgments
from abicheck.policy.disposition_ledger import Disposition, DispositionLedger
from abicheck.policy.finding_disposition.rollup import (
    SURFACE_ROLLUP_APPLICATION_POINT,
    dispose_surface_metric_rollups,
    dispose_surface_rollups,
)
from abicheck.policy.rule_provenance import RuleProvenance

_ROLLUP = ChangeKind.PUBLIC_SURFACE_SHRANK


def _change(kind: ChangeKind, symbol: str) -> Change:
    return Change(kind=kind, symbol=symbol, description=f"{kind.value} {symbol}")


def _hidden_by(rule_id: str, change: Change) -> Change:
    change.suppression_rule = rule_id
    return change


@pytest.mark.parametrize("base", ["func_removed", "var_removed"])
def test_elf_only_removal_kinds_feed_the_roll_up_with_their_base(base: str) -> None:
    members = rollup_member_kinds(_ROLLUP.value)
    assert base in members
    assert f"{base}_elf_only" in members


def test_type_removal_feeds_the_roll_up_without_a_variant() -> None:
    members = rollup_member_kinds(_ROLLUP.value)
    assert "type_removed" in members
    assert "type_removed_elf_only" not in members


@pytest.mark.parametrize(
    "kind",
    ["func_added", "var_added", "enum_member_removed", "public_surface_grew"],
)
def test_non_removal_kinds_are_not_members(kind: str) -> None:
    assert kind not in rollup_member_kinds(_ROLLUP.value)


def test_a_non_roll_up_kind_has_no_members() -> None:
    assert rollup_member_kinds(ChangeKind.FUNC_REMOVED.value) == frozenset()


def test_every_roll_up_mapping_names_real_change_kinds() -> None:
    known = {kind.value for kind in ChangeKind}
    for roll_up, bases in ROLLUP_MEMBER_KINDS.items():
        assert roll_up in known
        assert bases <= known
        assert all(rollup_member_kinds(roll_up) >= {b} for b in bases)


def _dispose(
    rollups: list[Change],
    *,
    kept: list[Change] | None = None,
    verdict_redundant: list[Change] | None = None,
    suppressed: list[Change],
    ledger: DispositionLedger | None = None,
) -> list[Change]:
    return dispose_surface_rollups(
        rollups,
        None,
        kept=kept or [],
        verdict_redundant=verdict_redundant or [],
        suppressed=suppressed,
        ledger=ledger,
    )


def test_all_covered_removals_suppressed_hides_the_roll_up() -> None:
    rollup = _change(_ROLLUP, "<surface>")
    first = _hidden_by("rule-a", _change(ChangeKind.FUNC_REMOVED, "gone_fn"))
    second = _hidden_by("rule-a", _change(ChangeKind.FUNC_REMOVED_ELF_ONLY, "gone_two"))
    ledger = DispositionLedger()
    for removal in (first, second):
        ledger.record(
            removal,
            Disposition.SUPPRESSED,
            application_point="apply_suppression",
            rule=RuleProvenance(rule_id="rule-a", reason="removed on purpose"),
        )
    suppressed = [first, second]

    visible = _dispose([rollup], suppressed=suppressed, ledger=ledger)

    assert visible == []
    assert suppressed == [first, second, rollup]
    assert rollup.suppression_rule == "rule-a"
    assert "gone_fn, gone_two" in rollup.description
    record = ledger.record_for(rollup)
    assert record is not None
    assert record.disposition is Disposition.SUPPRESSED
    assert record.application_point == SURFACE_ROLLUP_APPLICATION_POINT
    assert record.rule is not None
    assert record.rule.rule_id == "rule-a"


def test_a_visible_sibling_removal_keeps_the_roll_up() -> None:
    rollup = _change(_ROLLUP, "<surface>")
    hidden_removal = _hidden_by("rule-a", _change(ChangeKind.FUNC_REMOVED, "gone_fn"))
    visible_removal = _change(ChangeKind.FUNC_REMOVED, "gone_two")
    suppressed = [hidden_removal]

    visible = _dispose(
        [rollup], kept=[visible_removal], suppressed=suppressed, ledger=None
    )

    assert visible == [rollup]
    assert suppressed == [hidden_removal]
    assert rollup.suppression_rule is None


def test_a_visible_redundant_removal_also_keeps_the_roll_up() -> None:
    rollup = _change(_ROLLUP, "<surface>")
    hidden_removal = _hidden_by("rule-a", _change(ChangeKind.FUNC_REMOVED, "gone_fn"))
    folded = _change(ChangeKind.FUNC_REMOVED, "gone_fn")

    visible = _dispose(
        [rollup], verdict_redundant=[folded], suppressed=[hidden_removal]
    )

    assert visible == [rollup]


def test_no_covered_removal_leaves_the_roll_up_visible() -> None:
    rollup = _change(_ROLLUP, "<surface>")
    unrelated = _hidden_by("rule-a", _change(ChangeKind.FUNC_ADDED, "new_fn"))

    visible = _dispose([rollup], suppressed=[unrelated])

    assert visible == [rollup]


def test_a_non_roll_up_finding_passes_through_untouched() -> None:
    removal = _change(ChangeKind.FUNC_REMOVED, "gone_fn")
    suppressed: list[Change] = []

    visible = _dispose([removal], suppressed=suppressed)

    assert visible == [removal]
    assert suppressed == []


def _surface(names: list[str]) -> AbiSnapshot:
    functions = [
        Function(
            name=name,
            mangled=name,
            return_type="void",
            params=[],
            visibility=Visibility.PUBLIC,
            origin=ScopeOrigin.PUBLIC_HEADER,
        )
        for name in names
    ]
    return AbiSnapshot(
        library="lib", version="1", from_headers=True, functions=functions
    )


def test_roll_up_from_real_surfaces_is_hidden_when_every_removal_is_suppressed() -> (
    None
):
    """Two public functions removed, both suppressed: the derived roll-up is hidden."""
    old = _surface(["keep", "gone_b", "gone_c"])
    new = _surface(["keep"])
    suppressed = [
        _hidden_by("rule-a", _change(ChangeKind.FUNC_REMOVED, "gone_b")),
        _hidden_by("rule-a", _change(ChangeKind.FUNC_REMOVED, "gone_c")),
    ]

    visible = dispose_surface_metric_rollups(
        old,
        new,
        None,
        kept=[],
        verdict_redundant=[],
        suppressed=suppressed,
        ledger=None,
    )

    assert visible == []
    assert [c.kind for c in suppressed] == [
        ChangeKind.FUNC_REMOVED,
        ChangeKind.FUNC_REMOVED,
        ChangeKind.PUBLIC_SURFACE_SHRANK,
    ]
    rollup = suppressed[-1]
    assert rollup.suppression_rule == "rule-a"
    assert rollup.description.endswith(
        "(derived: 2 suppressed removal(s) of gone_b, gone_c)"
    )


def test_roll_up_from_real_surfaces_stays_visible_beside_a_kept_removal() -> None:
    old = _surface(["keep", "gone_b", "gone_c"])
    new = _surface(["keep"])
    suppressed = [_hidden_by("rule-a", _change(ChangeKind.FUNC_REMOVED, "gone_b"))]
    kept = [_change(ChangeKind.FUNC_REMOVED, "gone_c")]

    visible = dispose_surface_metric_rollups(
        old,
        new,
        None,
        kept=kept,
        verdict_redundant=[],
        suppressed=suppressed,
        ledger=None,
    )

    assert [c.kind for c in visible] == [ChangeKind.PUBLIC_SURFACE_SHRANK]
    assert visible[0].suppression_rule is None
    assert [c.kind for c in suppressed] == [ChangeKind.FUNC_REMOVED]


_ACK_REASON = "    reason: 'deliberate'\n"


def _removal_acks(tmp_path: Path, symbols: list[str]) -> AcknowledgmentList:
    body = "".join(
        f"  - symbol: {symbol}\n    change_kind: func_removed\n{_ACK_REASON}"
        for symbol in symbols
    )
    path = tmp_path / "ack.yml"
    path.write_text("version: 1\nacknowledgments:\n" + body)
    return AcknowledgmentList.load(path)


@pytest.mark.parametrize(
    ("removals", "expect_acknowledged"),
    [
        pytest.param(
            [
                ("gone_b", Disposition.GATING, True),
                ("gone_c", Disposition.GATING, True),
            ],
            True,
            id="every-covered-removal-acknowledged",
        ),
        pytest.param(
            [
                ("gone_b", Disposition.GATING, True),
                ("gone_c", Disposition.GATING, False),
            ],
            False,
            id="partially-acknowledged-stays-open",
        ),
        pytest.param(
            [
                ("gone_a", Disposition.SUPPRESSED, False),
                ("gone_b", Disposition.GATING, True),
                ("gone_c", Disposition.GATING, True),
            ],
            True,
            id="suppressed-removal-is-not-covered",
        ),
        pytest.param(
            [("gone_a", Disposition.SUPPRESSED, False)],
            False,
            id="no-covered-removal-leaves-roll-up-open",
        ),
    ],
)
def test_roll_up_is_acknowledged_only_when_its_covered_removals_all_are(
    tmp_path: Path,
    removals: list[tuple[str, Disposition, bool]],
    expect_acknowledged: bool,
) -> None:
    acks = _removal_acks(
        tmp_path, [symbol for symbol, _, acknowledged in removals if acknowledged]
    )
    ledger = DispositionLedger()
    records: dict[str, Change] = {}
    for symbol, disposition, _ in removals:
        removal = Change(
            kind=ChangeKind.FUNC_REMOVED, symbol=symbol, description=symbol
        )
        ledger.record(removal, disposition, application_point="apply_suppression")
        records[symbol] = removal
    rollup = _change(_ROLLUP, "<surface>")
    ledger.record(rollup, Disposition.NON_GATING, application_point="finalize")

    resolve_acknowledgments(ledger, acks, strict=True)

    rollup_record = ledger.record_for(rollup)
    assert rollup_record is not None
    if not expect_acknowledged:
        assert rollup_record.acknowledged_by is None
        return
    # The roll-up takes the first covered removal's acknowledgment, so a
    # suppressed removal (never covered) cannot be the source.
    first_covered = next(
        symbol
        for symbol, disposition, _ in removals
        if disposition is not Disposition.SUPPRESSED
    )
    first_record = ledger.record_for(records[first_covered])
    assert first_record is not None
    assert rollup_record.acknowledged_by is not None
    assert rollup_record.acknowledged_by == first_record.acknowledged_by
    assert rollup_record.acknowledgment_rule == first_record.acknowledgment_rule
