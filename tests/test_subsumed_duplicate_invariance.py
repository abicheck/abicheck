"""Subsumed AST/DWARF duplicates: order-invariant, and only where declared.

``deduplicate_ast_dwarf`` drops a DWARF-lane finding when an AST-lane finding
on the same subject makes it redundant. The subsumptions are declared on the
registry (``subsumed_by``, ADR-093 D4), and the matching engine is unchanged.

The expectations below are written out by hand, one per scenario, so they are
independent of the implementation's helpers. Each scenario names a subject
(``symbol``) that no other scenario shares, so the expected result of the
whole input is the union of the per-scenario results. Every permutation of the
input must give that same union: detector order must not be a semantic input.
"""

from __future__ import annotations

import random

import pytest

from abicheck.compare.finding_dedup import deduplicate_ast_dwarf
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind as K


def _change(kind: K, symbol: str, field: str | None = None) -> Change:
    # Transitions are left unset (None) so the DWARF finding agrees with any
    # AST partner, which is the case the declared subsumption describes.
    return Change(kind=kind, symbol=symbol, description="d", field_name=field)


# (scenario, the changes it contributes, the (symbol, kind) pairs it keeps)
_SCENARIOS: list[tuple[str, list[Change], set[tuple[str, str]]]] = [
    (
        "size",
        [_change(K.STRUCT_SIZE_CHANGED, "Size"), _change(K.TYPE_SIZE_CHANGED, "Size")],
        {("Size", "type_size_changed")},
    ),
    (
        "alignment",
        [
            _change(K.STRUCT_ALIGNMENT_CHANGED, "Align"),
            _change(K.TYPE_ALIGNMENT_CHANGED, "Align"),
        ],
        {("Align", "type_alignment_changed")},
    ),
    (
        "field_offset",
        [
            _change(K.STRUCT_FIELD_OFFSET_CHANGED, "Off", "x"),
            _change(K.TYPE_FIELD_OFFSET_CHANGED, "Off", "x"),
        ],
        {("Off", "type_field_offset_changed")},
    ),
    (
        "field_removed",
        [
            _change(K.STRUCT_FIELD_REMOVED, "Rem", "x"),
            _change(K.TYPE_FIELD_REMOVED, "Rem", "x"),
        ],
        {("Rem", "type_field_removed")},
    ),
    (
        "field_type_struct_partner",
        [
            _change(K.STRUCT_FIELD_TYPE_CHANGED, "FT", "x"),
            _change(K.TYPE_FIELD_TYPE_CHANGED, "FT", "x"),
        ],
        {("FT", "type_field_type_changed")},
    ),
    (
        "field_type_union_partner",
        [
            _change(K.STRUCT_FIELD_TYPE_CHANGED, "FTU", "x"),
            _change(K.UNION_FIELD_TYPE_CHANGED, "FTU", "x"),
        ],
        {("FTU", "union_field_type_changed")},
    ),
    # Negatives: a DWARF finding with no declared partner on its subject stays,
    # and so does a partner that names a different field.
    (
        "size_without_partner",
        [_change(K.STRUCT_SIZE_CHANGED, "SizeOnly")],
        {("SizeOnly", "struct_size_changed")},
    ),
    (
        "field_offset_other_field",
        [
            _change(K.STRUCT_FIELD_OFFSET_CHANGED, "OffOther", "x"),
            _change(K.TYPE_FIELD_OFFSET_CHANGED, "OffOther", "y"),
        ],
        {
            ("OffOther", "struct_field_offset_changed"),
            ("OffOther", "type_field_offset_changed"),
        },
    ),
]

_ALL_CHANGES: list[Change] = [c for _, changes, _ in _SCENARIOS for c in changes]
_EXPECTED_KEPT: set[tuple[str, str]] = set().union(*(k for _, _, k in _SCENARIOS))


def _kept_pairs(changes: list[Change]) -> set[tuple[str, str]]:
    return {(c.symbol, c.kind.value) for c in deduplicate_ast_dwarf(changes)}


def test_scenarios_match_the_hand_written_expectation() -> None:
    assert _kept_pairs(list(_ALL_CHANGES)) == _EXPECTED_KEPT


@pytest.mark.parametrize("name", [name for name, _, _ in _SCENARIOS])
def test_each_scenario_alone_keeps_its_expected_findings(name: str) -> None:
    changes, expected = next((c, k) for n, c, k in _SCENARIOS if n == name)
    assert _kept_pairs(list(changes)) == expected


def test_every_permutation_gives_the_same_result() -> None:
    rng = random.Random(20261115)
    for _ in range(40):
        shuffled = list(_ALL_CHANGES)
        rng.shuffle(shuffled)
        assert _kept_pairs(shuffled) == _EXPECTED_KEPT


def test_reversed_and_rotated_inputs_agree() -> None:
    forward = _kept_pairs(list(_ALL_CHANGES))
    assert _kept_pairs(list(reversed(_ALL_CHANGES))) == forward
    assert _kept_pairs(_ALL_CHANGES[3:] + _ALL_CHANGES[:3]) == forward
