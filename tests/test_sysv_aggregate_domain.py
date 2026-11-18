"""Domain edges of the SysV struct-return classifier (``model/sysv_aggregate``).

The E14b fragment (``test_call_interface_struct_returns.py``) pins the
classification of in-domain structs against execution. This file pins the
other direction: every shape the classifier cannot vouch for gives ``None``
(the call axis then answers ``UNKNOWN``), never a guessed register shape, and
the snapshot-level entry point resolves tags and typedef chains the way
castxml and clang spell them.
"""

from __future__ import annotations

import pytest

from abicheck.model.abi_fundamental import AbiFundamental, FundamentalEncoding
from abicheck.model.call_interface import ArgClass, Member, RetShape
from abicheck.model.declarations import Function
from abicheck.model.entities import RecordType, TypeField
from abicheck.model.fact import Fact
from abicheck.model.snapshot import AbiSnapshot
from abicheck.model.sysv_aggregate import (
    StructReturn,
    classify_record,
    struct_return_of,
)

_S = FundamentalEncoding.SIGNED
_U = FundamentalEncoding.UNSIGNED
_F = FundamentalEncoding.FLOAT


def _fact(enc: FundamentalEncoding, size: int, lanes: int = 0) -> AbiFundamental:
    return AbiFundamental(enc, size, size, lanes)


_INT = _fact(_S, 4)
_LONG = _fact(_S, 8)
_DOUBLE = _fact(_F, 8)


def _f(
    ty: str,
    offset: int | None,
    fact: AbiFundamental | None = None,
    bits: int | None = None,
) -> TypeField:
    return TypeField(
        "m",
        ty,
        offset_bits=offset,
        is_bitfield=bits is not None,
        bitfield_bits=bits,
        type_abi_fact=None if fact is None else Fact.present(fact),
    )


def _rec(fields: list[TypeField], size: int, align: int, **kw: object) -> RecordType:
    name = str(kw.pop("name", "r"))
    kind = str(kw.pop("kind", "struct"))
    return RecordType(
        name,
        kind,
        size_bits=size,
        alignment_bits=align,
        fields=fields,
        **kw,
    )


def _classify(rec: RecordType, *others: RecordType) -> StructReturn | None:
    by_name = {r.name: r for r in (rec, *others)}
    return classify_record(
        rec, lambda s: by_name.get(s.removeprefix("struct ")), c_language=True
    )


@pytest.mark.parametrize(
    ("rec", "why"),
    [
        (_rec([_f("v4si", 0, _fact(_S, 16, lanes=4))], 128, 128), "vector"),
        (_rec([_f("__int128", 0, _fact(_S, 16))], 128, 128), "16-byte integer"),
        (_rec([_f("long", None, _LONG)], 64, 64), "unknown offset"),
        (_rec([], 64, 64), "no fields"),
        (_rec([_f("long", 0, _LONG)], 64, 64, is_opaque=True), "opaque"),
        (_rec([_f("long", 0, _LONG)], 64, 64, kind="union"), "union kind"),
        (_rec([_f("long", 0, _LONG)], None, 64), "unknown size"),
        (
            _rec([_f("long", 0, _LONG)], 64, 64, bases_fact=Fact.present(["B"])),
            "base class",
        ),
        (
            _rec([_f("long", 0, _LONG)], 64, 64, vtable_fact=Fact.present(["f"])),
            "vtable",
        ),
        (_rec([_f("int", 16, _INT)], 64, 32), "misaligned member"),
        (_rec([_f("double", 0, _DOUBLE, bits=3)], 64, 64), "floating bitfield"),
        (_rec([_f("long", 60, _LONG, bits=8)], 128, 64), "bitfield straddles"),
        (_rec([_f("long *[0]", 0)], 64, 64), "zero-length array"),
        (_rec([_f("int", 0, _INT), _f("int", 0, _INT)], 64, 32), "overlap"),
        (_rec([_f("long", 64, _LONG)], 64, 64), "member past the end"),
        (_rec([_f("long", 0, _LONG)], 128, 64), "all-padding eightbyte"),
        (_rec([_f("long", 0, _LONG), _f("double", 64, _DOUBLE)], 128, 64), "mixed"),
    ],
)
def test_out_of_domain_records_are_not_classified(rec: RecordType, why: str) -> None:
    assert _classify(rec) is None, why


def test_a_record_nested_too_deep_is_not_classified() -> None:
    chain = [_rec([_f("long", 0, _LONG)], 64, 64, name="n0")]
    for i in range(1, 11):
        chain.append(_rec([_f(f"struct n{i - 1}", 0)], 64, 64, name=f"n{i}"))
    assert _classify(chain[-1], *chain[:-1]) is None
    assert _classify(chain[3], *chain[:3]) == StructReturn(
        RetShape.INT1, 64, (Member(0, 64, ArgClass.INTEGER),)
    )


def test_an_array_of_records_repeats_the_element_layout() -> None:
    pair = _rec([_f("int", 0, _INT), _f("int", 32, _INT)], 64, 32, name="p")
    outer = _rec([_f("struct p[2]", 0)], 128, 32)
    got = _classify(outer, pair)
    assert got is not None and got.shape is RetShape.INT2
    assert [m.offset for m in got.members or ()] == [0, 32, 64, 96]


def test_an_unsigned_and_a_char_member_are_integer_class() -> None:
    rec = _rec(
        [_f("unsigned int", 0, _fact(_U, 4)), _f("char", 32, _fact(_S, 1))], 64, 32
    )
    got = _classify(rec)
    assert got == StructReturn(
        RetShape.INT1,
        64,
        (Member(0, 32, ArgClass.INTEGER), Member(32, 8, ArgClass.INTEGER)),
    )


def _snapshot(*types: RecordType, typedefs: dict[str, str], lang: str) -> AbiSnapshot:
    snap = AbiSnapshot(
        library="libs.so", version="1", types=list(types), typedefs=typedefs
    )
    snap.language_profile = lang
    return snap


_POINT = _rec([_f("long", 0, _LONG), _f("long", 64, _LONG)], 128, 64, name="point")


@pytest.mark.parametrize(
    ("spelling", "typedefs"),
    [
        ("struct point", {}),
        ("point_t", {"point_t": "struct point"}),
        ("alias_t", {"alias_t": "point_t", "point_t": "point"}),
        ("point", {"point": ""}),  # castxml: typedef of an anonymous record
        ("point", {"point": "struct point"}),  # clang's self-named typedef
    ],
)
def test_struct_return_of_resolves_tags_and_typedef_chains(
    spelling: str, typedefs: dict[str, str]
) -> None:
    snap = _snapshot(_POINT, typedefs=typedefs, lang="c")
    got = struct_return_of(Function("f", "f", spelling), snap)
    assert got is not None and got.shape is RetShape.INT2 and got.size == 128


@pytest.mark.parametrize(
    ("fn", "lang"),
    [
        (Function("f", "f", "struct point *", return_pointer_depth=1), "c"),
        (Function("f", "f", "struct point*"), "c"),
        (Function("f", "f", "struct missing"), "c"),
        (Function("f", "_Z1fv", "point"), "cpp"),  # C++, triviality not proved
    ],
)
def test_struct_return_of_declines_non_record_and_unproved_returns(
    fn: Function, lang: str
) -> None:
    assert struct_return_of(fn, _snapshot(_POINT, typedefs={}, lang=lang)) is None
