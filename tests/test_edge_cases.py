"""Edge case tests for error handling, corrupt inputs, and boundary conditions.

These tests exercise unusual and extreme inputs that may occur in production
but are not typically covered by standard integration tests.
"""

from __future__ import annotations

import io
import json
import struct
import subprocess
import tarfile
import zipfile
from pathlib import Path
from unittest import mock

import pytest

from abicheck.checker import Change, DiffResult, Verdict, compare
from abicheck.checker_policy import ChangeKind
from abicheck.errors import ExtractionSecurityError
from abicheck.model import AbiSnapshot, Function
from abicheck.package import (
    TarExtractor,
    _is_elf_shared_object,
    _safe_zip_extract,
)
from abicheck.policy_file import PolicyFile
from abicheck.serialization import snapshot_from_dict, snapshot_to_json
from abicheck.suppression import Suppression

# ---------------------------------------------------------------------------
# 1. Corrupt ELF magic — _is_elf_shared_object
# ---------------------------------------------------------------------------


class TestCorruptElfMagic:
    """_is_elf_shared_object must return False for truncated/corrupt files."""

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.so"
        f.write_bytes(b"")
        assert _is_elf_shared_object(f) is False

    def test_partial_elf_magic_3_bytes(self, tmp_path: Path) -> None:
        f = tmp_path / "partial.so"
        f.write_bytes(b"\x7fEL")  # 3 bytes of ELF magic
        assert _is_elf_shared_object(f) is False

    def test_valid_elf_magic_truncated_header(self, tmp_path: Path) -> None:
        """Valid ELF magic but header too short for e_type read (needs offset 18)."""
        f = tmp_path / "truncated.so"
        # ELF magic (4) + EI_CLASS (1) + EI_DATA (1) = 6 bytes, but e_type at offset 16 needs 18 bytes
        f.write_bytes(b"\x7fELF\x02\x01" + b"\x00" * 4)  # only 10 bytes total
        assert _is_elf_shared_object(f) is False

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        f = tmp_path / "does_not_exist.so"
        assert _is_elf_shared_object(f) is False

    def test_non_elf_binary(self, tmp_path: Path) -> None:
        f = tmp_path / "pe_file.dll"
        f.write_bytes(b"MZ" + b"\x00" * 100)
        assert _is_elf_shared_object(f) is False


# ---------------------------------------------------------------------------
# 2. Archive extraction security
# ---------------------------------------------------------------------------


class TestArchiveExtractionSecurity:
    """Verify that archive extraction rejects unsafe members."""

    def test_tar_absolute_path(self, tmp_path: Path) -> None:
        """Tar member with absolute path must raise ExtractionSecurityError."""
        tar_path = tmp_path / "evil.tar"
        with tarfile.open(tar_path, "w") as tf:
            info = tarfile.TarInfo(name="/etc/passwd")
            info.size = 4
            tf.addfile(info, io.BytesIO(b"evil"))

        with pytest.raises(ExtractionSecurityError, match="absolute path"):
            TarExtractor._safe_extract(tar_path, tmp_path / "out")

    def test_tar_dotdot_traversal(self, tmp_path: Path) -> None:
        """Tar member with '..' traversal must raise ExtractionSecurityError."""
        tar_path = tmp_path / "traversal.tar"
        with tarfile.open(tar_path, "w") as tf:
            info = tarfile.TarInfo(name="foo/../../etc/passwd")
            info.size = 4
            tf.addfile(info, io.BytesIO(b"evil"))

        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError, match="path traversal"):
            TarExtractor._safe_extract(tar_path, out)

    def test_tar_symlink_escaping_root(self, tmp_path: Path) -> None:
        """Tar member with symlink escaping extraction root must be rejected."""
        tar_path = tmp_path / "symlink_escape.tar"
        with tarfile.open(tar_path, "w") as tf:
            info = tarfile.TarInfo(name="escape_link")
            info.type = tarfile.SYMTYPE
            info.linkname = "../../../../etc/passwd"
            tf.addfile(info)

        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError):
            TarExtractor._safe_extract(tar_path, out)

    def test_tar_device_file(self, tmp_path: Path) -> None:
        """Tar with character device entry must raise ExtractionSecurityError."""
        tar_path = tmp_path / "device.tar"
        with tarfile.open(tar_path, "w") as tf:
            info = tarfile.TarInfo(name="dev_null")
            info.type = tarfile.CHRTYPE
            info.devmajor = 1
            info.devminor = 3
            tf.addfile(info)

        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError, match="device or FIFO"):
            TarExtractor._safe_extract(tar_path, out)

    def test_tar_fifo(self, tmp_path: Path) -> None:
        """Tar with FIFO entry must raise ExtractionSecurityError."""
        tar_path = tmp_path / "fifo.tar"
        with tarfile.open(tar_path, "w") as tf:
            info = tarfile.TarInfo(name="my_fifo")
            info.type = tarfile.FIFOTYPE
            tf.addfile(info)

        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError, match="device or FIFO"):
            TarExtractor._safe_extract(tar_path, out)

    def test_zip_absolute_path(self, tmp_path: Path) -> None:
        """Zip member with absolute path must raise ExtractionSecurityError."""
        zip_path = tmp_path / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("/etc/passwd", "evil")

        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError, match="absolute path"):
            _safe_zip_extract(zip_path, out)

    def test_zip_path_traversal(self, tmp_path: Path) -> None:
        """Zip member with '..' path traversal must raise ExtractionSecurityError."""
        zip_path = tmp_path / "traversal.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("foo/../../etc/passwd", "evil")

        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError, match="path traversal"):
            _safe_zip_extract(zip_path, out)


