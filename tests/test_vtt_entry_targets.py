# SPDX-License-Identifier: Apache-2.0
"""VTT entry targets: a same-size ``_ZTT`` whose entry points elsewhere.

Synthetic ``ElfMetadata`` throughout (no toolchain); the compiled
reproducer with an old client lives in ``test_vtt_entry_targets_elf.py``.
The oracle for the resolver is independent of its implementation: an
address placed at a known offset inside a known table, before and after
every table is moved by the same arbitrary delta.
"""

from __future__ import annotations

import random

import pytest

from abicheck.checker import ChangeKind, compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.extract.elf_vtt_entries import _CoverIndex, _target
from abicheck.model import AbiSnapshot
from abicheck.model.elf_facts import VttEntryTarget
from abicheck.serialization import load_snapshot
from abicheck.storage.snapshot_codec import save_snapshot

T = VttEntryTarget
_OLD = (T("_ZTV1D", 24), T("_ZTC1D0_1L", 24), T("_ZTC1D0_1L", 56), T("_ZTV1D", 56))


def _snap(entries, *, vtt_size: int = 32, extra=()) -> AbiSnapshot:
    syms = [
        ElfSymbol(
            name="_ZTT1D",
            binding=SymbolBinding.WEAK,
            sym_type=SymbolType.OBJECT,
            size=vtt_size,
        ),
        *extra,
    ]
    return AbiSnapshot(
        library="libp.so",
        version="1",
        elf=ElfMetadata(
            symbols=syms,
            machine="EM_X86_64",
            vtt_entries=None if entries is None else {"_ZTT1D": tuple(entries)},
        ),
        elf_only_mode=True,
    )


def _vtt_findings(result):
    return [c for c in result.changes if c.kind is ChangeKind.VTT_ENTRY_TARGET_CHANGED]


def _declined(result) -> list:
    det = next(d for d in result.detector_results if d.name == "elf_vtt_entries")
    return list(det.declined)


# ── comparison ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("index", "target"),
    [
        (2, T("_ZTV1V", 16)),  # another table (the reproduced fault)
        (2, T("_ZTC1D0_1L", 24)),  # same table, another address point
        (0, T("_ZTV1D", 56)),  # same table, another address point
        (3, T("_ZTVN3ext1VE", 16)),  # an imported table
    ],
)
def test_retargeted_entry_is_breaking(index, target):
    new = list(_OLD)
    new[index] = target
    result = compare(_snap(_OLD), _snap(new))
    (finding,) = _vtt_findings(result)
    assert finding.symbol == "_ZTT1D"
    assert finding.review_evidence["entry_index"] == index
    assert finding.review_evidence["byte_offset"] == index * 8
    assert finding.review_evidence["new_table"] == target.table
    assert finding.review_evidence["new_address_point_offset"] == target.offset
    assert "'D'" in finding.description
    assert result.verdict.value == "BREAKING"


def test_equal_targets_report_nothing():
    result = compare(_snap(_OLD), _snap(_OLD))
    assert _vtt_findings(result) == []
    assert _declined(result) == []


@pytest.mark.parametrize("side", ["old", "new"])
def test_unknown_entry_is_never_a_change(side):
    blurred = list(_OLD)
    blurred[2] = None
    retargeted = list(_OLD)
    retargeted[2] = T("_ZTV1V", 16)
    old, new = (blurred, retargeted) if side == "old" else (retargeted, blurred)
    result = compare(_snap(old), _snap(new))
    assert _vtt_findings(result) == []
    ((entity, reason),) = _declined(result)
    assert entity == "_ZTT1D" and "[2]" in reason


@pytest.mark.parametrize("side", ["old", "new"])
def test_snapshot_without_the_fact_declines(side):
    retargeted = list(_OLD)
    retargeted[2] = T("_ZTV1V", 16)
    old, new = (None, retargeted) if side == "old" else (retargeted, None)
    result = compare(_snap(old), _snap(new))
    assert _vtt_findings(result) == []
    ((entity, reason),) = _declined(result)
    assert entity == "_ZTT1D" and f"{side} side" in reason


def test_size_change_stays_the_slot_count_finding():
    grown = (*_OLD, T("_ZTV1D", 88))
    result = compare(_snap(_OLD), _snap(grown, vtt_size=40))
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.VTT_SLOT_COUNT_CHANGED in kinds
    assert ChangeKind.VTT_ENTRY_TARGET_CHANGED not in kinds


def test_library_without_vtt_gets_no_new_finding():
    plain = ElfSymbol(name="f", sym_type=SymbolType.FUNC, size=4)
    old = AbiSnapshot(
        library="libc1.so",
        version="1",
        elf=ElfMetadata(symbols=[plain], machine="EM_X86_64", vtt_entries={}),
        elf_only_mode=True,
    )
    new = AbiSnapshot(
        library="libc1.so",
        version="1",
        elf=ElfMetadata(symbols=[plain], machine="EM_X86_64", vtt_entries=None),
        elf_only_mode=True,
    )
    result = compare(old, new)
    assert _vtt_findings(result) == []
    assert _declined(result) == []


# ── resolver: relink invariance and relocation-form equivalence ────────────


