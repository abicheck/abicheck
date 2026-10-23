"""Unit tests for ``compare/embedded_growth_shift`` and the
``shifted_by_type_pattern`` selector (issue #1759).

Oracle: a shift is explained iff every earlier member is unchanged and the
shift equals the summed growth of earlier by-value records (computed here by
hand from the fixture sizes, not by the module's own helpers).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from abicheck.compare.embedded_growth_shift import (
    OFFSET_SHIFT_CAUSE_KEY,
    _explained_shift,
    _resolve_by_value_record,
    stamp_embedded_growth_shifts,
)
from abicheck.model import RecordType, TypeField
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.policy.selectors import SelectorSet

_SUB_OLD = RecordType(name="_sub_t", kind="struct", size_bits=128, fields=[])
_SUB_NEW = RecordType(name="_sub_t", kind="struct", size_bits=192, fields=[])
_TYPEDEFS = {"sub_t": "_sub_t", "alias_t": "sub_t"}


def _agg(
    sub_type: str, shifted_offset: int, *, extra: TypeField | None = None
) -> RecordType:
    fields = [TypeField(name="s", type=sub_type, offset_bits=0)]
    if extra is not None:
        fields.append(extra)
    fields.append(TypeField(name="tail", type="void *", offset_bits=shifted_offset))
    return RecordType(
        name="_agg_t", kind="struct", size_bits=shifted_offset + 64, fields=fields
    )


def _sides() -> tuple[tuple, tuple]:
    return ({"_sub_t": _SUB_OLD}, _TYPEDEFS), ({"_sub_t": _SUB_NEW}, _TYPEDEFS)


@pytest.mark.parametrize(
    ("spelling", "found"),
    [
        ("_sub_t", True),
        ("struct _sub_t", True),
        ("const sub_t", True),
        ("alias_t", True),  # two typedef hops
        ("sub_t *", False),
        ("sub_t[2]", False),
        ("sub_t &", False),
        ("unknown_t", False),
    ],
)
def test_resolve_by_value_record(spelling: str, found: bool) -> None:
    rec = _resolve_by_value_record(spelling, {"_sub_t": _SUB_OLD}, _TYPEDEFS)
    assert (rec is _SUB_OLD) is found


def test_typedef_cycle_terminates() -> None:
    assert _resolve_by_value_record("a", {}, {"a": "b", "b": "a"}) is None


@pytest.mark.parametrize(
    ("new_offset", "explained"),
    [(128, False), (192, True), (256, False)],  # growth is 64 bits
)
def test_shift_explained_only_when_equal_to_growth(
    new_offset: int, explained: bool
) -> None:
    old_side, new_side = _sides()
    grown = _explained_shift(
        _agg("sub_t", 128), _agg("sub_t", new_offset), "tail", old_side, new_side
    )
    assert (grown is not None) is explained
    if explained:
        assert grown == [{"member": "s", "type": "_sub_t", "bits": 64}]


def test_unexplained_shapes() -> None:
    old_side, new_side = _sides()
    old = _agg("sub_t", 128)
    # Shifted member missing on either side, or retyped.
    assert _explained_shift(old, old, "nope", old_side, new_side) is None
    short = RecordType(
        name="_agg_t", kind="struct", size_bits=64, fields=old.fields[:1]
    )
    assert _explained_shift(old, short, "tail", old_side, new_side) is None
    retyped = _agg("sub_t", 192)
    retyped.fields[1] = TypeField(name="tail", type="int", offset_bits=192)
    assert _explained_shift(old, retyped, "tail", old_side, new_side) is None
    # Unknown offset.
    no_off = _agg("sub_t", 192)
    no_off.fields[1] = TypeField(name="tail", type="void *", offset_bits=None)
    assert _explained_shift(old, no_off, "tail", old_side, new_side) is None
    # An earlier member retyped.
    assert (
        _explained_shift(old, _agg("alias_t", 192), "tail", old_side, new_side) is None
    )
    # A bitfield before the shifted member.
    bf = TypeField(
        name="b", type="int", offset_bits=128, is_bitfield=True, bitfield_bits=3
    )
    assert (
        _explained_shift(
            _agg("sub_t", 192, extra=bf),
            _agg("sub_t", 256, extra=bf),
            "tail",
            old_side,
            new_side,
        )
        is None
    )
    # Unknown size, and a shrink.
    unsized = (
        {"_sub_t": RecordType(name="_sub_t", kind="struct", size_bits=None, fields=[])},
        _TYPEDEFS,
    )
    assert _explained_shift(old, _agg("sub_t", 192), "tail", old_side, unsized) is None
    assert _explained_shift(old, _agg("sub_t", 64), "tail", new_side, old_side) is None


def _shift_change(symbol: str = "_agg_t", field: str | None = "tail") -> Change:
    return Change(
        kind=ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
        symbol=symbol,
        description="shift",
        old_value="128",
        new_value="192",
        field_name=field,
    )


def _decls(sub: RecordType, agg: RecordType) -> SimpleNamespace:
    return SimpleNamespace(types=[sub, agg], typedefs=_TYPEDEFS)


def test_stamp_writes_evidence_and_keeps_other_findings() -> None:
    shift, other = _shift_change(), _shift_change(symbol="_missing_t")
    unnamed = _shift_change(field=None)
    size = Change(
        kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="_sub_t", description="size"
    )
    out = stamp_embedded_growth_shifts(
        [shift, other, unnamed, size],
        _decls(_SUB_OLD, _agg("sub_t", 128)),  # type: ignore[arg-type]
        _decls(_SUB_NEW, _agg("sub_t", 192)),  # type: ignore[arg-type]
    )
    assert out == [shift, other, unnamed, size]
    assert shift.review_evidence is not None
    assert (
        shift.review_evidence[OFFSET_SHIFT_CAUSE_KEY]["cause"]
        == "embedded_member_growth"
    )
    assert other.review_evidence is None and unnamed.review_evidence is None


def test_stamp_without_shifts_is_identity() -> None:
    size = Change(
        kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="_sub_t", description="size"
    )
    assert stamp_embedded_growth_shifts([size], None, None) == [size]  # type: ignore[arg-type]


def _with_evidence(evidence: object) -> SimpleNamespace:
    return SimpleNamespace(
        kind=ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
        symbol="_agg_t",
        qualified_name=None,
        caused_by_type=None,
        source_location=None,
        symbol_binding=None,
        review_evidence=evidence,
    )


_GROWN = {"cause": "embedded_member_growth", "grown_members": [{"type": "_sub_t"}]}


@pytest.mark.parametrize(
    ("evidence", "matches"),
    [
        (None, False),
        ({}, False),
        ({OFFSET_SHIFT_CAUSE_KEY: "bogus"}, False),
        (
            {
                OFFSET_SHIFT_CAUSE_KEY: {
                    "cause": "other",
                    "grown_members": [{"type": "_sub_t"}],
                }
            },
            False,
        ),
        (
            {
                OFFSET_SHIFT_CAUSE_KEY: {
                    "cause": "embedded_member_growth",
                    "grown_members": [],
                }
            },
            False,
        ),
        (
            {
                OFFSET_SHIFT_CAUSE_KEY: {
                    **_GROWN,
                    "grown_members": [{"type": "_sub_t"}, {"type": "x"}],
                }
            },
            False,
        ),
        ({OFFSET_SHIFT_CAUSE_KEY: _GROWN}, True),
    ],
)
def test_shifted_by_type_pattern_selector(evidence: object, matches: bool) -> None:
    sel = SelectorSet(type_pattern="_agg_t", shifted_by_type_pattern="_sub_t")
    assert sel.matches_selectors(_with_evidence(evidence)) is matches  # type: ignore[arg-type]
