"""The Mach-O dSYM DWARF reader (abicheck/extract/macho_dsym.py).

Two kinds of oracle: a bundle built here must read back the same DWARF
pyelftools reads from the ELF it was copied from (the real-DWARF equivalence,
which needs gcc and is marked ``integration``), and every refusal must be
absent evidence, never an empty DWARF surface. The Mach-O containers are
written by hand (struct layout from <mach-o/loader.h>), so these run on Linux.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import uuid as uuidlib
from pathlib import Path

import pytest
from elftools.elf.elffile import ELFFile

from abicheck.extract.macho_dsym import (
    DsymAbsent,
    DsymDwarf,
    macho_uuid,
    read_dsym_dwarf,
)

_MH_MAGIC_64 = 0xFEEDFACF
_MH_DSYM = 0xA
_LC_SEGMENT_64 = 0x19
_LC_UUID = 0x1B
_CPU_X86_64 = 0x01000007


def _macho(path: Path, *, filetype: int, uuid: str, sections=(), magic=_MH_MAGIC_64):
    """A thin 64-bit Mach-O: header, LC_SEGMENT_64(__DWARF), LC_UUID, section bytes."""
    seg_cmd_size = 72 + 80 * len(sections)
    cmds = bytearray()
    data_off = 32 + seg_cmd_size + 24  # header + segment + uuid command
    body = bytearray()
    sect_records = bytearray()
    for name, payload in sections:
        off = data_off + len(body)
        body += payload
        sect = (
            name.encode().ljust(16, b"\0")
            + b"__DWARF".ljust(16, b"\0")
            + struct.pack("<QQII", 0, len(payload), off, 0)
            + struct.pack("<IIII", 0, 0, 0, 0)
            + struct.pack("<II", 0, 0)
            + b""
        )
        sect_records += sect
    seg = struct.pack(
        "<II16sQQQQiiII",
        _LC_SEGMENT_64,
        seg_cmd_size,
        b"__DWARF".ljust(16, b"\0"),
        0,
        0,
        data_off,
        len(body),
        7,
        7,
        len(sections),
        0,
    )
    cmds += seg + sect_records
    cmds += struct.pack("<II", _LC_UUID, 24) + uuidlib.UUID(uuid).bytes
    header = struct.pack(
        "<IiiIIIII", magic, _CPU_X86_64, 3, filetype, 2, len(cmds), 0, 0
    )
    path.write_bytes(header + cmds + body)
    return path


def _dylib(path: Path, uuid: str) -> Path:
    """A minimal dylib carrying only the header and LC_UUID."""
    header = struct.pack("<IiiIIIII", _MH_MAGIC_64, _CPU_X86_64, 3, 6, 1, 24, 0, 0)
    path.write_bytes(
        header + struct.pack("<II", _LC_UUID, 24) + uuidlib.UUID(uuid).bytes
    )
    return path


def _sections_from_elf(elf_path: Path) -> list[tuple[str, bytes]]:
    """The ELF's ``.debug_*`` sections, named the Mach-O way (``__debug_*``)."""
    out: list[tuple[str, bytes]] = []
    with open(elf_path, "rb") as f:
        elf = ELFFile(f)
        for sec in elf.iter_sections():
            name = sec.name
            if name.startswith(".debug_"):
                out.append(("__debug_" + name[len(".debug_") :], sec.data()))
    return out


def test_uuid_is_read_from_the_load_command(tmp_path: Path) -> None:
    uid = "11111111-2222-3333-4444-555555555555"
    lib = _dylib(tmp_path / "libx.dylib", uid)
    assert macho_uuid(lib) == uid.upper()


def test_a_bundle_for_another_dylib_is_refused_not_read(tmp_path: Path) -> None:
    dylib = _dylib(tmp_path / "libx.dylib", "11111111-2222-3333-4444-555555555555")
    dsym = _macho(
        tmp_path / "libx.dSYM.bin",
        filetype=_MH_DSYM,
        uuid="99999999-2222-3333-4444-555555555555",
        sections=[("__debug_info", b"\x00" * 16)],
    )
    got = read_dsym_dwarf(dsym, dylib_uuid=macho_uuid(dylib))
    assert isinstance(got, DsymAbsent)
    assert "does not match" in got.reason


