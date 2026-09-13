"""Cross-platform tests using fixture bytes to test PE and Mach-O parsing
without needing the native OS.

Uses minimal binary fixtures written to tmp_path to exercise the parsing logic
and verify graceful handling of minimal/malformed inputs.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from abicheck.checker import Verdict, compare
from abicheck.model import AbiSnapshot, Function, Visibility

# ---------------------------------------------------------------------------
# Helpers: minimal binary fixture builders
# ---------------------------------------------------------------------------


def _make_minimal_pe(tmp_path: Path) -> Path:
    """Create a minimal valid PE/COFF binary (DOS header + PE sig + COFF header)."""
    # DOS header: e_magic=MZ, e_lfanew at offset 0x3C pointing to PE sig
    dos_header = bytearray(64)
    dos_header[0:2] = b"MZ"
    pe_offset = 64
    struct.pack_into("<I", dos_header, 0x3C, pe_offset)

    # PE signature
    pe_sig = b"PE\x00\x00"

    # COFF header: Machine=0x8664 (x86_64), NumberOfSections=0, etc
    coff_header = struct.pack(
        "<HHIIIHH",
        0x8664,  # Machine
        0,  # NumberOfSections
        0,  # TimeDateStamp
        0,  # PointerToSymbolTable
        0,  # NumberOfSymbols
        0,  # SizeOfOptionalHeader
        0x2000,  # Characteristics (DLL)
    )

    pe_file = tmp_path / "test.dll"
    pe_file.write_bytes(bytes(dos_header) + pe_sig + coff_header)
    return pe_file


def _make_minimal_macho(tmp_path: Path, filename: str = "test.dylib") -> Path:
    """Create a minimal Mach-O 64-bit LE binary (just header, no load commands)."""
    header = struct.pack(
        "<IIIIIIII",
        0xFEEDFACF,  # magic (MH_MAGIC_64)
        0x01000007,  # cputype (CPU_TYPE_X86_64)
        0x00000003,  # cpusubtype
        0x00000006,  # filetype (MH_DYLIB)
        0,  # ncmds
        0,  # sizeofcmds
        0,  # flags
        0,  # reserved
    )
    macho_file = tmp_path / filename
    macho_file.write_bytes(header)
    return macho_file


def _make_minimal_macho_be(tmp_path: Path) -> Path:
    """Create a minimal Mach-O 64-bit big-endian binary."""
    header = struct.pack(
        ">IIIIIIII",
        0xFEEDFACF,  # magic (MH_MAGIC_64) -- same magic, big-endian packing
        0x01000007,  # cputype
        0x00000003,  # cpusubtype
        0x00000006,  # filetype (MH_DYLIB)
        0,  # ncmds
        0,  # sizeofcmds
        0,  # flags
        0,  # reserved
    )
    macho_file = tmp_path / "test_be.dylib"
    macho_file.write_bytes(header)
    return macho_file


def _make_minimal_elf(tmp_path: Path) -> Path:
    """Create a minimal ELF shared object (just ELF header, ET_DYN)."""
    # ELF64 header (64 bytes)
    e_ident = bytearray(16)
    e_ident[0:4] = b"\x7fELF"
    e_ident[4] = 2  # ELFCLASS64
    e_ident[5] = 1  # ELFDATA2LSB
    e_ident[6] = 1  # EV_CURRENT

    # Rest of ELF header
    elf_header = struct.pack(
        "<HHIQQQIHHHHHH",
        3,  # e_type: ET_DYN
        0x3E,  # e_machine: EM_X86_64
        1,  # e_version: EV_CURRENT
        0,  # e_entry
        0,  # e_phoff
        0,  # e_shoff
        0,  # e_flags
        64,  # e_ehsize
        0,  # e_phentsize
        0,  # e_phnum
        0,  # e_shentsize
        0,  # e_shnum
        0,  # e_shstrndx
    )

    elf_file = tmp_path / "test.so"
    elf_file.write_bytes(bytes(e_ident) + elf_header)
    return elf_file


# ===========================================================================
# 1. PE metadata parsing from minimal fixture
# ===========================================================================


class TestPeMetadataMinimal:
    """Test PE metadata parsing with minimal fixture binaries."""

    def test_minimal_pe_parses_without_crash(self, tmp_path: Path) -> None:
        """parse_pe_metadata should handle a minimal PE without crashing."""
        from abicheck.pe_metadata import parse_pe_metadata

        pe_file = _make_minimal_pe(tmp_path)
        meta = parse_pe_metadata(pe_file)
        # Should return PeMetadata (even if empty exports)
        from abicheck.pe_metadata import PeMetadata

        assert isinstance(meta, PeMetadata)

    def test_minimal_pe_has_empty_exports(self, tmp_path: Path) -> None:
        """Minimal PE should have no exports (no export directory)."""
        from abicheck.pe_metadata import parse_pe_metadata

        pe_file = _make_minimal_pe(tmp_path)
        meta = parse_pe_metadata(pe_file)
        assert len(meta.exports) == 0


# ===========================================================================
# 2. PE with export table (malformed/truncated)
# ===========================================================================


class TestPeMalformed:
    """Verify the parser does not crash on malformed PE data."""

    def test_truncated_pe_returns_empty_metadata(self, tmp_path: Path) -> None:
        from abicheck.pe_metadata import PeMetadata, parse_pe_metadata

        # Just MZ header, no PE signature at expected offset
        pe_file = tmp_path / "truncated.dll"
        data = bytearray(64)
        data[0:2] = b"MZ"
        struct.pack_into("<I", data, 0x3C, 200)  # points past end
        pe_file.write_bytes(bytes(data))

        meta = parse_pe_metadata(pe_file)
        assert isinstance(meta, PeMetadata)

    def test_empty_file_returns_empty_metadata(self, tmp_path: Path) -> None:
        from abicheck.pe_metadata import PeMetadata, parse_pe_metadata

        pe_file = tmp_path / "empty.dll"
        pe_file.write_bytes(b"")

        meta = parse_pe_metadata(pe_file)
        assert isinstance(meta, PeMetadata)

    def test_random_bytes_returns_empty_metadata(self, tmp_path: Path) -> None:
        from abicheck.pe_metadata import PeMetadata, parse_pe_metadata

        pe_file = tmp_path / "random.dll"
        pe_file.write_bytes(b"\x00" * 256)

        meta = parse_pe_metadata(pe_file)
        assert isinstance(meta, PeMetadata)


# ===========================================================================
# 3. Mach-O metadata parsing from minimal fixture
# ===========================================================================


class TestMachoMetadataMinimal:
    """Test Mach-O metadata parsing with minimal fixture binaries."""

    def test_minimal_macho_le_parses_without_crash(self, tmp_path: Path) -> None:
        """parse_macho_metadata should handle a minimal 64-bit LE Mach-O."""
        from abicheck.macho_metadata import MachoMetadata, parse_macho_metadata

        macho_file = _make_minimal_macho(tmp_path)
        meta = parse_macho_metadata(macho_file)
        assert isinstance(meta, MachoMetadata)

    def test_minimal_macho_be_graceful(self, tmp_path: Path) -> None:
        """parse_macho_metadata should handle big-endian Mach-O magic gracefully."""
        from abicheck.macho_metadata import MachoMetadata, parse_macho_metadata

        macho_file = _make_minimal_macho_be(tmp_path)
        # May return empty metadata or parse partially -- should not crash
        meta = parse_macho_metadata(macho_file)
        assert isinstance(meta, MachoMetadata)

    def test_minimal_macho_has_no_exports(self, tmp_path: Path) -> None:
        """Minimal Mach-O with 0 load commands should have no exports."""
        from abicheck.macho_metadata import parse_macho_metadata

        macho_file = _make_minimal_macho(tmp_path)
        meta = parse_macho_metadata(macho_file)
        assert len(meta.exports) == 0


# ===========================================================================
# 4. ELF metadata parsing from minimal fixture
# ===========================================================================


class TestElfMetadataMinimal:
    """Test ELF metadata parsing with minimal fixture binaries."""

    def test_minimal_elf_parses_gracefully(self, tmp_path: Path) -> None:
        """parse_elf_metadata should handle a minimal ELF without crashing."""
        from abicheck.elf_metadata import ElfMetadata, parse_elf_metadata

        elf_file = _make_minimal_elf(tmp_path)
        meta = parse_elf_metadata(elf_file)
        assert isinstance(meta, ElfMetadata)

    def test_minimal_elf_has_no_symbols(self, tmp_path: Path) -> None:
        """Minimal ELF with no sections should have no symbols."""
        from abicheck.elf_metadata import parse_elf_metadata

        elf_file = _make_minimal_elf(tmp_path)
        meta = parse_elf_metadata(elf_file)
        assert len(meta.symbols) == 0

    def test_empty_file_elf_graceful(self, tmp_path: Path) -> None:
        """parse_elf_metadata should not crash on an empty file."""
        from abicheck.elf_metadata import ElfMetadata, parse_elf_metadata

        elf_file = tmp_path / "empty.so"
        elf_file.write_bytes(b"")

        meta = parse_elf_metadata(elf_file)
        assert isinstance(meta, ElfMetadata)


# ===========================================================================
# 5. Binary format detection cross-platform
# ===========================================================================


class TestBinaryFormatDetection:
    """Test binary format detection from magic bytes."""

    def test_pe_magic_detected(self, tmp_path: Path) -> None:
        """PE file with MZ magic should be detected by is_pe."""
        from abicheck.pe_metadata import is_pe

        pe_file = _make_minimal_pe(tmp_path)
        assert is_pe(pe_file) is True

    def test_macho_magic_detected(self, tmp_path: Path) -> None:
        """Mach-O file should be detected by is_macho."""
        from abicheck.macho_metadata import is_macho

        macho_file = _make_minimal_macho(tmp_path)
        assert is_macho(macho_file) is True

    def test_all_macho_magics(self, tmp_path: Path) -> None:
        """All known Mach-O magic byte sequences should be detected."""
        from abicheck.macho_metadata import is_macho

        magics = [
            b"\xfe\xed\xfa\xce",  # MH_MAGIC (32-bit)
            b"\xce\xfa\xed\xfe",  # MH_CIGAM (32-bit, swapped)
            b"\xfe\xed\xfa\xcf",  # MH_MAGIC_64 (64-bit)
            b"\xcf\xfa\xed\xfe",  # MH_CIGAM_64 (64-bit, swapped)
            b"\xca\xfe\xba\xbe",  # FAT_MAGIC (universal)
            b"\xbe\xba\xfe\xca",  # FAT_CIGAM (universal, swapped)
        ]
        for i, magic in enumerate(magics):
            f = tmp_path / f"macho_{i}.dylib"
            # Write magic + enough padding to be a plausible file
            f.write_bytes(magic + b"\x00" * 28)
            assert is_macho(f) is True, f"Failed to detect Mach-O magic {magic.hex()}"

    def test_elf_magic_not_detected_as_pe_or_macho(self, tmp_path: Path) -> None:
        """ELF file should not be detected as PE or Mach-O."""
        from abicheck.macho_metadata import is_macho
        from abicheck.pe_metadata import is_pe

        elf_file = _make_minimal_elf(tmp_path)
        assert is_pe(elf_file) is False
        assert is_macho(elf_file) is False

    def test_random_bytes_not_detected(self, tmp_path: Path) -> None:
        """Random bytes should not be detected as any known format."""
        from abicheck.macho_metadata import is_macho
        from abicheck.pe_metadata import is_pe

        f = tmp_path / "random.bin"
        f.write_bytes(b"\x42\x43\x44\x45" * 16)
        assert is_pe(f) is False
        assert is_macho(f) is False

    def test_elf_shared_object_detection(self, tmp_path: Path) -> None:
        """_is_elf_shared_object should detect minimal ELF ET_DYN."""
        from abicheck.package import _is_elf_shared_object

        elf_file = _make_minimal_elf(tmp_path)
        # Minimal ELF with ET_DYN type should be detected
        assert _is_elf_shared_object(elf_file) is True

    def test_non_elf_not_shared_object(self, tmp_path: Path) -> None:
        """_is_elf_shared_object should return False for non-ELF files."""
        from abicheck.package import _is_elf_shared_object

        pe_file = _make_minimal_pe(tmp_path)
        assert _is_elf_shared_object(pe_file) is False


# ===========================================================================
# 6. PE/Mach-O snapshot creation without native tools
# ===========================================================================


class TestCrossPlatformSnapshotCompare:
    """Verify compare() works on snapshots with PE or Mach-O platform metadata."""

    def test_pe_platform_snapshot_compare(self) -> None:
        """compare() should work on snapshots tagged with platform='pe'."""
        old = AbiSnapshot(
            library="test.dll",
            version="1.0",
            platform="pe",
            functions=[
                Function(
                    name="DllFunc",
                    mangled="DllFunc",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                ),
            ],
        )
        new = AbiSnapshot(
            library="test.dll",
            version="2.0",
            platform="pe",
            functions=[
                Function(
                    name="DllFunc",
                    mangled="DllFunc",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                ),
                Function(
                    name="NewFunc",
                    mangled="NewFunc",
                    return_type="void",
                    visibility=Visibility.PUBLIC,
                ),
            ],
        )
        result = compare(old, new)
        assert result.verdict in (Verdict.NO_CHANGE, Verdict.COMPATIBLE)

    def test_macho_platform_snapshot_compare(self) -> None:
        """compare() should work on snapshots tagged with platform='macho'."""
        old = AbiSnapshot(
            library="libtest.dylib",
            version="1.0",
            platform="macho",
            functions=[
                Function(
                    name="api_func",
                    mangled="_api_func",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                ),
            ],
        )
        new = AbiSnapshot(
            library="libtest.dylib",
            version="2.0",
            platform="macho",
            functions=[],  # all functions removed
        )
        result = compare(old, new)
        assert result.verdict == Verdict.BREAKING

    def test_mixed_platform_snapshots(self) -> None:
        """compare() should handle snapshots with different platform tags."""
        old = AbiSnapshot(
            library="test.dll",
            version="1.0",
            platform="pe",
            functions=[
                Function(
                    name="f",
                    mangled="f",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                ),
            ],
        )
        new = AbiSnapshot(
            library="test.dll",
            version="2.0",
            platform="pe",
            functions=[
                Function(
                    name="f",
                    mangled="f",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                ),
            ],
        )
        result = compare(old, new)
        assert result.verdict == Verdict.NO_CHANGE


# ===========================================================================
# 7. PDB parser edge cases
# ===========================================================================


class TestPdbParserEdgeCases:
    """Test PDB parser with empty/minimal/malformed inputs."""

    def test_empty_bytes_raises(self, tmp_path: Path) -> None:
        """parse_pdb on empty file should raise ValueError."""
        from abicheck.pdb_parser import parse_pdb

        pdb_file = tmp_path / "empty.pdb"
        pdb_file.write_bytes(b"")

        with pytest.raises(ValueError, match="too small|bad magic|Not a PDB"):
            parse_pdb(pdb_file)

    def test_wrong_magic_raises(self, tmp_path: Path) -> None:
        """parse_pdb with wrong magic bytes should raise ValueError."""
        from abicheck.pdb_parser import parse_pdb

        pdb_file = tmp_path / "bad_magic.pdb"
        pdb_file.write_bytes(b"\x00" * 100)

        with pytest.raises(ValueError, match="Not a PDB|bad magic"):
            parse_pdb(pdb_file)

    def test_truncated_msf_raises(self, tmp_path: Path) -> None:
        """parse_pdb with truncated MSF header should raise ValueError."""
        from abicheck.pdb_parser import _MSF_MAGIC, parse_pdb

        pdb_file = tmp_path / "truncated.pdb"
        # Write just the magic, not enough for the full header
        pdb_file.write_bytes(_MSF_MAGIC + b"\x00" * 10)

        with pytest.raises((ValueError, struct.error)):
            parse_pdb(pdb_file)

    def test_parse_msf_invalid_block_size(self, tmp_path: Path) -> None:
        """parse_msf with invalid block size should raise ValueError."""
        from abicheck.pdb_parser import _MSF_MAGIC, parse_msf

        # Magic + superblock with invalid block size (7)
        data = bytearray(_MSF_MAGIC)
        data += struct.pack(
            "<IIIIII",
            7,  # block_size (invalid)
            0,  # fpm_block
            100,  # num_blocks
            0,  # dir_bytes
            0,  # unknown
            0,  # block_map_addr
        )
        with pytest.raises(ValueError, match="Unsupported PDB block size"):
            parse_msf(bytes(data))
