"""ADR-095: a header-only layout conclusion that a config-level layout flag would undo.

Bug class: a stripped ``{B,H}`` compare read a record grown into its padding
(or an enum whose value range outgrew its ``-fshort-enums`` width) as a clean
``COMPATIBLE`` although the library may be built with ``-fpack-struct`` or
``-fshort-enums``. The oracle is the layout a packed / short-enum build gives,
computed here by hand (sum of field sizes; smallest of 1/2/4 bytes), not by
the detector's helpers.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.compare.layout_build_config import build_config_sensitive_changes
from abicheck.model import AbiSnapshot
from abicheck.model.dwarf_facts import DwarfMetadata
from abicheck.model.entities import EnumMember, EnumType, RecordType, TypeField

_K = ChangeKind.LAYOUT_BUILD_CONFIG_UNVERIFIED

# (name, C type, size bytes, align bytes)
_SCALARS = [
    ("c", "char", 1, 1),
    ("s", "short", 2, 2),
    ("i", "int", 4, 4),
    ("d", "double", 8, 8),
]


def _natural_layout(
    fields: list[tuple[str, str, int, int]],
) -> tuple[list[TypeField], int]:
    off, align, out = 0, 1, []
    for name, ty, size, al in fields:
        off = (off + al - 1) // al * al
        out.append(TypeField(name=name, type=ty, offset_bits=off * 8))
        off += size
        align = max(align, al)
    return out, (off + align - 1) // align * align


def _snap(types=(), enums=(), *, dwarf: bool = False, **kw) -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so.1",
        version="1",
        types=list(types),
        enums=list(enums),
        from_headers=True,
        dwarf=DwarfMetadata(has_dwarf=True) if dwarf else None,
        **kw,
    )


def _rec(fields):
    laid, size = _natural_layout(fields)
    return RecordType(name="R", kind="struct", size_bits=size * 8, fields=laid)


def _record_pairs():
    """Every old record of 1-3 scalars plus one appended or inserted scalar."""
    for n in (1, 2, 3):
        for old in itertools.product(_SCALARS, repeat=n):
            old_f = [(f"f{k}", t, s, a) for k, (_, t, s, a) in enumerate(old)]
            for pos in range(n + 1):
                for _, t, s, a in _SCALARS:
                    new_f = old_f[:pos] + [("added", t, s, a)] + old_f[pos:]
                    yield old_f, new_f


def test_record_signal_matches_packed_layout_oracle() -> None:
    checked = 0
    for old_f, new_f in _record_pairs():
        _, old_size = _natural_layout(old_f)
        _, new_size = _natural_layout(new_f)
        old_offsets = {
            f[0]: o.offset_bits for f, o in zip(old_f, _natural_layout(old_f)[0])
        }
        new_offsets = {
            f[0]: o.offset_bits for f, o in zip(new_f, _natural_layout(new_f)[0])
        }
        if old_size != new_size or any(
            old_offsets[k] != new_offsets[k] for k in old_offsets
        ):
            continue  # the header layout already shows the break
        # Packed oracle: the packed size is the plain sum of field sizes,
        # so adding a field always grows a packed record.
        assert sum(f[2] for f in new_f) > sum(f[2] for f in old_f)
        changes = build_config_sensitive_changes(
            _snap([_rec(old_f)]), _snap([_rec(new_f)])
        )
        assert [c.kind for c in changes] == [_K], (old_f, new_f)
        checked += 1
    assert checked > 50


def test_renaming_fields_in_place_is_not_flagged() -> None:
    """A rename or a reserved slot put to use keeps every offset (case54/case35).

    Oracle: the new field starts where an old field started, so it occupies
    storage the old layout already had; packing cannot shift it.
    """
    checked = 0
    for n in (1, 2, 3):
        for old in itertools.product(_SCALARS, repeat=n):
            old_f = [(f"f{k}", t, s, a) for k, (_, t, s, a) in enumerate(old)]
            for renamed in range(1, 2**n):
                new_f = [
                    (f"used{k}" if renamed >> k & 1 else name, t, s, a)
                    for k, (name, t, s, a) in enumerate(old_f)
                ]
                changes = build_config_sensitive_changes(
                    _snap([_rec(old_f)]), _snap([_rec(new_f)])
                )
                assert changes == [], (old_f, new_f)
                checked += 1
    assert checked > 50


def test_record_growth_visible_in_header_layout_is_not_flagged() -> None:
    old = _rec([("a", "int", 4, 4)])
    new = _rec([("a", "int", 4, 4), ("b", "int", 4, 4)])
    assert build_config_sensitive_changes(_snap([old]), _snap([new])) == []


def _oracle_width(values: list[int]) -> int:
    for width, lo, hi in ((1, -128, 255), (2, -32768, 65535), (4, -(2**31), 2**32 - 1)):
        signed = min(values) < 0
        if (
            signed
            and -(2 ** (width * 8 - 1)) <= min(values)
            and max(values) < 2 ** (width * 8 - 1)
        ):
            return width
        if not signed and max(values) <= hi:
            return width
    return 8


@pytest.mark.parametrize(
    "added", [0, 3, 127, 128, 255, 256, 300, 32767, 65535, 65536, -1, -129]
)
def test_enum_signal_matches_short_enum_oracle(added: int) -> None:
    base = [EnumMember("RED", 0), EnumMember("BLUE", 2)]
    old = EnumType(name="color", members=base, underlying_type="unsigned int")
    new = EnumType(
        name="color",
        members=[*base, EnumMember("WIDE", added)],
        underlying_type="unsigned int",
    )
    flagged = bool(
        build_config_sensitive_changes(_snap(enums=[old]), _snap(enums=[new]))
    )
    assert flagged == (_oracle_width([0, 2, added]) != _oracle_width([0, 2]))


@pytest.mark.parametrize(
    ("underlying", "scoped"), [("unsigned char", None), ("unsigned int", True)]
)
def test_enum_with_fixed_underlying_type_is_not_flagged(
    underlying: str, scoped: bool | None
) -> None:
    old = EnumType(
        name="e",
        members=[EnumMember("A", 0)],
        underlying_type=underlying,
        is_scoped=scoped,
    )
    new = EnumType(
        name="e",
        members=[EnumMember("A", 0), EnumMember("B", 300)],
        underlying_type=underlying,
        is_scoped=scoped,
    )
    assert build_config_sensitive_changes(_snap(enums=[old]), _snap(enums=[new])) == []


_TAIL_OLD = [("a", "int", 4, 4), ("b", "char", 1, 1)]
_TAIL_NEW = [*_TAIL_OLD, ("c", "char", 1, 1)]


@pytest.mark.parametrize(
    "side_kw",
    [
        {"dwarf": True},
        {"parsed_with_build_context": True},
        {"ast_compile_args": ("-fpack-struct=1",)},
        {"ast_compile_args": ("-fshort-enums",)},
    ],
)
@pytest.mark.parametrize("side", ["old", "new"])
def test_layout_evidence_on_either_side_silences_the_signal(
    side_kw: dict, side: str
) -> None:
    old = _snap([_rec(_TAIL_OLD)], **(side_kw if side == "old" else {}))
    new = _snap([_rec(_TAIL_NEW)], **(side_kw if side == "new" else {}))
    assert build_config_sensitive_changes(old, new) == []


def test_unrelated_compile_args_keep_the_signal() -> None:
    old = _snap([_rec(_TAIL_OLD)], ast_compile_args=("-DFOO", "-std=c11"))
    new = _snap([_rec(_TAIL_NEW)], ast_compile_args=("-DFOO", "-std=c11"))
    assert [c.kind for c in build_config_sensitive_changes(old, new)] == [_K]


def test_compare_cannot_read_clean_compatible() -> None:
    result = compare(_snap([_rec(_TAIL_OLD)]), _snap([_rec(_TAIL_NEW)]))
    kinds = {c.kind for c in result.changes}
    assert _K in kinds
    assert result.verdict is Verdict.COMPATIBLE_WITH_RISK


def test_unchanged_pair_stays_clean() -> None:
    result = compare(_snap([_rec(_TAIL_OLD)]), _snap([_rec(_TAIL_OLD)]))
    assert result.verdict is Verdict.NO_CHANGE


# --- abiforge ground truth (ADR-095 D4) --------------------------------------


@pytest.mark.parametrize(
    ("op_name", "seed_name", "flags"),
    [
        ("field_added_in_tail_padding", "cxx_more", {"pack": 1}),
        ("field_inserted_in_padding", "cxx_more", {"pack": 1}),
        ("enum_append_wide_value", "c_core", {"short_enums": True}),
    ],
)
def test_abiforge_owes_reduced_assurance_only_at_stripped_headers(
    op_name: str, seed_name: str, flags: dict
) -> None:
    from dataclasses import replace

    from abiforge.model.config import Flags, default_config
    from abiforge.model.expect import LAYOUT_CONFIG_SIGNAL
    from abiforge.model.seeds import SEED_BY_NAME
    from abiforge.operators import OP_BY_NAME
    from abiforge.run.harness import label_toolchain

    op = OP_BY_NAME[op_name]
    base = replace(
        default_config(SEED_BY_NAME[seed_name].lang, label_toolchain("gcc")),
        platform=("linux", "elf"),
    )
    flagged = replace(base, flags=Flags(**flags), evidence="headers")
    stripped = op.expect(replace(flagged, stripped=True))
    assert any(LAYOUT_CONFIG_SIGNAL in g for g in stripped.any_of)
    assert stripped.verdict_min == "COMPATIBLE_WITH_RISK"
    assert stripped.undecided
    # With DWARF (unstripped) or build data the flag is visible: the break is owed.
    assert op.expect(flagged).verdict_min == "BREAKING"
    assert (
        op.expect(replace(flagged, build_data=True, stripped=True)).verdict_min
        == "BREAKING"
    )
    plain = op.expect(replace(base, evidence="headers", stripped=True))
    assert plain.verdict_max == "COMPATIBLE_WITH_RISK"
    # A PE build keeps no PDB, so its unstripped headers cell is {B,H} for
    # layout as well: the same reduced assurance is owed, and allowed.
    pe = ("win32", "pe")
    pe_flagged = op.expect(replace(flagged, platform=pe))
    assert any(LAYOUT_CONFIG_SIGNAL in g for g in pe_flagged.any_of)
    assert pe_flagged.verdict_min == "COMPATIBLE_WITH_RISK"
    pe_plain = op.expect(replace(base, evidence="headers", platform=pe))
    assert pe_plain.verdict_max == "COMPATIBLE_WITH_RISK"