def test_a_missing_bundle_is_absent_evidence(tmp_path: Path) -> None:
    got = read_dsym_dwarf(
        tmp_path / "nope.dSYM", dylib_uuid="00000000-0000-0000-0000-000000000000"
    )
    assert isinstance(got, DsymAbsent)
    assert got.reason == "no dSYM file"


def test_a_bundle_without_a_dylib_uuid_cannot_be_matched(tmp_path: Path) -> None:
    dsym = _macho(
        tmp_path / "x.dSYM.bin",
        filetype=_MH_DSYM,
        uuid="11111111-2222-3333-4444-555555555555",
        sections=[("__debug_info", b"\x00" * 16)],
    )
    got = read_dsym_dwarf(dsym, dylib_uuid=None)
    assert isinstance(got, DsymAbsent)
    assert "cannot be matched" in got.reason


def test_a_fat_or_foreign_file_is_absent_evidence(tmp_path: Path) -> None:
    fat = tmp_path / "fat.dSYM.bin"
    fat.write_bytes(struct.pack(">II", 0xCAFEBABE, 2) + b"\0" * 32)
    got = read_dsym_dwarf(fat, dylib_uuid="11111111-2222-3333-4444-555555555555")
    assert isinstance(got, DsymAbsent)
    assert "not a thin 64-bit Mach-O" in got.reason


def test_a_matching_bundle_without_debug_info_is_absent(tmp_path: Path) -> None:
    uid = "11111111-2222-3333-4444-555555555555"
    dsym = _macho(tmp_path / "x.dSYM.bin", filetype=_MH_DSYM, uuid=uid, sections=[])
    got = read_dsym_dwarf(dsym, dylib_uuid=uid)
    assert isinstance(got, DsymAbsent)
    assert "no __debug_info" in got.reason


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc")
def test_a_bundle_reads_back_the_dwarf_pyelftools_reads_from_the_elf(
    tmp_path: Path,
) -> None:
    src = tmp_path / "lib.c"
    src.write_text(
        "struct Point { int x; int y; };\n"
        "int area(struct Point p) { return p.x * p.y; }\n"
    )
    elf = tmp_path / "libp.so"
    subprocess.run(
        ["gcc", "-g", "-shared", "-fPIC", "-o", str(elf), str(src)], check=True
    )
    uid = "11111111-2222-3333-4444-555555555555"
    dylib = _dylib(tmp_path / "libp.dylib", uid)
    dsym = _macho(
        tmp_path / "libp.dSYM.bin",
        filetype=_MH_DSYM,
        uuid=uid,
        sections=_sections_from_elf(elf),
    )

    got = read_dsym_dwarf(dsym, dylib_uuid=macho_uuid(dylib))
    assert isinstance(got, DsymDwarf)

    with open(elf, "rb") as f:
        expected = ELFFile(f).get_dwarf_info()

    def cu_names(dwarf) -> list[str]:
        names = []
        for cu in dwarf.iter_CUs():
            top = cu.get_top_DIE()
            names.append(top.attributes["DW_AT_name"].value.decode())
        return names

    assert cu_names(got.dwarf) == cu_names(expected)
    assert cu_names(got.dwarf)  # not vacuous: the compile unit is there


