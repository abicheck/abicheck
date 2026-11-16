"""Subsumed AST/DWARF duplicates: order-invariant, and only where declared.

``deduplicate_ast_dwarf`` drops a DWARF-lane finding when an AST-lane finding
on the same subject makes it redundant. The subsumptions are declared on the
registry (``subsumed_by``, ADR-093 D4), and the matching engine is unchanged.

The expectations below are written out by hand, one per scenario, so they are
independent of the implementation's helpers. Each scenario names a subject
(``symbol``) that no other scenario shares, so the expected result of the
whole input is the union of the per-scenario results. Every permutation of the
input must give that same union: detector order must not be a semantic input.

The oracle is a multiset over ``(symbol, kind, field_name)``, not a set of
``(symbol, kind)`` pairs: a set would hide a duplicate that survives twice, and
would hide a same-kind finding kept on the wrong field of one record.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from abicheck.compare.finding_dedup import _sorted_kinds, deduplicate_ast_dwarf
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind as K

Kept = tuple[str, str, str | None]


def _change(kind: K, symbol: str, field: str | None = None) -> Change:
    # Transitions are left unset (None) so the DWARF finding agrees with any
    # AST partner, which is the case the declared subsumption describes.
    return Change(kind=kind, symbol=symbol, description="d", field_name=field)


# (scenario, the changes it contributes, the multiset of findings it keeps)
_SCENARIOS: list[tuple[str, list[Change], Counter[Kept]]] = [
    (
        "size",
        [_change(K.STRUCT_SIZE_CHANGED, "Size"), _change(K.TYPE_SIZE_CHANGED, "Size")],
        Counter({("Size", "type_size_changed", None): 1}),
    ),
    (
        "alignment",
        [
            _change(K.STRUCT_ALIGNMENT_CHANGED, "Align"),
            _change(K.TYPE_ALIGNMENT_CHANGED, "Align"),
        ],
        Counter({("Align", "type_alignment_changed", None): 1}),
    ),
    (
        "field_offset",
        [
            _change(K.STRUCT_FIELD_OFFSET_CHANGED, "Off", "x"),
            _change(K.TYPE_FIELD_OFFSET_CHANGED, "Off", "x"),
        ],
        Counter({("Off", "type_field_offset_changed", "x"): 1}),
    ),
    (
        "field_removed",
        [
            _change(K.STRUCT_FIELD_REMOVED, "Rem", "x"),
            _change(K.TYPE_FIELD_REMOVED, "Rem", "x"),
        ],
        Counter({("Rem", "type_field_removed", "x"): 1}),
    ),
    (
        "field_type_struct_partner",
        [
            _change(K.STRUCT_FIELD_TYPE_CHANGED, "FT", "x"),
            _change(K.TYPE_FIELD_TYPE_CHANGED, "FT", "x"),
        ],
        Counter({("FT", "type_field_type_changed", "x"): 1}),
    ),
    (
        "field_type_union_partner",
        [
            _change(K.STRUCT_FIELD_TYPE_CHANGED, "FTU", "x"),
            _change(K.UNION_FIELD_TYPE_CHANGED, "FTU", "x"),
        ],
        Counter({("FTU", "union_field_type_changed", "x"): 1}),
    ),
    # Negatives: a DWARF finding with no declared partner on its subject stays,
    # and so does a partner that names a different field.
    (
        "size_without_partner",
        [_change(K.STRUCT_SIZE_CHANGED, "SizeOnly")],
        Counter({("SizeOnly", "struct_size_changed", None): 1}),
    ),
    (
        "field_offset_other_field",
        [
            _change(K.STRUCT_FIELD_OFFSET_CHANGED, "OffOther", "x"),
            _change(K.TYPE_FIELD_OFFSET_CHANGED, "OffOther", "y"),
        ],
        Counter(
            {
                ("OffOther", "struct_field_offset_changed", "x"): 1,
                ("OffOther", "type_field_offset_changed", "y"): 1,
            }
        ),
    ),
]

_ALL_CHANGES: list[Change] = [c for _, changes, _ in _SCENARIOS for c in changes]
_EXPECTED_KEPT: Counter[Kept] = sum((k for _, _, k in _SCENARIOS), Counter())


def _kept(changes: list[Change]) -> Counter[Kept]:
    return Counter(
        (c.symbol, c.kind.value, c.field_name) for c in deduplicate_ast_dwarf(changes)
    )


def test_scenarios_match_the_hand_written_expectation() -> None:
    assert _kept(list(_ALL_CHANGES)) == _EXPECTED_KEPT


@pytest.mark.parametrize("name", [name for name, _, _ in _SCENARIOS])
def test_each_scenario_alone_keeps_its_expected_findings(name: str) -> None:
    changes, expected = next((c, k) for n, c, k in _SCENARIOS if n == name)
    assert _kept(list(changes)) == expected


def test_every_permutation_gives_the_same_result() -> None:
    rng = random.Random(20261115)
    for _ in range(40):
        shuffled = list(_ALL_CHANGES)
        rng.shuffle(shuffled)
        assert _kept(shuffled) == _EXPECTED_KEPT


def test_reversed_and_rotated_inputs_agree() -> None:
    forward = _kept(list(_ALL_CHANGES))
    assert forward == _EXPECTED_KEPT
    assert _kept(list(reversed(_ALL_CHANGES))) == forward
    assert _kept(_ALL_CHANGES[3:] + _ALL_CHANGES[:3]) == forward


def test_sorted_kinds_orders_by_value_whatever_the_input_order() -> None:
    # Value order: "type_size_changed" < "typedef_removed" ('_' sorts before 'd').
    # Name order is the reverse here, so a key on the name, a reversed sort, or
    # no sort at all each give a different list and fail this check.
    expected = [K.TYPE_SIZE_CHANGED, K.TYPEDEF_REMOVED]
    assert _sorted_kinds([K.TYPEDEF_REMOVED, K.TYPE_SIZE_CHANGED]) == expected
    assert _sorted_kinds([K.TYPE_SIZE_CHANGED, K.TYPEDEF_REMOVED]) == expected
