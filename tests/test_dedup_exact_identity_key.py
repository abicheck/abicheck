# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""`finding_dedup._dedup_exact` keeps distinct same-kind findings apart and
collapses true duplicates, for every `ChangeKind`.

Bug class: a hand-picked dedup field tuple that omits an identity-bearing
fact merges two real findings of one kind (lost break). AGENTS.md "Record
before disposing" forbids that.

The oracle is written from first principles, not from
``compare.finding_identity``: a hand-written per-field table, where every
field listed is a fact that distinguishes two findings of one kind.

- ``symbol``: the exported name is the identity of a symbol-level finding, so
  two different symbols are two findings.
- ``old_value`` / ``new_value``: the recorded change itself.
- ``disambiguator``: an ODR/multi-TU occurrence discriminator.
- ``qualified_name`` when ``symbol`` is empty: the only identity a
  header-only finding (inline, python API, header constant) has.

A qualified-name difference on a *non-empty* symbol is deliberately not
asserted: for a symbol-identified kind the qualified name derives from the
symbol, so it is not an independent fact there.

Each test changes exactly one field of an otherwise identical pair, so the
expected outcome is read off that one field's row.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.compare.finding_dedup import _dedup_exact
from abicheck.model.change import Change
from abicheck.model.identity import Namespace, entity_id_for_typedef

#: Hand-written: each field's pair of values that make two findings distinct.
_DISTINGUISHING_PAIRS: dict[str, tuple[dict[str, object], dict[str, object]]] = {
    "symbol": ({"symbol": "Foo"}, {"symbol": "Bar"}),
    "old_value": ({"old_value": "int"}, {"old_value": "long"}),
    "new_value": ({"new_value": "long"}, {"new_value": "short"}),
    "disambiguator": ({"disambiguator": "tu-a"}, {"disambiguator": "tu-b"}),
    "qualified_name_header_only": (
        {"symbol": "", "qualified_name": "ns1::Foo"},
        {"symbol": "", "qualified_name": "ns2::Foo"},
    ),
}

#: Kinds whose occurrences are told apart by `entity_id` (occurrence-aware),
#: as documented in `finding_dedup._dedup_exact`. An AST/DWARF twin pair is
#: therefore not the same occurrence for these and is not asserted to collapse.
_OCCURRENCE_AWARE_KINDS = frozenset(
    {
        ChangeKind.TYPEDEF_REMOVED,
        ChangeKind.TYPEDEF_BASE_CHANGED,
        ChangeKind.TYPEDEF_VERSION_SENTINEL,
        ChangeKind.CONSTANT_ADDED,
        ChangeKind.CONSTANT_CHANGED,
        ChangeKind.CONSTANT_REMOVED,
    }
)


#: Batch-shaped kinds (`change_identity._ALWAYS_BATCH_SHAPED_KIND_SLUGS`): one
#: finding per comparison, sampling an arbitrary export. The sample and the
#: qualified name are not per-entity facts, so two of them can never be two
#: distinct findings of this kind. Hand-listed here as the oracle's statement.
_BATCH_SHAPED_KINDS = frozenset(
    {
        ChangeKind.VISIBILITY_LEAK,
        ChangeKind.ALLOCATOR_REPLACEMENT_ADDED,
        ChangeKind.ALLOCATOR_REPLACEMENT_REMOVED,
    }
)


def _expected_distinct(kind: ChangeKind, field: str) -> bool:
    if field == "qualified_name_header_only":
        return kind not in _BATCH_SHAPED_KINDS
    return True


def _finding(kind: ChangeKind, **overrides: object) -> Change:
    fields: dict[str, object] = {
        "symbol": "Foo",
        "description": f"{kind.value} on Foo",
        "old_value": "old",
        "new_value": "new",
    }
    fields.update(overrides)
    return Change(kind=kind, **fields)  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", list(ChangeKind), ids=lambda k: k.value)
def test_identical_copies_collapse_to_one(kind: ChangeKind) -> None:
    assert len(_dedup_exact([_finding(kind), _finding(kind)])) == 1


@pytest.mark.parametrize("kind", list(ChangeKind), ids=lambda k: k.value)
@pytest.mark.parametrize("field", list(_DISTINGUISHING_PAIRS))
def test_distinguishing_field_keeps_both_findings(kind: ChangeKind, field: str) -> None:
    a, b = _DISTINGUISHING_PAIRS[field]
    group = [_finding(kind, **a), _finding(kind, **b)]
    assert len(_dedup_exact(group)) == (2 if _expected_distinct(kind, field) else 1)


@pytest.mark.parametrize(
    "kind",
    [k for k in ChangeKind if k not in _OCCURRENCE_AWARE_KINDS],
    ids=lambda k: k.value,
)
def test_ast_dwarf_twin_without_entity_id_still_collapses(kind: ChangeKind) -> None:
    """An AST finding carrying `entity_id` and its DWARF twin without one, the
    same occurrence otherwise, are one finding (cross-tier duplicate)."""
    eid = entity_id_for_typedef((Namespace("a"),), "Foo")
    group = [_finding(kind, entity_id=eid), _finding(kind)]
    assert len(_dedup_exact(group)) == 1


def test_header_only_findings_differing_only_in_qualified_name_both_survive() -> None:
    """The reported bug, concretely: the old key collapsed these."""
    group = [
        _finding(
            ChangeKind.INLINE_FUNCTION_REMOVED, symbol="", qualified_name="ns1::Foo"
        ),
        _finding(
            ChangeKind.INLINE_FUNCTION_REMOVED, symbol="", qualified_name="ns2::Foo"
        ),
    ]
    assert len(_dedup_exact(group)) == 2


def _symbol_bearing_primary_ids() -> dict[str, str]:
    from abicheck.compare.finding_identity import resolve_change_identity

    out: dict[str, str] = {}
    for kind in ChangeKind:
        for label, extra in (
            ("plain", {}),
            ("qn", {"qualified_name": "ns::Foo"}),
            ("disamb", {"disambiguator": "tu-a"}),
        ):
            change = _finding(kind, **extra)
            out[f"{kind.value}|{label}"] = resolve_change_identity(change).primary_id
    return out


def test_symbol_bearing_primary_ids_are_byte_identical_to_base() -> None:
    """`finding_identity` is untouched by this fix, and the dedup key only
    *reads* its `primary_id`. The fixture was generated from base
    (`a2b6a1f9e`) over every ChangeKind; every symbol-bearing finding must still
    resolve to the identical id, so cross-tier reconciliation keys are
    unchanged."""
    import json
    from pathlib import Path

    fixture = (
        Path(__file__).parent
        / "identity_fixtures"
        / "dedup_exact_primary_ids_base.json"
    )
    expected = json.loads(fixture.read_text(encoding="utf-8"))
    assert _symbol_bearing_primary_ids() == expected
