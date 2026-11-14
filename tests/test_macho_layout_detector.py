# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The binary-only layout detectors read Mach-O the way they read ELF.

``diff_elf_layout`` decides vtable growth, thunk offset drift, thunk set
change and typeinfo shape from symbol sizes and names. On Mach-O those come
from the dylib's nlist and sections. Each case below is one logical layout
change written twice, once as ELF ``st_size`` and once as a Mach-O address
layout; the expected findings are hand-computed from the layout, so the
oracle is the constructed input, not the detector.

Bug class ``macho.layout_detector_blind``: a detector that silently skips a
format it can read (or a Mach-O symbol read as ELF) reports nothing for a
real break, and a pair that is only comparable across formats must not be
compared at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.diff_elf_layout import _diff_elf_layout
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolType
from abicheck.macho_metadata import parse_macho_metadata
from abicheck.model import AbiSnapshot
from abicheck.serialization import load_snapshot
from abicheck.storage.snapshot_codec import save_snapshot
from tests._macho_synthetic import N_EXT, N_SECT, MachoSymbol, write_dylib

_CONST = ("__DATA_CONST", "__const", 0x1000, 0x28)  # Base vtable, old size
_TYPEINFO = ("__DATA_CONST", "__typeinfo", 0x2000, 0x18)
_TEXT = ("__TEXT", "__text", 0x3000, 0x40)


def _macho_snap(lib: Path) -> AbiSnapshot:
    return AbiSnapshot(
        library="libshape.dylib",
        version="1",
        platform="macho",
        macho=parse_macho_metadata(lib),
    )


def _elf_snap(*symbols: ElfSymbol) -> AbiSnapshot:
    return AbiSnapshot(
        library="libshape.so",
        version="1",
        elf=ElfMetadata(symbols=list(symbols), pointer_size=8),
    )


def _vtable_macho(
    tmp: Path, name: str, vtable_size: int, extra: list[MachoSymbol] = (), sections=None
) -> Path:
    """A dylib whose ``_ZTV4Base`` fills its own section of *vtable_size* bytes."""
    syms = [
        MachoSymbol("__ZTV4Base", N_SECT | N_EXT, n_sect=1, n_value=0x1000),
        MachoSymbol("__ZTI4Base", N_SECT | N_EXT, n_sect=2, n_value=0x2000),
        *extra,
    ]
    secs = sections or [
        ("__DATA_CONST", "__vtable", 0x1000, vtable_size),
        _TYPEINFO,
        _TEXT,
    ]
    return write_dylib(tmp / name, syms, sections=secs)


def _kinds(changes) -> list[str]:
    return [c.kind.name if hasattr(c.kind, "name") else str(c.kind) for c in changes]


def _by_kind(changes, kind: str):
    return [
        c
        for c in changes
        if (c.kind.name if hasattr(c.kind, "name") else str(c.kind)) == kind
    ]


# ── vtable growth ───────────────────────────────────────────────────────────


def test_vtable_growth_on_macho_matches_elf_findings(tmp_path: Path) -> None:
    """Base gains one slot: 40 -> 48 bytes is one pointer-sized word (+1)."""
    old_macho = _macho_snap(_vtable_macho(tmp_path, "old.dylib", 0x28))
    new_macho = _macho_snap(_vtable_macho(tmp_path, "new.dylib", 0x30))
    old_elf = _elf_snap(
        ElfSymbol(name="_ZTV4Base", sym_type=SymbolType.OBJECT, size=40),
        ElfSymbol(name="_ZTI4Base", sym_type=SymbolType.OBJECT, size=24),
    )
    new_elf = _elf_snap(
        ElfSymbol(name="_ZTV4Base", sym_type=SymbolType.OBJECT, size=48),
        ElfSymbol(name="_ZTI4Base", sym_type=SymbolType.OBJECT, size=24),
    )

    macho = _by_kind(
        _diff_elf_layout(old_macho, new_macho), "VTABLE_SLOT_COUNT_CHANGED"
    )
    elf = _by_kind(_diff_elf_layout(old_elf, new_elf), "VTABLE_SLOT_COUNT_CHANGED")

    assert len(macho) == 1 and len(elf) == 1
    assert (macho[0].symbol, macho[0].old_value, macho[0].new_value) == (
        "_ZTV4Base",
        "40",
        "48",
    )
    assert (macho[0].symbol, macho[0].description) == (
        elf[0].symbol,
        elf[0].description,
    )
    assert "(+1 pointer-sized word)" in macho[0].description


def test_vtable_shrink_on_macho_is_reported_with_its_own_sign(tmp_path: Path) -> None:
    """A removed slot: 48 -> 40 bytes is -1 word, reported as a shrink."""
    old_macho = _macho_snap(_vtable_macho(tmp_path, "old2.dylib", 0x30))
    new_macho = _macho_snap(_vtable_macho(tmp_path, "new2.dylib", 0x28))
    changes = _by_kind(
        _diff_elf_layout(old_macho, new_macho), "VTABLE_SLOT_COUNT_CHANGED"
    )
    assert [(c.old_value, c.new_value) for c in changes] == [("48", "40")]
    assert "(-1 pointer-sized word)" in changes[0].description