def _bundle_beside(lib: Path, dsym_bin: Path) -> None:
    """Place *dsym_bin* where ADR-021 step 5 looks: <lib>.dSYM/Contents/Resources/DWARF/<name>."""
    dwarf_dir = lib.parent / f"{lib.name}.dSYM" / "Contents" / "Resources" / "DWARF"
    dwarf_dir.mkdir(parents=True)
    (dwarf_dir / lib.name).write_bytes(dsym_bin.read_bytes())


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc")
def test_discovered_bundle_beside_the_library_supplies_its_dwarf(
    tmp_path: Path,
) -> None:
    from abicheck.dwarf_unified import _open_dwarf_session_with_status
    from abicheck.model.dwarf_facts import AdvancedExtractionStatus

    src = tmp_path / "lib.c"
    src.write_text("int twice(int x) { return 2 * x; }\n")
    elf = tmp_path / "libp.so"
    subprocess.run(
        ["gcc", "-g", "-shared", "-fPIC", "-o", str(elf), str(src)], check=True
    )
    uid = "11111111-2222-3333-4444-555555555555"
    lib = _dylib(tmp_path / "libp.dylib", uid)
    dsym_bin = _macho(
        tmp_path / "bundle.bin",
        filetype=_MH_DSYM,
        uuid=uid,
        sections=_sections_from_elf(elf),
    )
    _bundle_beside(lib, dsym_bin)

    session, status = _open_dwarf_session_with_status(lib)
    assert session is not None
    assert status is AdvancedExtractionStatus.COMPLETED
    session.close()


def test_a_library_with_no_bundle_is_not_collected(tmp_path: Path) -> None:
    from abicheck.dwarf_unified import _open_dwarf_session_with_status
    from abicheck.model.dwarf_facts import AdvancedExtractionStatus

    lib = _dylib(tmp_path / "libp.dylib", "11111111-2222-3333-4444-555555555555")
    session, status = _open_dwarf_session_with_status(lib)
    assert session is None
    assert status is AdvancedExtractionStatus.NOT_RUN


def test_a_mismatched_bundle_beside_the_library_is_not_used(tmp_path: Path) -> None:
    from abicheck.dwarf_unified import _open_dwarf_session_with_status
    from abicheck.model.dwarf_facts import AdvancedExtractionStatus

    lib = _dylib(tmp_path / "libp.dylib", "11111111-2222-3333-4444-555555555555")
    other = _macho(
        tmp_path / "other.bin",
        filetype=_MH_DSYM,
        uuid="99999999-2222-3333-4444-555555555555",
        sections=[("__debug_info", b"\x00" * 16)],
    )
    _bundle_beside(lib, other)
    session, status = _open_dwarf_session_with_status(lib)
    assert session is None
    assert status is AdvancedExtractionStatus.NOT_RUN


def _explicit_fixture(tmp_path: Path, *, uuid: str) -> tuple[Path, Path]:
    """A Mach-O library and an explicit bundle file (not beside it) with *uuid*."""
    lib = _dylib(tmp_path / "libq.dylib", "11111111-2222-3333-4444-555555555555")
    bundle = _macho(
        tmp_path / "explicit.dSYM.bin",
        filetype=_MH_DSYM,
        uuid=uuid,
        sections=[("__debug_info", b"\x00" * 16)],
    )
    return lib, bundle


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc")
def test_an_explicit_bundle_for_the_library_is_read(tmp_path: Path) -> None:
    from abicheck.extract.debug_dump import _resolve_debug_metadata

    src = tmp_path / "lib.c"
    src.write_text("int triple(int x) { return 3 * x; }\n")
    elf = tmp_path / "libq.so"
    subprocess.run(
        ["gcc", "-g", "-shared", "-fPIC", "-o", str(elf), str(src)], check=True
    )
    uid = "11111111-2222-3333-4444-555555555555"
    lib = _dylib(tmp_path / "libq.dylib", uid)
    # The bundle is named explicitly and lives somewhere other than beside the library.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    bundle = _macho(
        elsewhere / "bundle.bin",
        filetype=_MH_DSYM,
        uuid=uid,
        sections=_sections_from_elf(elf),
    )
    meta, adv = _resolve_debug_metadata(lib, None, dwarf_source=bundle)
    assert meta.has_dwarf is True
    assert adv.extraction.name == "COMPLETED"