# ---------------------------------------------------------------------------
# 3. Empty / minimal snapshots
# ---------------------------------------------------------------------------


class TestEmptySnapshots:
    """Edge cases with empty or minimal snapshots.

    Note: basic empty-snapshot comparison is covered in
    test_adversarial_inputs.py::TestEdgeCaseModelObjects.
    """

    def test_compare_empty_snapshot_with_types_only(self) -> None:
        """Snapshots with no functions but types should still yield NO_CHANGE."""
        from abicheck.model import RecordType

        old = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            types=[RecordType(name="T", kind="struct", size_bits=8)],
        )
        new = AbiSnapshot(
            library="libfoo.so",
            version="2.0",
            types=[RecordType(name="T", kind="struct", size_bits=8)],
        )
        result = compare(old, new)
        assert result.verdict == Verdict.NO_CHANGE


# ---------------------------------------------------------------------------
# 4. Serialization edge cases
# ---------------------------------------------------------------------------


class TestSerializationEdgeCases:
    """Edge cases for JSON serialization/deserialization."""

    def test_roundtrip_unicode_names(self) -> None:
        snap = AbiSnapshot(
            library="lib\u00e9ncod\u00e9.so",
            version="1.0",
            functions=[
                Function(
                    name="\u00fcber_func\u2603",
                    mangled="_Z\u00fcber",
                    return_type="int",
                ),
            ],
        )
        json_str = snapshot_to_json(snap)
        roundtripped = snapshot_from_dict(json.loads(json_str))
        assert roundtripped.library == snap.library
        assert roundtripped.functions[0].name == snap.functions[0].name

    def test_roundtrip_very_long_function_name(self) -> None:
        long_name = "f" * 10_000
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(name=long_name, mangled=long_name, return_type="void"),
            ],
        )
        json_str = snapshot_to_json(snap)
        roundtripped = snapshot_from_dict(json.loads(json_str))
        assert roundtripped.functions[0].name == long_name

    def test_load_corrupted_json(self) -> None:
        """Corrupted JSON must raise json.JSONDecodeError, not an internal error."""
        corrupted = '{"library": "libfoo.so", "version": "1.0", CORRUPT'
        with pytest.raises(json.JSONDecodeError):
            json.loads(corrupted)

    def test_load_json_missing_fields(self) -> None:
        """JSON with missing required fields should raise a meaningful error."""
        minimal = '{"library": "lib.so"}'
        with pytest.raises((KeyError, TypeError)):
            snapshot_from_dict(json.loads(minimal))


# ---------------------------------------------------------------------------
# 5. Suppression with edge patterns
# ---------------------------------------------------------------------------


