# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""A dump refuses a binary whose export table a parser noted as unread.

The class-wide, CLI-level matrix lives in
``tests/test_export_table_unreadable_matrix.py``; these pin the helper's
contract and the Mach-O dylib case (a load-command area with no export trie
and no symbol table) that has no gcc-free CLI route here.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from abicheck.errors import SnapshotError
from abicheck.extract.export_table_read import read_exports_or_fail
from abicheck.extract.parse_failures import note_export_read_failure
from abicheck.macho_metadata import parse_macho_metadata


def _minimal_dylib(path: Path) -> Path:
    """A 64-bit little-endian MH_DYLIB header with no load commands at all."""
    header = struct.pack(
        "<IIIIIIII",
        0xFEEDFACF,  # MH_MAGIC_64
        0x01000007,  # CPU_TYPE_X86_64
        0x00000003,  # cpusubtype
        0x00000006,  # MH_DYLIB
        0,  # ncmds
        0,  # sizeofcmds
        0,  # flags
        0,  # reserved
    )
    path.write_bytes(header)
    return path


def test_reader_result_is_returned_when_nothing_was_noted() -> None:
    assert read_exports_or_fail("lib.so", lambda _p: ["exported"]) == ["exported"]


def test_noted_failure_refuses_the_dump_with_the_reason() -> None:
    def parser(_path: Path) -> list[str]:
        note_export_read_failure("malformed .dynsym section: boom")
        return []  # the empty result a swallowed failure leaves behind

    with pytest.raises(SnapshotError, match="malformed .dynsym section: boom"):
        read_exports_or_fail("lib.so", parser)


def test_repeated_reasons_are_named_once() -> None:
    def parser(_path: Path) -> None:
        note_export_read_failure("same reason")
        note_export_read_failure("same reason")

    with pytest.raises(SnapshotError) as exc:
        read_exports_or_fail("lib.so", parser)
    assert str(exc.value).count("same reason") == 1


def test_reader_exception_propagates_unchanged() -> None:
    def parser(_path: Path) -> None:
        raise ValueError("not a snapshot error")

    with pytest.raises(ValueError, match="not a snapshot error"):
        read_exports_or_fail("lib.so", parser)


def test_dylib_without_export_trie_or_symbol_table_is_refused(tmp_path: Path) -> None:
    dylib = _minimal_dylib(tmp_path / "libempty.dylib")
    with pytest.raises(
        SnapshotError, match="neither an export trie nor a symbol table"
    ):
        read_exports_or_fail(dylib, parse_macho_metadata)


def test_dylib_whose_export_trie_exceeds_the_read_limit_is_refused(
    tmp_path: Path,
) -> None:
    """A trie the reader skips for size is unread, not an empty export list."""
    dyld_info = struct.pack(
        "<12I",
        0x80000022,  # LC_DYLD_INFO_ONLY
        48,  # cmdsize
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,  # rebase/bind/weak_bind/lazy_bind
        32 + 48,  # export_off: just past the load commands
        64 * 1024 * 1024 + 1,  # export_size: one byte over the limit
    )
    header = struct.pack(
        "<IIIIIIII", 0xFEEDFACF, 0x01000007, 0x00000003, 0x00000006, 1, 48, 0, 0
    )
    dylib = tmp_path / "libhuge.dylib"
    dylib.write_bytes(header + dyld_info)
    with pytest.raises(SnapshotError, match="exceeds the 64 MiB read limit"):
        read_exports_or_fail(dylib, parse_macho_metadata)
