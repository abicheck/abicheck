"""Edit operators for the record- and base-layout classes of ``_edit_corpus_edit_model``.

One operator per model class that had none. Each edits one probe role of the
``cxx_more`` seed (``lay.*`` for record layout, ``layb.*`` for base
subobjects). Ground truth is declared from the SysV/Itanium layout rules:
members are char/short/int/unsigned only, so the offsets below hold at -m32
and LP64 alike, and the only config axis that moves them is
``-fpack-struct=N`` (``c.flags.pack``), which caps every member's alignment
at N. A rule states each packed case where the layout effect differs.

Effects named in the descriptions are offsets and sizes in bytes, unpacked.
"""

from __future__ import annotations

from typing import Any

from tests._edit_corpus_expect import Rule
from tests._edit_corpus_operators_platform import _X86_HOSTS, _x86_64
from tests._edit_corpus_opkit import (
    _ALIGN,
    _FIELD_TYPE,
    _OFFSET,
    _SIZE,
    Edit,
    EditOp,
    Roles,
    _set,
)
from tests._edit_corpus_seeds import XCXX_SEED

__all__ = ["LAYOUT_OPERATORS"]

_FIELD_REMOVED = frozenset({"STRUCT_FIELD_REMOVED", "TYPE_FIELD_REMOVED"})
_FIELD_ADDED = frozenset({"TYPE_FIELD_ADDED", "TYPE_FIELD_ADDED_COMPATIBLE"})
_BITFIELD = frozenset({"FIELD_BITFIELD_CHANGED"})
_BREAKING: dict[str, Any] = {"verdict_min": "BREAKING"}
_DWARF = {"min_evidence": "dwarf"}


def _replace(
    role: str, old: str, new: str, *, source: tuple[str, str] | None = None
) -> Edit:
    base_h, base_c = XCXX_SEED.roles[role]
    if old not in base_h:
        raise ValueError(f"{role}: {old!r} is not in its header text")
    header = base_h.replace(old, new, 1)
    src = None
    if source is not None:
        if source[0] not in base_c:
            raise ValueError(f"{role}: {source[0]!r} is not in its source text")
        src = base_c.replace(source[0], source[1], 1)

    def edit(r: Roles) -> dict[str, tuple[str, str]]:
        return _set(r, role, header, src)

    return edit


def _op(name: str, role: str, edit: Edit, why: str, **kw: Any) -> EditOp:
    kw.setdefault("touches", (role,))
    kw.setdefault("min_evidence", "dwarf")
    return EditOp(name, (role,), edit, why, **kw)


def _pack(n: int):
    return lambda c: c.flags.pack is not None and c.flags.pack <= n


_MEMBER_EDITS = (
    _op(
        "field_inserted_at_head",
        "lay.ihead",
        _replace(
            "lay.ihead",
            "struct LIH { int a; int b; };",
            "struct LIH { int z; int a; int b; };",
        ),
        "z goes before a: a moves 0 -> 4, b 4 -> 8, sizeof 8 -> 12",
        any_of=(_OFFSET, _SIZE),
        **_BREAKING,
    ),
    _op(
        "field_inserted_in_middle",
        "lay.imid",
        _replace(
            "lay.imid",
            "struct LIM { int a; int b; };",
            "struct LIM { int a; int z; int b; };",
        ),
        "z goes between a and b: b moves 4 -> 8, sizeof 8 -> 12",
        any_of=(_OFFSET, _SIZE),
        **_BREAKING,
    ),
    _op(
        "field_removed_at_head",
        "lay.rhead",
        _replace(
            "lay.rhead",
            "struct LRH { int z; int a; int b; };",
            "struct LRH { int a; int b; };",
        ),
        "removing the first member: a moves 4 -> 0, b 8 -> 4, sizeof 12 -> 8",
        any_of=(_FIELD_REMOVED, _OFFSET, _SIZE),
        **_BREAKING,
    ),
    _op(
        "field_removed_in_middle",
        "lay.rmid",
        _replace(
            "lay.rmid",
            "struct LRM { int a; int z; int b; };",
            "struct LRM { int a; int b; };",
        ),
        "removing a middle member: b moves 8 -> 4, sizeof 12 -> 8",
        any_of=(_FIELD_REMOVED, _OFFSET, _SIZE),
        **_BREAKING,
    ),
    _op(
        "field_removed_into_tail_padding",
        "lay.rtail",
        _replace(
            "lay.rtail",
            "struct LRT { int a; char b; char c; };",
            "struct LRT { int a; char b; };",
        ),
        "c sat in LRT's tail padding: sizeof stays 8 and no offset moves, but the "
        "removed member is gone for every reader",
        any_of=(_FIELD_REMOVED,),
        **_BREAKING,
        rules=(
            Rule(
                _pack(1),
                {"any_of": (_FIELD_REMOVED, _SIZE)},
                "-fpack-struct=1 leaves no tail padding: sizeof 6 -> 5",
            ),
        ),
    ),
)