class TestSuppressionEdgePatterns:
    """Edge cases for suppression pattern matching."""

    def test_wildcard_pattern_matches_everything(self) -> None:
        s = Suppression(symbol_pattern=".*")
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="any_symbol_at_all",
            description="removed",
        )
        assert s.matches(change) is True

    def test_pattern_with_special_regex_chars(self) -> None:
        """Pattern with special regex chars should be treated as literal regex."""
        s = Suppression(symbol_pattern=r"std::vector<int>\(\)")
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="std::vector<int>()",
            description="removed",
        )
        assert s.matches(change) is True

    def test_suppression_requires_at_least_one_selector(self) -> None:
        """Creating a Suppression with no selectors must raise ValueError."""
        with pytest.raises(ValueError, match="at least one"):
            Suppression()


# ---------------------------------------------------------------------------
# 6. Policy file edge cases
# ---------------------------------------------------------------------------


class TestPolicyFileEdgeCases:
    """Edge cases for policy file loading.

    Note: basic policy file edge cases (empty, unknown kind, invalid severity,
    non-mapping) are covered in test_adversarial_inputs.py::TestMalformedPolicyFiles.
    """

    def test_policy_with_multiple_valid_overrides(self, tmp_path: Path) -> None:
        """Multiple valid overrides are all loaded."""
        policy_path = tmp_path / "multi.yaml"
        policy_path.write_text(
            "base_policy: strict_abi\n"
            "overrides:\n"
            "  func_removed: ignore\n"
            "  func_added: ignore\n",
            encoding="utf-8",
        )
        pf = PolicyFile.load(policy_path)
        assert len(pf.overrides) == 2


# ---------------------------------------------------------------------------
# 7. Checker with extreme inputs
# ---------------------------------------------------------------------------


class TestCheckerExtremeInputs:
    """Checker must handle unusual function/type attributes without crashing."""

    def test_function_with_very_long_name(self) -> None:
        long_name = "x" * 50_000
        old = AbiSnapshot(
            library="lib.so",
            version="1.0",
            functions=[
                Function(name=long_name, mangled=long_name, return_type="int"),
            ],
        )
        new = AbiSnapshot(library="lib.so", version="2.0", functions=[])
        result = compare(old, new)
        assert any(c.symbol == long_name for c in result.changes)

    def test_function_with_empty_name(self) -> None:
        old = AbiSnapshot(
            library="lib.so",
            version="1.0",
            functions=[
                Function(name="", mangled="", return_type="int"),
            ],
        )
        new = AbiSnapshot(library="lib.so", version="2.0", functions=[])
        result = compare(old, new)
        assert isinstance(result, DiffResult)
        # The empty-named function was removed, so we expect a BREAKING verdict
        assert result.verdict == Verdict.BREAKING
        assert len(result.changes) == 1

    def test_type_with_size_zero(self) -> None:
        from abicheck.model import RecordType

        old = AbiSnapshot(
            library="lib.so",
            version="1.0",
            types=[
                RecordType(name="Empty", kind="struct", size_bits=0),
            ],
        )
        new = AbiSnapshot(
            library="lib.so",
            version="2.0",
            types=[
                RecordType(name="Empty", kind="struct", size_bits=8),
            ],
        )
        result = compare(old, new)
        assert isinstance(result, DiffResult)
        # Size changed from 0 to 8 — should detect a type change
        assert any(
            c.symbol == "Empty" or "Empty" in c.description for c in result.changes
        )

    def test_type_with_negative_alignment(self) -> None:
        from abicheck.model import RecordType

        old = AbiSnapshot(
            library="lib.so",
            version="1.0",
            types=[
                RecordType(name="Weird", kind="struct", alignment_bits=-1),
            ],
        )
        new = AbiSnapshot(
            library="lib.so",
            version="2.0",
            types=[
                RecordType(name="Weird", kind="struct", alignment_bits=32),
            ],
        )
        result = compare(old, new)
        assert isinstance(result, DiffResult)
        # Alignment changed from -1 to 32 — should detect a type change
        assert any(
            c.symbol == "Weird" or "Weird" in c.description for c in result.changes
        )


