# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""An export table that could not be read is an error, never a clean verdict.

Bug class: a parser that swallows a failure returns whatever it managed to
read, and a library whose dynamic symbol table was unreadable looked exactly
like one exporting nothing. A dump built on that became an empty baseline, and
a compare against it read every export as "added" (removals hidden) or
reported ``COMPATIBLE`` / ``NO_CHANGE`` with exit 0.

The invariant is stated over a corruption matrix rather than the one reported
repro: each structural way the export table can be lost is applied to the OLD
side, the NEW side, and both, and the public CLI must refuse every one with
exit 1 and an error line -- never exit 0, never a compatibility verdict, never
a traceback. The oracle is the CLI's exit code and output, not the parser.

The intact pair is compared first, so a broken fixture cannot make the matrix
pass for the wrong reason.
"""

from __future__ import annotations

import io
import shutil
import struct
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from elftools.elf.elffile import ELFFile

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc required"),
]

_V1 = (
    "int keep_me(int x) { return x + 1; }\n"
    "int removed_fn(int x) { return x + 2; }\n"
    "int counter_v = 3;\n"
)
_V2 = _V1 + "int added_fn(int x) { return x * 2; }\n"

_SIDES = ("old", "new", "both")


def _build(tmp_path: Path, name: str, source: str) -> Path:
    src = tmp_path / f"{name}.c"
    src.write_text(source)
    so = tmp_path / f"lib{name}.so"
    built = subprocess.run(
        ["gcc", "-shared", "-fPIC", "-g", "-o", str(so), str(src)],
        capture_output=True,
        text=True,
    )
    # gcc is present (the skipif above) -- a failing build is a failure, with
    # its diagnostics attached, not a skip.
    assert built.returncode == 0, built.stderr
    return so


def _section(data: bytes, name: str):  # -> pyelftools section header
    elf = ELFFile(io.BytesIO(data))
    return next(s.header for s in elf.iter_sections() if s.name == name)


def _zero_section_table(data: bytes) -> bytes:
    shoff = struct.unpack_from("<Q", data, 0x28)[0]
    entsize, count = struct.unpack_from("<HH", data, 0x3A)
    out = bytearray(data)
    out[shoff : shoff + entsize * count] = b"\0" * (entsize * count)
    return bytes(out)


def _zero_section_offset(data: bytes) -> bytes:
    out = bytearray(data)
    struct.pack_into("<Q", out, 0x28, 0)
    return bytes(out)


def _zero_section_count(data: bytes) -> bytes:
    out = bytearray(data)
    struct.pack_into("<H", out, 0x3C, 0)
    return bytes(out)


def _bad_name_section_index(data: bytes) -> bytes:
    out = bytearray(data)
    struct.pack_into("<H", out, 0x3E, 0xFFFF)
    return bytes(out)


def _dynsym_filled(data: bytes) -> bytes:
    hdr = _section(data, ".dynsym")
    out = bytearray(data)
    out[hdr.sh_offset : hdr.sh_offset + hdr.sh_size] = b"\xff" * hdr.sh_size
    return bytes(out)


def _dynsym_names_outside_dynstr(data: bytes) -> bytes:
    """Every .dynsym entry's st_name points past the end of .dynstr."""
    hdr = _section(data, ".dynsym")
    out = bytearray(data)
    for entry in range(hdr.sh_offset, hdr.sh_offset + hdr.sh_size, 24):
        struct.pack_into("<I", out, entry, 0xFFFFFFF0)
    return bytes(out)


def _dynsym_truncated(data: bytes) -> bytes:
    hdr = _section(data, ".dynsym")
    return data[: hdr.sh_offset + hdr.sh_size // 2]


#: Each way the export table can be lost while the file still starts as an ELF.
MUTATIONS: dict[str, Callable[[bytes], bytes]] = {
    "section_table_zeroed": _zero_section_table,
    "section_table_offset_zero": _zero_section_offset,
    "section_count_zero": _zero_section_count,
    "section_name_index_bad": _bad_name_section_index,
    "dynsym_filled_with_ff": _dynsym_filled,
    "dynsym_names_outside_dynstr": _dynsym_names_outside_dynstr,
    "dynsym_truncated": _dynsym_truncated,
}


def _compare(old: Path, new: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "abicheck", "compare", str(old), str(new)],
        capture_output=True,
        text=True,
        timeout=300,
    )


def _dump(binary: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "abicheck", "dump", str(binary), "-o", str(out)],
        capture_output=True,
        text=True,
        timeout=300,
    )


def _assert_refused(proc: subprocess.CompletedProcess[str]) -> None:
    output = proc.stdout + proc.stderr
    assert "Traceback" not in output, output
    assert proc.returncode == 1, output
    assert "Error:" in output, output
    assert "COMPATIBLE" not in output and "NO_CHANGE" not in output, output


@pytest.fixture
def intact_pair(tmp_path: Path) -> tuple[bytes, bytes]:
    old = _build(tmp_path, "v1", _V1).read_bytes()
    new = _build(tmp_path, "v2", _V2).read_bytes()
    return old, new


def test_intact_pair_is_compatible(tmp_path: Path, intact_pair) -> None:
    """Oracle sanity: without corruption the same pair is a clean COMPATIBLE."""
    old, new = intact_pair
    (tmp_path / "old.so").write_bytes(old)
    (tmp_path / "new.so").write_bytes(new)
    proc = _compare(tmp_path / "old.so", tmp_path / "new.so")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "COMPATIBLE" in proc.stdout


@pytest.mark.parametrize("side", _SIDES)
@pytest.mark.parametrize("mutation", sorted(MUTATIONS))
def test_unreadable_export_table_is_refused_by_compare(
    tmp_path: Path, intact_pair, mutation: str, side: str
) -> None:
    old, new = intact_pair
    corrupt = MUTATIONS[mutation]
    old_path = tmp_path / "old.so"
    new_path = tmp_path / "new.so"
    old_path.write_bytes(corrupt(old) if side in ("old", "both") else old)
    new_path.write_bytes(corrupt(new) if side in ("new", "both") else new)
    _assert_refused(_compare(old_path, new_path))


@pytest.mark.parametrize("mutation", sorted(MUTATIONS))
def test_unreadable_export_table_writes_no_snapshot(
    tmp_path: Path, intact_pair, mutation: str
) -> None:
    """A dump of the same corruption fails and leaves no baseline behind."""
    old, _new = intact_pair
    binary = tmp_path / "corrupt.so"
    binary.write_bytes(MUTATIONS[mutation](old))
    out = tmp_path / "snapshot.json"
    proc = _dump(binary, out)
    _assert_refused(proc)
    assert not out.exists()