_RETYPES = (
    _op(
        "field_retyped_same_layout",
        "lay.tsame",
        _replace(
            "lay.tsame",
            "struct LTS { int a; int b; };",
            "struct LTS { int a; unsigned b; };",
        ),
        "int -> unsigned: size, alignment and every offset stay, but the member's "
        "type, and so the value range old code assumes, changes",
        any_of=(_FIELD_TYPE,),
    ),
    _op(
        "field_retyped_smaller",
        "lay.tshrink",
        _replace(
            "lay.tshrink",
            "struct LTK { int a; char c; };",
            "struct LTK { short a; char c; };",
        ),
        "int -> short: c moves 4 -> 2 and sizeof 8 -> 4",
        any_of=(_FIELD_TYPE, _OFFSET, _SIZE),
        **_BREAKING,
    ),
    _op(
        "field_retyped_alignment_only",
        "lay.talign",
        _replace(
            "lay.talign",
            "struct LTA { char a; char b[4]; };",
            "struct LTA { char a; int b; };",
        ),
        "char[4] -> int keeps b's size at 4 but raises its alignment 1 -> 4: b "
        "moves 1 -> 4, sizeof 5 -> 8 and alignof(LTA) 1 -> 4",
        any_of=(_FIELD_TYPE, _OFFSET, _SIZE),
        **_BREAKING,
        rules=(
            Rule(
                _pack(1),
                {"any_of": (_FIELD_TYPE,), "verdict_min": "API_BREAK"},
                "-fpack-struct=1 caps int's alignment at 1: b stays at 1, sizeof at 5; "
                "only the member type changes",
            ),
            Rule(
                _pack(2),
                {"any_of": (_FIELD_TYPE, _OFFSET, _SIZE)},
                "-fpack-struct=2: b moves 1 -> 2, sizeof 5 -> 6",
            ),
        ),
    ),
    _op(
        "array_shrunk_shifting",
        "lay.ashrink",
        _replace(
            "lay.ashrink",
            "struct LAS { char a[8]; int b; };",
            "struct LAS { char a[4]; int b; };",
        ),
        "char[8] -> char[4]: b moves 8 -> 4 and sizeof 12 -> 8",
        any_of=(_FIELD_TYPE, _OFFSET, _SIZE),
        **_BREAKING,
    ),
    _op(
        "array_grown_into_padding",
        "lay.agrow",
        _replace(
            "lay.agrow",
            "struct LAG { char a[1]; int b; };",
            "struct LAG { char a[2]; int b; };",
        ),
        "char[1] -> char[2] fills the hole before b: b stays at 4 and sizeof at 8",
        any_of=(_FIELD_TYPE,),
        rules=(
            Rule(
                _pack(1),
                {"any_of": (_FIELD_TYPE, _OFFSET, _SIZE), **_BREAKING},
                "-fpack-struct=1 leaves no hole: b moves 1 -> 2, sizeof 5 -> 6",
            ),
        ),
    ),
)

_BITFIELDS = (
    _op(
        "bitfield_width_within_unit",
        "lay.bwabs",
        _replace(
            "lay.bwabs",
            "struct LBW { unsigned a : 3; unsigned b : 4; };",
            "struct LBW { unsigned a : 3; unsigned b : 6; };",
        ),
        "b widens 4 -> 6 bits inside the same 32-bit unit: no member moves and "
        "sizeof stays 4, but b's width (its value range) changes",
        any_of=(_BITFIELD,),
    ),
    _op(
        "bitfield_width_crosses_unit",
        "lay.bwshift",
        _replace(
            "lay.bwshift",
            "struct LBS { unsigned a : 16; unsigned b : 16; int c; };",
            "struct LBS { unsigned a : 20; unsigned b : 16; int c; };",
        ),
        "a widens 16 -> 20: b no longer fits the first unit and moves to the "
        "next, c moves 4 -> 8 and sizeof 8 -> 12",
        any_of=(_BITFIELD, _OFFSET, _SIZE),
        **_BREAKING,
    ),
    _op(
        "bitfield_became_field",
        "lay.bftof",
        _replace(
            "lay.bftof",
            "struct LBF { unsigned a : 8; unsigned b : 8; int c; };",
            "struct LBF { unsigned a; unsigned b : 8; int c; };",
        ),
        "a becomes a full unsigned: b moves from bit 8 to byte 4, c 4 -> 8, "
        "sizeof 8 -> 12",
        any_of=(_BITFIELD, _OFFSET, _SIZE),
        **_BREAKING,
    ),
)

