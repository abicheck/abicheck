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
