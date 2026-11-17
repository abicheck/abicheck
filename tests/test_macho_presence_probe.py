# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Presence-only Mach-O DWARF reads the dSYM's section headers and parses no CU.

ELF's contract: under symbols_only / debug_presence_only the presence fact is
recorded and no type information is built. These tests make that structural: the
full DWARF reader is patched to fail, so any CU parse fails the test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.errors import SnapshotError
from abicheck.extract import macho_debug
from abicheck.extract.extraction_limitations import collecting_extraction_limitations
from abicheck.model.dwarf_facts import AdvancedExtractionStatus
from tests.test_macho_dsym_reader import (
    _MH_DSYM,
    _bundle_beside,
    _exporting_dylib,
    _macho,
)

UID = "11111111-2222-3333-4444-555555555555"
OTHER_UID = "99999999-8888-7777-6666-555555555555"


def _no_full_read(monkeypatch) -> None:
    """Any full DWARF read (a CU parse) is a failure of the presence contract."""

    def forbidden(*_a, **_k):
        raise AssertionError("presence-only must not parse DWARF compile units")

    import abicheck.dwarf_unified as dwarf_unified
    import abicheck.extract.macho_dsym as macho_dsym

    monkeypatch.setattr(macho_dsym, "read_dsym_dwarf", forbidden)
    monkeypatch.setattr(dwarf_unified, "parse_dwarf_from_macho_dsym", forbidden)
    monkeypatch.setattr(dwarf_unified, "parse_dwarf_from_session", forbidden)


def test_presence_is_recorded_from_section_headers_without_parsing(
    tmp_path: Path, monkeypatch
) -> None:
    lib = _exporting_dylib(tmp_path / "libq.dylib", UID)
    _bundle_beside(
        lib,
        _macho(
            tmp_path / "x.bin",
            filetype=_MH_DSYM,
            uuid=UID,
            sections=[("__debug_info", b"\x00" * 11)],
        ),
    )
    _no_full_read(monkeypatch)
    kw = macho_debug.macho_dwarf_kwargs(lib, None, {"debug_presence_only": True})
    assert kw["dwarf"].has_dwarf is True
    assert kw["dwarf_advanced"].extraction is AdvancedExtractionStatus.NOT_RUN


def test_symbols_only_takes_the_same_presence_route(
    tmp_path: Path, monkeypatch
) -> None:
    lib = _exporting_dylib(tmp_path / "libq.dylib", UID)
    _bundle_beside(
        lib,
        _macho(
            tmp_path / "x.bin",
            filetype=_MH_DSYM,
            uuid=UID,
            sections=[("__debug_info", b"\x00" * 11)],
        ),
    )
    _no_full_read(monkeypatch)
    kw = macho_debug.macho_dwarf_kwargs(lib, None, {"symbols_only": True})
    assert kw["dwarf_advanced"].extraction is AdvancedExtractionStatus.NOT_RUN


def test_a_bundle_without_debug_info_is_absent_and_silent(
    tmp_path: Path, monkeypatch
) -> None:
    lib = _exporting_dylib(tmp_path / "libq.dylib", UID)
    _bundle_beside(
        lib, _macho(tmp_path / "x.bin", filetype=_MH_DSYM, uuid=UID, sections=[])
    )
    _no_full_read(monkeypatch)
    with collecting_extraction_limitations() as limits:
        kw = macho_debug.macho_dwarf_kwargs(lib, None, {"debug_presence_only": True})
    assert kw == {}
    assert limits == []


def test_an_adjacent_bundle_from_another_binary_is_refused_with_a_note(
    tmp_path: Path, monkeypatch
) -> None:
    lib = _exporting_dylib(tmp_path / "libq.dylib", UID)
    _bundle_beside(
        lib,
        _macho(
            tmp_path / "x.bin",
            filetype=_MH_DSYM,
            uuid=OTHER_UID,
            sections=[("__debug_info", b"\x00" * 11)],
        ),
    )
    _no_full_read(monkeypatch)
    with collecting_extraction_limitations() as limits:
        kw = macho_debug.macho_dwarf_kwargs(lib, None, {"debug_presence_only": True})
    assert kw == {}
    assert limits == [macho_debug.ADJACENT_DSYM_UNREADABLE_LIMITATION]


def test_an_explicit_bundle_from_another_binary_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    lib = _exporting_dylib(tmp_path / "libq.dylib", UID)
    bundle_bin = _macho(
        tmp_path / "x.bin",
        filetype=_MH_DSYM,
        uuid=OTHER_UID,
        sections=[("__debug_info", b"\x00" * 11)],
    )
    _no_full_read(monkeypatch)
    with pytest.raises(SnapshotError, match="does not match"):
        macho_debug.macho_dwarf_kwargs(
            lib, None, {"debug_presence_only": True, "debug_info_path": bundle_bin}
        )
