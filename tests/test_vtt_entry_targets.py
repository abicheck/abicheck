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


class _FakeElf:
    elfclass = 64

    def __getitem__(self, key: str) -> str:
        assert key == "e_machine"
        return "EM_X86_64"


@pytest.mark.parametrize(
    ("symbols", "failed", "expected"),
    [
        ([], False, {}),  # a complete read with zero exports: no VTT
        ([], True, None),  # the read failed before any export
        ([ElfSymbol(name="f", size=4)], True, None),  # failed after some exports
        ([ElfSymbol(name="f", size=4)], False, {}),  # complete, no VTT
    ],
)
def test_export_read_outcome_decides_unknown_vs_no_vtt(symbols, failed, expected):
    from abicheck.extract.elf_vtt_entries import read_vtt_entries
    from abicheck.extract.parse_failures import (
        note_export_read_failure,
        recording_export_read_failures,
    )

    with recording_export_read_failures():
        if failed:
            note_export_read_failure("dynsym unreadable")
        got = read_vtt_entries(_FakeElf(), ElfMetadata(symbols=symbols))
    assert got == expected


@pytest.mark.parametrize(
    ("sh_size", "st_size", "file_len", "expected"),
    [
        (0x100, 56, 0x2000, True),  # an honest VTT inside its section's bytes
        (0x100, 0x200, 0x2000, False),  # the VTT runs past its section
        (1 << 40, 1 << 39, 0x2000, False),  # forged section and VTT sizes
        (0x100, 56, 0x1010, False),  # the section is cut short by the file end
    ],
)
def test_vtt_span_must_exist_in_the_file(sh_size, st_size, file_len, expected):
    import io

    from abicheck.extract.elf_vtt_entries import _backed

    class _Elf:
        stream = io.BytesIO(b"\0" * file_len)

        def get_section(self, _index):
            return {
                "sh_type": "SHT_PROGBITS",
                "sh_addr": 0x4000,
                "sh_size": sh_size,
                "sh_offset": 0x1000,
            }

    assert _backed(_Elf(), 22, (0x4000, st_size)) is expected


def _sym_entry(value: int, size: int) -> dict:
    return {
        "st_shndx": 22,
        "st_size": size,
        "st_value": value,
        "st_info": {"type": "STT_OBJECT"},
    }


@pytest.mark.parametrize(
    ("second_size", "expected_rows"),
    [
        (56, 7),  # a versioned alias repeating the same span: read once
        (1 << 39, None),  # a forged, larger alias: the VTT is not read
        (24, None),  # a smaller disagreeing alias: not read either
    ],
)
def test_duplicate_vtt_names_never_size_the_read(second_size, expected_rows):
    import io

    from elftools.elf.sections import SymbolTableSection

    from abicheck.extract.elf_vtt_entries import _read

    class _Sym:
        def __init__(self, size: int) -> None:
            self.name = "_ZTT1D"
            self.entry = _sym_entry(0x4000, size)

    class _Dynsym(SymbolTableSection):
        def __init__(self) -> None:  # no pyelftools parsing
            pass

        def __getitem__(self, key):
            return "SHT_DYNSYM"

        def iter_symbols(self):
            return iter([_Sym(56), _Sym(second_size)])

    class _Elf:
        stream = io.BytesIO(b"\0" * 0x2000)

        def iter_sections(self):
            return iter([_Dynsym()])

        def iter_segments(self):
            return iter([])

        def get_section(self, _index):
            return {
                "sh_type": "SHT_PROGBITS",
                "sh_addr": 0x4000,
                "sh_size": 0x100,
                "sh_offset": 0x1000,
            }

    got = _read(_Elf(), {"_ZTT1D": second_size})
    rows = got.get("_ZTT1D") if got is not None else None
    assert (None if rows is None else len(rows)) == expected_rows
