# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The edges of the corrupt-input rules: what is refused and what is not.

Companion to ``tests/test_corrupt_input_matrix.py``. Each test here pins
one boundary from review of that change:

* a detached debug file (``--debug-root``) that cannot be opened at all is a
  recorded debug-info failure. The DWARF readers record failures only once
  they have located DWARF, and a detached file has no export reader to refuse
  it, so this one is recorded by the debug-metadata resolver;
* a PE whose optional header declares **zero** data directories has no
  export directory, which is not the same as an unreadable header;
* the surface graph, decoded lazily on first access, refuses a malformed
  graph with the same typed error as the rest of the snapshot.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from abicheck.errors import MalformedSnapshotError, SnapshotError
from abicheck.extract.debug_dump import _resolve_debug_metadata
from abicheck.extract.parse_failures import (
    debug_info_read_required,
    recording_export_read_failures,
)
from abicheck.model import AbiSnapshot
from abicheck.pe_metadata import parse_pe_metadata
from abicheck.storage.surface_graph_codec import decode_surface_graph
from tests._corrupt_input_matrix import PE_SEED


def _elf_with_debug(tmp_path: Path) -> Path:
    if shutil.which("gcc") is None:
        pytest.skip("gcc is required to build the ELF object")
    src = tmp_path / "lib.c"
    src.write_text("struct p { int x; };\nint get(struct p v) { return v.x; }\n")
    out = tmp_path / "lib.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-g", "-o", str(out), str(src)],
        check=True,
        capture_output=True,
    )
    if not out.read_bytes().startswith(b"\x7fELF"):
        pytest.skip("the host gcc does not link ELF shared objects")
    return out


@pytest.mark.parametrize("debug_format", (None, "dwarf"))
@pytest.mark.parametrize(
    "detached", (b"", b"not an ELF file at all", b"\x7fELF\x02\x01\x01")
)
def test_an_unreadable_detached_debug_file_refuses_the_dump(
    tmp_path: Path, debug_format: str | None, detached: bytes
) -> None:
    """Auto-detect and explicit ``dwarf``: a detached file that is empty, not
    ELF, or a cut ELF header is refused, never a symbols-only dump."""
    so = _elf_with_debug(tmp_path)
    debug_file = tmp_path / "lib.so.debug"
    debug_file.write_bytes(detached)
    with (
        pytest.raises(SnapshotError, match="detached debug file"),
        debug_info_read_required(so),
    ):
        _resolve_debug_metadata(so, debug_format, dwarf_source=debug_file)


def test_a_readable_detached_debug_file_is_read(tmp_path: Path) -> None:
    """The control: a real detached debug file (here, an unstripped copy) reads
    its types and records nothing."""
    so = _elf_with_debug(tmp_path)
    debug_file = tmp_path / "lib.so.debug"
    debug_file.write_bytes(so.read_bytes())
    with debug_info_read_required(so):
        meta, _adv = _resolve_debug_metadata(so, None, dwarf_source=debug_file)
    assert meta.has_dwarf
    assert "p" in meta.structs


def _pe_with_directory_count(count: int, *, magic: int | None = None) -> bytes:
    data = bytearray(PE_SEED.read_bytes())
    optional = struct.unpack_from("<I", data, 0x3C)[0] + 24
    assert struct.unpack_from("<H", data, optional)[0] == 0x20B, "PE32+ seed"
    struct.pack_into("<I", data, optional + 108, count)  # NumberOfRvaAndSizes
    if magic is not None:
        struct.pack_into("<H", data, optional, magic)
    return bytes(data)


def _parse_pe(tmp_path: Path, data: bytes) -> tuple[list[str], list[str]]:
    target = tmp_path / "seed.dll"
    target.write_bytes(data)
    with recording_export_read_failures() as sink:
        meta = parse_pe_metadata(target)
    return [e.name for e in meta.exports], sink


def test_a_pe_declaring_zero_data_directories_has_no_exports_unrecorded(
    tmp_path: Path,
) -> None:
    """``NumberOfRvaAndSizes == 0`` is a valid header with no export directory:
    no exports, and no failure recorded."""
    names, sink = _parse_pe(tmp_path, _pe_with_directory_count(0))
    assert (names, sink) == ([], [])


@pytest.mark.parametrize("magic", (0x0000, 0x0107, 0xFFFF))
def test_a_pe_with_an_unknown_optional_header_magic_is_recorded(
    tmp_path: Path, magic: int
) -> None:
    """A magic that is neither PE32 nor PE32+ is an unreadable header."""
    names, sink = _parse_pe(tmp_path, _pe_with_directory_count(16, magic=magic))
    assert names == []
    assert sink, f"magic 0x{magic:04x} read as a clean empty table"


def test_the_intact_pe_reads_its_three_exports_unrecorded(tmp_path: Path) -> None:
    names, sink = _parse_pe(tmp_path, PE_SEED.read_bytes())
    assert (len(names), sink) == (3, [])


