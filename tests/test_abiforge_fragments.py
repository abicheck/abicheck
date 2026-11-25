"""Union and bit-field operators (abiforge/operators/fragments.py): exact rows per config.

Oracle: the measured tables of the GCC research campaigns
(``docs/contribute/plans/abiforge-language-fragments-campaign.md``), copied
here by hand, plus this repository's own compiler measurements under the
generator's real ``-fpack-struct=1`` flag; never the model under test.
"""

from __future__ import annotations

from collections import Counter

import pytest

from abiforge.model.config import Flags, Toolchain, default_config
from abiforge.model.exact import Bits, Bytes, ExactRow, Width
from abiforge.model.fragments import LAYOUT_FIXTURES, bitfield_record
from abiforge.model.seeds import SEED_BY_NAME
from abiforge.operators import OP_BY_NAME, OPERATORS
from abiforge.operators.fragments import FRAGMENT_OPERATORS, expected_rows

GCC = Toolchain("gcc", 13, "gcc", "g++")
CLANG = Toolchain("clang", 18, "clang", "clang++")


def _rows(fid_old, w_old, bar_old, w_new, bar_new, pack, producer="gcc"):
    old = (bitfield_record("t", w_old, bar_old),)
    new = (bitfield_record("t", w_new, bar_new),)
    return Counter(
        expected_rows(old, new, old_pack=pack, new_pack=pack, producer=producer)
    )


def _off(member, a, b):
    return ExactRow("TYPE_FIELD_OFFSET_CHANGED", "Bits_t", member, Bits(a), Bits(b))


_A = lambda a, b: ExactRow("FIELD_BITFIELD_CHANGED", "Bits_t", "a", Width(a), Width(b))  # noqa: E731

# Research table, "Observations for abiforge" (offsets in bits, size bytes).
RESEARCH = [
    # width 16 -> 17, no barrier, default pack: spill moves b,c,tail; size 8 -> 8
    (
        (16, "none", 17, "none", None),
        {_A(16, 17), _off("b", 16, 32), _off("c", 32, 48), _off("tail", 40, 56)},
    ),
    # width 16 -> 17, barrier before b, default pack: absorbed, width only
    ((16, "before", 17, "before", None), {_A(16, 17)}),
    # insert barrier before b at width 17, default pack: nothing moves
    ((17, "none", 17, "before", None), set()),
    # insert barrier after b at width 17: c 48 -> 64, tail 56 -> 72, size 8 -> 12
    (
        (17, "none", 17, "after", None),
        {
            _off("c", 48, 64),
            _off("tail", 56, 72),
            ExactRow("TYPE_SIZE_CHANGED", "Bits_t", None, Bytes(8), Bytes(12)),
        },
    ),
]


@pytest.mark.parametrize("edit,want", RESEARCH)
def test_rows_reproduce_the_research_measurements(edit, want) -> None:
    assert _rows("t", *edit) == Counter(want)


def test_packing_changes_the_expected_rows() -> None:
    # Same width edit under -fpack-struct=1: b and c move by one bit, tail stays,
    # size stays 6 (research: pack 1 row; identical under the flag here).
    assert _rows("t", 16, "none", 17, "none", 1) == Counter(
        {_A(16, 17), _off("b", 16, 17), _off("c", 32, 33)}
    )
    # 7 -> 8 packed: b 7->8, c 23->24, tail fixed (research pack-1 row).
    assert _rows("t", 7, "none", 8, "none", 1) == Counter(
        {_A(7, 8), _off("b", 7, 8), _off("c", 23, 24)}
    )
    # 15 -> 16: unpacked, c already starts the next unit (31 + 3 straddles),
    # so only b moves; packed, c follows b bit for bit.
    assert _rows("t", 15, "none", 16, "none", None) == Counter(
        {_A(15, 16), _off("b", 15, 16)}
    )
    assert _rows("t", 15, "none", 16, "none", 1) == Counter(
        {_A(15, 16), _off("b", 15, 16), _off("c", 31, 32)}
    )


def test_a_barrier_under_the_generator_flag_depends_on_the_producer() -> None:
    # Measured with gcc 13 / clang 18 and -fpack-struct=1: gcc rounds the
    # barrier to a byte, clang to the 32-bit unit.
    gcc = _rows("t", 7, "none", 7, "before", 1, "gcc")
    clang = _rows("t", 7, "none", 7, "before", 1, "clang")
    assert gcc == Counter({_off("b", 7, 8), _off("c", 23, 24)})
    assert clang == Counter(
        {
            _off("b", 7, 32),
            _off("c", 23, 48),
            _off("tail", 32, 56),
            ExactRow("TYPE_SIZE_CHANGED", "Bits_t", None, Bytes(5), Bytes(8)),
        }
    )


def _exact(op, config):
    return op.expect(config)


def _cfg(tc=GCC, pack=None, evidence="headers", stripped=False):
    return default_config("c", tc).with_(
        flags=Flags(pack=pack), evidence=evidence, stripped=stripped
    )


def test_union_member_rows_stay_apart_from_storage_rows() -> None:
    op = OP_BY_NAME["union_member_retyped_same_size"]
    (a,) = _exact(op, _cfg()).assertions
    assert a.rows == (
        ExactRow(
            "UNION_FIELD_TYPE_CHANGED", "Payload_uretype", "bits", "uint64_t", "double"
        ),
    )
    add = OP_BY_NAME["union_member_added_same_layout"]
    exp = _exact(add, _cfg())
    assert exp.assertions[0].rows == (
        ExactRow("UNION_FIELD_ADDED", "Payload_uadd", "real", None, "double"),
    )
    assert exp.verdict_min == "COMPATIBLE" and exp.verdict_max == "COMPATIBLE_WITH_RISK"


