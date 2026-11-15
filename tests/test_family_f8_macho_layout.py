# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Harness H8 cell: the Mach-O layout detectors against the ELF pair (family F8).

Family F8 (target and toolchain parity): the same logical library, built for
another target, must yield the same artifact facts. Here the target is a
Mach-O dylib and the reference is the ELF pair of the same logical change.

**Oracle.** The ELF side is literal ``ElfSymbol`` sizes, so the expected
``VTABLE_SLOT_COUNT_CHANGED`` findings do not come from the Mach-O reader under
test. The Mach-O side is a synthetic dylib (``write_dylib``) in each fixup
encoding (classic rebase stream, chained fixups), read through the real reader.

**Seeded mutants.** Each family bug is patched back in and the oracle must
report it: the fixups are dropped (no size can be corroborated, so a real
growth is silently not evaluated), and the chained decoder is disabled (the
chained encoding is read as having no fixups). Both run on any host.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck import macho_metadata
from abicheck.diff_elf_layout import _diff_elf_layout
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolType
from abicheck.macho_metadata import parse_macho_metadata
from abicheck.model import AbiSnapshot
from tests._macho_synthetic import N_EXT, N_SECT, MachoSymbol, write_dylib

#: The logical change: _ZTV4Base is (old, new) bytes; the closing typeinfo is
#: 24 bytes on both sides, as in the ELF pair.
_CASES = {
    "grow": (0x28, 0x30),
    "shrink": (0x30, 0x28),
    "unchanged": (0x28, 0x28),
}
_ENCODINGS = ("classic", "chained")
_TEXT = ("__TEXT", "__text", 0x0, 0x800)


def _elf_snap(size: int) -> AbiSnapshot:
    return AbiSnapshot(
        library="libshape.so",
        version="1",
        elf=ElfMetadata(
            symbols=[
                ElfSymbol(name="_ZTV4Base", sym_type=SymbolType.OBJECT, size=size),
                ElfSymbol(name="_ZTI4Base", sym_type=SymbolType.OBJECT, size=24),
            ],
            pointer_size=8,
        ),
    )


def _macho_snap(tmp: Path, name: str, size: int, encoding: str) -> AbiSnapshot:
    """A dylib whose _ZTV4Base is closed by _ZTI4Base at +size, every pointer
    word but offset-to-top relocated (the layout a real vtable has)."""
    syms = [
        MachoSymbol("__ZTV4Base", N_SECT | N_EXT, n_sect=1, n_value=0x1000),
        MachoSymbol("__ZTI4Base", N_SECT | N_EXT, n_sect=1, n_value=0x1000 + size),
        MachoSymbol("__tail", N_SECT, n_sect=1, n_value=0x1000 + size + 0x18),
    ]
    secs = [("__DATA_CONST", "__vtable", 0x1000, size + 0x20), _TEXT]
    slots = [0x1000 + 8 * k for k in range(1, size // 8)]
    path = write_dylib(
        tmp / name,
        syms,
        sections=secs,
        fixup_slots=slots,
        fixup_encoding=encoding,
    )
    return AbiSnapshot(
        library="libshape.dylib",
        version="1",
        platform="macho",
        macho=parse_macho_metadata(path),
    )


def _slot_findings(changes) -> list[tuple[str, str, str, str]]:
    found = []
    for c in changes:
        kind = c.kind.name if hasattr(c.kind, "name") else str(c.kind)
        if kind == "VTABLE_SLOT_COUNT_CHANGED":
            found.append((c.symbol, c.old_value, c.new_value, c.description))
    return sorted(found)


def parity_violations(tmp: Path, case: str, encoding: str) -> list[str]:
    """The ELF pair and the Mach-O pair of one logical change must agree on
    their VTABLE_SLOT_COUNT_CHANGED findings. Empty means parity holds."""
    old_size, new_size = _CASES[case]
    elf = _slot_findings(_diff_elf_layout(_elf_snap(old_size), _elf_snap(new_size)))
    macho = _slot_findings(
        _diff_elf_layout(
            _macho_snap(tmp, f"{case}-old-{encoding}.dylib", old_size, encoding),
            _macho_snap(tmp, f"{case}-new-{encoding}.dylib", new_size, encoding),
        )
    )
    if macho == elf:
        return []
    return [f"{case}/{encoding}: mach-o {macho} != elf {elf}"]


@pytest.mark.parametrize("encoding", _ENCODINGS)
@pytest.mark.parametrize("case", sorted(_CASES))
def test_macho_layout_matches_the_elf_pair(
    case: str, encoding: str, tmp_path: Path
) -> None:
    assert parity_violations(tmp_path, case, encoding) == []


def test_seeded_mutant_dropped_fixups_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without fixups no size is corroborated: a real growth is not evaluated."""
    monkeypatch.setattr(
        macho_metadata, "_pointer_fixup_addresses", lambda path, header: frozenset()
    )
    assert parity_violations(tmp_path, "grow", "classic")
    assert parity_violations(tmp_path, "shrink", "classic")


def test_seeded_mutant_chained_decoder_disabled_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chained dylib with its decoder off reads as not derived: the chained
    growth cell must report the parity break, the classic one must not."""
    monkeypatch.setattr(
        macho_metadata, "decode_chained_fixup_addresses", lambda *a, **k: None
    )
    assert parity_violations(tmp_path, "grow", "chained")
    assert parity_violations(tmp_path, "grow", "classic") == []
