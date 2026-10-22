"""ABI_SURFACE_EXPLOSION is growth only; a shrink is recorded per symbol.

Field report: a 281 -> 138 export shrink was emitted as ``abi_surface_explosion``
with the growth advice ("check -fvisibility=hidden"). The detector fired on
``ratio < 0.5`` as well as ``ratio > 2.0``, and it reused the one kind for both
directions. The fix is growth only. The removed exports are already reported
one by one, so the shrink is not lost; it is just not called an explosion.
"""

from __future__ import annotations

import pytest

from abicheck.checker import ChangeKind, compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.extract.export_symbol_identity import itanium_export_function
from abicheck.model import AbiSnapshot


def _names(count: int) -> list[str]:
    return [f"_ZN3lib{i:04d}fEv" for i in range(count)]


def _lib(version: str, count: int) -> AbiSnapshot:
    """An ELF-only dump of *count* exports, shaped as the real symbols-only
    dumper builds it (``workflows/dump/elf_fallback.py``): one export-table
    ``Function`` per symbol plus the ELF symbol table, ``elf_only_mode`` set.
    Without the ``Function`` records the removals have nothing to be classified
    from, and the shrink would look empty for a reason that has nothing to do
    with the detector under test."""
    names = _names(count)
    return AbiSnapshot(
        library="libsurface.so",
        version=version,
        functions=[itanium_export_function(name) for name in names],
        variables=[],
        types=[],
        elf=ElfMetadata(
            symbols=[
                ElfSymbol(
                    name=name,
                    binding=SymbolBinding.GLOBAL,
                    sym_type=SymbolType.FUNC,
                    size=32,
                )
                for name in names
            ]
        ),
        elf_only_mode=True,
        platform="elf",
    )


def _explosions(old_count: int, new_count: int):
    result = compare(_lib("1.0", old_count), _lib("2.0", new_count))
    return [c for c in result.changes if c.kind == ChangeKind.ABI_SURFACE_EXPLOSION]


@pytest.mark.parametrize(
    ("old_count", "new_count", "fires"),
    [
        # Growth: fires only past 2x and 50 added exports.
        (100, 250, True),
        (100, 201, True),  # just over 2x
        (10, 100, True),  # smallest base the detector judges
        (100, 200, False),  # exactly 2x is not past the threshold
        (100, 149, False),  # under 50 added exports
        (100, 100, False),  # equal surfaces
        (9, 100, False),  # base below the 10-symbol floor
        # Shrink: never an explosion, however large.
        (281, 138, False),  # the field report
        (300, 150, False),  # exactly 0.5x
        (200, 50, False),
        (200, 99, False),  # just under 0.5x
        (281, 281, False),
    ],
)
def test_explosion_fires_for_growth_only(old_count, new_count, fires):
    hits = _explosions(old_count, new_count)
    assert bool(hits) is fires, (old_count, new_count, hits)


@pytest.mark.parametrize(("old_count", "new_count"), [(100, 250), (10, 100)])
def test_growth_description_says_grew_and_never_shrank(old_count, new_count):
    (hit,) = _explosions(old_count, new_count)
    assert hit.description.startswith("ABI surface grew dramatically")
    assert "shrank" not in hit.description
    assert f"{old_count} → {new_count} exported symbols" in hit.description


def test_shrink_is_recorded_per_removed_export_not_as_an_explosion():
    """The aggregate is dropped, not the evidence: 143 removed exports stay
    visible as their own findings, so a shrink is never silently absent."""
    result = compare(_lib("1.0", 281), _lib("2.0", 138))
    assert not [c for c in result.changes if c.kind == ChangeKind.ABI_SURFACE_EXPLOSION]
    removed = [c for c in result.changes if c.kind == ChangeKind.FUNC_REMOVED_ELF_ONLY]
    assert len(removed) == 143