@pytest.mark.parametrize(
    "op,storage",
    [
        ("union_char_extent_7_to_8", False),
        ("union_char_extent_8_to_9", True),
        ("union_char_extent_15_to_16", False),
        ("union_char_extent_16_to_17", True),
    ],
)
def test_union_extent_boundaries(op, storage) -> None:
    rows = _exact(OP_BY_NAME[op], _cfg()).assertions[0].rows
    kinds = Counter(r.kind for r in rows)
    assert kinds["UNION_FIELD_TYPE_CHANGED"] == 1
    assert bool(kinds["TYPE_SIZE_CHANGED"]) is storage
    if storage:  # union and envelope grow 8 bytes, the trailing word moves 8
        assert kinds == Counter(
            {
                "UNION_FIELD_TYPE_CHANGED": 1,
                "TYPE_SIZE_CHANGED": 2,
                "TYPE_FIELD_OFFSET_CHANGED": 1,
            }
        )
    packed = _exact(OP_BY_NAME[op], _cfg(pack=1, evidence="dwarf")).assertions[0].rows
    # Packed, every byte past the 8-byte scalar counts: only 7 -> 8 stays put.
    assert Counter(r.kind for r in packed)["TYPE_SIZE_CHANGED"] == (
        0 if op.endswith("7_to_8") else 2
    )


@pytest.mark.parametrize(
    "op,fid,offsets",
    [
        ("union_array_stride_grows", "ustride_grow", (32, 56)),
        ("union_array_stride_shrinks", "ustride_shrink", (56, 32)),
        ("union_array_stride_unchanged", "ustride_same", None),
    ],
)
def test_three_union_elements_set_the_trailing_word_offset(op, fid, offsets) -> None:
    rows = _exact(OP_BY_NAME[op], _cfg(stripped=True)).assertions[0].rows
    moved = [r for r in rows if r.kind == "TYPE_FIELD_OFFSET_CHANGED"]
    # Eight leading bytes, then three unions of eight or sixteen bytes each.
    assert moved == (
        [
            ExactRow(
                "TYPE_FIELD_OFFSET_CHANGED",
                f"Envelope_{fid}",
                "t",
                Bytes(offsets[0]),
                Bytes(offsets[1]),
            )
        ]
        if offsets
        else []
    )
    if offsets is None:
        assert [r.kind for r in rows] == ["UNION_FIELD_TYPE_CHANGED"]


def test_alignment_rows_need_headers_and_are_unknown_below() -> None:
    op = OP_BY_NAME["union_rebuild_packed_alignment_only"]
    with_h = _exact(op, _cfg())
    assert [r.kind for r in with_h.assertions[0].rows] == ["TYPE_ALIGNMENT_CHANGED"]
    assert not with_h.undecided and with_h.verdict_min == "BREAKING"
    dw = _exact(op, _cfg(evidence="dwarf"))
    assert dw.assertions[0].rows == () and dw.undecided
    assert dw.verdict_min == "NO_CHANGE"


def test_unrecorded_packing_leaves_storage_unknown_but_judges_members() -> None:
    op = OP_BY_NAME["union_char_extent_8_to_9"]
    exp = _exact(op, _cfg(pack=1, stripped=True))
    (a,) = exp.assertions
    assert [r.kind for r in a.rows] == ["UNION_FIELD_TYPE_CHANGED"]
    assert "TYPE_SIZE_CHANGED" not in a.scope.kinds and exp.undecided


def test_controls_are_required_absences() -> None:
    for name in ("union_rebuild_optimized", "bitfield_rebuild_optimized"):
        exp = _exact(OP_BY_NAME[name], _cfg(evidence="dwarf"))
        (a,) = exp.assertions
        assert a.rows == () and "TYPE_FIELD_OFFSET_CHANGED" in a.scope.kinds
        assert exp.verdict_max == "COMPATIBLE" and not exp.undecided


@pytest.mark.parametrize(
    "flags,reason",
    [(Flags(pack=2), "packing"), (Flags(bits=32), "LP64")],
)
def test_unmodelled_configs_are_not_guessed(flags, reason) -> None:
    op = OP_BY_NAME["bitfield_width_16_to_17"]
    why = op.requires(default_config("c", GCC).with_(flags=flags))
    assert why is not None and reason in why
    msvc = Toolchain("msvc", 19, "cl", "cl")
    assert "gcc and clang" in op.requires(default_config("c", msvc))


def test_operators_reach_the_seed_through_normal_interfaces() -> None:
    seed = SEED_BY_NAME["c_layout"]
    ops = [o for o in OPERATORS if o.applies_to(seed)]
    assert {o.name for o in ops} == {o.name for o in FRAGMENT_OPERATORS}
    assert len(FRAGMENT_OPERATORS) >= 25
    roles = {fx.role for fx in LAYOUT_FIXTURES}
    for op in FRAGMENT_OPERATORS:
        assert set(op.roles) - {"lf.gate"} <= roles
        # Other seeds never take a fragment operator (the gate role).
        assert not op.applies_to(SEED_BY_NAME["c_core"])
    widths = {
        o.name for o in FRAGMENT_OPERATORS if o.name.startswith("bitfield_width_")
    }
    for w in (1, 7, 8, 9, 15, 16, 17, 31, 32):
        assert any(f"_{w}_" in n or n.endswith(f"_{w}") for n in widths), w
