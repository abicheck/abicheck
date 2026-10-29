"""A base ``overrides:`` entry also pins its ``*_elf_only`` variant.

``overrides: {func_removed: ignore}`` pins a verdict per ``ChangeKind``. The
variant ``func_removed_elf_only`` is the same removal found without headers, so
the base entry covers it too. Otherwise the verdict would depend on whether
headers were supplied. An explicit variant entry always wins over the base
entry. The relation is one-directional: a variant entry never covers its base.

The oracle is the verdict the policy document produces (through the public
``effective_verdict_for_change``), not the implementation's own expansion helper.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.model.change import Change
from abicheck.model.change_catalog.kind_variants import expand_variant_overrides
from abicheck.policy.reclassify import effective_verdict_for_change
from abicheck.policy_file import PolicyFile

_PAIRS = [
    ("func_removed", "func_removed_elf_only"),
    ("func_added", "func_added_elf_only"),
    ("var_removed", "var_removed_elf_only"),
    ("var_added", "var_added_elf_only"),
]


def _change(value: str) -> Change:
    return Change(kind=ChangeKind(value), symbol="_Z3foov", description="x")


def _verdict(value: str, overrides: dict[ChangeKind, Verdict]) -> Verdict:
    policy_file = PolicyFile(base_policy="strict_abi", overrides=overrides)
    return effective_verdict_for_change(_change(value), policy_file=policy_file)


def _other_than(verdict: Verdict) -> Verdict:
    """A verdict the override map can express that differs from *verdict*."""
    return Verdict.COMPATIBLE if verdict != Verdict.COMPATIBLE else Verdict.BREAKING


@pytest.mark.parametrize(("base", "variant"), _PAIRS)
def test_base_entry_pins_base_and_variant(base: str, variant: str) -> None:
    default = _verdict(base, {})
    assert _verdict(variant, {}) == default, "precondition: same default verdict"
    target = _other_than(default)
    overrides = {ChangeKind(base): target}
    assert _verdict(base, overrides) == target
    assert _verdict(variant, overrides) == target


@pytest.mark.parametrize(("base", "variant"), _PAIRS)
def test_explicit_variant_entry_wins_over_base_entry(base: str, variant: str) -> None:
    default = _verdict(base, {})
    base_target = _other_than(default)
    variant_target = default  # distinct from the base entry's verdict
    assert variant_target != base_target
    overrides = {ChangeKind(base): base_target, ChangeKind(variant): variant_target}
    assert _verdict(base, overrides) == base_target
    assert _verdict(variant, overrides) == variant_target


@pytest.mark.parametrize(("base", "variant"), _PAIRS)
def test_variant_entry_does_not_cover_its_base(base: str, variant: str) -> None:
    default = _verdict(base, {})
    target = _other_than(default)
    overrides = {ChangeKind(variant): target}
    assert _verdict(variant, overrides) == target
    assert _verdict(base, overrides) == default


@pytest.mark.parametrize(("base", "variant"), _PAIRS)
def test_unrelated_kind_is_not_covered(base: str, variant: str) -> None:
    unrelated = "type_removed"
    default = _verdict(unrelated, {})
    overrides = {ChangeKind(base): _other_than(_verdict(base, {}))}
    assert _verdict(unrelated, overrides) == default


@pytest.mark.parametrize(("base", "variant"), _PAIRS)
def test_expansion_is_a_fresh_mapping(base: str, variant: str) -> None:
    overrides = {ChangeKind(base): Verdict.BREAKING}
    expanded = expand_variant_overrides(overrides)
    assert expanded[ChangeKind(variant)] == Verdict.BREAKING
    assert ChangeKind(variant) not in overrides, "input is not modified"


@pytest.mark.parametrize(("base", "variant"), _PAIRS)
def test_policy_file_compute_verdict_applies_the_base_entry_to_the_variant(
    base: str, variant: str
) -> None:
    """The document-level verdict (``PolicyFile.compute_verdict``) agrees too."""
    default = _verdict(base, {})
    target = _other_than(default)
    policy_file = PolicyFile(
        base_policy="strict_abi", overrides={ChangeKind(base): target}
    )
    assert policy_file.compute_verdict([_change(variant)]) == target
    assert policy_file.compute_verdict([_change(base)]) == target