def test_unchanged_vtable_on_macho_is_silent(tmp_path: Path) -> None:
    old_macho = _macho_snap(_vtable_macho(tmp_path, "old3.dylib", 0x28))
    new_macho = _macho_snap(_vtable_macho(tmp_path, "new3.dylib", 0x28))
    assert _diff_elf_layout(old_macho, new_macho) == []


# ── thunks ─────────────────────────────────────────────────────────────────


def _thunk_macho(tmp: Path, name: str, thunk: str | None) -> AbiSnapshot:
    extra = [MachoSymbol("__ZN1D1fEv", N_SECT | N_EXT, n_sect=3, n_value=0x3010)]
    if thunk is not None:
        extra.append(
            MachoSymbol(
                f"__ZTh{thunk}_N1D1fEv", N_SECT | N_EXT, n_sect=3, n_value=0x3000
            )
        )
    return _macho_snap(_vtable_macho(tmp, name, 0x28, extra=extra))


@pytest.mark.parametrize(
    ("old_thunk", "new_thunk", "expected"),
    [
        pytest.param("n8", "n16", ["VTABLE_THUNK_OFFSET_CHANGED"], id="offset-moved"),
        pytest.param(None, "n8", ["VTABLE_THUNK_SET_CHANGED"], id="thunk-added"),
        pytest.param("n8", None, ["VTABLE_THUNK_SET_CHANGED"], id="thunk-removed"),
        pytest.param("n8", "n8", [], id="same-offset"),
    ],
)
def test_thunk_changes_on_macho(
    tmp_path: Path, old_thunk: str | None, new_thunk: str | None, expected: list[str]
) -> None:
    old_macho = _thunk_macho(tmp_path, f"told-{old_thunk}.dylib", old_thunk)
    new_macho = _thunk_macho(tmp_path, f"tnew-{new_thunk}.dylib", new_thunk)
    assert sorted(set(_kinds(_diff_elf_layout(old_macho, new_macho)))) == sorted(
        expected
    )


# ── RTTI shape ─────────────────────────────────────────────────────────────


def test_typeinfo_growing_a_base_is_reported_on_macho(tmp_path: Path) -> None:
    """_ZTI 16 -> 24 bytes: a class type gains one base (__si_class_type_info)."""
    old = _macho_snap(
        write_dylib(
            tmp_path / "ti-old.dylib",
            [
                MachoSymbol("__ZTI4Base", N_SECT | N_EXT, n_sect=1, n_value=0x2000),
            ],
            sections=[("__DATA_CONST", "__typeinfo", 0x2000, 0x10)],
        )
    )
    new = _macho_snap(
        write_dylib(
            tmp_path / "ti-new.dylib",
            [
                MachoSymbol("__ZTI4Base", N_SECT | N_EXT, n_sect=1, n_value=0x2000),
            ],
            sections=[("__DATA_CONST", "__typeinfo", 0x2000, 0x18)],
        )
    )
    changes = _by_kind(_diff_elf_layout(old, new), "RTTI_INHERITANCE_CHANGED")
    assert [(c.symbol, c.old_value, c.new_value) for c in changes] == [
        ("_ZTI4Base", "16", "24")
    ]
    assert "single base class" in changes[0].description


# ── support gate and absent evidence ────────────────────────────────────────


def test_cross_format_pair_is_not_compared(tmp_path: Path) -> None:
    """ELF st_size against a Mach-O derived size is not a comparison: no findings."""
    macho = _macho_snap(_vtable_macho(tmp_path, "cross.dylib", 0x30))
    elf = _elf_snap(ElfSymbol(name="_ZTV4Base", sym_type=SymbolType.OBJECT, size=40))
    from abicheck.diff_elf_layout import _layout_support

    supported, reason = _layout_support(elf, macho)
    assert supported is False
    assert "missing ELF or Mach-O" in reason


def test_missing_derived_size_is_no_fact_not_a_zero(tmp_path: Path) -> None:
    """A vtable with no derived size on one side yields no finding, never a 0-byte change."""
    with_size = _macho_snap(_vtable_macho(tmp_path, "sized.dylib", 0x28))
    lib = write_dylib(
        tmp_path / "unsized.dylib",
        [MachoSymbol("__ZTV4Base", N_SECT | N_EXT, n_sect=1, n_value=0x1000)],
    )
    unsized = _macho_snap(lib)
    assert (
        _by_kind(_diff_elf_layout(with_size, unsized), "VTABLE_SLOT_COUNT_CHANGED")
        == []
    )


# ── persistence ────────────────────────────────────────────────────────────


def test_derived_size_survives_snapshot_round_trip(tmp_path: Path) -> None:
    """A stored snapshot keeps the size, so a live-vs-stored comparison agrees."""
    snap = _macho_snap(_vtable_macho(tmp_path, "stored.dylib", 0x30))
    out = tmp_path / "stored.json"
    save_snapshot(snap, out)
    loaded = load_snapshot(out)
    assert loaded.macho is not None
    assert {e.name: e.size for e in loaded.macho.exports}["_ZTV4Base"] == 0x30
