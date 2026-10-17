"""An appended struct field is compatible only when the struct is known to be
non-polymorphic.

`TYPE_FIELD_ADDED_COMPATIBLE` also lets the struct's size change be filtered
out, so reading "the producer could not see vtables" (PDB layout's
UNSUPPORTED, a FAILED extraction, empty known-incomplete DWARF PARTIAL) as
"no vtable" turned a field added to a polymorphic class into a passing
compare. NOT_COLLECTED keeps meaning "no virtuals" (typed-API omission,
flat-C debug layout), so those comparisons must not change.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.compare.field_addition import new_field_change_kind
from abicheck.model import AbiSnapshot, Function, RecordType, TypeField, Visibility
from abicheck.model.fact import Fact

ADDED = ChangeKind.TYPE_FIELD_ADDED
COMPATIBLE = ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE

#: (label, list-fact factory, "producer saw the family", resolved entries)
_LIST_FACTS = {
    "present-empty": (lambda: Fact.present([]), True, False),
    "present-entries": (lambda: Fact.present(["_ZN1S1fEv"]), True, True),
    "not-collected": (lambda: Fact.not_collected(), True, False),
    "not-applicable": (lambda: Fact.not_applicable(), True, False),
    "unsupported": (lambda: Fact.unsupported("pdb", producer="pdb"), False, False),
    "failed": (lambda: Fact.failed("boom"), False, False),
    "partial-empty": (lambda: Fact.partial([], "tu gap"), False, False),
    "partial-entries": (lambda: Fact.partial(["_ZN1S1fEv"], "tu gap"), True, True),
}
_TRAITS = {
    "unknown": (lambda: Fact.not_collected(), False),
    "confirmed-true": (lambda: Fact.present(True), True),
    "confirmed-false": (lambda: Fact.present(False), False),
}


def _expected(kind: str, vt: str, vb: str, std: str, triv: str) -> ChangeKind:
    """Independent oracle written from the contract, not the implementation."""
    _, vt_seen, vt_has = _LIST_FACTS[vt]
    _, vb_seen, vb_has = _LIST_FACTS[vb]
    if kind != "struct" or vt_has or vb_has:
        return ADDED
    proven = _TRAITS[std][1] or _TRAITS[triv][1]
    if proven or (vt_seen and vb_seen):
        return COMPATIBLE
    return ADDED


@pytest.mark.parametrize(
    ("kind", "vt", "vb", "std", "triv"),
    list(
        itertools.product(
            ["struct", "class", "union"], _LIST_FACTS, _LIST_FACTS, _TRAITS, _TRAITS
        )
    ),
)
def test_kind_over_every_evidence_state(kind, vt, vb, std, triv) -> None:
    rec = RecordType(
        name="S",
        kind=kind,
        vtable_fact=_LIST_FACTS[vt][0](),
        virtual_bases_fact=_LIST_FACTS[vb][0](),
        is_standard_layout_fact=_TRAITS[std][0](),
        is_trivially_copyable_fact=_TRAITS[triv][0](),
    )
    assert new_field_change_kind(rec) is _expected(kind, vt, vb, std, triv)


def _snap(version: str, rec: RecordType) -> AbiSnapshot:
    """``S`` reached only through a pointer factory: the opaque-handle shape
    (case62) where a compatible append also drops the size change."""
    factory = Function(
        name="s_create",
        mangled="s_create",
        return_type="S *",
        params=[],
        visibility=Visibility.PUBLIC,
    )
    return AbiSnapshot(
        library="libs.so.1", version=version, functions=[factory], types=[rec]
    )


def _struct(fields: list[TypeField], size: int, **facts) -> RecordType:
    return RecordType(name="S", kind="struct", fields=fields, size_bits=size, **facts)


_A = TypeField(name="a", type="int", offset_bits=0)
_B = TypeField(name="b", type="int", offset_bits=32)


@pytest.mark.parametrize(
    ("facts", "verdict", "kind"),
    [
        # Producer could not see polymorphism: the append is a break.
        (
            {"vtable_fact": Fact.unsupported("pdb", producer="pdb")},
            Verdict.BREAKING,
            ADDED,
        ),
        ({"vtable_fact": Fact.failed("boom")}, Verdict.BREAKING, ADDED),
        ({"vtable_fact": Fact.partial([], "tu gap")}, Verdict.BREAKING, ADDED),
        # Negative controls: unchanged behaviour.
        ({"vtable_fact": Fact.present([])}, Verdict.COMPATIBLE, COMPATIBLE),
        ({}, Verdict.COMPATIBLE, COMPATIBLE),  # typed-API omission: NOT_COLLECTED
        (
            {
                "vtable_fact": Fact.unsupported("pdb", producer="pdb"),
                "is_standard_layout_fact": Fact.present(True),
            },
            Verdict.COMPATIBLE,
            COMPATIBLE,
        ),
    ],
)
def test_compare_verdict_for_an_appended_field(facts, verdict, kind) -> None:
    old = _snap("1", _struct([_A], 32, **facts))
    new = _snap("2", _struct([_A, _B], 64, **facts))
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    assert kind in kinds
    assert ({ADDED, COMPATIBLE} - {kind}).isdisjoint(kinds)
    assert result.verdict == verdict
