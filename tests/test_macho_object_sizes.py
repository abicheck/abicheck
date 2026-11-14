# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Mach-O vtable / typeinfo / VTT object sizes, derived from symbol addresses.

A Mach-O nlist has no size, so ``_ZTV``/``_ZTI``/``_ZTT`` sizes come from the
gap to the next defined symbol of the same section (see
``abicheck.extract.macho_object_sizes``). Every expected value below is
computed by hand from the constructed address layout, so the oracle does not
run the implementation's own helper.

Bug class ``macho.derived_object_size``: a size that is read from the wrong
neighbour (a local or private extern not counted as a boundary, a debug stab
counted as one, an alias changing the next address) reads a different
vtable size and fabricates or hides a layout break.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.extract.macho_object_sizes import derive_object_sizes
from abicheck.macho_metadata import parse_macho_metadata
from tests._macho_synthetic import (
    N_EXT,
    N_PEXT,
    N_SECT,
    MachoSymbol,
    write_dylib,
)

#: n_type of a debug stab entry (N_STAB = 0xe0): not a symbol with an address.
_STAB = 0x24

# (defined (n_sect, value, name), sections {ordinal: (addr, size)}, expected)
_DERIVE_CASES = [
    pytest.param(
        [(1, 0x1000, "_ZTV4Base"), (1, 0x1028, "_ZTV4Derived")],
        {1: (0x1000, 0x80)},
        # Base ends where Derived starts: 0x1028-0x1000. Derived is last: 0x1080-0x1028.
        {"_ZTV4Base": 0x28, "_ZTV4Derived": 0x58},
        id="two-objects-one-section",
    ),
    pytest.param(
        [(1, 0x1000, "_ZTV4A"), (1, 0x1000, "_ZTI4A"), (1, 0x1010, "_ZTV4B")],
        {1: (0x1000, 0x20)},
        # Aliases at one address share their size: the next *distinct* address is 0x1010.
        {"_ZTV4A": 0x10, "_ZTI4A": 0x10, "_ZTV4B": 0x10},
        id="same-address-aliases",
    ),
    pytest.param(
        [(1, 0x1000, "_ZTV4A"), (1, 0x1010, "local_label")],
        {1: (0x1000, 0x40)},
        # A local (non-external) symbol still bounds the object before it.
        {"_ZTV4A": 0x10},
        id="local-symbol-bounds",
    ),
    pytest.param(
        [(1, 0x1000, "_ZTT4A"), (1, 0x1010, "_ZTI4A")],
        {1: (0x1000, 0x18)},
        {"_ZTT4A": 0x10, "_ZTI4A": 0x8},
        id="vtt-and-typeinfo-families",
    ),
    pytest.param(
        [(1, 0x1000, "_ZN4A1fEv"), (1, 0x1008, "_main")],
        {1: (0x1000, 0x10)},
        # Only the three object families are sized; functions are not.
        {},
        id="non-object-names-ignored",
    ),
    pytest.param(
        [(1, 0x1010, "_ZTV4A")],
        {1: (0x1000, 0x10)},
        # Address at the section end lies outside the section: no size.
        {},
        id="address-at-section-end",
    ),
    pytest.param(
        [(9, 0x1000, "_ZTV4A")],
        {1: (0x1000, 0x10)},
        # Ordinal 9 has no section extent: no size rather than a guessed one.
        {},
        id="unknown-section-ordinal",
    ),
    pytest.param(
        [(1, 0x1000, "_ZTV4A"), (2, 0x2000, "_ZTV4B")],
        {1: (0x1000, 0x18), 2: (0x2000, 0x30)},
        # Sections are independent: each object is bounded by its own section.
        {"_ZTV4A": 0x18, "_ZTV4B": 0x30},
        id="sections-are-independent",
    ),
]


@pytest.mark.parametrize(("defined", "sections", "expected"), _DERIVE_CASES)
def test_derived_sizes_match_hand_computed_layout(
    defined: list[tuple[int, int, str]],
    sections: dict[int, tuple[int, int]],
    expected: dict[str, int],
) -> None:
    assert derive_object_sizes(defined, sections) == expected


def test_reader_derives_sizes_with_locals_private_externs_and_stabs(
    tmp_path: Path,
) -> None:
    """Boundary rules as the Mach-O reader applies them, on a real dylib image.

    Section ``__const`` (0x1000..0x1100) holds ``_ZTV4Base`` at 0x1000, a
    private-extern helper at 0x1020 (a boundary, not an export), ``_ZTI4Base``
    at 0x1020 too... see the layout below: ``_ZTI4Base`` runs to the section
    end because the stab at 0x1030 is not a symbol.
    """
    lib = write_dylib(
        tmp_path / "libshape.dylib",
        [
            MachoSymbol("__ZTV4Base", N_SECT | N_EXT, n_sect=1, n_value=0x1000),
            MachoSymbol(
                "__private_helper", N_SECT | N_EXT | N_PEXT, n_sect=1, n_value=0x1020
            ),
            MachoSymbol("__ZTI4Base", N_SECT | N_EXT, n_sect=1, n_value=0x1028),
            MachoSymbol("__debug_stab", _STAB, n_sect=1, n_value=0x1040),
            MachoSymbol("__ZTV5Other", N_SECT | N_EXT, n_sect=2, n_value=0x2008),
        ],
        sections=[
            ("__DATA_CONST", "__const", 0x1000, 0x100),
            ("__DATA_CONST", "__other", 0x2000, 0x30),
        ],
    )
    meta = parse_macho_metadata(lib)
    sizes = {e.name: e.size for e in meta.exports}
    assert "private_helper" not in sizes  # private extern: not an export
    # _ZTV4Base: next symbol 0x1020 (the private extern) -> 0x20.
    assert sizes["_ZTV4Base"] == 0x20
    # _ZTI4Base: the stab at 0x1040 is not a symbol, so it runs to 0x1100 -> 0xd8.
    assert sizes["_ZTI4Base"] == 0x1100 - 0x1028
    # _ZTV5Other alone in its section: 0x2030 - 0x2008 -> 0x28.
    assert sizes["_ZTV5Other"] == 0x2030 - 0x2008


def test_export_without_section_information_has_no_size(tmp_path: Path) -> None:
    """A dylib written without sections gives no sizes (no fabricated value)."""
    lib = write_dylib(
        tmp_path / "libnosect.dylib",
        [MachoSymbol("__ZTV4Base", N_SECT | N_EXT, n_sect=1, n_value=0x1000)],
    )
    meta = parse_macho_metadata(lib)
    assert [e.size for e in meta.exports] == [None]