_RECORDS = (
    _op(
        "record_alignment_raised",
        "lay.ralign",
        _replace(
            "lay.ralign", "struct LRA { int a; };", "struct alignas(16) LRA { int a; };"
        ),
        "alignas(16): alignof(LRA) 4 -> 16 and sizeof 4 -> 16",
        any_of=(_SIZE,),
        **_BREAKING,
        rules=(
            Rule(
                lambda c: c.evidence == "headers",
                {"any_of": (_ALIGN, _SIZE)},
                "the header layout records alignof; DWARF carries no record "
                "alignment, so below headers only the size change is observable",
            ),
        ),
    ),
    _op(
        "cxx_record_added",
        "lay.radd",
        lambda r: _set(
            r,
            "lay.radd",
            "struct LNA { int a; };\nint lna_use(LNA* p);",
            "int lna_use(LNA* p) { return p->a; }",
        ),
        "a new struct and a function taking it: nothing existing changes",
        expected=frozenset({"FUNC_ADDED"}),
        any_of=(frozenset({"TYPE_ADDED"}),),
        verdict_min="COMPATIBLE",
        verdict_max="COMPATIBLE",
        min_evidence="headers",
    ),
)

_BASES = (
    _op(
        "base_removed",
        "layb.remove",
        _replace(
            "layb.remove", "struct LD0 : LB0 { int d; };", "struct LD0 { int d; };"
        ),
        "LD0 loses its LB0 subobject: d moves 4 -> 0 and sizeof 8 -> 4",
        any_of=(frozenset({"TYPE_BASE_CHANGED"}), _OFFSET, _SIZE),
        **_BREAKING,
    ),
    _op(
        "base_added_nonvirtual",
        "layb.add",
        _replace("layb.add", "struct LD1 { int d; };", "struct LD1 : LB1 { int d; };"),
        "LD1 gains an LB1 subobject at offset 0: d moves 0 -> 4 and sizeof 4 -> 8",
        any_of=(frozenset({"TYPE_BASE_CHANGED"}), _OFFSET, _SIZE),
        **_BREAKING,
    ),
    _op(
        "base_became_nonvirtual",
        "layb.nonvirt",
        _replace(
            "layb.nonvirt",
            "struct LD2 : virtual LB2 { int d; };",
            "struct LD2 : LB2 { int d; };",
        ),
        "the virtual base becomes an ordinary one: the vptr and the VTT go, LB2 "
        "moves to offset 0 and sizeof shrinks",
        any_of=(frozenset({"BASE_CLASS_VIRTUAL_CHANGED", "TYPE_BASE_CHANGED"}), _SIZE),
        **_BREAKING,
    ),
    _op(
        "base_access_narrowed",
        "layb.access",
        _replace(
            "layb.access",
            "struct LD3 : public LB3 { int d; };",
            "struct LD3 : private LB3 { int d; };",
        ),
        "public -> private base: layout and symbols are unchanged, but code "
        "converting LD3* to LB3* or naming k through LD3 no longer compiles",
        any_of=(frozenset({"BASE_CLASS_ACCESS_NARROWED", "TYPE_BASE_CHANGED"}),),
        verdict_min="API_BREAK",
        min_evidence="headers",
    ),
)

_TRAITS = (
    _op(
        "record_lost_trivially_copyable",
        "lay.tcopy",
        _replace(
            "lay.tcopy",
            "struct LTC { int a; };",
            "struct LTC { int a; LTC() = default; LTC(const LTC& o); };",
            source=(
                "int ltc_get(LTC v) { return v.a; }",
                "LTC::LTC(const LTC& o) : a(o.a) {}\nint ltc_get(LTC v) { return v.a; }",
            ),
        ),
        "a user-provided copy constructor makes LTC non-trivial for calls: "
        "ltc_get now takes it by invisible reference where old callers pass it "
        "in a register, and it is no longer trivially copyable",
        any_of=(frozenset({"TRIVIALLY_COPYABLE_LOST", "VALUE_ABI_TRAIT_CHANGED"}),),
        verdict_min="BREAKING",
        precondition=_x86_64,
        host_arch=_X86_HOSTS,
        min_evidence="dwarf",
    ),
    _op(
        "record_lost_standard_layout",
        "lay.stdlay",
        _replace(
            "lay.stdlay",
            "struct LSL { int a; int b; };",
            "struct LSL { int a; protected: int b; };",
        ),
        "mixed access control makes LSL non-standard-layout: offsetof and "
        "C interop on it lose their guarantees, and b is no longer public",
        any_of=(
            frozenset({"STANDARD_LAYOUT_LOST"}),
            frozenset({"FIELD_ACCESS_CHANGED"}),
        ),
        verdict_min="API_BREAK",
        min_evidence="headers",
    ),
)

LAYOUT_OPERATORS: tuple[EditOp, ...] = (
    *_TRAITS,
    *_MEMBER_EDITS,
    *_RETYPES,
    *_BITFIELDS,
    *_RECORDS,
    *_BASES,
)
