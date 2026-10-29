"""A base-kind selector also matches that kind's ``*_elf_only`` variant.

A removal is ``FUNC_REMOVED`` or ``FUNC_REMOVED_ELF_ONLY`` depending on whether
headers were supplied, not on the change. A suppression, reclassification or
acknowledgment naming the base kind must therefore hit both, or its acceptance
would depend on the evidence supplied (AGENTS.md "Policy decides acceptance, not
facts"). The relation is one-directional: a rule naming the variant matches only
the variant.

The oracle for the pairs is the enum's spelling, not the implementation's own
helper: a pair is ``(base, base + "_elf_only")`` when both names are kinds.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.model.change import Change
from abicheck.model.change_catalog.kind_variants import (
    ELF_ONLY_VARIANT_OF,
    kind_match_set,
    matched_via_variant,
)
from abicheck.policy.disposition_ledger import DispositionLedger
from abicheck.policy.finding_disposition.suppression import dispose_by_suppression
from abicheck.policy.reclassify import ReclassifyRule, first_matching_reclassify_verdict
from abicheck.policy.rule_inventory import suppression_rules
from abicheck.policy.suppression.rules import Suppression, SuppressionList

_ALL_VALUES = frozenset(kind.value for kind in ChangeKind)

# The four pairs the catalog defines today, spelled out so that adding a new
# variant is a deliberate, reviewed change rather than a silent widening.
_EXPECTED_PAIRS = frozenset(
    {
        ("func_removed", "func_removed_elf_only"),
        ("func_added", "func_added_elf_only"),
        ("var_removed", "var_removed_elf_only"),
        ("var_added", "var_added_elf_only"),
    }
)


def _oracle_pairs() -> frozenset[tuple[str, str]]:
    """Every (base, variant) pair, from the spelling of the enum alone."""
    return frozenset(
        (value[: -len("_elf_only")], value)
        for value in _ALL_VALUES
        if value.endswith("_elf_only") and value[: -len("_elf_only")] in _ALL_VALUES
    )


_PAIRS = sorted(_oracle_pairs())


def _change(value: str) -> Change:
    return Change(
        kind=ChangeKind(value),
        symbol="_Z3foov",
        description="removed",
    )


def _suppresses(change_kind: str, change: Change) -> bool:
    rules = SuppressionList(
        suppressions=[Suppression(change_kind=change_kind, symbol="_Z3foov")]
    )
    return rules.is_suppressed(change)


def test_catalog_defines_exactly_the_expected_pairs() -> None:
    assert _oracle_pairs() == _EXPECTED_PAIRS
    assert ELF_ONLY_VARIANT_OF == {base: variant for base, variant in _EXPECTED_PAIRS}


@pytest.mark.parametrize(("base", "variant"), _PAIRS)
def test_base_rule_suppresses_base_and_variant(base: str, variant: str) -> None:
    assert _suppresses(base, _change(base)) is True
    assert _suppresses(base, _change(variant)) is True


@pytest.mark.parametrize(("base", "variant"), _PAIRS)
def test_variant_rule_suppresses_only_the_variant(base: str, variant: str) -> None:
    assert _suppresses(variant, _change(variant)) is True
    assert _suppresses(variant, _change(base)) is False


@pytest.mark.parametrize(("base", "variant"), _PAIRS)
def test_neither_rule_matches_an_unrelated_kind(base: str, variant: str) -> None:
    unrelated = sorted(_ALL_VALUES - {base, variant})
    assert unrelated, "catalog has kinds beyond the pair"
    for other in unrelated:
        assert _suppresses(base, _change(other)) is False, other
        assert _suppresses(variant, _change(other)) is False, other


@pytest.mark.parametrize(("base", "variant"), _PAIRS)
def test_match_set_and_variant_marker(base: str, variant: str) -> None:
    assert kind_match_set(base) == frozenset({base, variant})
    assert kind_match_set(variant) == frozenset({variant})
    assert matched_via_variant(base, variant) is True
    assert matched_via_variant(base, base) is False
    assert matched_via_variant(variant, base) is False
    assert matched_via_variant(None, variant) is False


def test_kinds_without_a_variant_match_only_themselves() -> None:
    assert kind_match_set("type_removed") == frozenset({"type_removed"})
    assert _suppresses("type_removed", _change("type_removed")) is True
    assert _suppresses("type_removed", _change("func_removed")) is False


@pytest.mark.parametrize(("base", "variant"), _PAIRS)
def test_reclassify_base_rule_reaches_the_variant(base: str, variant: str) -> None:
    rule = ReclassifyRule(
        to_verdict=Verdict.COMPATIBLE_WITH_RISK,
        to="risk",
        change_kind=base,
        symbol="_Z3foov",
    )
    other = ReclassifyRule(
        to_verdict=Verdict.COMPATIBLE,
        to="other",
        change_kind="type_removed",
        symbol="_Z3foov",
    )
    # Two rules, so the per-kind bucketing (not the short-list fast path) runs.
    verdict = first_matching_reclassify_verdict([other, rule], _change(variant))
    assert verdict == Verdict.COMPATIBLE_WITH_RISK


@pytest.mark.parametrize(("base", "variant"), _PAIRS)
def test_rule_inventory_names_both_kinds_for_a_base_rule(
    base: str, variant: str
) -> None:
    base_list = SuppressionList(
        suppressions=[Suppression(change_kind=base, symbol="_Z3foov")]
    )
    variant_list = SuppressionList(
        suppressions=[Suppression(change_kind=variant, symbol="_Z3foov")]
    )
    (base_rule,) = suppression_rules(base_list)
    (variant_rule,) = suppression_rules(variant_list)
    assert base_rule.change_kinds == frozenset({base, variant})
    assert variant_rule.change_kinds == frozenset({variant})


@pytest.mark.parametrize(("base", "variant"), _PAIRS)
def test_variant_match_is_recorded_on_the_disposition(base: str, variant: str) -> None:
    suppression = SuppressionList(
        suppressions=[Suppression(change_kind=base, symbol="_Z3foov", reason="t")]
    )
    ledger = DispositionLedger()
    disposition = dispose_by_suppression(
        [_change(variant), _change(base)],
        suppression,
        ledger=ledger,
        application_point="apply_suppression",
    )
    assert disposition.kept == []
    by_kind = {record.kind: record for record in ledger.records}
    # The variant matched through the base kind: the record says so.
    assert by_kind[variant].rule is not None
    assert by_kind[variant].rule.matched_via_kind == base
    assert by_kind[variant].rule.to_dict()["matched_via_kind"] == base
    # An exact-kind match carries no variant marker.
    assert by_kind[base].rule is not None
    assert by_kind[base].rule.matched_via_kind is None
    assert "matched_via_kind" not in by_kind[base].rule.to_dict()


@pytest.mark.parametrize(("base", "variant"), _PAIRS)
def test_one_rule_matching_both_ways_is_one_tally_entry(
    base: str, variant: str
) -> None:
    """The variant marker is per finding; the rule's tally identity ignores it."""
    suppression = SuppressionList(
        suppressions=[Suppression(change_kind=base, symbol="_Z3foov", reason="t")]
    )
    ledger = DispositionLedger()
    dispose_by_suppression(
        [_change(base), _change(variant), _change(base)],
        suppression,
        ledger=ledger,
        application_point="apply_suppression",
    )
    tallies = ledger.rules()
    assert len(tallies) == 1
    rule, count = tallies[0]
    assert count == 3
    assert rule.matched_via_kind is None
    # The per-finding record still says which match was the variant one.
    via = [r.rule.matched_via_kind for r in ledger.records]
    assert via == [None, base, None]