def _layout(rng: random.Random, base: int):
    """Random non-overlapping tables; returns ``{name: (start, size)}``."""
    objects, cursor = {}, base
    for i in range(rng.randint(2, 12)):
        size = 8 * rng.randint(2, 16)
        objects[f"_ZTV{i}T{i}"] = (cursor, size)
        cursor += size + 8 * rng.randint(0, 3)
    return objects


@pytest.mark.parametrize("seed", range(40))
def test_resolution_survives_moving_every_table(seed):
    rng = random.Random(seed)
    objects = _layout(rng, 0x4000)
    name = rng.choice(sorted(objects))
    start, size = objects[name]
    offset = 8 * rng.randint(2, size // 8)  # an address point, end included
    expected = T(name, offset)
    delta = 8 * rng.randint(1, 4096)
    moved = {n: (s + delta, z) for n, (s, z) in objects.items()}
    for objs, addr in ((objects, start + offset), (moved, start + delta + offset)):
        index = _CoverIndex(objs)
        # R_X86_64_RELATIVE carries the address; R_X86_64_64 against the
        # table symbol reaches it as S + A. Both resolve to the same target.
        assert _target(("addr", addr), objs, index) == expected
        s_plus_a = ("addr", objs[name][0] + offset)
        assert _target(s_plus_a, objs, index) == expected


def test_ambiguous_or_uncovered_address_is_unknown():
    objs = {"_ZTV1A": (0x100, 0x20), "_ZTV1B": (0x110, 0x20), "_ZTV1C": (0x200, 8)}
    index = _CoverIndex(objs)
    assert _target(("addr", 0x118), objs, index) is None  # two tables cover it
    assert _target(("addr", 0x180), objs, index) is None  # nothing covers it
    assert _target(("addr", 0x200), objs, index) is None  # a table's first byte
    assert _target(None, objs, index) is None  # no/unsupported relocation
    assert _target(("sym", "_ZTV3ext", 16), objs, index) == T("_ZTV3ext", 16)


# ── storage ─────────────────────────────────────────────────────────────────


def test_round_trip_keeps_targets_and_unknowns(tmp_path):
    entries = (*_OLD[:2], None, _OLD[3])
    path = tmp_path / "s.json"
    save_snapshot(_snap(entries), path)
    assert load_snapshot(path).elf.vtt_entries == {"_ZTT1D": entries}


def test_unread_fact_round_trips_as_unknown(tmp_path):
    path = tmp_path / "s.json"
    save_snapshot(_snap(None), path)
    assert "elf_vtt_entries" not in path.read_text()
    assert load_snapshot(path).elf.vtt_entries is None


# ── relocation forms and imported-table identity (review follow-ups) ───────


class _Sym:
    def __init__(self, name: str, shndx: object, value: int = 0) -> None:
        self.name = name
        self.entry = {"st_shndx": shndx, "st_value": value}


class _Symtab:
    def __init__(self, *syms: _Sym) -> None:
        self._syms = syms

    def get_symbol(self, index: int) -> _Sym:
        return self._syms[index]


def _rel(rtype: int, index: int, addend: int) -> dict:
    return {"r_info_type": rtype, "r_info_sym": index, "r_addend": addend}


def test_glob_dat_ignores_its_addend_and_r64_keeps_it():
    from abicheck.extract.elf_vtt_entries import _resolve

    tab = _Symtab(_Sym("", 0), _Sym("_ZTV3ext", "SHN_UNDEF"), _Sym("_ZTV1A", 5, 0x100))
    # R_X86_64_GLOB_DAT writes S: two addends, one target.
    assert _resolve(_rel(6, 1, 0), tab, None) == _resolve(_rel(6, 1, 16), tab, None)
    assert _resolve(_rel(6, 2, 24), tab, None) == ("addr", 0x100)
    # R_X86_64_64 writes S + A.
    assert _resolve(_rel(1, 1, 16), tab, None) == ("sym", "_ZTV3ext", 16)
    assert _resolve(_rel(1, 2, 24), tab, None) == ("addr", 0x118)
    assert _resolve(_rel(8, 0, 0x4C28), tab, None) == ("addr", 0x4C28)
    assert _resolve(_rel(2, 1, 0), tab, None) is None  # PC32: unsupported


def test_imported_table_keeps_its_required_version():
    from abicheck.extract.elf_vtt_entries import _resolve

    tab = _Symtab(_Sym("", 0), _Sym("_ZTV1V", "SHN_UNDEF"))
    v1 = _resolve(_rel(1, 1, 16), tab, {1: "V1"})
    v2 = _resolve(_rel(1, 1, 16), tab, {1: "V2"})
    assert v1 == ("sym", "_ZTV1V@V1", 16) and v2 == ("sym", "_ZTV1V@V2", 16)
    assert _resolve(_rel(1, 1, 16), tab, {}) == ("sym", "_ZTV1V", 16)
    assert _resolve(_rel(1, 1, 16), tab, {1: None}) is None  # unreadable version


@pytest.mark.parametrize("short_side", ["old", "new"])
def test_stored_rows_shorter_than_the_vtt_decline(short_side):
    full, short = _OLD, _OLD[:3]  # a 32-byte VTT holds four entries
    old, new = (short, full) if short_side == "old" else (full, short)
    result = compare(_snap(old), _snap(new))
    assert _vtt_findings(result) == []
    ((entity, _reason),) = _declined(result)
    assert entity == "_ZTT1D"