# ---------------------------------------------------------------------------
# 8. Subprocess timeout in package extraction
# ---------------------------------------------------------------------------


class TestSubprocessTimeout:
    """Mocked subprocess scenarios for extraction edge cases."""

    def test_deb_extraction_ar_timeout(self, tmp_path: Path) -> None:
        """Mock ar command timing out during deb extraction."""
        from abicheck.package import DebExtractor

        deb_path = tmp_path / "test.deb"
        deb_path.write_bytes(b"!<arch>\n" + b"\x00" * 100)

        out = tmp_path / "out"
        out.mkdir()

        with mock.patch("abicheck.package.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="ar", timeout=120)
            with pytest.raises(subprocess.TimeoutExpired):
                DebExtractor().extract(deb_path, out)

    def test_tar_extraction_of_corrupt_archive(self, tmp_path: Path) -> None:
        """Corrupt tar archive must raise a clean error, not crash."""
        tar_path = tmp_path / "corrupt.tar.gz"
        tar_path.write_bytes(b"\x1f\x8b\x08" + b"\x00" * 50)  # partial gzip header

        out = tmp_path / "out"
        out.mkdir()

        with pytest.raises((tarfile.TarError, EOFError, OSError)):
            TarExtractor._safe_extract(tar_path, out)


# ---------------------------------------------------------------------------
# 9. ELF metadata with missing sections
# ---------------------------------------------------------------------------


class TestElfMissingSections:
    """Mock scenarios with minimal/incomplete ELF files."""

    def test_elf_no_dynamic_section(self, tmp_path: Path) -> None:
        """A minimal valid ELF header (ET_DYN) but no actual sections.

        _is_elf_shared_object should still return True since it only checks
        the ELF header e_type field, not sections.
        """
        f = tmp_path / "no_dynamic.so"
        # Build a minimal little-endian 64-bit ELF with ET_DYN (e_type=3)
        elf = bytearray(64)
        elf[0:4] = b"\x7fELF"
        elf[4] = 2  # EI_CLASS = ELFCLASS64
        elf[5] = 1  # EI_DATA = ELFDATA2LSB
        elf[6] = 1  # EI_VERSION = EV_CURRENT
        # e_type at offset 16 (little-endian uint16 = 3 = ET_DYN)
        struct.pack_into("<H", elf, 16, 3)

        f.write_bytes(bytes(elf))
        assert _is_elf_shared_object(f) is True

    def test_elf_exec_not_shared(self, tmp_path: Path) -> None:
        """An ELF with ET_EXEC (type 2) should return False."""
        f = tmp_path / "executable"
        elf = bytearray(64)
        elf[0:4] = b"\x7fELF"
        elf[4] = 2  # ELFCLASS64
        elf[5] = 1  # ELFDATA2LSB
        elf[6] = 1  # EV_CURRENT
        struct.pack_into("<H", elf, 16, 2)  # ET_EXEC

        f.write_bytes(bytes(elf))
        assert _is_elf_shared_object(f) is False

    def test_elf_big_endian(self, tmp_path: Path) -> None:
        """Big-endian ELF with ET_DYN should be detected correctly."""
        f = tmp_path / "big_endian.so"
        elf = bytearray(64)
        elf[0:4] = b"\x7fELF"
        elf[4] = 2  # ELFCLASS64
        elf[5] = 2  # ELFDATA2MSB (big-endian)
        elf[6] = 1  # EV_CURRENT
        struct.pack_into(">H", elf, 16, 3)  # ET_DYN big-endian

        f.write_bytes(bytes(elf))
        assert _is_elf_shared_object(f) is True

    def test_elf_32bit(self, tmp_path: Path) -> None:
        """32-bit ELF with ET_DYN should also be detected."""
        f = tmp_path / "lib32.so"
        elf = bytearray(52)  # 32-bit ELF header is 52 bytes
        elf[0:4] = b"\x7fELF"
        elf[4] = 1  # ELFCLASS32
        elf[5] = 1  # ELFDATA2LSB
        elf[6] = 1  # EV_CURRENT
        struct.pack_into("<H", elf, 16, 3)  # ET_DYN

        f.write_bytes(bytes(elf))
        assert _is_elf_shared_object(f) is True