@pytest.mark.parametrize(
    "graph",
    (
        {"nodes": [{}]},
        {"nodes": [None]},
        {"nodes": ["a node"]},
        {"edges": [{}]},
    ),
)
def test_a_malformed_lazily_decoded_surface_graph_is_a_typed_error(
    graph: dict[str, object],
) -> None:
    """The legacy graph decodes on first access, after the load returned; a
    missing key or a wrong-typed entry is still ``MalformedSnapshotError``."""
    snap = AbiSnapshot(library="lib.so", version="1")
    decode_surface_graph({"surface_graph": graph}, snap)
    with pytest.raises(MalformedSnapshotError, match="malformed surface graph"):
        _ = snap.surface_graph


# --------------------------------------------------------------------------
# Failures past DWARF detection are recorded on every reader path, including
# the ones a well-formed seed never reaches (an indexer or unit-walk raising,
# the snapshot builder's own open). Failures before detection (not ELF) are
# the export reader's, and record nothing here.
# --------------------------------------------------------------------------


def _recorded_debug(fn: object) -> list[str]:
    from abicheck.extract.parse_failures import recording_debug_info_read_failures

    with recording_debug_info_read_failures() as sink:
        fn()  # type: ignore[operator]
    return sink


@pytest.mark.parametrize(
    "module", ("abicheck.dwarf_unified", "abicheck.dwarf_metadata")
)
@pytest.mark.parametrize("error", (MemoryError(), ValueError("bad index")))
def test_a_dwarf_indexer_failure_after_detection_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: str, error: Exception
) -> None:
    """Both readers locate ``.debug_info``, then the indexer raises (a
    ``MemoryError`` from a bogus decompressed size included): recorded, and
    the reader still never raises."""
    import importlib

    so = _elf_with_debug(tmp_path)
    reader = importlib.import_module(module)

    def boom(_elf: object) -> object:
        raise error

    monkeypatch.setattr(reader, "open_indexed_dwarf_info", boom)
    sink = _recorded_debug(lambda: reader.parse_dwarf_metadata(so))
    assert sink, f"{module}: an indexer {type(error).__name__} read as no DWARF"


def test_a_unit_walk_failure_in_the_unified_reader_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``iter_CUs()`` itself raising (a malformed unit header before any
    per-unit guard) is recorded and reads as a failed extraction."""
    from abicheck import dwarf_unified
    from abicheck.model.dwarf_facts import AdvancedExtractionStatus

    so = _elf_with_debug(tmp_path)

    def boom(_session: object) -> object:
        raise ValueError("unit header past the section")

    monkeypatch.setattr(dwarf_unified, "parse_dwarf_from_session", boom)
    result: list[object] = []
    sink = _recorded_debug(lambda: result.append(dwarf_unified.parse_dwarf(so)))
    assert sink
    _meta, adv = result[0]  # type: ignore[misc]
    assert adv.extraction is AdvancedExtractionStatus.FAILED


def _snapshot_builder_records(path: Path) -> list[str]:
    from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder
    from abicheck.elf_metadata import parse_elf_metadata

    builder = _DwarfSnapshotBuilder(path, parse_elf_metadata(path))
    return _recorded_debug(builder.extract)


def test_the_snapshot_builder_records_corrupt_dwarf_on_its_own_open(
    tmp_path: Path,
) -> None:
    """The builder's open-it-here path (no shared session) records a corrupt
    abbreviation table like the metadata readers do."""
    from elftools.elf.elffile import ELFFile

    so = _elf_with_debug(tmp_path)
    data = bytearray(so.read_bytes())
    with so.open("rb") as fh:
        abbrev = ELFFile(fh).get_section_by_name(".debug_abbrev")
        assert abbrev is not None
        at = abbrev["sh_offset"]
    data[at] ^= 0xFF
    corrupt = tmp_path / "corrupt.so"
    corrupt.write_bytes(bytes(data))
    assert _snapshot_builder_records(so) == []
    assert _snapshot_builder_records(corrupt)


def test_the_snapshot_builder_leaves_a_non_elf_to_the_export_reader(
    tmp_path: Path,
) -> None:
    """A file that is not ELF fails before DWARF is located: nothing is
    recorded here (the export reader refuses it first)."""
    from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder
    from abicheck.model.elf_facts import ElfMetadata

    not_elf = tmp_path / "lib.so"
    not_elf.write_bytes(b"not an ELF file")
    builder = _DwarfSnapshotBuilder(not_elf, ElfMetadata())
    assert _recorded_debug(builder.extract) == []


def test_a_macho_trie_listing_nothing_against_a_defining_nlist_is_recorded(
    tmp_path: Path,
) -> None:
    """An export trie that parses to no exports while the symbol table defines
    some was misread, not emptied: a recorded failure. The same dylib with no
    trie at all keeps its nlist exports and records nothing."""
    from abicheck.macho_metadata import parse_macho_metadata
    from tests._macho_synthetic import N_EXT, N_SECT, MachoSymbol, write_dylib

    symbols = [MachoSymbol(n, N_EXT | N_SECT) for n in ("_add", "_sub")]
    empty_trie = write_dylib(tmp_path / "empty.dylib", symbols, export_trie=[])
    no_trie = write_dylib(tmp_path / "plain.dylib", symbols)
    for path, expect_recorded in ((empty_trie, True), (no_trie, False)):
        with recording_export_read_failures() as sink:
            meta = parse_macho_metadata(path)
        assert bool(sink) is expect_recorded, path.name
        if not expect_recorded:
            assert sorted(e.name for e in meta.exports) == ["add", "sub"]
