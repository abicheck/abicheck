"""Validation branches of ``model/change_catalog/observability.py`` (ADR-093).

The oracle is the ADR-093 rule as written: disjoint platform/language
conditions, a non-empty evidence configuration, canonical relation targets
and an acyclic ``implies`` chain. Entries are small stubs, so the check does
not go through the module's own registry helpers.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from abicheck.model.change_catalog.observability import (
    UNCLASSIFIED,
    KindRelations,
    Language,
    ObservabilityCondition,
    Platform,
    observable,
    unclassified_kinds,
    validate_catalog,
    validate_observable_at,
)


def _entry(
    *,
    observable_at=UNCLASSIFIED,
    relations=KindRelations(),
    contract_subject="declaration",
    default_verdict="BREAKING",
):
    return SimpleNamespace(
        observable_at=observable_at,
        relations=relations,
        contract_subject=contract_subject,
        default_verdict=default_verdict,
    )


def test_condition_must_restrict_an_axis() -> None:
    with pytest.raises(ValueError, match="must restrict an axis"):
        ObservabilityCondition()


@pytest.mark.parametrize("axis", ["platform", "language"])
def test_condition_axis_may_not_be_empty(axis: str) -> None:
    with pytest.raises(ValueError, match="may not be empty"):
        ObservabilityCondition(**{axis: frozenset()})


def test_condition_overlap_and_match() -> None:
    elf = ObservabilityCondition(platform=frozenset({Platform.ELF}))
    pe = ObservabilityCondition(platform=frozenset({Platform.PE}))
    c_only = ObservabilityCondition(language=frozenset({Language.C}))

    assert elf.overlaps(None)
    assert not elf.overlaps(pe)
    assert elf.overlaps(c_only)  # the language axis is unrestricted on elf
    assert elf.matches(Platform.ELF, None)
    assert not elf.matches(Platform.PE, Language.C)
    assert elf.matches(None, Language.CXX)  # an unknown run axis matches


def test_observable_rejects_an_empty_evidence_configuration() -> None:
    with pytest.raises(ValueError, match="may not be empty"):
        observable(())


def test_unclassified_repr_and_copy_identity() -> None:
    assert repr(UNCLASSIFIED) == "UNCLASSIFIED"
    assert copy.deepcopy(UNCLASSIFIED) is UNCLASSIFIED


@pytest.mark.parametrize(
    ("relations", "truthy"),
    [
        (KindRelations(), False),
        (KindRelations(variant_of="b"), True),
        (KindRelations(implies=("b",)), True),
    ],
)
def test_kind_relations_truthiness(relations: KindRelations, truthy: bool) -> None:
    assert bool(relations) is truthy


def test_observable_at_must_be_a_non_empty_tuple_of_observability() -> None:
    with pytest.raises(ValueError, match="non-empty tuple"):
        validate_observable_at("k", ())
    with pytest.raises(ValueError, match="non-Observability"):
        validate_observable_at("k", ("not-an-observability",))


def test_observable_at_entries_need_disjoint_conditions() -> None:
    elf = observable(
        ("B",), condition=ObservabilityCondition(platform=frozenset({Platform.ELF}))
    )
    pe = observable(
        ("B",), condition=ObservabilityCondition(platform=frozenset({Platform.PE}))
    )
    validate_observable_at("k", (elf, pe))  # disjoint platforms: accepted

    elf_again = observable(
        ("B",), condition=ObservabilityCondition(platform=frozenset({Platform.ELF}))
    )
    with pytest.raises(ValueError, match="pairwise disjoint"):
        validate_observable_at("k", (elf, elf_again))  # overlapping: refused
    with pytest.raises(ValueError, match="pairwise disjoint"):
        validate_observable_at("k", (observable(("B",)), pe))  # unconditioned


def test_relation_target_must_be_a_kind() -> None:
    entries = {"a": _entry(relations=KindRelations(variant_of="missing"))}
    with pytest.raises(ValueError, match="is not a kind"):
        validate_catalog(entries)


def test_kind_may_not_relate_to_itself() -> None:
    entries = {"a": _entry(relations=KindRelations(variant_of="a"))}
    with pytest.raises(ValueError, match="may not relate to itself"):
        validate_catalog(entries)


def test_variant_target_must_be_canonical() -> None:
    entries = {
        "a": _entry(relations=KindRelations(variant_of="b")),
        "b": _entry(relations=KindRelations(variant_of="c")),
        "c": _entry(),
    }
    with pytest.raises(ValueError, match="must be canonical"):
        validate_catalog(entries)


def test_relation_target_must_share_the_contract_subject() -> None:
    entries = {
        "a": _entry(
            relations=KindRelations(variant_of="b"), contract_subject="declaration"
        ),
        "b": _entry(contract_subject="contract"),
    }
    with pytest.raises(ValueError, match="different contract_subject"):
        validate_catalog(entries)


def test_same_event_target_must_share_the_default_verdict() -> None:
    entries = {
        "a": _entry(
            relations=KindRelations(same_event_as="b"), default_verdict="BREAKING"
        ),
        "b": _entry(default_verdict="COMPATIBLE"),
    }
    with pytest.raises(ValueError, match="different default_verdict"):
        validate_catalog(entries)


def test_implies_cycle_is_refused_with_its_path() -> None:
    entries = {
        "a": _entry(relations=KindRelations(implies=("b",))),
        "b": _entry(relations=KindRelations(implies=("a",))),
    }
    with pytest.raises(ValueError, match="implies cycle: a -> b -> a"):
        validate_catalog(entries)


def test_implies_diamond_is_accepted() -> None:
    # d is reached twice: once through b, then again through c, after it is done.
    entries = {
        "a": _entry(relations=KindRelations(implies=("b", "c"))),
        "b": _entry(relations=KindRelations(implies=("d",))),
        "c": _entry(relations=KindRelations(implies=("d",))),
        "d": _entry(),
    }
    assert validate_catalog(entries) is None


def test_unclassified_kinds_lists_only_sentinel_entries_sorted() -> None:
    entries = {
        "z": _entry(),
        "b": _entry(observable_at=(observable(("B",)),)),
        "a": _entry(),
    }
    assert unclassified_kinds(entries) == ["a", "z"]
