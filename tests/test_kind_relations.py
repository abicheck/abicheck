"""The ``variant_of`` resolution pass is behaviour-preserving (ADR-093 D5, WS-5 PR 1).

Before the pass, the variant relation was derived from names by
``model/change_catalog/kind_variants.py``. It is now declared on the registry
entries and resolved by ``policy/kind_relations.py``. The oracle for the
old behaviour is the enum spelling, computed here and not by the
implementation: ``<base>_elf_only`` is the variant of ``<base>`` when both
are kinds.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.model.change_catalog.assembled import REGISTRY
from abicheck.model.change_catalog.observability import KindRelations
from abicheck.policy.kind_relations import kind_match_set, variant_index

_SUFFIX = "_elf_only"
_ALL_VALUES = frozenset(kind.value for kind in ChangeKind)


def _oracle_match_set(kind: str) -> frozenset[str]:
    """The old name-derived rule, spelled out independently of the resolver."""
    variant = kind + _SUFFIX
    if variant in _ALL_VALUES:
        return frozenset({kind, variant})
    return frozenset({kind})


def _oracle_pairs() -> frozenset[tuple[str, str]]:
    return frozenset(
        (value[: -len(_SUFFIX)], value)
        for value in _ALL_VALUES
        if value.endswith(_SUFFIX) and value[: -len(_SUFFIX)] in _ALL_VALUES
    )


@pytest.mark.parametrize("kind", sorted(_ALL_VALUES))
def test_match_set_equals_old_name_rule_for_every_kind(kind: str) -> None:
    assert kind_match_set(kind) == _oracle_match_set(kind)


def test_declared_variants_are_exactly_the_name_derived_pairs() -> None:
    declared = frozenset(
        (meta.relations.variant_of, kind)
        for kind, meta in REGISTRY.entries.items()
        if meta.relations.variant_of is not None
    )
    assert declared == _oracle_pairs()
    assert declared, "the catalog must declare at least one variant"


def test_every_elf_only_kind_declares_its_base_as_variant_of() -> None:
    for kind in _ALL_VALUES:
        if kind.endswith(_SUFFIX) and kind[: -len(_SUFFIX)] in _ALL_VALUES:
            assert REGISTRY.get(kind).relations.variant_of == kind[: -len(_SUFFIX)]


def test_variant_index_is_one_directional_and_groups_variants() -> None:
    index = variant_index(
        {
            "a": KindRelations(),
            "a_v1": KindRelations(variant_of="a"),
            "a_v2": KindRelations(variant_of="a"),
            "b": KindRelations(),
        }
    )
    assert index == {"a": frozenset({"a_v1", "a_v2"})}
    assert "a_v1" not in index  # a variant never appears as a base
