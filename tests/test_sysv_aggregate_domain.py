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
        (
            _rec([_f("long", 0, _LONG)], 64, 64, bases_fact=Fact.partial(["B"])),
            "partially known base class",
        ),
        (
            _rec(
                [_f("long", 0, _LONG)], 64, 64, virtual_bases_fact=Fact.partial(["V"])
            ),
            "partially known virtual base",
        ),
        (
            _rec([_f("long", 0, _LONG)], 64, 64, vtable_fact=Fact.partial(["f"])),
            "partially known vtable",
        ),
        (_rec([_f("_Bool", 0, _fact(FundamentalEncoding.BOOLEAN, 1))], 8, 8), "bool"),
        (
            _rec(
                [
                    _f("int", 0, _INT),
                    _f("_Bool", 32, _fact(FundamentalEncoding.BOOLEAN, 1)),
                ],
                64,
                32,
            ),
            "bool after int",
        ),
        (
            _rec([_f("_Bool", 0, _fact(FundamentalEncoding.BOOLEAN, 1), bits=1)], 8, 8),
            "bool bitfield",
        ),
        (_rec([_f("void *[10000000]", 0)], 640000000, 64), "huge pointer array"),
        (_rec([_f("void *[257]", 0)], 257 * 64, 64), "over the member budget"),
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


def test_an_array_at_the_member_budget_is_classified() -> None:
    got = _classify(_rec([_f("void *[256]", 0)], 256 * 64, 64))
    assert got is not None and got.shape is RetShape.MEMORY
    assert len(got.members or ()) == 256


def test_many_scalar_fields_past_the_budget_are_not_classified() -> None:
    fields = [_f("char", i * 8, _fact(_S, 1)) for i in range(257)]
    assert _classify(_rec(fields, 257 * 8, 8)) is None
    assert _classify(_rec(fields[:256], 256 * 8, 8)) is not None


def _named_bit(name: str, offset: int, bits: int, fact: AbiFundamental) -> TypeField:
    f = _f("unsigned int", offset, fact, bits=bits)
    f.name = name
    return f


@pytest.mark.parametrize(
    ("widths", "fact", "size", "align"),
    [
        ((3, 5), _fact(_U, 4), 32, 32),  # struct { unsigned a:3, b:5; }
        ((1,), _fact(_U, 4), 32, 32),
        ((7, 9, 16), _fact(_U, 4), 32, 32),
        ((3, 5), _fact(_U, 8), 64, 64),  # unsigned long bitfields
        ((4, 4), _fact(_U, 2), 16, 16),  # unsigned short bitfields
    ],
)
def test_named_bitfields_align_the_record_to_their_type(
    widths: tuple[int, ...], fact: AbiFundamental, size: int, align: int
) -> None:
    fields, at = [], 0
    for i, w in enumerate(widths):
        fields.append(_named_bit(f"b{i}", at, w, fact))
        at += w
    got = _classify(_rec(fields, size, align))
    assert got is not None and got.shape is RetShape.INT1
    assert [(m.offset, m.size) for m in got.members or ()] == [
        (sum(widths[:i]), w) for i, w in enumerate(widths)
    ]


@pytest.mark.parametrize(
    ("align", "classified"),
    [(8, True), (32, False)],
)
def test_an_unnamed_bitfield_does_not_align_the_record(
    align: int, classified: bool
) -> None:
    """``struct { char c; unsigned :1; }`` is byte-aligned: the unnamed
    bitfield adds no alignment, so a record claiming 32-bit alignment from it
    alone is outside the model (it would need padding the members do not
    explain)."""
    c = _named_bit("c", 0, 8, _fact(_S, 1))
    c.is_bitfield, c.bitfield_bits = False, None
    pad = _named_bit("", 8, 1, _fact(_U, 4))
    got = _classify(_rec([c, pad], max(16, align), align))
    assert (got is not None) is classified
    if got is not None:
        assert got.shape is RetShape.INT1
        assert [(m.offset, m.size) for m in got.members or ()] == [(0, 8), (8, 1)]


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
        # A tag names the tag even when an ordinary typedef shares its name.
        ("struct point", {"point": "struct other"}),
        ("struct point", {"point": "int"}),
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
        (Function("f", "f", "struct other"), "c"),  # tag absent, no typedef hop
    ],
)
def test_struct_return_of_declines_non_record_and_unproved_returns(
    fn: Function, lang: str
) -> None:
    assert struct_return_of(fn, _snapshot(_POINT, typedefs={}, lang=lang)) is None


@pytest.mark.parametrize(
    ("producer", "classified"),
    [
        ("castxml", True),
        ("clang", False),
        ("gcc", False),
        ("hybrid", False),
        (None, False),
    ],
)
def test_a_cxx_record_needs_the_castxml_trait(
    producer: str | None, classified: bool
) -> None:
    """Only castxml's trait excludes all-deleted copy/move constructors."""
    rec = _rec(
        [_f("long", 0, _LONG), _f("long", 64, _LONG)],
        128,
        64,
        name="point",
        is_trivially_copyable=True,
    )
    snap = _snapshot(rec, typedefs={}, lang="cpp")
    snap.ast_producer = producer
    got = struct_return_of(Function("f", "_Z1fv", "point"), snap)
    assert (got is not None and got.shape is RetShape.INT2) is classified
