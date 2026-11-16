"""Mach-O DWARF resolution: explicit fails closed, adjacent becomes a note.

The resolver is patched so these run in the unit lane without a dSYM; the
real-DWARF path is covered by the integration reader tests.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from abicheck.errors import SnapshotError
from abicheck.extract import macho_debug
from abicheck.extract.extraction_limitations import collecting_extraction_limitations
from abicheck.model.dwarf_facts import AdvancedExtractionStatus

LIB = Path("/tmp/libq.dylib")


def _patch(monkeypatch, *, status=AdvancedExtractionStatus.COMPLETED, raises=None):
    seen = {}

    def fake(dylib, debug_format, *, dwarf_source=None):
        seen.update(dylib=dylib, debug_format=debug_format, dwarf_source=dwarf_source)
        if raises is not None:
            raise raises
        return SimpleNamespace(has_dwarf=True), SimpleNamespace(extraction=status)

    monkeypatch.setattr(macho_debug, "_resolve_debug_metadata", fake)
    return seen


def test_symbols_only_resolves_nothing(monkeypatch) -> None:
    seen = _patch(monkeypatch)
    assert macho_debug.macho_dwarf_kwargs(LIB, None, {"symbols_only": True}) == {}
    assert seen == {}


def test_a_completed_resolution_supplies_dwarf_and_advanced(monkeypatch) -> None:
    seen = _patch(monkeypatch)
    kw = macho_debug.macho_dwarf_kwargs(LIB, "dwarf", {})
    assert set(kw) == {"dwarf", "dwarf_advanced"}
    assert seen["debug_format"] == "dwarf"
    assert seen["dwarf_source"] is None


def test_an_uncompleted_resolution_supplies_nothing(monkeypatch) -> None:
    _patch(monkeypatch, status=AdvancedExtractionStatus.NOT_RUN)
    assert macho_debug.macho_dwarf_kwargs(LIB, None, {}) == {}


def test_an_unreadable_adjacent_dsym_is_a_non_fatal_note(monkeypatch) -> None:
    _patch(monkeypatch, raises=SnapshotError("present but unreadable"))
    with collecting_extraction_limitations() as limits:
        kw = macho_debug.macho_dwarf_kwargs(LIB, None, {})
    assert kw == {}
    assert len(limits) == 1
    assert limits[0].startswith(f"adjacent dSYM of {LIB} is unreadable and not used")
    assert "present but unreadable" in limits[0]


def test_an_unreadable_explicit_bundle_stays_fail_closed(monkeypatch) -> None:
    _patch(monkeypatch, raises=SnapshotError("present but unreadable"))
    with pytest.raises(SnapshotError, match="present but unreadable"):
        macho_debug.macho_dwarf_kwargs(LIB, None, {"debug_info_path": Path("x.dSYM")})
