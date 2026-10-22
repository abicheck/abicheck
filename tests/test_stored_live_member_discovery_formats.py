# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""A stored-baseline compare discovers NEW's PE and Mach-O members (#1715).

The stored-OLD/live-NEW driver (and its per-library manifest validation)
listed a live NEW directory with ``discover_shared_libraries``, an ELF-only
walk, so a directory of PE ``.dll``/Mach-O ``.dylib`` libraries supplied no
member: every library read "not supplied" and NEW's header-versus-exports
reconciliation was lost (the Windows CI failure of
``test_stored_baseline_new_side_header_surface``).

Invariant, over every subset of a small domain of directory entries: the
discovered member set is exactly the entries whose *magic bytes* make them
PE or Mach-O binaries -- whatever their file name -- and never a
non-binary. The oracle is the fixture table below, not abicheck's
classifier.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from abicheck.workflows.bundle_facts_library_overrides import (
    known_libraries_for_new_side,
)
from abicheck.workflows.release_inputs import discover_live_member_libraries

_PE = b"MZ" + b"\0" * 254
_MACHO_64 = bytes.fromhex("cffaedfe") + b"\0" * 252
_MACHO_32 = bytes.fromhex("cefaedfe") + b"\0" * 252

#: name -> (content, is a native library member)
_DOMAIN: dict[str, tuple[bytes, bool]] = {
    "foo.dll": (_PE, True),
    "libpe_named_like_elf.so": (_PE, True),
    "libbar.dylib": (_MACHO_64, True),
    "libbaz32.dylib": (_MACHO_32, True),
    "README.txt": (b"not a library\n", False),
    "notes.so": (b"plain text with a .so name\n", False),
}

_SUBSETS = [
    combo
    for r in range(len(_DOMAIN) + 1)
    for combo in itertools.combinations(sorted(_DOMAIN), r)
]


@pytest.mark.parametrize("names", _SUBSETS, ids=lambda c: "+".join(c) or "empty")
def test_directory_discovery_finds_every_pe_and_macho_member(
    tmp_path: Path, names: tuple[str, ...]
) -> None:
    root = tmp_path / "new"
    (root / "nested").mkdir(parents=True)
    for i, name in enumerate(names):
        # Alternate depth: discovery walks the whole tree.
        target = (root / "nested" if i % 2 else root) / name
        target.write_bytes(_DOMAIN[name][0])
    expected = {n for n in names if _DOMAIN[n][1]}

    found = discover_live_member_libraries(root, include_private=False)
    assert {p.name for p in found} == expected
    # The per-library manifest validation lists the same members.
    assert set(known_libraries_for_new_side(root, include_private_dso=False)) == (
        expected
    )


def test_single_file_operand_is_itself(tmp_path: Path) -> None:
    dll = tmp_path / "foo.dll"
    dll.write_bytes(_PE)
    assert discover_live_member_libraries(dll, include_private=False) == [dll]
