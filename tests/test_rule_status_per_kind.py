"""ADR-067 P1 addendum: ``not_evaluated`` is decided per rule, not globally.

A zero-match rule reads ``unused`` when at least one detector that can produce
one of its change kinds ran, and ``not_evaluated`` only when none of them did.
Before this, any refused detector made every unmatched rule ``not_evaluated``,
so a stale ELF symbol rule could never read ``unused`` on an ELF-only run.

The oracle is the table ``EXPECTED`` below, written by hand from the detector
source (a DWARF-only kind is produced by the DWARF detector alone; a symbol
size kind by the ELF detector alone; a kind-less rule could match anything).
Nothing here calls ``candidate_detectors`` or ``rule_status``.
"""

from __future__ import annotations

from datetime import date

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.detector_registry import registry
from abicheck.detectors import DetectorResult
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.policy.disposition_close import ledger_for
from abicheck.policy.disposition_rules import configured_rule_rows
from abicheck.policy.rule_inventory import (
    STATUS_NOT_EVALUATED,
    STATUS_UNUSED,
    ConfiguredRule,
    RuleRef,
    build_rule_rows,
)
from abicheck.policy.suppression.rules import Suppression, SuppressionList

TODAY = date(2026, 10, 21)
_NOPE = "_ZN2ns4nopeEv"

#: The rule shapes: the change kind each one selects (``None``: no kind).
SHAPES: dict[str, str | None] = {
    "dwarf_only_kind": ChangeKind.STRUCT_SIZE_CHANGED.value,
    "elf_symbol_kind": ChangeKind.SYMBOL_SIZE_CHANGED.value,
    "kindless": None,
    "kind_outside_registry": ChangeKind.INLINE_BODY_CHANGED.value,
}

#: The run states. ``dwarf_refused`` is the typical ELF-only comparison: every
#: detector ran except the DWARF one. ``no_records`` is a result that recorded
#: no detector at all, which must not be read as "every detector ran".
STATES = ("all_ran", "dwarf_refused", "all_refused", "no_records")

#: Hand-written expected statuses for a zero-match rule.
EXPECTED: dict[tuple[str, str], str] = {
    ("dwarf_only_kind", "all_ran"): STATUS_UNUSED,
    ("dwarf_only_kind", "dwarf_refused"): STATUS_NOT_EVALUATED,
    ("dwarf_only_kind", "all_refused"): STATUS_NOT_EVALUATED,
    ("dwarf_only_kind", "no_records"): STATUS_NOT_EVALUATED,
    ("elf_symbol_kind", "all_ran"): STATUS_UNUSED,
    ("elf_symbol_kind", "dwarf_refused"): STATUS_UNUSED,
    ("elf_symbol_kind", "all_refused"): STATUS_NOT_EVALUATED,
    ("elf_symbol_kind", "no_records"): STATUS_NOT_EVALUATED,
    ("kindless", "all_ran"): STATUS_UNUSED,
    ("kindless", "dwarf_refused"): STATUS_UNUSED,
    ("kindless", "all_refused"): STATUS_NOT_EVALUATED,
    ("kindless", "no_records"): STATUS_NOT_EVALUATED,
    # No detector produces this kind, so no detector run can have checked it.
    ("kind_outside_registry", "all_ran"): STATUS_NOT_EVALUATED,
    ("kind_outside_registry", "dwarf_refused"): STATUS_NOT_EVALUATED,
    ("kind_outside_registry", "all_refused"): STATUS_NOT_EVALUATED,
    ("kind_outside_registry", "no_records"): STATUS_NOT_EVALUATED,
}


def _ran_set(state: str) -> frozenset[str]:
    """The detectors that ran under *state*, from the live registry's names."""
    names = frozenset(registry.detector_names)
    if state == "all_ran":
        return names
    if state == "dwarf_refused":
        return names - {"dwarf"}
    return frozenset()


def _results_for(state: str) -> list[DetectorResult]:
    if state == "no_records":
        return []
    ran = _ran_set(state)
    return [
        DetectorResult(name=name, changes_count=0, enabled=True)
        if name in ran
        else DetectorResult(
            name=name,
            changes_count=0,
            enabled=False,
            coverage_gap="test: evidence absent",
            not_evaluated=True,
        )
        for name in registry.detector_names
    ]


@pytest.fixture(scope="module", autouse=True)
def _registered() -> None:
    registry.ensure_loaded()


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("shape", list(SHAPES))
def test_a_zero_match_rule_reads_the_status_its_shape_and_run_state_dictate(
    shape: str, state: str
) -> None:
    """The pure row builder, fed a hand-written run state."""
    rule = ConfiguredRule(
        ref=RuleRef("suppression", "policy.yml", 0),
        rule_id="stale",
        change_kinds=None if SHAPES[shape] is None else frozenset({SHAPES[shape]}),
    )
    (row,) = build_rule_rows(
        [rule],
        {},
        today=TODAY,
        ran_detectors=_ran_set(state),
    )
    assert row.matched_count == 0
    assert row.status == EXPECTED[(shape, state)]


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("shape", list(SHAPES))
def test_the_same_table_holds_through_a_real_comparison(shape: str, state: str) -> None:
    """The wiring: a real compare's rule rows, with the detector records
    replaced by the state under test, read the same statuses."""
    old = AbiSnapshot(library="libper", version="1.0")
    new = AbiSnapshot(library="libper", version="2.0")
    fn = Function(
        name="_ZN2ns4keepEv",
        mangled="_ZN2ns4keepEv",
        return_type="void",
        visibility=Visibility.PUBLIC,
    )
    old.declarations.functions.append(fn)
    new.declarations.functions.append(fn)
    kind = SHAPES[shape]
    if kind is None:
        rule = Suppression(symbol=_NOPE, reason="stale")
    else:
        rule = Suppression(symbol_pattern=".*", change_kind=kind, reason="stale")
    result = compare(old, new, SuppressionList([rule]))
    result.detector_results = _results_for(state)
    (row,) = configured_rule_rows(result, ledger=ledger_for(result), today=TODAY)
    assert row.status == EXPECTED[(shape, state)]


def test_an_expired_rule_still_reads_expired_whatever_ran() -> None:
    rule = ConfiguredRule(
        ref=RuleRef("suppression", "policy.yml", 0),
        rule_id="old",
        change_kinds=frozenset({ChangeKind.SYMBOL_SIZE_CHANGED.value}),
        expires=date(2000, 1, 1),
    )
    (row,) = build_rule_rows([rule], {}, today=TODAY, ran_detectors=_ran_set("all_ran"))
    assert row.status == "expired"