def test_an_explicit_bundle_whose_dwarf_is_unreadable_is_reported_not_dropped(
    tmp_path: Path,
) -> None:
    from abicheck.errors import SnapshotError
    from abicheck.extract.debug_dump import _resolve_debug_metadata

    lib, bundle = _explicit_fixture(
        tmp_path, uuid="11111111-2222-3333-4444-555555555555"
    )
    with pytest.raises(SnapshotError, match="could not be read"):
        _resolve_debug_metadata(lib, None, dwarf_source=bundle)


def test_an_explicit_bundle_with_a_mismatched_uuid_is_a_reported_failure(
    tmp_path: Path,
) -> None:
    from abicheck.errors import SnapshotError
    from abicheck.extract.debug_dump import _resolve_debug_metadata

    lib, bundle = _explicit_fixture(
        tmp_path, uuid="99999999-2222-3333-4444-555555555555"
    )
    with pytest.raises(SnapshotError, match="does not match"):
        _resolve_debug_metadata(lib, None, dwarf_source=bundle)


def test_a_missing_explicit_bundle_is_a_reported_failure(tmp_path: Path) -> None:
    from abicheck.errors import SnapshotError
    from abicheck.extract.debug_dump import _resolve_debug_metadata

    lib = _dylib(tmp_path / "libq.dylib", "11111111-2222-3333-4444-555555555555")
    with pytest.raises(SnapshotError):
        _resolve_debug_metadata(lib, None, dwarf_source=tmp_path / "gone.dSYM")


def test_a_refused_adjacent_bundle_is_reported_in_the_dump(tmp_path: Path) -> None:
    """A stale dSYM beside the library is a visible failure, not silently ignored."""
    from abicheck.errors import SnapshotError
    from abicheck.extract.debug_dump import _resolve_debug_metadata

    lib = _dylib(tmp_path / "libp.dylib", "11111111-2222-3333-4444-555555555555")
    other = _macho(
        tmp_path / "other.bin",
        filetype=_MH_DSYM,
        uuid="99999999-2222-3333-4444-555555555555",
        sections=[("__debug_info", b"\x00" * 16)],
    )
    _bundle_beside(lib, other)
    with pytest.raises(SnapshotError, match="adjacent dSYM .* ignored"):
        _resolve_debug_metadata(lib, None)


@pytest.mark.parametrize(
    "magic",
    [
        b"\xca\xfe\xba\xbe",  # fat (universal), big-endian header
        b"\xce\xfa\xed\xfe",  # 32-bit Mach-O, little-endian
        b"\xfe\xed\xfa\xcf",  # 64-bit Mach-O, big-endian
    ],
)
def test_fat_and_non_64_bit_mach_o_are_absent_not_an_elf_failure(
    tmp_path: Path, magic: bytes
) -> None:
    from abicheck.dwarf_unified import _open_dwarf_session_with_status
    from abicheck.extract.debug_dump import _resolve_debug_metadata
    from abicheck.model.dwarf_facts import AdvancedExtractionStatus

    lib = tmp_path / "libfat.dylib"
    lib.write_bytes(magic + b"\x00" * 60)
    session, status = _open_dwarf_session_with_status(lib)
    assert session is None
    assert status is AdvancedExtractionStatus.NOT_RUN
    meta, _adv = _resolve_debug_metadata(lib, None)
    assert meta.has_dwarf is False


def test_an_explicit_bundle_for_a_fat_library_is_a_reported_failure(
    tmp_path: Path,
) -> None:
    from abicheck.errors import SnapshotError
    from abicheck.extract.debug_dump import _resolve_debug_metadata

    lib = tmp_path / "libfat.dylib"
    lib.write_bytes(b"\xca\xfe\xba\xbe" + b"\x00" * 60)
    bundle = _macho(
        tmp_path / "explicit.bin",
        filetype=_MH_DSYM,
        uuid="11111111-2222-3333-4444-555555555555",
        sections=[("__debug_info", b"\x00" * 16)],
    )
    with pytest.raises(SnapshotError, match="not a thin"):
        _resolve_debug_metadata(lib, None, dwarf_source=bundle)
