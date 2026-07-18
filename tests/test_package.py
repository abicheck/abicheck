"""Tests for package extraction layer (ADR-006)."""
from __future__ import annotations

import io
import struct
import sys
import tarfile
import zipfile
from pathlib import Path
from unittest import mock

import pytest

from abicheck.errors import ExtractionSecurityError
from abicheck.package import (
    CondaExtractor,
    DebExtractor,
    DirExtractor,
    ExtractResult,
    PackageExtractor,
    RpmExtractor,
    TarExtractor,
    WheelExtractor,
    _implementation_name_from_wheel_filename,
    _implementation_version_from_wheel_filename,
    _is_elf_shared_object,
    _os_name_from_wheel_filename,
    _platform_machine_from_wheel_filename,
    _platform_python_implementation_from_wheel_filename,
    _platform_system_from_wheel_filename,
    _python_full_version_from_wheel_filename,
    _python_version_from_wheel_filename,
    _read_build_id,
    _safe_zip_extract,
    _sys_platform_from_wheel_filename,
    _validate_member_path,
    _validate_symlink_target,
    detect_extractor,
    discover_shared_libraries,
    is_package,
    parse_macos_deployment_target_floor,
    parse_manylinux_glibc_floor,
    parse_musllinux_floor,
    parse_numpy_requirement_from_metadata,
    parse_wheel_architecture_claim,
    parse_wheel_numpy_requirement,
    resolve_debug_info,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_minimal_elf_so(path: Path) -> None:
    """Write a minimal valid ELF shared object (ET_DYN) file.

    This is a stripped-down 64-bit little-endian ELF header with e_type=ET_DYN.
    Not a real executable, but enough for magic/type detection.
    """
    # ELF header: 64 bytes for 64-bit
    e_ident = b"\x7fELF"  # magic
    e_ident += b"\x02"  # EI_CLASS: 64-bit
    e_ident += b"\x01"  # EI_DATA: little-endian
    e_ident += b"\x01"  # EI_VERSION: current
    e_ident += b"\x00" * 9  # padding
    e_type = struct.pack("<H", 3)  # ET_DYN
    e_machine = struct.pack("<H", 0x3E)  # EM_X86_64
    e_version = struct.pack("<I", 1)
    # Rest of header (entry, phoff, shoff, flags, etc.)
    rest = b"\x00" * (64 - 16 - 2 - 2 - 4)
    path.write_bytes(e_ident + e_type + e_machine + e_version + rest)


def _make_minimal_elf_exec(path: Path) -> None:
    """Write a minimal ELF executable (ET_EXEC, not ET_DYN)."""
    e_ident = b"\x7fELF\x02\x01\x01" + b"\x00" * 9
    e_type = struct.pack("<H", 2)  # ET_EXEC
    rest = b"\x00" * (64 - 16 - 2)
    path.write_bytes(e_ident + e_type + rest)


def _make_minimal_elf_dso_with_interp(path: Path) -> None:
    """Write a minimal ET_DYN ELF with a PT_INTERP program header."""
    elf = bytearray(64)
    elf[0:4] = b"\x7fELF"
    elf[4] = 2  # EI_CLASS = ELFCLASS64
    elf[5] = 1  # EI_DATA = ELFDATA2LSB
    elf[6] = 1  # EI_VERSION = EV_CURRENT
    struct.pack_into("<H", elf, 16, 3)  # e_type = ET_DYN
    struct.pack_into("<H", elf, 18, 0x3E)  # e_machine = EM_X86_64
    struct.pack_into("<I", elf, 20, 1)  # e_version
    struct.pack_into("<Q", elf, 32, 64)  # e_phoff
    struct.pack_into("<H", elf, 54, 56)  # e_phentsize
    struct.pack_into("<H", elf, 56, 1)  # e_phnum

    phdr = bytearray(56)
    struct.pack_into("<I", phdr, 0, 3)  # p_type = PT_INTERP
    struct.pack_into("<I", phdr, 4, 4)  # p_flags = PF_R
    struct.pack_into("<Q", phdr, 48, 1)  # p_align
    path.write_bytes(bytes(elf) + bytes(phdr))


def _make_malformed_elf_dso_with_missing_phdr(path: Path) -> None:
    """Write ET_DYN ELF header that advertises a missing program header."""
    elf = bytearray(64)
    elf[0:4] = b"\x7fELF"
    elf[4] = 2  # EI_CLASS = ELFCLASS64
    elf[5] = 1  # EI_DATA = ELFDATA2LSB
    elf[6] = 1  # EI_VERSION = EV_CURRENT
    struct.pack_into("<H", elf, 16, 3)  # e_type = ET_DYN
    struct.pack_into("<H", elf, 18, 0x3E)  # e_machine = EM_X86_64
    struct.pack_into("<I", elf, 20, 1)  # e_version
    struct.pack_into("<Q", elf, 32, 64)  # e_phoff
    struct.pack_into("<H", elf, 54, 56)  # e_phentsize
    struct.pack_into("<H", elf, 56, 1)  # e_phnum
    path.write_bytes(bytes(elf))


def _make_malformed_elf_dso_with_invalid_phentsize(path: Path) -> None:
    """Write ET_DYN ELF with an invalid program-header entry size."""
    elf = bytearray(64)
    elf[0:4] = b"\x7fELF"
    elf[4] = 2  # EI_CLASS = ELFCLASS64
    elf[5] = 1  # EI_DATA = ELFDATA2LSB
    elf[6] = 1  # EI_VERSION = EV_CURRENT
    struct.pack_into("<H", elf, 16, 3)  # e_type = ET_DYN
    struct.pack_into("<H", elf, 18, 0x3E)  # e_machine = EM_X86_64
    struct.pack_into("<I", elf, 20, 1)  # e_version
    struct.pack_into("<Q", elf, 32, 64)  # e_phoff
    struct.pack_into("<H", elf, 54, 0)  # e_phentsize = invalid
    struct.pack_into("<H", elf, 56, 1)  # e_phnum
    path.write_bytes(bytes(elf) + b"\x00" * 4)


def _make_tar(archive_path: Path, files: dict[str, bytes]) -> None:
    """Create a tar.gz archive with given file contents."""
    with tarfile.open(archive_path, "w:gz") as tf:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))


def _make_wheel(archive_path: Path, files: dict[str, bytes]) -> None:
    """Create a zip archive (used for .whl and .conda)."""
    with zipfile.ZipFile(archive_path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def _make_conda_legacy(archive_path: Path, files: dict[str, bytes]) -> None:
    """Create a legacy conda .tar.bz2 package with info/ directory."""
    files_with_info = {"info/index.json": b'{"name":"test"}', **files}
    with tarfile.open(archive_path, "w:bz2") as tf:
        for name, content in files_with_info.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))


# ── Security validation tests ────────────────────────────────────────────────


class TestValidateMemberPath:
    def test_safe_path(self, tmp_path: Path) -> None:
        result = _validate_member_path("usr/lib/libfoo.so", tmp_path)
        assert result == (tmp_path / "usr/lib/libfoo.so").resolve()

    def test_absolute_path_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ExtractionSecurityError, match="absolute path"):
            _validate_member_path("/etc/passwd", tmp_path)

    def test_traversal_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ExtractionSecurityError, match="path traversal"):
            _validate_member_path("usr/../../etc/passwd", tmp_path)

    def test_traversal_at_start(self, tmp_path: Path) -> None:
        with pytest.raises(ExtractionSecurityError, match="path traversal"):
            _validate_member_path("../etc/passwd", tmp_path)

    def test_simple_filename(self, tmp_path: Path) -> None:
        result = _validate_member_path("libfoo.so", tmp_path)
        assert result == (tmp_path / "libfoo.so").resolve()

    def test_nested_safe_path(self, tmp_path: Path) -> None:
        result = _validate_member_path("a/b/c/d.so", tmp_path)
        assert result == (tmp_path / "a/b/c/d.so").resolve()


class TestValidateSymlinkTarget:
    def test_safe_symlink(self, tmp_path: Path) -> None:
        # Create the directory so resolve works
        (tmp_path / "usr" / "lib").mkdir(parents=True)
        (tmp_path / "usr" / "lib" / "libfoo.so.1").touch()
        _validate_symlink_target(
            "usr/lib/libfoo.so", "libfoo.so.1", tmp_path
        )

    def test_escaping_symlink_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "usr" / "lib").mkdir(parents=True)
        with pytest.raises(ExtractionSecurityError, match="symlink target"):
            _validate_symlink_target(
                "usr/lib/evil", "../../../../etc/passwd", tmp_path
            )


# ── Format detection tests ──────────────────────────────────────────────────


class TestIsPackage:
    def test_rpm_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "libfoo.rpm"
        f.write_bytes(b"\xed\xab\xee\xdb" + b"\x00" * 100)
        assert is_package(f) is True

    def test_deb_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "libfoo.deb"
        f.write_bytes(b"!<arch>\n" + b"\x00" * 100)
        assert is_package(f) is True

    def test_tar_gz_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "sdk.tar.gz"
        _make_tar(f, {"README": b"hello"})
        assert is_package(f) is True

    def test_tgz_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "sdk.tgz"
        _make_tar(f, {"README": b"hello"})
        assert is_package(f) is True

    def test_directory_not_package(self, tmp_path: Path) -> None:
        assert is_package(tmp_path) is False

    def test_so_file_not_package(self, tmp_path: Path) -> None:
        f = tmp_path / "libfoo.so"
        _make_minimal_elf_so(f)
        assert is_package(f) is False

    def test_json_not_package(self, tmp_path: Path) -> None:
        f = tmp_path / "snapshot.json"
        f.write_text("{}")
        assert is_package(f) is False

    def test_rpm_by_magic(self, tmp_path: Path) -> None:
        f = tmp_path / "unknown_file"
        f.write_bytes(b"\xed\xab\xee\xdb" + b"\x00" * 100)
        assert is_package(f) is True

    def test_deb_by_magic(self, tmp_path: Path) -> None:
        f = tmp_path / "unknown_file"
        f.write_bytes(b"!<arch>\n" + b"\x00" * 100)
        assert is_package(f) is True

    def test_conda_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "numpy-1.26.conda"
        _make_wheel(f, {"lib/libfoo.so": b"elf"})
        assert is_package(f) is True

    def test_whl_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "numpy-1.26-cp311-linux_x86_64.whl"
        _make_wheel(f, {"numpy/core/_multiarray_umath.so": b"elf"})
        assert is_package(f) is True


class TestDetectExtractor:
    def test_directory(self, tmp_path: Path) -> None:
        ext = detect_extractor(tmp_path)
        assert isinstance(ext, DirExtractor)

    def test_tar_gz(self, tmp_path: Path) -> None:
        f = tmp_path / "test.tar.gz"
        _make_tar(f, {"README": b"hello"})
        ext = detect_extractor(f)
        assert isinstance(ext, TarExtractor)

    def test_tar_xz(self, tmp_path: Path) -> None:
        f = tmp_path / "test.tar.xz"
        f.write_bytes(b"\xfd7zXZ\x00" + b"\x00" * 100)
        ext = detect_extractor(f)
        assert isinstance(ext, TarExtractor)

    def test_rpm(self, tmp_path: Path) -> None:
        f = tmp_path / "test.rpm"
        f.write_bytes(b"\xed\xab\xee\xdb" + b"\x00" * 100)
        ext = detect_extractor(f)
        assert isinstance(ext, RpmExtractor)

    def test_deb(self, tmp_path: Path) -> None:
        f = tmp_path / "test.deb"
        f.write_bytes(b"!<arch>\n" + b"\x00" * 100)
        ext = detect_extractor(f)
        assert isinstance(ext, DebExtractor)

    def test_conda(self, tmp_path: Path) -> None:
        f = tmp_path / "test.conda"
        _make_wheel(f, {"metadata.json": b"{}"})
        ext = detect_extractor(f)
        assert isinstance(ext, CondaExtractor)

    def test_whl(self, tmp_path: Path) -> None:
        f = tmp_path / "test-1.0-py3-none-any.whl"
        _make_wheel(f, {"test/__init__.py": b""})
        ext = detect_extractor(f)
        assert isinstance(ext, WheelExtractor)

    def test_conda_legacy_tar_bz2(self, tmp_path: Path) -> None:
        f = tmp_path / "numpy-1.26-h123-0.tar.bz2"
        _make_conda_legacy(f, {"lib/libopenblas.so": b"elf"})
        ext = detect_extractor(f)
        assert isinstance(ext, CondaExtractor)

    def test_unknown(self, tmp_path: Path) -> None:
        f = tmp_path / "test.xyz"
        f.write_bytes(b"unknown format")
        ext = detect_extractor(f)
        assert ext is None


# ── TarExtractor tests ──────────────────────────────────────────────────────


class TestTarExtractor:
    def test_basic_extraction(self, tmp_path: Path) -> None:
        archive = tmp_path / "test.tar.gz"
        _make_tar(archive, {
            "usr/lib/libfoo.so": b"\x7fELF fake",
            "usr/lib/libbar.so": b"\x7fELF fake",
        })
        out = tmp_path / "output"
        out.mkdir()
        ext = TarExtractor()
        result = ext.extract(archive, out)
        assert result.lib_dir == out
        assert (out / "usr/lib/libfoo.so").exists()
        assert (out / "usr/lib/libbar.so").exists()

    def test_detect_tar_gz(self, tmp_path: Path) -> None:
        f = tmp_path / "test.tar.gz"
        _make_tar(f, {"a": b""})
        assert TarExtractor().detect(f)

    def test_detect_tar_xz(self, tmp_path: Path) -> None:
        f = tmp_path / "test.tar.xz"
        f.touch()
        assert TarExtractor().detect(f)

    def test_detect_tgz(self, tmp_path: Path) -> None:
        f = tmp_path / "test.tgz"
        f.touch()
        assert TarExtractor().detect(f)

    def test_detect_plain_tar(self, tmp_path: Path) -> None:
        f = tmp_path / "test.tar"
        f.touch()
        assert TarExtractor().detect(f)

    def test_not_detect_so(self, tmp_path: Path) -> None:
        f = tmp_path / "libfoo.so"
        f.touch()
        assert not TarExtractor().detect(f)

    def test_path_traversal_rejected(self, tmp_path: Path) -> None:
        archive = tmp_path / "evil.tar.gz"
        import io
        with tarfile.open(archive, "w:gz") as tf:
            info = tarfile.TarInfo(name="../../../etc/passwd")
            info.size = 4
            tf.addfile(info, io.BytesIO(b"evil"))

        out = tmp_path / "output"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError, match="path traversal"):
            TarExtractor().extract(archive, out)

    def test_absolute_path_rejected(self, tmp_path: Path) -> None:
        archive = tmp_path / "evil.tar.gz"
        import io
        with tarfile.open(archive, "w:gz") as tf:
            info = tarfile.TarInfo(name="/etc/passwd")
            info.size = 4
            tf.addfile(info, io.BytesIO(b"evil"))

        out = tmp_path / "output"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError, match="absolute path"):
            TarExtractor().extract(archive, out)


# ── DirExtractor tests ──────────────────────────────────────────────────────


class TestDirExtractor:
    def test_detect_directory(self, tmp_path: Path) -> None:
        assert DirExtractor().detect(tmp_path)

    def test_detect_file_false(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.touch()
        assert not DirExtractor().detect(f)

    def test_passthrough(self, tmp_path: Path) -> None:
        result = DirExtractor().extract(tmp_path, tmp_path / "unused")
        assert result.lib_dir == tmp_path


# ── RpmExtractor tests ──────────────────────────────────────────────────────


class TestRpmExtractor:
    def test_detect_rpm_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "test.rpm"
        f.write_bytes(b"\xed\xab\xee\xdb" + b"\x00" * 100)
        assert RpmExtractor().detect(f)

    def test_detect_rpm_magic(self, tmp_path: Path) -> None:
        f = tmp_path / "noext"
        f.write_bytes(b"\xed\xab\xee\xdb" + b"\x00" * 100)
        assert RpmExtractor().detect(f)

    def test_detect_non_rpm(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_bytes(b"not an rpm")
        assert not RpmExtractor().detect(f)


# ── DebExtractor tests ──────────────────────────────────────────────────────


class TestDebExtractor:
    def test_detect_deb_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "test.deb"
        f.write_bytes(b"!<arch>\n" + b"\x00" * 100)
        assert DebExtractor().detect(f)

    def test_detect_deb_magic(self, tmp_path: Path) -> None:
        f = tmp_path / "noext"
        f.write_bytes(b"!<arch>\n" + b"\x00" * 100)
        assert DebExtractor().detect(f)

    def test_detect_non_deb(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_bytes(b"not a deb")
        assert not DebExtractor().detect(f)


# ── ELF shared object detection ─────────────────────────────────────────────


class TestIsElfSharedObject:
    def test_shared_object(self, tmp_path: Path) -> None:
        f = tmp_path / "libfoo.so"
        _make_minimal_elf_so(f)
        assert _is_elf_shared_object(f) is True

    def test_executable(self, tmp_path: Path) -> None:
        f = tmp_path / "prog"
        _make_minimal_elf_exec(f)
        assert _is_elf_shared_object(f) is False

    def test_so_named_dso_with_interp(self, tmp_path: Path) -> None:
        f = tmp_path / "libcap.so.2.66"
        _make_minimal_elf_dso_with_interp(f)
        assert _is_elf_shared_object(f) is True

    def test_alphanumeric_versioned_lib_dso_with_interp(self, tmp_path: Path) -> None:
        for name in ("libtidy.so.5deb1.6.0", "libstemmer.so.0d.0.0"):
            f = tmp_path / name
            _make_minimal_elf_dso_with_interp(f)
            assert _is_elf_shared_object(f) is True

    def test_pie_like_name_with_interp(self, tmp_path: Path) -> None:
        f = tmp_path / "capsh"
        _make_minimal_elf_dso_with_interp(f)
        assert _is_elf_shared_object(f) is False

    def test_pie_name_containing_so_without_boundary_is_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "app.solver"
        _make_minimal_elf_dso_with_interp(f)
        assert _is_elf_shared_object(f) is False

    def test_pie_name_with_non_versioned_so_suffix_is_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "app.so.tmp"
        _make_minimal_elf_dso_with_interp(f)
        assert _is_elf_shared_object(f) is False

    def test_pie_name_with_partial_version_so_suffix_is_rejected(self, tmp_path: Path) -> None:
        for name in ("app.so.1.tmp", "app.so.1a"):
            f = tmp_path / name
            _make_minimal_elf_dso_with_interp(f)
            assert _is_elf_shared_object(f) is False

    def test_malformed_program_header_table_is_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "libbad.so"
        _make_malformed_elf_dso_with_missing_phdr(f)
        assert _is_elf_shared_object(f) is False

    def test_invalid_program_header_entry_size_is_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "libbad.so"
        _make_malformed_elf_dso_with_invalid_phentsize(f)
        assert _is_elf_shared_object(f) is False

    def test_non_elf(self, tmp_path: Path) -> None:
        f = tmp_path / "text.txt"
        f.write_text("hello")
        assert _is_elf_shared_object(f) is False

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty"
        f.touch()
        assert _is_elf_shared_object(f) is False


# ── Binary discovery tests ──────────────────────────────────────────────────


class TestDiscoverSharedLibraries:
    def test_finds_so_in_lib(self, tmp_path: Path) -> None:
        lib_dir = tmp_path / "usr" / "lib64"
        lib_dir.mkdir(parents=True)
        _make_minimal_elf_so(lib_dir / "libfoo.so.1.0")
        _make_minimal_elf_so(lib_dir / "libbar.so.2.0")

        result = discover_shared_libraries(tmp_path)
        names = [p.name for p in result]
        assert "libfoo.so.1.0" in names
        assert "libbar.so.2.0" in names

    def test_skips_executables(self, tmp_path: Path) -> None:
        lib_dir = tmp_path / "usr" / "lib"
        lib_dir.mkdir(parents=True)
        _make_minimal_elf_so(lib_dir / "libfoo.so")
        _make_minimal_elf_exec(lib_dir / "myapp")

        result = discover_shared_libraries(tmp_path)
        names = [p.name for p in result]
        assert "libfoo.so" in names
        assert "myapp" not in names

    def test_finds_so_named_dso_with_interp(self, tmp_path: Path) -> None:
        lib_dir = tmp_path / "usr" / "lib"
        lib_dir.mkdir(parents=True)
        _make_minimal_elf_dso_with_interp(lib_dir / "libcap.so.2.66")
        _make_minimal_elf_dso_with_interp(lib_dir / "capsh")

        result = discover_shared_libraries(tmp_path)
        names = [p.name for p in result]
        assert "libcap.so.2.66" in names
        assert "capsh" not in names

    def test_skips_private_by_default(self, tmp_path: Path) -> None:
        # A DSO in a non-standard path without .so in name
        priv_dir = tmp_path / "opt" / "vendor" / "plugins"
        priv_dir.mkdir(parents=True)
        _make_minimal_elf_so(priv_dir / "myplugin.bin")

        result = discover_shared_libraries(tmp_path)
        assert len(result) == 0

    def test_includes_private_with_flag(self, tmp_path: Path) -> None:
        priv_dir = tmp_path / "opt" / "vendor" / "plugins"
        priv_dir.mkdir(parents=True)
        _make_minimal_elf_so(priv_dir / "myplugin.bin")

        result = discover_shared_libraries(tmp_path, include_private=True)
        names = [p.name for p in result]
        assert "myplugin.bin" in names

    def test_finds_so_in_flat_layout(self, tmp_path: Path) -> None:
        """DSOs with .so in name should be found even in non-standard paths."""
        _make_minimal_elf_so(tmp_path / "libfoo.so")
        result = discover_shared_libraries(tmp_path)
        assert len(result) == 1
        assert result[0].name == "libfoo.so"

    def test_finds_alphanumeric_versioned_libs_in_flat_layout(
        self, tmp_path: Path
    ) -> None:
        _make_minimal_elf_so(tmp_path / "libtidy.so.5deb1.6.0")
        _make_minimal_elf_so(tmp_path / "libstemmer.so.0d.0.0")
        result = discover_shared_libraries(tmp_path)
        names = [p.name for p in result]
        assert "libtidy.so.5deb1.6.0" in names
        assert "libstemmer.so.0d.0.0" in names

    def test_skips_flat_layout_name_containing_so_without_boundary(
        self, tmp_path: Path
    ) -> None:
        _make_minimal_elf_so(tmp_path / "tool.something")
        result = discover_shared_libraries(tmp_path)
        assert result == []

    def test_skips_flat_layout_non_versioned_so_suffix(self, tmp_path: Path) -> None:
        _make_minimal_elf_so(tmp_path / "tool.so.tmp")
        result = discover_shared_libraries(tmp_path)
        assert result == []

    def test_skips_flat_layout_partial_version_so_suffix(self, tmp_path: Path) -> None:
        _make_minimal_elf_so(tmp_path / "tool.so.1a")
        _make_minimal_elf_so(tmp_path / "tool.so.1.tmp")
        result = discover_shared_libraries(tmp_path)
        assert result == []

    def test_empty_directory(self, tmp_path: Path) -> None:
        result = discover_shared_libraries(tmp_path)
        assert result == []

    def test_sorted_by_name(self, tmp_path: Path) -> None:
        lib_dir = tmp_path / "usr" / "lib"
        lib_dir.mkdir(parents=True)
        _make_minimal_elf_so(lib_dir / "libzoo.so")
        _make_minimal_elf_so(lib_dir / "libalpha.so")
        _make_minimal_elf_so(lib_dir / "libmid.so")

        result = discover_shared_libraries(tmp_path)
        names = [p.name for p in result]
        assert names == sorted(names)

    def test_skips_non_elf_files(self, tmp_path: Path) -> None:
        lib_dir = tmp_path / "usr" / "lib"
        lib_dir.mkdir(parents=True)
        (lib_dir / "libfoo.so").write_text("not elf")
        (lib_dir / "readme.txt").write_text("hello")

        result = discover_shared_libraries(tmp_path)
        assert len(result) == 0


# ── CLI integration tests (tar-based, no system deps) ───────────────────────


class TestCompareReleaseTarPackages:
    """Integration tests using tar archives (no rpm2cpio/ar needed)."""

    def _make_snapshot_tar(
        self, tmp_path: Path, name: str, snapshot_json: str,
    ) -> Path:
        """Create a tar.gz containing a JSON snapshot in usr/lib/."""
        archive = tmp_path / name
        import io
        with tarfile.open(archive, "w:gz") as tf:
            data = snapshot_json.encode()
            info = tarfile.TarInfo(name="libfoo.so.json")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        return archive

    def test_tar_packages_accepted(self, tmp_path: Path) -> None:
        """Verify that compare-release accepts tar.gz inputs."""
        from click.testing import CliRunner

        from abicheck.cli import main
        from abicheck.model import AbiSnapshot, Function, Visibility
        from abicheck.serialization import snapshot_to_json

        snap_old = AbiSnapshot(
            library="libfoo.so", version="1.0",
            functions=[Function(name="foo", mangled="_Z3foov",
                                return_type="int", visibility=Visibility.PUBLIC)],
        )
        snap_new = AbiSnapshot(
            library="libfoo.so", version="2.0",
            functions=[Function(name="foo", mangled="_Z3foov",
                                return_type="int", visibility=Visibility.PUBLIC)],
        )

        old_tar = self._make_snapshot_tar(
            tmp_path, "old.tar.gz", snapshot_to_json(snap_old),
        )
        new_tar = self._make_snapshot_tar(
            tmp_path, "new.tar.gz", snapshot_to_json(snap_new),
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_tar), str(new_tar),
            "--format", "json",
        ])
        # Should succeed — NO_CHANGE since snapshots are identical
        assert result.exit_code == 0, f"Exit {result.exit_code}: {result.output}"

    def test_keep_extracted_flag(self, tmp_path: Path) -> None:
        """Verify --keep-extracted prevents cleanup."""
        from click.testing import CliRunner

        from abicheck.cli import main
        from abicheck.model import AbiSnapshot, Function, Visibility
        from abicheck.serialization import snapshot_to_json

        snap = AbiSnapshot(
            library="libfoo.so", version="1.0",
            functions=[Function(name="foo", mangled="_Z3foov",
                                return_type="int", visibility=Visibility.PUBLIC)],
        )
        tar = self._make_snapshot_tar(
            tmp_path, "pkg.tar.gz", snapshot_to_json(snap),
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(tar), str(tar),
            "--format", "json", "--keep-extracted",
        ])
        assert result.exit_code == 0, f"Exit {result.exit_code}: {result.output}"
        # The stderr should mention kept files
        # (CliRunner combines output by default)


class TestCompareReleaseDirectoryPassthrough:
    """Verify existing directory-based compare-release still works."""

    def test_directories_still_work(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main
        from abicheck.model import AbiSnapshot, Function, Visibility
        from abicheck.serialization import snapshot_to_json

        snap = AbiSnapshot(
            library="libfoo.so", version="1.0",
            functions=[Function(name="foo", mangled="_Z3foov",
                                return_type="int", visibility=Visibility.PUBLIC)],
        )

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        (old_dir / "libfoo.so.json").write_text(snapshot_to_json(snap))
        (new_dir / "libfoo.so.json").write_text(snapshot_to_json(snap))

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_dir), str(new_dir),
            "--format", "json",
        ])
        assert result.exit_code == 0, f"Exit {result.exit_code}: {result.output}"


# ── WheelExtractor tests ─────────────────────────────────────────────────────


class TestWheelExtractor:
    def test_detect_whl(self, tmp_path: Path) -> None:
        f = tmp_path / "numpy-1.26.whl"
        _make_wheel(f, {"numpy/__init__.py": b""})
        assert WheelExtractor().detect(f)

    def test_detect_non_whl(self, tmp_path: Path) -> None:
        f = tmp_path / "test.zip"
        _make_wheel(f, {"a": b""})
        assert not WheelExtractor().detect(f)

    def test_extract_whl(self, tmp_path: Path) -> None:
        whl = tmp_path / "test.whl"
        _make_wheel(whl, {
            "mylib/core.so": b"\x7fELF fake",
            "mylib/__init__.py": b"import core",
            "mylib-1.0.dist-info/METADATA": b"Name: mylib",
        })
        out = tmp_path / "output"
        out.mkdir()
        result = WheelExtractor().extract(whl, out)
        assert result.lib_dir == out
        assert (out / "mylib/core.so").exists()
        assert (out / "mylib/__init__.py").exists()

    def test_whl_path_traversal_rejected(self, tmp_path: Path) -> None:
        whl = tmp_path / "evil.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr("../../etc/passwd", "evil")
        out = tmp_path / "output"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError, match="path traversal"):
            WheelExtractor().extract(whl, out)

    def test_whl_absolute_path_rejected(self, tmp_path: Path) -> None:
        whl = tmp_path / "evil.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr("/etc/passwd", "evil")
        out = tmp_path / "output"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError, match="absolute path"):
            WheelExtractor().extract(whl, out)


# ── CondaExtractor tests ────────────────────────────────────────────────────


class TestParseManylinuxGlibcFloor:
    """G10: derive a declared glibc floor from a manylinux wheel tag."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            pytest.param(
                "scipy-1.18.0-cp312-cp312-manylinux_2_17_x86_64.whl",
                "2.17",
                id="pep600_tag",
            ),
            pytest.param(
                "numpy-1.26.0-cp311-cp311-manylinux1_x86_64.whl",
                "2.5",
                id="manylinux1",
            ),
            pytest.param(
                "pkg-1.0-cp311-cp311-manylinux2010_x86_64.whl",
                "2.12",
                id="manylinux2010",
            ),
            pytest.param(
                "pkg-1.0-cp311-cp311-manylinux2014_aarch64.whl",
                "2.17",
                id="manylinux2014",
            ),
            # A wheel claiming compatibility with both manylinux_2_17 and the
            # (older/stricter) manylinux2014 alias is claiming to work on
            # both — the actual binary must not exceed the lower (2.17).
            pytest.param(
                "pkg-1.0-cp311-cp311-manylinux_2_28_x86_64.manylinux2014_x86_64.whl",
                "2.17",
                id="compressed_multi_tag_picks_strictest",
            ),
            pytest.param(
                "pkg-1.0-cp311-cp311-macosx_11_0_arm64.whl",
                None,
                id="no_manylinux_tag_macosx",
            ),
            pytest.param(
                "pkg-1.0-py3-none-any.whl", None, id="no_manylinux_tag_any",
            ),
            pytest.param(
                "manylinux_2_27", "2.27", id="bare_tag_without_arch_suffix",
            ),
            # A distribution named "manylinux_2_17_helper" makes no
            # manylinux promise at all — only its platform-tag segment (the
            # last -delimited component, "linux_x86_64") may be scanned.
            pytest.param(
                "manylinux_2_17_helper-1.0-cp312-cp312-linux_x86_64.whl",
                None,
                id="manylinux_prefixed_distribution_name_not_mistaken_for_tag",
            ),
            # Same trap, but this one's actual platform tag IS manylinux —
            # must still be picked up from the platform-tag segment.
            pytest.param(
                "manylinux_2_17_helper-1.0-cp312-cp312-manylinux_2_28_x86_64.whl",
                "2.28",
                id="manylinux_prefixed_distribution_with_real_manylinux_platform",
            ),
        ],
    )
    def test_parse_manylinux_glibc_floor(
        self, name: str, expected: str | None
    ) -> None:
        assert parse_manylinux_glibc_floor(name) == expected


class TestParseMusllinuxFloor:
    """G27: PEP 656 musllinux platform-tag parsing."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            pytest.param(
                "scipy-1.18.0-cp312-cp312-musllinux_1_2_x86_64.whl",
                "1.2",
                id="basic_tag",
            ),
            pytest.param(
                "pkg-1.0-cp311-cp311-musllinux_1_1_aarch64.whl",
                "1.1",
                id="different_arch",
            ),
            # A compressed multi-tag segment claims compatibility with every
            # listed baseline — the strictest (lowest) applies.
            pytest.param(
                "pkg-1.0-cp311-cp311-musllinux_1_2_x86_64.musllinux_1_1_x86_64.whl",
                "1.1",
                id="compressed_multi_tag_picks_strictest",
            ),
            pytest.param(
                "pkg-1.0-cp311-cp311-manylinux_2_17_x86_64.whl",
                None,
                id="no_musllinux_tag_manylinux",
            ),
            pytest.param(
                "pkg-1.0-py3-none-any.whl", None, id="no_musllinux_tag_any",
            ),
            pytest.param(
                "pkg-1.0-cp311-cp311-musllinux_bogus_x86_64.whl",
                None,
                id="malformed_tag_no_crash",
            ),
            # Distribution name prefix trap, same as manylinux's equivalent.
            pytest.param(
                "musllinux_1_2_helper-1.0-cp312-cp312-linux_x86_64.whl",
                None,
                id="musllinux_prefixed_distribution_name_not_mistaken_for_tag",
            ),
        ],
    )
    def test_parse_musllinux_floor(self, name: str, expected: str | None) -> None:
        assert parse_musllinux_floor(name) == expected


class TestParseMacosDeploymentTargetFloor:
    """G27: macOS wheel platform-tag deployment-target parsing."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            pytest.param(
                "scipy-1.18.0-cp312-cp312-macosx_11_0_arm64.whl",
                "11.0",
                id="arm64",
            ),
            pytest.param(
                "pkg-1.0-cp311-cp311-macosx_10_9_x86_64.whl",
                "10.9",
                id="x86_64_underscore_arch",
            ),
            # A lone "universal2" token is itself multi-architecture (x86_64
            # + arm64 slices bundled under one string) — a real universal2
            # wheel's arm64 slice commonly has a genuinely higher minimum OS
            # than its x86_64 slice, so no single floor is safely derivable
            # from the tag alone (Codex review #583, follow-up).
            pytest.param(
                "pkg-1.0-cp311-cp311-macosx_10_9_universal2.whl",
                None,
                id="universal2_is_multi_slice_unresolvable",
            ),
            pytest.param(
                "pkg-1.0-cp311-cp311-macosx_10_9_universal.whl",
                None,
                id="universal_is_multi_slice_unresolvable",
            ),
            pytest.param(
                "pkg-1.0-cp311-cp311-macosx_10_9_intel.whl",
                None,
                id="intel_is_multi_slice_unresolvable",
            ),
            # A compressed segment naming two DIFFERENT architectures with
            # DIFFERENT targets cannot be collapsed to one number without
            # losing the fact that the floor is arch-specific — the arm64
            # slice's own Mach-O minimum OS is legitimately 11.0, so
            # reducing to x86_64's lower 10.9 would falsely flag it as
            # exceeding the floor (Codex review #583). No single floor is
            # derivable, so this returns None rather than guess.
            pytest.param(
                "pkg-1.0-cp311-cp311-macosx_10_9_x86_64.macosx_11_0_arm64.whl",
                None,
                id="compressed_multi_tag_different_archs_unresolvable",
            ),
            # Same architecture named twice with different targets (redundant
            # aliasing, the manylinux-legacy-tag analog) still resolves to
            # the strictest within that one arch.
            pytest.param(
                "pkg-1.0-cp311-cp311-macosx_10_9_x86_64.macosx_10_12_x86_64.whl",
                "10.9",
                id="compressed_multi_tag_same_arch_picks_strictest",
            ),
            # Two tags for different architectures that happen to agree on
            # the same target resolve to that shared value.
            pytest.param(
                "pkg-1.0-cp311-cp311-macosx_11_0_x86_64.macosx_11_0_arm64.whl",
                "11.0",
                id="compressed_multi_tag_different_archs_same_floor",
            ),
            pytest.param(
                "pkg-1.0-cp311-cp311-manylinux_2_17_x86_64.whl",
                None,
                id="no_macos_tag_manylinux",
            ),
            pytest.param(
                "pkg-1.0-py3-none-any.whl", None, id="no_macos_tag_any",
            ),
            pytest.param(
                "pkg-1.0-cp311-cp311-macosx_11_arm64.whl",
                None,
                id="malformed_tag_missing_minor_no_crash",
            ),
            # Distribution name prefix trap, same as manylinux's equivalent.
            pytest.param(
                "macosx_11_0_helper-1.0-cp312-cp312-linux_x86_64.whl",
                None,
                id="macosx_prefixed_distribution_name_not_mistaken_for_tag",
            ),
        ],
    )
    def test_parse_macos_deployment_target_floor(
        self, name: str, expected: str | None
    ) -> None:
        assert parse_macos_deployment_target_floor(name) == expected


class TestParseWheelArchitectureClaim:
    """G27: public wrapper for the wheel-tag architecture-mismatch check —
    thin delegation to _platform_machine_from_wheel_filename."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            pytest.param(
                "scipy-1.18.0-cp312-cp312-manylinux_2_17_x86_64.whl",
                "x86_64",
                id="manylinux_x86_64",
            ),
            pytest.param(
                "scipy-1.18.0-cp312-cp312-manylinux_2_17_aarch64.whl",
                "aarch64",
                id="manylinux_aarch64",
            ),
            pytest.param(
                "scipy-1.18.0-cp312-cp312-macosx_11_0_arm64.whl",
                "arm64",
                id="macosx_arm64",
            ),
            pytest.param(
                "scipy-1.18.0-cp312-cp312-macosx_10_9_universal2.whl",
                None,
                id="macosx_universal2_ambiguous",
            ),
            pytest.param(
                "pkg-1.0-cp311-cp311-win_amd64.whl",
                None,
                id="windows_not_derived",
            ),
        ],
    )
    def test_parse_wheel_architecture_claim(
        self, name: str, expected: str | None
    ) -> None:
        assert parse_wheel_architecture_claim(name) == expected


class TestParseNumpyRequirementFromMetadata:
    """G26: declared numpy requirement from a wheel's *.dist-info/METADATA."""

    def test_versioned_requirement(self) -> None:
        text = (
            "Metadata-Version: 2.1\nName: pkg\nVersion: 1.0\n"
            "Requires-Dist: numpy>=1.23.5,<3\n"
        )
        assert parse_numpy_requirement_from_metadata(text) == "<3,>=1.23.5"

    def test_no_requires_dist_at_all(self) -> None:
        text = "Metadata-Version: 2.1\nName: pkg\nVersion: 1.0\n"
        assert parse_numpy_requirement_from_metadata(text) is None

    def test_requires_dist_for_other_packages_only(self) -> None:
        text = "Metadata-Version: 2.1\nRequires-Dist: scipy>=1.10\n"
        assert parse_numpy_requirement_from_metadata(text) is None

    def test_bare_numpy_with_no_version_constraint(self) -> None:
        text = "Metadata-Version: 2.1\nRequires-Dist: numpy\n"
        assert parse_numpy_requirement_from_metadata(text) == ""

    def test_marker_gated_numpy_is_not_a_real_requirement(self) -> None:
        text = 'Metadata-Version: 2.1\nRequires-Dist: numpy>=1.20; extra == "test"\n'
        assert parse_numpy_requirement_from_metadata(text) is None

    def test_extra_seeded_even_on_packaging_without_auto_default(
        self, monkeypatch
    ) -> None:
        # packaging>=22 auto-defaults "extra" to "" inside Marker.evaluate();
        # this project's pinned floor is packaging>=21.0, whose evaluate()
        # has no such default and raises UndefinedEnvironmentName on a bare
        # `extra == "test"` marker if the caller doesn't seed it. Simulate
        # that stricter contract by asserting "extra" is always present in
        # the dict this module passes, regardless of installed packaging
        # version (Codex review; verified for real against a packaging==21.0
        # venv during development).
        from packaging.markers import Marker

        real_evaluate = Marker.evaluate

        def strict_evaluate(
            self: Marker, environment: dict[str, str] | None = None
        ) -> bool:
            assert environment is not None and "extra" in environment, (
                "caller must seed 'extra' -- packaging>=21.0's evaluate() "
                "has no auto-default and would raise UndefinedEnvironmentName"
            )
            return real_evaluate(self, environment)

        monkeypatch.setattr(Marker, "evaluate", strict_evaluate)
        text = 'Metadata-Version: 2.1\nRequires-Dist: numpy>=1.20; extra == "test"\n'
        assert parse_numpy_requirement_from_metadata(text) is None

    def test_extra_gated_combined_with_other_condition_is_still_skipped(self) -> None:
        text = (
            "Metadata-Version: 2.1\n"
            'Requires-Dist: numpy>=1.20; extra == "test" and python_version >= "3.8"\n'
        )
        assert parse_numpy_requirement_from_metadata(text) is None

    def test_extra_inequality_marker_is_a_real_base_requirement(self) -> None:
        # `extra != "docs"` mentions "extra" but isn't an optional-extra
        # gate -- it's true for a plain (no-extras) install, since the
        # active extra is "". A blanket "marker text contains extra" skip
        # would incorrectly discard this real requirement (CodeRabbit /
        # Codex review).
        text = 'Metadata-Version: 2.1\nRequires-Dist: numpy>=1.20; extra != "docs"\n'
        assert parse_numpy_requirement_from_metadata(text) == ">=1.20"

    def test_extra_disjunction_marker_is_a_real_base_requirement(self) -> None:
        # `python_version >= "3.9" or extra == "test"` is true for a plain
        # install on Python 3.9+ regardless of any extra -- same
        # over-skip risk as the inequality case above.
        text = (
            "Metadata-Version: 2.1\n"
            'Requires-Dist: numpy>=1.20; python_version >= "3.9" or extra == "test"\n'
        )
        assert (
            parse_numpy_requirement_from_metadata(
                text, environment={"python_version": "3.12"}
            )
            == ">=1.20"
        )

    def test_python_version_gated_numpy_is_a_real_requirement(self) -> None:
        # A marker that isn't extra-gated (e.g. python_version) still makes
        # this an unconditional *base install* requirement -- it's not an
        # optional extra, just conditional on the interpreter version. A
        # blanket "any marker at all" skip previously discarded this real
        # requirement (Codex review).
        text = (
            'Metadata-Version: 2.1\nRequires-Dist: numpy>=1.23; python_version >= "3.9"\n'
        )
        assert parse_numpy_requirement_from_metadata(text) == ">=1.23"

    def test_platform_gated_numpy_is_a_real_requirement(self) -> None:
        # Explicit environment override -- must not depend on which OS
        # actually runs the test suite (Linux/macOS/Windows CI lanes).
        text = (
            "Metadata-Version: 2.1\n"
            'Requires-Dist: numpy>=1.23; platform_system == "Linux"\n'
        )
        assert (
            parse_numpy_requirement_from_metadata(
                text, environment={"platform_system": "Linux"}
            )
            == ">=1.23"
        )

    def test_inactive_marker_is_skipped_not_returned(self) -> None:
        # A marker that IS a real (non-extra) base requirement but doesn't
        # hold for the given environment must not be reported as the active
        # promise (Codex review).
        text = (
            "Metadata-Version: 2.1\n"
            'Requires-Dist: numpy>=1.23; platform_system == "Linux"\n'
        )
        assert (
            parse_numpy_requirement_from_metadata(
                text, environment={"platform_system": "Darwin"}
            )
            is None
        )

    def test_active_branch_wins_over_inactive_earlier_branch(self) -> None:
        # Codex review's exact scenario: a wheel lists two base numpy
        # requirements split by mutually exclusive python_version markers.
        # Returning the first non-extra line regardless of applicability
        # would report the inactive 1.23 floor for a cp312 wheel instead of
        # the active 2.0 floor.
        text = (
            "Metadata-Version: 2.1\n"
            'Requires-Dist: numpy>=1.23; python_version < "3.12"\n'
            'Requires-Dist: numpy>=2; python_version >= "3.12"\n'
        )
        assert (
            parse_numpy_requirement_from_metadata(
                text, environment={"python_version": "3.12"}
            )
            == ">=2"
        )
        assert (
            parse_numpy_requirement_from_metadata(
                text, environment={"python_version": "3.11"}
            )
            == ">=1.23"
        )

    def test_simultaneously_active_requirements_are_combined_not_first_wins(
        self,
    ) -> None:
        # Codex review: unlike the mutually-exclusive-markers case above,
        # markers here aren't exclusive -- both "python_version >= 3.9" and
        # the stricter "python_version >= 3.12" hold on Python 3.12. An
        # installer enforces the intersection of every active constraint,
        # so returning only the first active line (>=1.23) would understate
        # the real (>=2) floor.
        text = (
            "Metadata-Version: 2.1\n"
            'Requires-Dist: numpy>=1.23; python_version >= "3.9"\n'
            'Requires-Dist: numpy>=2; python_version >= "3.12"\n'
        )
        combined = parse_numpy_requirement_from_metadata(
            text, environment={"python_version": "3.12"}
        )
        from packaging.specifiers import SpecifierSet

        assert SpecifierSet(combined) == SpecifierSet(">=1.23,>=2")
        assert "2.5" in SpecifierSet(combined)
        assert "1.5" not in SpecifierSet(combined)

    def test_malformed_requires_dist_line_is_skipped_not_raised(self) -> None:
        # A malformed Requires-Dist value (unparseable as a PEP 508
        # requirement) must not crash the scan -- just be ignored, same as
        # any other package's Requires-Dist line would be.
        text = (
            "Metadata-Version: 2.1\n"
            "Requires-Dist: not a valid requirement !!!\n"
            "Requires-Dist: numpy>=1.23.5\n"
        )
        assert parse_numpy_requirement_from_metadata(text) == ">=1.23.5"

    def test_case_insensitive_package_name(self) -> None:
        text = "Metadata-Version: 2.1\nRequires-Dist: NumPy>=1.24\n"
        assert parse_numpy_requirement_from_metadata(text) == ">=1.24"

    def test_first_unconditional_match_wins(self) -> None:
        text = (
            "Metadata-Version: 2.1\n"
            'Requires-Dist: numpy>=1.20; extra == "test"\n'
            "Requires-Dist: numpy>=1.23.5\n"
        )
        assert parse_numpy_requirement_from_metadata(text) == ">=1.23.5"

    def test_folded_header_continuation_line_is_unfolded(self) -> None:
        # Core Metadata is an RFC 5322-style header block; a long
        # Requires-Dist value can be folded across physical lines with
        # leading whitespace on the continuation. A plain
        # line.startswith("Requires-Dist:") scan only sees the first
        # physical line ("numpy") and mangles the folded specifier/marker
        # (independent review finding, confirmed by Codex).
        text = (
            "Metadata-Version: 2.1\n"
            "Name: pkg\n"
            'Requires-Dist: numpy\n >=2; python_version >= "3.12"\n'
        )
        assert (
            parse_numpy_requirement_from_metadata(
                text, environment={"python_version": "3.12"}
            )
            == ">=2"
        )
        assert (
            parse_numpy_requirement_from_metadata(
                text, environment={"python_version": "3.11"}
            )
            is None
        )


class TestParseWheelNumpyRequirement:
    """G26: reads *.dist-info/METADATA directly out of the wheel zip."""

    def test_reads_metadata_from_wheel(self, tmp_path: Path) -> None:
        whl = tmp_path / "pkg-1.0-cp311-cp311-linux_x86_64.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr(
                "pkg-1.0.dist-info/METADATA",
                "Metadata-Version: 2.1\nRequires-Dist: numpy>=1.23.5\n",
            )
        assert parse_wheel_numpy_requirement(whl) == ">=1.23.5"

    def test_oversized_metadata_member_rejected_not_decompressed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # An attacker-controlled wheel could declare a METADATA member that
        # decompresses far beyond a real METADATA file's size (a zip bomb).
        # Lower the cap to a few bytes so the test doesn't need to actually
        # write megabytes of data (CodeRabbit review).
        import abicheck.package as package_mod

        monkeypatch.setattr(package_mod, "_MAX_METADATA_SIZE", 8)
        whl = tmp_path / "pkg-1.0-cp311-cp311-linux_x86_64.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr(
                "pkg-1.0.dist-info/METADATA",
                "Metadata-Version: 2.1\nRequires-Dist: numpy>=1.23.5\n",
            )

        # Prove rejection happens from the declared size alone, before any
        # decompression -- a test that only checks the final return value
        # would pass equally if the implementation opened and decompressed
        # the oversized member first (CodeRabbit review).
        def unexpected_open(*args, **kwargs):
            raise AssertionError("oversized METADATA must not be opened")

        monkeypatch.setattr(zipfile.ZipFile, "open", unexpected_open)
        assert parse_wheel_numpy_requirement(whl) is None

    def test_bounded_read_independently_rejects_an_oversized_result(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # zipfile.ZipExtFile happens to truncate reads to a member's
        # declared uncompressed size today, so the declared-size guard
        # above already catches an honestly-labeled oversized member. This
        # code doesn't want to depend on that as its only safety margin --
        # the bounded f.read(cap + 1) call is meant to independently catch
        # an oversized result even if the reader it's given doesn't
        # truncate. Simulate that by monkeypatching ZipFile.open to return
        # a reader that ignores the declared size entirely.
        import io

        import abicheck.package as package_mod

        monkeypatch.setattr(package_mod, "_MAX_METADATA_SIZE", 8)
        whl = tmp_path / "pkg-1.0-cp311-cp311-linux_x86_64.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr("pkg-1.0.dist-info/METADATA", "short")

        def non_truncating_open(self, *args, **kwargs):
            return io.BytesIO(b"x" * (package_mod._MAX_METADATA_SIZE + 100))

        monkeypatch.setattr(zipfile.ZipFile, "open", non_truncating_open)
        assert parse_wheel_numpy_requirement(whl) is None

    def test_no_dist_info_metadata_member(self, tmp_path: Path) -> None:
        whl = tmp_path / "pkg-1.0-cp311-cp311-linux_x86_64.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr("pkg/__init__.py", "")
        assert parse_wheel_numpy_requirement(whl) is None

    def test_not_a_zip_returns_none(self, tmp_path: Path) -> None:
        whl = tmp_path / "not-a-wheel.whl"
        whl.write_bytes(b"not a zip file")
        assert parse_wheel_numpy_requirement(whl) is None

    def test_nonexistent_wheel_returns_none(self, tmp_path: Path) -> None:
        assert parse_wheel_numpy_requirement(tmp_path / "missing.whl") is None

    def test_python_version_derived_from_wheel_filename_not_running_interpreter(
        self, tmp_path: Path
    ) -> None:
        # Codex review: a cp311 wheel scanned by a different (e.g. 3.12)
        # interpreter running abicheck must have its markers evaluated
        # against ITS OWN cp311 tag, not the host interpreter -- otherwise a
        # real under-declared floor on that wheel could go undetected.
        whl = tmp_path / "pkg-1.0-cp311-cp311-linux_x86_64.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr(
                "pkg-1.0.dist-info/METADATA",
                "Metadata-Version: 2.1\n"
                'Requires-Dist: numpy>=1.23; python_version < "3.12"\n'
                'Requires-Dist: numpy>=2; python_version >= "3.12"\n',
            )
        assert parse_wheel_numpy_requirement(whl) == ">=1.23"

    def test_abi3_wheel_python_version_marker_falls_back_to_running_interpreter(
        self, tmp_path: Path
    ) -> None:
        # Codex review: a cp39-abi3 wheel genuinely installs on Python 3.9
        # AND every later 3.x minor, so pinning python_version="3.9" would
        # make a "later minor" marker wrongly evaluate inactive. This
        # project requires Python 3.10+ to run at all (CLAUDE.md), so
        # python_version >= "3.10" is guaranteed true on whatever host runs
        # this test -- the old buggy derivation (pinning "3.9") would have
        # made this assert ">=1.23" instead.
        whl = tmp_path / "pkg-1.0-cp39-abi3-manylinux_2_17_x86_64.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr(
                "pkg-1.0.dist-info/METADATA",
                "Metadata-Version: 2.1\n"
                'Requires-Dist: numpy>=1.23; python_version < "3.10"\n'
                'Requires-Dist: numpy>=2; python_version >= "3.10"\n',
            )
        assert parse_wheel_numpy_requirement(whl) == ">=2"

    def test_python_full_version_spelling_also_derived_from_wheel_filename(
        self, tmp_path: Path
    ) -> None:
        # Codex review: python_full_version ("3.11.0") is a different, less
        # common PEP 508 marker spelling than python_version ("3.11") --
        # deriving only the latter still leaves the former falling back to
        # the host interpreter's actual full version.
        whl = tmp_path / "pkg-1.0-cp311-cp311-linux_x86_64.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr(
                "pkg-1.0.dist-info/METADATA",
                "Metadata-Version: 2.1\n"
                'Requires-Dist: numpy>=1.23; python_full_version < "3.12"\n'
                'Requires-Dist: numpy>=2; python_full_version >= "3.12"\n',
            )
        assert parse_wheel_numpy_requirement(whl) == ">=1.23"

    def test_explicit_environment_overrides_wheel_filename_derivation(
        self, tmp_path: Path
    ) -> None:
        whl = tmp_path / "pkg-1.0-cp311-cp311-linux_x86_64.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr(
                "pkg-1.0.dist-info/METADATA",
                "Metadata-Version: 2.1\n"
                'Requires-Dist: numpy>=1.23; python_version < "3.12"\n'
                'Requires-Dist: numpy>=2; python_version >= "3.12"\n',
            )
        assert (
            parse_wheel_numpy_requirement(
                whl, environment={"python_version": "3.12"}
            )
            == ">=2"
        )

    def test_generic_py3_tag_falls_back_to_running_interpreter(
        self, tmp_path: Path
    ) -> None:
        # A "py3" tag doesn't pin a minor version -- nothing useful to
        # derive, so this must not raise and must fall back cleanly.
        whl = tmp_path / "pkg-1.0-py3-none-any.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr(
                "pkg-1.0.dist-info/METADATA",
                "Metadata-Version: 2.1\nRequires-Dist: numpy>=1.23.5\n",
            )
        assert parse_wheel_numpy_requirement(whl) == ">=1.23.5"

    def test_platform_system_derived_from_wheel_filename_not_host_os(
        self, tmp_path: Path
    ) -> None:
        # Codex review: a macosx wheel scanned by abicheck running on Linux
        # (or any other host) must have its platform_system-gated markers
        # evaluated against ITS OWN Darwin platform tag, not the host OS.
        whl = tmp_path / "pkg-1.0-cp311-cp311-macosx_11_0_arm64.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr(
                "pkg-1.0.dist-info/METADATA",
                "Metadata-Version: 2.1\n"
                'Requires-Dist: numpy>=1.23; platform_system == "Linux"\n'
                'Requires-Dist: numpy>=2; platform_system == "Darwin"\n',
            )
        assert parse_wheel_numpy_requirement(whl) == ">=2"

    def test_sys_platform_spelling_also_derived_from_wheel_filename(
        self, tmp_path: Path
    ) -> None:
        # Codex review: sys_platform ("darwin"/"linux"/"win32") is a
        # different, equally common PEP 508 marker spelling for the same OS
        # distinction as platform_system ("Darwin"/"Linux"/"Windows") --
        # deriving only one still leaves the other spelling falling back to
        # the host OS.
        whl = tmp_path / "pkg-1.0-cp311-cp311-macosx_11_0_arm64.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr(
                "pkg-1.0.dist-info/METADATA",
                "Metadata-Version: 2.1\n"
                'Requires-Dist: numpy>=1.23; sys_platform == "linux"\n'
                'Requires-Dist: numpy>=2; sys_platform == "darwin"\n',
            )
        assert parse_wheel_numpy_requirement(whl) == ">=2"

    def test_os_name_spelling_also_derived_from_wheel_filename(
        self, tmp_path: Path
    ) -> None:
        # Codex review's exact scenario: os_name ("posix"/"nt") is a third
        # PEP 508 marker spelling for the same OS distinction as
        # platform_system/sys_platform -- a win_amd64 wheel scanned on
        # Linux must have an os_name-gated marker evaluated against ITS OWN
        # "nt", not the host's "posix".
        whl = tmp_path / "pkg-1.0-cp311-cp311-win_amd64.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr(
                "pkg-1.0.dist-info/METADATA",
                "Metadata-Version: 2.1\n"
                'Requires-Dist: numpy>=1.23; os_name == "posix"\n'
                'Requires-Dist: numpy>=2; os_name == "nt"\n',
            )
        assert parse_wheel_numpy_requirement(whl) == ">=2"

    def test_implementation_markers_derived_from_wheel_filename(
        self, tmp_path: Path
    ) -> None:
        # Codex review's exact scenario: a PyPy-tagged wheel scanned while
        # abicheck itself runs under CPython must have implementation
        # markers (both spellings) evaluated against ITS OWN "PyPy"/"pypy",
        # not the host interpreter's "CPython"/"cpython".
        whl = tmp_path / "pkg-1.0-pp39-pypy39_pp73-linux_x86_64.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr(
                "pkg-1.0.dist-info/METADATA",
                "Metadata-Version: 2.1\n"
                'Requires-Dist: numpy>=1.23; platform_python_implementation == "CPython"\n'
                'Requires-Dist: numpy>=2; platform_python_implementation == "PyPy"\n',
            )
        assert parse_wheel_numpy_requirement(whl) == ">=2"

        whl2 = tmp_path / "pkg-2.0-pp39-pypy39_pp73-linux_x86_64.whl"
        with zipfile.ZipFile(whl2, "w") as zf:
            zf.writestr(
                "pkg-1.0.dist-info/METADATA",
                "Metadata-Version: 2.1\n"
                'Requires-Dist: numpy>=1.23; implementation_name == "cpython"\n'
                'Requires-Dist: numpy>=2; implementation_name == "pypy"\n',
            )
        assert parse_wheel_numpy_requirement(whl2) == ">=2"

    def test_implementation_version_derived_from_cpython_wheel_filename(
        self, tmp_path: Path
    ) -> None:
        # Codex review's exact scenario: a cp310 wheel scanned on a
        # different (e.g. 3.12) host must have implementation_version
        # evaluated against ITS OWN synthetic "3.10.0", not the host's
        # actual implementation_version.
        whl = tmp_path / "pkg-1.0-cp310-cp310-linux_x86_64.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr(
                "pkg-1.0.dist-info/METADATA",
                "Metadata-Version: 2.1\n"
                'Requires-Dist: numpy>=1.23; implementation_version < "3.11"\n'
                'Requires-Dist: numpy>=2; implementation_version >= "3.11"\n',
            )
        assert parse_wheel_numpy_requirement(whl) == ">=1.23"

    def test_platform_machine_derived_from_wheel_filename_for_single_arch(
        self, tmp_path: Path
    ) -> None:
        # Codex review's exact scenario: a manylinux aarch64 wheel scanned
        # on an x86_64 host must have its platform_machine-gated markers
        # evaluated against ITS OWN architecture, not the host's.
        whl = tmp_path / "pkg-1.0-cp311-cp311-manylinux_2_17_aarch64.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr(
                "pkg-1.0.dist-info/METADATA",
                "Metadata-Version: 2.1\n"
                'Requires-Dist: numpy>=1.23; platform_machine == "x86_64"\n'
                'Requires-Dist: numpy>=2; platform_machine == "aarch64"\n',
            )
        assert parse_wheel_numpy_requirement(whl) == ">=2"

    def test_compressed_multi_arch_macosx_wheel_falls_back_to_host(
        self, tmp_path: Path
    ) -> None:
        # Codex review: a genuinely multi-architecture wheel (dotted
        # macosx x86_64/arm64 tags) must NOT derive a platform_machine at
        # all -- checking one arch's slice of it must not silently pick up
        # the OTHER arch's marker evaluation.
        whl = (
            tmp_path
            / "pkg-1.0-cp311-cp311-macosx_10_9_x86_64.macosx_11_0_arm64.whl"
        )
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr(
                "pkg-1.0.dist-info/METADATA",
                "Metadata-Version: 2.1\nRequires-Dist: numpy>=1.23.5\n",
            )
        # No platform_machine-gated markers here, so this stays trivially
        # correct either way -- the real assertion is in the unit test for
        # _platform_machine_from_wheel_filename itself returning None for
        # this tag. This just confirms end-to-end parsing doesn't crash on
        # a compressed multi-arch tag.
        assert parse_wheel_numpy_requirement(whl) == ">=1.23.5"


class TestPythonVersionFromWheelFilename:
    def test_cp_tag(self) -> None:
        assert (
            _python_version_from_wheel_filename(
                "pkg-1.0-cp311-cp311-linux_x86_64.whl"
            )
            == "3.11"
        )

    def test_cp_tag_single_digit_minor(self) -> None:
        assert (
            _python_version_from_wheel_filename("pkg-1.0-cp39-cp39-linux_x86_64.whl")
            == "3.9"
        )

    def test_build_tag_does_not_shift_python_tag_position(self) -> None:
        assert (
            _python_version_from_wheel_filename(
                "pkg-1.0-2-cp311-cp311-linux_x86_64.whl"
            )
            == "3.11"
        )

    def test_pypy_tag(self) -> None:
        assert (
            _python_version_from_wheel_filename(
                "pkg-1.0-pp39-pypy39_pp73-linux_x86_64.whl"
            )
            == "3.9"
        )

    def test_generic_py3_tag_has_no_minor_returns_none(self) -> None:
        assert (
            _python_version_from_wheel_filename("pkg-1.0-py3-none-any.whl") is None
        )

    def test_non_wheel_filename_returns_none(self) -> None:
        assert _python_version_from_wheel_filename("pkg-1.0.tar.gz") is None

    def test_too_few_segments_returns_none(self) -> None:
        assert _python_version_from_wheel_filename("weird.whl") is None

    def test_abi3_tag_names_a_floor_not_one_minor_returns_none(self) -> None:
        # Codex review: a cp39-abi3 wheel genuinely installs on Python 3.9
        # AND every later 3.x minor (the stable/limited API's whole point),
        # so pinning python_version="3.9" would be wrong -- confirmed
        # against packaging.tags, which puts cp39-abi3 in the accepted tag
        # set for a 3.12 interpreter too.
        assert (
            _python_version_from_wheel_filename(
                "pkg-1.0-cp39-abi3-manylinux_2_17_x86_64.whl"
            )
            is None
        )

    def test_compressed_abi_tag_including_abi3_also_names_a_floor(self) -> None:
        # Codex review: a PEP 425 compressed multi-tag ABI segment
        # (cp39.abi3, dot-joined) is a different spelling of the same
        # ambiguity -- packaging.utils.parse_wheel_filename expands
        # "cp39-cp39.abi3-..." to BOTH an exact cp39-cp39 tag and a
        # cp39-abi3 stable-ABI tag, so this wheel is just as installable
        # on later 3.x minors as a plain cp39-abi3 wheel. An exact
        # string-equality check against the whole ABI segment misses this.
        assert (
            _python_version_from_wheel_filename(
                "pkg-1.0-cp39-cp39.abi3-manylinux_2_17_x86_64.whl"
            )
            is None
        )

    def test_compressed_python_tag_spanning_multiple_minors_returns_none(
        self,
    ) -> None:
        # Codex review: a PEP 425 compressed multi-tag Python segment
        # (cp310.cp311, dot-joined) is valid too -- packaging.utils.
        # parse_wheel_filename expands "cp310.cp311-cp310.cp311-..." to
        # tags for both 3.10 and 3.11, so this wheel genuinely installs on
        # either. The single-version-anchored _WHEEL_PYTHON_TAG_RE already
        # fails to match a dot-joined segment and safely returns None
        # (same "can't pin one value -> fall back to host" contract as the
        # abi3 case) with no code change needed -- this test locks that
        # in.
        assert (
            _python_version_from_wheel_filename(
                "pkg-1.0-cp310.cp311-cp310.cp311-linux_x86_64.whl"
            )
            is None
        )


class TestPythonFullVersionFromWheelFilename:
    def test_cp_tag_gets_synthetic_micro(self) -> None:
        assert (
            _python_full_version_from_wheel_filename(
                "pkg-1.0-cp311-cp311-linux_x86_64.whl"
            )
            == "3.11.0"
        )

    def test_generic_py3_tag_has_no_minor_returns_none(self) -> None:
        assert (
            _python_full_version_from_wheel_filename("pkg-1.0-py3-none-any.whl")
            is None
        )

    def test_non_wheel_filename_returns_none(self) -> None:
        assert _python_full_version_from_wheel_filename("pkg-1.0.tar.gz") is None

    def test_abi3_tag_names_a_floor_not_one_minor_returns_none(self) -> None:
        assert (
            _python_full_version_from_wheel_filename(
                "pkg-1.0-cp39-abi3-manylinux_2_17_x86_64.whl"
            )
            is None
        )


class TestImplementationVersionFromWheelFilename:
    def test_cp_tag_gets_synthetic_micro(self) -> None:
        # implementation_version == python_full_version for CPython
        # specifically (packaging.markers.default_environment() computes
        # both from the same sys.implementation.version there).
        assert (
            _implementation_version_from_wheel_filename(
                "pkg-1.0-cp311-cp311-linux_x86_64.whl"
            )
            == "3.11.0"
        )

    def test_pypy_tag_returns_none(self) -> None:
        # A pp39 tag's "39" is CPython-ABI-compatibility, not PyPy's own
        # release number -- deriving "3.9.0" here would be actively wrong
        # (PyPy's real implementation_version is its own X.Y.Z, e.g.
        # 7.3.x), not just imprecise, so this must not guess (Codex
        # review).
        assert (
            _implementation_version_from_wheel_filename(
                "pkg-1.0-pp39-pypy39_pp73-linux_x86_64.whl"
            )
            is None
        )

    def test_abi3_tag_names_a_floor_not_one_minor_returns_none(self) -> None:
        assert (
            _implementation_version_from_wheel_filename(
                "pkg-1.0-cp39-abi3-manylinux_2_17_x86_64.whl"
            )
            is None
        )

    def test_generic_py3_tag_has_no_minor_returns_none(self) -> None:
        assert (
            _implementation_version_from_wheel_filename("pkg-1.0-py3-none-any.whl")
            is None
        )

    def test_non_wheel_filename_returns_none(self) -> None:
        assert _implementation_version_from_wheel_filename("pkg-1.0.tar.gz") is None

    def test_too_few_segments_returns_none(self) -> None:
        assert _implementation_version_from_wheel_filename("weird.whl") is None


class TestImplementationNameFromWheelFilename:
    def test_cp_tag(self) -> None:
        assert (
            _implementation_name_from_wheel_filename(
                "pkg-1.0-cp311-cp311-linux_x86_64.whl"
            )
            == "cpython"
        )

    def test_pypy_tag(self) -> None:
        assert (
            _implementation_name_from_wheel_filename(
                "pkg-1.0-pp39-pypy39_pp73-linux_x86_64.whl"
            )
            == "pypy"
        )

    def test_generic_py_tag_makes_no_implementation_promise(self) -> None:
        assert (
            _implementation_name_from_wheel_filename("pkg-1.0-py3-none-any.whl")
            is None
        )

    def test_non_wheel_filename_returns_none(self) -> None:
        assert _implementation_name_from_wheel_filename("pkg-1.0.tar.gz") is None

    def test_too_few_segments_returns_none(self) -> None:
        assert _implementation_name_from_wheel_filename("weird.whl") is None


class TestPlatformPythonImplementationFromWheelFilename:
    def test_cp_tag(self) -> None:
        assert (
            _platform_python_implementation_from_wheel_filename(
                "pkg-1.0-cp311-cp311-linux_x86_64.whl"
            )
            == "CPython"
        )

    def test_pypy_tag(self) -> None:
        assert (
            _platform_python_implementation_from_wheel_filename(
                "pkg-1.0-pp39-pypy39_pp73-linux_x86_64.whl"
            )
            == "PyPy"
        )

    def test_generic_py_tag_makes_no_implementation_promise(self) -> None:
        assert (
            _platform_python_implementation_from_wheel_filename(
                "pkg-1.0-py3-none-any.whl"
            )
            is None
        )

    def test_non_wheel_filename_returns_none(self) -> None:
        assert (
            _platform_python_implementation_from_wheel_filename("pkg-1.0.tar.gz")
            is None
        )

    def test_too_few_segments_returns_none(self) -> None:
        assert _platform_python_implementation_from_wheel_filename("weird.whl") is None


class TestPlatformSystemFromWheelFilename:
    def test_manylinux_tag(self) -> None:
        assert (
            _platform_system_from_wheel_filename(
                "pkg-1.0-cp311-cp311-manylinux_2_17_x86_64.whl"
            )
            == "Linux"
        )

    def test_musllinux_tag(self) -> None:
        assert (
            _platform_system_from_wheel_filename(
                "pkg-1.0-cp311-cp311-musllinux_1_1_x86_64.whl"
            )
            == "Linux"
        )

    def test_plain_linux_tag(self) -> None:
        assert (
            _platform_system_from_wheel_filename(
                "pkg-1.0-cp311-cp311-linux_x86_64.whl"
            )
            == "Linux"
        )

    def test_macosx_tag(self) -> None:
        assert (
            _platform_system_from_wheel_filename(
                "pkg-1.0-cp311-cp311-macosx_11_0_arm64.whl"
            )
            == "Darwin"
        )

    def test_win_tag(self) -> None:
        assert (
            _platform_system_from_wheel_filename(
                "pkg-1.0-cp311-cp311-win_amd64.whl"
            )
            == "Windows"
        )

    def test_any_tag_returns_none(self) -> None:
        assert (
            _platform_system_from_wheel_filename("pkg-1.0-py3-none-any.whl") is None
        )

    def test_non_wheel_filename_returns_none(self) -> None:
        assert _platform_system_from_wheel_filename("pkg-1.0.tar.gz") is None

    def test_too_few_segments_returns_none(self) -> None:
        assert _platform_system_from_wheel_filename("weird.whl") is None


class TestSysPlatformFromWheelFilename:
    def test_manylinux_tag(self) -> None:
        assert (
            _sys_platform_from_wheel_filename(
                "pkg-1.0-cp311-cp311-manylinux_2_17_x86_64.whl"
            )
            == "linux"
        )

    def test_macosx_tag(self) -> None:
        assert (
            _sys_platform_from_wheel_filename(
                "pkg-1.0-cp311-cp311-macosx_11_0_arm64.whl"
            )
            == "darwin"
        )

    def test_win_tag(self) -> None:
        assert (
            _sys_platform_from_wheel_filename("pkg-1.0-cp311-cp311-win_amd64.whl")
            == "win32"
        )

    def test_any_tag_returns_none(self) -> None:
        assert _sys_platform_from_wheel_filename("pkg-1.0-py3-none-any.whl") is None

    def test_non_wheel_filename_returns_none(self) -> None:
        assert _sys_platform_from_wheel_filename("pkg-1.0.tar.gz") is None

    def test_too_few_segments_returns_none(self) -> None:
        assert _sys_platform_from_wheel_filename("weird.whl") is None


class TestOsNameFromWheelFilename:
    def test_manylinux_tag(self) -> None:
        assert (
            _os_name_from_wheel_filename(
                "pkg-1.0-cp311-cp311-manylinux_2_17_x86_64.whl"
            )
            == "posix"
        )

    def test_macosx_tag(self) -> None:
        assert (
            _os_name_from_wheel_filename("pkg-1.0-cp311-cp311-macosx_11_0_arm64.whl")
            == "posix"
        )

    def test_win_tag(self) -> None:
        assert (
            _os_name_from_wheel_filename("pkg-1.0-cp311-cp311-win_amd64.whl") == "nt"
        )

    def test_any_tag_returns_none(self) -> None:
        assert _os_name_from_wheel_filename("pkg-1.0-py3-none-any.whl") is None

    def test_non_wheel_filename_returns_none(self) -> None:
        assert _os_name_from_wheel_filename("pkg-1.0.tar.gz") is None

    def test_too_few_segments_returns_none(self) -> None:
        assert _os_name_from_wheel_filename("weird.whl") is None


class TestPlatformMachineFromWheelFilename:
    def test_manylinux_x86_64(self) -> None:
        assert (
            _platform_machine_from_wheel_filename(
                "pkg-1.0-cp311-cp311-manylinux_2_17_x86_64.whl"
            )
            == "x86_64"
        )

    def test_manylinux_aarch64(self) -> None:
        assert (
            _platform_machine_from_wheel_filename(
                "pkg-1.0-cp311-cp311-manylinux_2_17_aarch64.whl"
            )
            == "aarch64"
        )

    def test_manylinux_ppc64le_not_confused_with_ppc64(self) -> None:
        assert (
            _platform_machine_from_wheel_filename(
                "pkg-1.0-cp311-cp311-manylinux2014_ppc64le.whl"
            )
            == "ppc64le"
        )

    def test_macosx_x86_64(self) -> None:
        assert (
            _platform_machine_from_wheel_filename(
                "pkg-1.0-cp311-cp311-macosx_10_9_x86_64.whl"
            )
            == "x86_64"
        )

    def test_macosx_arm64(self) -> None:
        assert (
            _platform_machine_from_wheel_filename(
                "pkg-1.0-cp311-cp311-macosx_11_0_arm64.whl"
            )
            == "arm64"
        )

    def test_macosx_universal2_is_ambiguous_returns_none(self) -> None:
        assert (
            _platform_machine_from_wheel_filename(
                "pkg-1.0-cp311-cp311-macosx_11_0_universal2.whl"
            )
            is None
        )

    def test_compressed_multi_arch_macosx_tag_is_ambiguous_returns_none(
        self,
    ) -> None:
        # PEP 600 compressed multi-tag platform segment covering two
        # DIFFERENT architectures -- naively checking the whole segment's
        # suffix would derive "arm64" just because that's the last
        # dot-joined component, even though the wheel also covers x86_64
        # (Codex review).
        assert (
            _platform_machine_from_wheel_filename(
                "pkg-1.0-cp311-cp311-macosx_10_9_x86_64.macosx_11_0_arm64.whl"
            )
            is None
        )

    def test_compressed_multi_tag_agreeing_on_one_arch_is_derived(self) -> None:
        # Multiple manylinux baselines for the SAME architecture (a
        # genuinely common case: a wheel built compatible with both an
        # older and a newer glibc floor) still names exactly one arch.
        assert (
            _platform_machine_from_wheel_filename(
                "pkg-1.0-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
            )
            == "x86_64"
        )

    def test_win_tag_not_derived(self) -> None:
        # Windows arch-string conventions in platform.machine() are less
        # standardized than Linux/macOS -- deliberately left undetermined.
        assert (
            _platform_machine_from_wheel_filename("pkg-1.0-cp311-cp311-win_amd64.whl")
            is None
        )

    def test_any_tag_returns_none(self) -> None:
        assert (
            _platform_machine_from_wheel_filename("pkg-1.0-py3-none-any.whl") is None
        )

    def test_non_wheel_filename_returns_none(self) -> None:
        assert _platform_machine_from_wheel_filename("pkg-1.0.tar.gz") is None

    def test_too_few_segments_returns_none(self) -> None:
        assert _platform_machine_from_wheel_filename("weird.whl") is None

    def test_unrecognized_linux_architecture_returns_none(self) -> None:
        assert (
            _platform_machine_from_wheel_filename(
                "pkg-1.0-cp311-cp311-manylinux_2_17_mips64.whl"
            )
            is None
        )

    def test_riscv64_linux_architecture_is_derived(self) -> None:
        # Codex review #583: packaging's own _manylinux._ALLOWED_ARCHS
        # includes riscv64/loongarch64 — omitting them here silently
        # skipped wheel_tag_architecture_mismatch derivation entirely for
        # otherwise valid single-arch manylinux/musllinux wheels.
        assert (
            _platform_machine_from_wheel_filename(
                "pkg-1.0-cp311-cp311-manylinux_2_39_riscv64.whl"
            )
            == "riscv64"
        )

    def test_loongarch64_linux_architecture_is_derived(self) -> None:
        assert (
            _platform_machine_from_wheel_filename(
                "pkg-1.0-cp311-cp311-manylinux_2_39_loongarch64.whl"
            )
            == "loongarch64"
        )


class TestCondaExtractor:
    def test_detect_conda_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "numpy-1.26.conda"
        _make_wheel(f, {"metadata.json": b"{}"})
        assert CondaExtractor().detect(f)

    def test_detect_legacy_conda_tar_bz2(self, tmp_path: Path) -> None:
        f = tmp_path / "numpy-1.26-h123-0.tar.bz2"
        _make_conda_legacy(f, {"lib/libfoo.so": b"elf"})
        assert CondaExtractor().detect(f)

    def test_detect_generic_tar_bz2_not_conda(self, tmp_path: Path) -> None:
        """A tar.bz2 without info/ dir is NOT detected as conda."""
        f = tmp_path / "data-1.0-x86.tar.bz2"
        with tarfile.open(f, "w:bz2") as tf:
            info = tarfile.TarInfo(name="README")
            info.size = 5
            tf.addfile(info, io.BytesIO(b"hello"))
        assert not CondaExtractor().detect(f)

    def test_detect_non_conda(self, tmp_path: Path) -> None:
        f = tmp_path / "test.zip"
        _make_wheel(f, {"a": b""})
        assert not CondaExtractor().detect(f)

    def test_extract_legacy_tar_bz2(self, tmp_path: Path) -> None:
        f = tmp_path / "numpy-1.26-h123-0.tar.bz2"
        _make_conda_legacy(f, {"lib/libopenblas.so": b"\x7fELF fake"})
        out = tmp_path / "output"
        out.mkdir()
        result = CondaExtractor().extract(f, out)
        assert result.lib_dir == out
        assert (out / "lib/libopenblas.so").exists()
        assert (out / "info/index.json").exists()


# ── Zip security tests ──────────────────────────────────────────────────────


class TestSafeZipExtract:
    def test_basic_extraction(self, tmp_path: Path) -> None:
        z = tmp_path / "test.zip"
        _make_wheel(z, {"a/b.txt": b"hello", "c.txt": b"world"})
        out = tmp_path / "output"
        out.mkdir()
        _safe_zip_extract(z, out)
        assert (out / "a/b.txt").read_bytes() == b"hello"
        assert (out / "c.txt").read_bytes() == b"world"

    def test_path_traversal_rejected(self, tmp_path: Path) -> None:
        z = tmp_path / "evil.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("../../../etc/passwd", "evil")
        out = tmp_path / "output"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError, match="path traversal"):
            _safe_zip_extract(z, out)

    def test_absolute_path_rejected(self, tmp_path: Path) -> None:
        z = tmp_path / "evil.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("/etc/passwd", "evil")
        out = tmp_path / "output"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError, match="absolute path"):
            _safe_zip_extract(z, out)


# ── CLI integration tests (wheel) ────────────────────────────────────────────


class TestCompareReleaseWheelPackages:
    """Integration tests using wheel (.whl) archives."""

    def test_whl_packages_accepted(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main
        from abicheck.model import AbiSnapshot, Function, Visibility
        from abicheck.serialization import snapshot_to_json

        snap = AbiSnapshot(
            library="libfoo.so", version="1.0",
            functions=[Function(name="foo", mangled="_Z3foov",
                                return_type="int", visibility=Visibility.PUBLIC)],
        )

        old_whl = tmp_path / "old.whl"
        new_whl = tmp_path / "new.whl"
        _make_wheel(old_whl, {"libfoo.so.json": snapshot_to_json(snap).encode()})
        _make_wheel(new_whl, {"libfoo.so.json": snapshot_to_json(snap).encode()})

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_whl), str(new_whl),
            "--format", "json",
        ])
        assert result.exit_code == 0, f"Exit {result.exit_code}: {result.output}"


# ── ExtractResult tests ─────────────────────────────────────────────────────


class TestExtractResult:
    def test_defaults(self, tmp_path: Path) -> None:
        r = ExtractResult(lib_dir=tmp_path)
        assert r.debug_dir is None
        assert r.header_dir is None
        assert r.metadata == {}

    def test_with_all_fields(self, tmp_path: Path) -> None:
        r = ExtractResult(
            lib_dir=tmp_path,
            debug_dir=tmp_path / "debug",
            header_dir=tmp_path / "headers",
            metadata={"name": "libfoo", "version": "1.0"},
        )
        assert r.debug_dir == tmp_path / "debug"
        assert r.header_dir == tmp_path / "headers"
        assert r.metadata["name"] == "libfoo"


# ── Additional security validation tests ─────────────────────────────────


class TestValidateMemberPathExtended:
    def test_leading_slash_rejected_crossplatform(self, tmp_path: Path) -> None:
        """Ensure /etc/passwd is caught even when os.path.isabs returns False (Windows)."""
        with mock.patch("abicheck.package.os.path.isabs", return_value=False):
            with pytest.raises(ExtractionSecurityError, match="absolute path"):
                _validate_member_path("/etc/passwd", tmp_path)

    def test_resolved_path_escape(self, tmp_path: Path) -> None:
        """Path that doesn't contain '..' but resolves outside root via symlink."""
        # Create a symlink inside tmp_path pointing outside
        escape_dir = tmp_path / "escape"
        escape_dir.mkdir()
        link = tmp_path / "root" / "link"
        link.parent.mkdir(parents=True)
        link.symlink_to(tmp_path.parent)
        # Now "link/something" resolves outside "root"
        root = tmp_path / "root"
        with pytest.raises(ExtractionSecurityError, match="resolved path escapes"):
            _validate_member_path("link/something", root)


class TestTarExtractorSymlinks:
    def test_symlink_within_root_accepted(self, tmp_path: Path) -> None:
        """Tar with internal symlink should extract fine."""
        archive = tmp_path / "symlink.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            # Add a regular file
            info = tarfile.TarInfo(name="lib/libfoo.so.1.0")
            info.size = 4
            tf.addfile(info, io.BytesIO(b"data"))
            # Add a symlink
            sym = tarfile.TarInfo(name="lib/libfoo.so")
            sym.type = tarfile.SYMTYPE
            sym.linkname = "libfoo.so.1.0"
            tf.addfile(sym)

        out = tmp_path / "output"
        out.mkdir()
        TarExtractor().extract(archive, out)
        assert (out / "lib/libfoo.so.1.0").exists()

    def test_symlink_escaping_rejected(self, tmp_path: Path) -> None:
        """Tar with symlink pointing outside root should be rejected."""
        archive = tmp_path / "evil_sym.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            sym = tarfile.TarInfo(name="lib/evil")
            sym.type = tarfile.SYMTYPE
            sym.linkname = "../../../../etc/passwd"
            tf.addfile(sym)

        out = tmp_path / "output"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError, match="symlink target"):
            TarExtractor().extract(archive, out)


# ── ELF detection extended ───────────────────────────────────────────────


class TestIsElfSharedObjectExtended:
    def test_big_endian_elf(self, tmp_path: Path) -> None:
        """Big-endian ELF shared object (e.g. MIPS/PowerPC)."""
        f = tmp_path / "libfoo.so"
        e_ident = b"\x7fELF\x02\x02\x01" + b"\x00" * 9  # EI_DATA=2 (big-endian)
        e_type = struct.pack(">H", 3)  # ET_DYN big-endian
        rest = b"\x00" * (64 - 16 - 2)
        f.write_bytes(e_ident + e_type + rest)
        assert _is_elf_shared_object(f) is True

    def test_big_endian_exec(self, tmp_path: Path) -> None:
        """Big-endian ELF executable should not be detected as DSO."""
        f = tmp_path / "myapp"
        e_ident = b"\x7fELF\x02\x02\x01" + b"\x00" * 9
        e_type = struct.pack(">H", 2)  # ET_EXEC big-endian
        rest = b"\x00" * (64 - 16 - 2)
        f.write_bytes(e_ident + e_type + rest)
        assert _is_elf_shared_object(f) is False

    def test_truncated_file(self, tmp_path: Path) -> None:
        """File with ELF magic but truncated before e_type."""
        f = tmp_path / "truncated"
        f.write_bytes(b"\x7fELF\x02\x01")  # only 6 bytes
        assert _is_elf_shared_object(f) is False

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Non-existent file should return False."""
        assert _is_elf_shared_object(tmp_path / "nonexistent") is False


# ── is_package extended ──────────────────────────────────────────────────


class TestIsPackageExtended:
    def test_tar_xz_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "sdk.tar.xz"
        f.write_bytes(b"\xfd7zXZ\x00" + b"\x00" * 100)
        assert is_package(f) is True

    def test_tar_bz2_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "sdk.tar.bz2"
        f.write_bytes(b"BZ" + b"\x00" * 100)
        assert is_package(f) is True

    def test_plain_tar_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "sdk.tar"
        f.write_bytes(b"\x00" * 100)
        assert is_package(f) is True

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Non-existent file should return False (OSError branch)."""
        assert is_package(tmp_path / "nonexistent.bin") is False

    def test_unreadable_file(self, tmp_path: Path) -> None:
        """File that can't be opened triggers OSError path."""
        f = tmp_path / "unreadable.bin"
        f.write_bytes(b"hello")
        with mock.patch("builtins.open", side_effect=OSError("denied")):
            assert is_package(f) is False


# ── discover_shared_libraries extended ───────────────────────────────────


class TestDiscoverSharedLibrariesExtended:
    def test_broken_symlink_skipped(self, tmp_path: Path) -> None:
        """Broken symlinks should be skipped without error."""
        lib_dir = tmp_path / "usr" / "lib"
        lib_dir.mkdir(parents=True)
        broken = lib_dir / "libfoo.so"
        broken.symlink_to("/nonexistent/target")
        result = discover_shared_libraries(tmp_path)
        assert len(result) == 0

    def test_valid_symlink_to_dso(self, tmp_path: Path) -> None:
        """Symlink to a real DSO should be included."""
        lib_dir = tmp_path / "usr" / "lib"
        lib_dir.mkdir(parents=True)
        real = lib_dir / "libfoo.so.1.0"
        _make_minimal_elf_so(real)
        link = lib_dir / "libfoo.so"
        link.symlink_to("libfoo.so.1.0")
        result = discover_shared_libraries(tmp_path)
        names = [p.name for p in result]
        assert "libfoo.so" in names
        assert "libfoo.so.1.0" in names

    def test_symlink_to_interp_executable_is_skipped(self, tmp_path: Path) -> None:
        """A .so symlink to a PT_INTERP executable should not be included."""
        bin_dir = tmp_path / "usr" / "bin"
        lib_dir = tmp_path / "usr" / "lib"
        bin_dir.mkdir(parents=True)
        lib_dir.mkdir(parents=True)
        app = bin_dir / "app"
        _make_minimal_elf_dso_with_interp(app)
        link = lib_dir / "libapp.so"
        link.symlink_to("../bin/app")

        result = discover_shared_libraries(tmp_path)
        assert link not in result

    def test_symlink_to_partial_version_interp_executable_is_skipped(
        self, tmp_path: Path
    ) -> None:
        """A .so symlink to a malformed-version PT_INTERP executable is skipped."""
        bin_dir = tmp_path / "usr" / "bin"
        lib_dir = tmp_path / "usr" / "lib"
        bin_dir.mkdir(parents=True)
        lib_dir.mkdir(parents=True)
        app = bin_dir / "app.so.1.tmp"
        _make_minimal_elf_dso_with_interp(app)
        link = lib_dir / "libapp.so"
        link.symlink_to("../bin/app.so.1.tmp")

        result = discover_shared_libraries(tmp_path)
        assert link not in result

    def test_symlink_to_interp_dso_is_included(self, tmp_path: Path) -> None:
        """A .so symlink to a PT_INTERP DSO should still be included."""
        lib_dir = tmp_path / "usr" / "lib"
        lib_dir.mkdir(parents=True)
        real = lib_dir / "libcap.so.2.66"
        _make_minimal_elf_dso_with_interp(real)
        link = lib_dir / "libcap.so"
        link.symlink_to("libcap.so.2.66")

        result = discover_shared_libraries(tmp_path)
        names = [p.name for p in result]
        assert "libcap.so" in names
        assert "libcap.so.2.66" in names

    def test_usr_local_lib(self, tmp_path: Path) -> None:
        """DSOs in usr/local/lib should be found."""
        lib_dir = tmp_path / "usr" / "local" / "lib"
        lib_dir.mkdir(parents=True)
        _make_minimal_elf_so(lib_dir / "libcustom.so")
        result = discover_shared_libraries(tmp_path)
        assert len(result) == 1
        assert result[0].name == "libcustom.so"

    def test_lib64_path(self, tmp_path: Path) -> None:
        """DSOs in lib64 (no usr prefix) should be found."""
        lib_dir = tmp_path / "lib64"
        lib_dir.mkdir()
        _make_minimal_elf_so(lib_dir / "libfoo.so")
        result = discover_shared_libraries(tmp_path)
        assert len(result) == 1

    def test_lib_path(self, tmp_path: Path) -> None:
        """DSOs in lib (no usr prefix) should be found."""
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        _make_minimal_elf_so(lib_dir / "libfoo.so")
        result = discover_shared_libraries(tmp_path)
        assert len(result) == 1


# ── resolve_debug_info tests ─────────────────────────────────────────────


class TestResolveDebugInfo:
    def test_path_convention_match(self, tmp_path: Path) -> None:
        """Debug file found by name.debug convention."""
        binary = tmp_path / "usr" / "lib" / "libfoo.so"
        binary.parent.mkdir(parents=True)
        _make_minimal_elf_so(binary)

        debug_dir = tmp_path / "debug"
        debug_file = debug_dir / "usr" / "lib" / "debug" / "libfoo.so.debug"
        debug_file.parent.mkdir(parents=True)
        debug_file.write_bytes(b"debug data")

        result = resolve_debug_info(binary, debug_dir)
        assert result is not None
        assert result.name == "libfoo.so.debug"

    def test_no_debug_found(self, tmp_path: Path) -> None:
        """Returns None when no debug file exists."""
        binary = tmp_path / "libfoo.so"
        _make_minimal_elf_so(binary)
        debug_dir = tmp_path / "debug"
        debug_dir.mkdir()

        result = resolve_debug_info(binary, debug_dir)
        assert result is None

    def test_build_id_match(self, tmp_path: Path) -> None:
        """Debug file found by build-id when _read_build_id returns a value."""
        binary = tmp_path / "libfoo.so"
        _make_minimal_elf_so(binary)

        debug_dir = tmp_path / "debug"
        bid_file = debug_dir / ".build-id" / "ab" / "cdef1234.debug"
        bid_file.parent.mkdir(parents=True)
        bid_file.write_bytes(b"debug data")

        with mock.patch("abicheck.package._read_build_id", return_value="abcdef1234"):
            result = resolve_debug_info(binary, debug_dir)

        assert result is not None
        assert result == bid_file

    def test_build_id_in_usr_lib_debug(self, tmp_path: Path) -> None:
        """Build-id lookup in usr/lib/debug/.build-id subpath."""
        binary = tmp_path / "libfoo.so"
        _make_minimal_elf_so(binary)

        debug_dir = tmp_path / "debug"
        bid_file = debug_dir / "usr" / "lib" / "debug" / ".build-id" / "ab" / "cdef1234.debug"
        bid_file.parent.mkdir(parents=True)
        bid_file.write_bytes(b"debug data")

        with mock.patch("abicheck.package._read_build_id", return_value="abcdef1234"):
            result = resolve_debug_info(binary, debug_dir)

        assert result is not None
        assert result == bid_file


class TestReadBuildId:
    def test_returns_none_without_elftools(self, tmp_path: Path) -> None:
        """_read_build_id returns None when elftools is not available."""
        binary = tmp_path / "libfoo.so"
        _make_minimal_elf_so(binary)
        with mock.patch.dict("sys.modules", {"elftools": None, "elftools.elf": None, "elftools.elf.elffile": None}):
            result = _read_build_id(binary)
        assert result is None

    def test_returns_none_for_non_elf(self, tmp_path: Path) -> None:
        """_read_build_id returns None for non-ELF files."""
        f = tmp_path / "not_elf.txt"
        f.write_text("hello")
        result = _read_build_id(f)
        assert result is None


# ── RPM extractor extended ───────────────────────────────────────────────


class TestRpmExtractorExtended:
    def test_detect_oserror_returns_false(self, tmp_path: Path) -> None:
        """RPM detect returns False when file can't be read."""
        f = tmp_path / "noext"
        # File doesn't exist → OSError
        assert not RpmExtractor().detect(f)

    def test_extract_missing_rpm2cpio(self, tmp_path: Path) -> None:
        """RuntimeError when rpm2cpio is not installed."""
        f = tmp_path / "test.rpm"
        f.write_bytes(b"\xed\xab\xee\xdb" + b"\x00" * 100)
        out = tmp_path / "output"
        out.mkdir()
        with mock.patch("abicheck.package.shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="rpm2cpio not found"):
                RpmExtractor().extract(f, out)

    def test_extract_missing_cpio(self, tmp_path: Path) -> None:
        """RuntimeError when cpio is not installed."""
        f = tmp_path / "test.rpm"
        f.write_bytes(b"\xed\xab\xee\xdb" + b"\x00" * 100)
        out = tmp_path / "output"
        out.mkdir()

        def _which(cmd: str) -> str | None:
            return "/usr/bin/rpm2cpio" if cmd == "rpm2cpio" else None

        with mock.patch("abicheck.package.shutil.which", side_effect=_which):
            with pytest.raises(RuntimeError, match="cpio not found"):
                RpmExtractor().extract(f, out)


# ── Deb extractor extended ───────────────────────────────────────────────


class TestDebExtractorExtended:
    def test_detect_oserror_returns_false(self, tmp_path: Path) -> None:
        """Deb detect returns False when file can't be read."""
        assert not DebExtractor().detect(tmp_path / "nonexistent")

    def test_extract_missing_ar(self, tmp_path: Path) -> None:
        """RuntimeError when ar is not installed."""
        f = tmp_path / "test.deb"
        f.write_bytes(b"!<arch>\n" + b"\x00" * 100)
        out = tmp_path / "output"
        out.mkdir()
        with mock.patch("abicheck.package.shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="ar not found"):
                DebExtractor().extract(f, out)

    def test_extract_data_tar_zst_uses_zstd_tar_helper(self, tmp_path: Path) -> None:
        """DebExtractor handles modern Debian data.tar.zst payloads."""
        f = tmp_path / "test.deb"
        f.write_bytes(b"!<arch>\n" + b"\x00" * 100)
        out = tmp_path / "output"
        out.mkdir()

        def fake_run(*args, **kwargs):
            staging = Path(kwargs.get("cwd", "."))
            (staging / "data.tar.zst").write_bytes(b"zstd payload")
            return mock.Mock(returncode=0)

        with mock.patch("abicheck.package.shutil.which", return_value="/usr/bin/ar"):
            with mock.patch("abicheck.package.subprocess.run", side_effect=fake_run) as mock_run:
                with mock.patch("abicheck.package.TarExtractor._safe_extract_zst_tar") as extract_zst:
                    DebExtractor().extract(f, out)

        mock_run.assert_called_once()
        ar_cmd = mock_run.call_args.args[0]
        assert Path(ar_cmd[2]).is_absolute()
        extract_zst.assert_called_once()
        assert extract_zst.call_args.args[0].name == "data.tar.zst"

    def test_extract_symbols_file_from_control_tar(self, tmp_path: Path) -> None:
        """CLI-audit P2: DebExtractor only ever read data.tar.*; control.tar.*
        (which carries the dpkg-gensymbols(1) symbols contract) was never
        extracted at all, so the Debian symbols contract could not
        participate in a package compare. Builds a real (uncompressed)
        control.tar containing a symbols file and verifies
        ExtractResult.symbols_file points at the extracted copy with the
        right content."""
        f = tmp_path / "test.deb"
        f.write_bytes(b"!<arch>\n" + b"\x00" * 100)
        out = tmp_path / "output"
        out.mkdir()
        symbols_text = "libfoo.so.1 libfoo1 #MINVER#\n _ZN3foo3barEv@Base 1.0\n"

        def fake_run(*args, **kwargs):
            staging = Path(kwargs.get("cwd", "."))
            with tarfile.open(staging / "data.tar", "w"):
                pass
            with tarfile.open(staging / "control.tar", "w") as tf:
                data = symbols_text.encode()
                info = tarfile.TarInfo(name="symbols")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            return mock.Mock(returncode=0)

        with mock.patch("abicheck.package.shutil.which", return_value="/usr/bin/ar"):
            with mock.patch("abicheck.package.subprocess.run", side_effect=fake_run):
                result = DebExtractor().extract(f, out)

        assert result.symbols_file is not None
        assert result.symbols_file.name == "symbols"
        assert result.symbols_file.read_text() == symbols_text

    def test_extract_no_control_tar_leaves_symbols_file_none(self, tmp_path: Path) -> None:
        """A .deb with no control.tar.* member (malformed, but data.tar.*
        alone is enough to raise SnapshotError only when data.tar.* itself
        is missing) leaves symbols_file None rather than raising."""
        f = tmp_path / "test.deb"
        f.write_bytes(b"!<arch>\n" + b"\x00" * 100)
        out = tmp_path / "output"
        out.mkdir()

        def fake_run(*args, **kwargs):
            staging = Path(kwargs.get("cwd", "."))
            with tarfile.open(staging / "data.tar", "w"):
                pass
            return mock.Mock(returncode=0)

        with mock.patch("abicheck.package.shutil.which", return_value="/usr/bin/ar"):
            with mock.patch("abicheck.package.subprocess.run", side_effect=fake_run):
                result = DebExtractor().extract(f, out)

        assert result.symbols_file is None

    def test_extract_control_tar_without_symbols_leaves_symbols_file_none(
        self, tmp_path: Path,
    ) -> None:
        """A control.tar.* present but with no ./symbols member (the common
        case -- most packages aren't built with dpkg-gensymbols) leaves
        symbols_file None."""
        f = tmp_path / "test.deb"
        f.write_bytes(b"!<arch>\n" + b"\x00" * 100)
        out = tmp_path / "output"
        out.mkdir()

        def fake_run(*args, **kwargs):
            staging = Path(kwargs.get("cwd", "."))
            with tarfile.open(staging / "data.tar", "w"):
                pass
            with tarfile.open(staging / "control.tar", "w") as tf:
                data = b"Package: libfoo1\n"
                info = tarfile.TarInfo(name="control")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            return mock.Mock(returncode=0)

        with mock.patch("abicheck.package.shutil.which", return_value="/usr/bin/ar"):
            with mock.patch("abicheck.package.subprocess.run", side_effect=fake_run):
                result = DebExtractor().extract(f, out)

        assert result.symbols_file is None

    def test_extract_data_tar_planted_deb_control_symbols_is_not_trusted(
        self, tmp_path: Path,
    ) -> None:
        """Codex review: data.tar.*'s own payload can contain a member
        literally named .deb_control/symbols (crafted or coincidental); if
        control.tar.* then has no symbols member of its own, the fixed
        .deb_control extraction directory must not silently keep the
        payload's planted file and return it as though it were the genuine
        dpkg-gensymbols(1) contract from control.tar.*."""
        f = tmp_path / "test.deb"
        f.write_bytes(b"!<arch>\n" + b"\x00" * 100)
        out = tmp_path / "output"
        out.mkdir()
        planted = "libfoo.so.1 libfoo1 9999.0\n _ZN3foo3evilEv@Base 9999.0\n"

        def fake_run(*args, **kwargs):
            staging = Path(kwargs.get("cwd", "."))
            with tarfile.open(staging / "data.tar", "w") as tf:
                data = planted.encode()
                info = tarfile.TarInfo(name=".deb_control/symbols")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            with tarfile.open(staging / "control.tar", "w") as tf:
                data = b"Package: libfoo1\n"
                info = tarfile.TarInfo(name="control")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            return mock.Mock(returncode=0)

        with mock.patch("abicheck.package.shutil.which", return_value="/usr/bin/ar"):
            with mock.patch("abicheck.package.subprocess.run", side_effect=fake_run):
                result = DebExtractor().extract(f, out)

        assert result.symbols_file is None
        # The planted payload file must have been wiped, not left in place.
        assert not (out / ".deb_control" / "symbols").exists()

    def test_extract_data_tar_planted_deb_control_as_plain_file_is_removed(
        self, tmp_path: Path,
    ) -> None:
        """Same collision as above, but data.tar.* plants .deb_control itself
        as a plain file (not a directory) -- control_dir.mkdir() would raise
        FileExistsError against a stale file the same way it would against a
        stale directory; the pre-extraction cleanup must handle both shapes."""
        f = tmp_path / "test.deb"
        f.write_bytes(b"!<arch>\n" + b"\x00" * 100)
        out = tmp_path / "output"
        out.mkdir()
        symbols_text = "libfoo.so.1 libfoo1 1.0\n _ZN3foo3barEv@Base 1.0\n"

        def fake_run(*args, **kwargs):
            staging = Path(kwargs.get("cwd", "."))
            with tarfile.open(staging / "data.tar", "w") as tf:
                data = b"not a directory"
                info = tarfile.TarInfo(name=".deb_control")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            with tarfile.open(staging / "control.tar", "w") as tf:
                data = symbols_text.encode()
                info = tarfile.TarInfo(name="symbols")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            return mock.Mock(returncode=0)

        with mock.patch("abicheck.package.shutil.which", return_value="/usr/bin/ar"):
            with mock.patch("abicheck.package.subprocess.run", side_effect=fake_run):
                result = DebExtractor().extract(f, out)

        assert result.symbols_file is not None
        assert result.symbols_file.read_text() == symbols_text

    def test_extract_relative_deb_path_passes_absolute_path_to_ar(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """DebExtractor changes cwd for ar, so relative input paths must be resolved."""
        f = tmp_path / "test.deb"
        f.write_bytes(b"!<arch>\n" + b"\x00" * 100)
        out = tmp_path / "output"
        out.mkdir()
        monkeypatch.chdir(tmp_path)
        ar_input_paths: list[Path] = []

        def fake_run(args, **kwargs):
            ar_input_paths.append(Path(args[2]))
            staging = Path(kwargs.get("cwd", "."))
            (staging / "data.tar.xz").write_bytes(b"tar payload")
            return mock.Mock(returncode=0)

        with mock.patch("abicheck.package.shutil.which", return_value="/usr/bin/ar"):
            with mock.patch("abicheck.package.subprocess.run", side_effect=fake_run):
                with mock.patch("abicheck.package.TarExtractor._safe_extract"):
                    DebExtractor().extract(Path("test.deb"), out)

        assert ar_input_paths == [f.resolve()]

    def test_tar_zst_is_package(self, tmp_path: Path) -> None:
        """Plain .tar.zst archives are recognized as package inputs."""
        f = tmp_path / "sdk.tar.zst"
        f.write_bytes(b"not real zstd")
        assert TarExtractor().detect(f)
        assert is_package(f)

    def test_safe_extract_zst_tar_uses_private_staging_dir(self, tmp_path: Path) -> None:
        """Decompression must not clobber a sibling .tar next to user input."""
        cache = tmp_path / "cache"
        target = tmp_path / "target"
        cache.mkdir()
        target.mkdir()
        zst_path = cache / "sdk.tar.zst"
        sibling_tar = cache / "sdk.tar"
        zst_path.write_bytes(b"compressed")
        sibling_tar.write_text("do not touch")

        mock_zstd = mock.MagicMock()
        mock_dctx = mock.MagicMock()
        mock_zstd.ZstdDecompressor.return_value = mock_dctx

        class FakeReader:
            def __enter__(self):
                return io.BytesIO(b"tar data")

            def __exit__(self, *args):
                return None

        mock_dctx.stream_reader.return_value = FakeReader()

        with mock.patch.dict(sys.modules, {"zstandard": mock_zstd}):
            with mock.patch("abicheck.package.TarExtractor._safe_extract") as safe_extract:
                TarExtractor._safe_extract_zst_tar(zst_path, target)

        tar_path = safe_extract.call_args.args[0]
        extract_target = safe_extract.call_args.args[1]
        assert tar_path.parent.parent == target
        assert not tar_path.exists()
        assert not tar_path.parent.exists()
        assert extract_target == target
        assert sibling_tar.read_text() == "do not touch"

    def test_safe_extract_zst_tar_cli_fallback_writes_to_staging_dir(
        self, tmp_path: Path,
    ) -> None:
        """The zstd CLI fallback must also avoid writing next to the input."""
        cache = tmp_path / "cache"
        target = tmp_path / "target"
        cache.mkdir()
        target.mkdir()
        zst_path = cache / "sdk.tar.zst"
        sibling_tar = cache / "sdk.tar"
        zst_path.write_bytes(b"compressed")
        sibling_tar.write_text("do not touch")

        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "zstandard":
                raise ImportError
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=fake_import):
            with mock.patch("abicheck.package.shutil.which", return_value="/usr/bin/zstd"):
                with mock.patch("abicheck.package.subprocess.run") as run_zstd:
                    with mock.patch("abicheck.package.TarExtractor._safe_extract") as safe_extract:
                        TarExtractor._safe_extract_zst_tar(zst_path, target)

        cmd = run_zstd.call_args.args[0]
        output_path = Path(cmd[cmd.index("-o") + 1])
        tar_path = safe_extract.call_args.args[0]
        assert output_path == tar_path
        assert output_path.parent.parent == target
        assert not output_path.parent.exists()
        assert safe_extract.call_args.args[1] == target
        assert sibling_tar.read_text() == "do not touch"


# ── Conda extractor extended ────────────────────────────────────────────


class TestCondaExtractorExtended:
    def test_detect_tar_bz2_few_dashes_rejected(self, tmp_path: Path) -> None:
        """tar.bz2 with fewer than 2 dashes is not conda."""
        f = tmp_path / "data.tar.bz2"
        with tarfile.open(f, "w:bz2") as tf:
            info = tarfile.TarInfo(name="info/index.json")
            info.size = 2
            tf.addfile(info, io.BytesIO(b"{}"))
        assert not CondaExtractor().detect(f)

    def test_detect_corrupt_tar_bz2(self, tmp_path: Path) -> None:
        """Corrupt tar.bz2 with conda-style name should return False."""
        f = tmp_path / "numpy-1.26-h123-0.tar.bz2"
        f.write_bytes(b"not a valid bz2 archive")
        assert not CondaExtractor().detect(f)


# ── PackageExtractor protocol tests ─────────────────────────────────────


class TestPackageExtractorProtocol:
    def test_tar_is_package_extractor(self) -> None:
        assert isinstance(TarExtractor(), PackageExtractor)

    def test_rpm_is_package_extractor(self) -> None:
        assert isinstance(RpmExtractor(), PackageExtractor)

    def test_deb_is_package_extractor(self) -> None:
        assert isinstance(DebExtractor(), PackageExtractor)

    def test_conda_is_package_extractor(self) -> None:
        assert isinstance(CondaExtractor(), PackageExtractor)

    def test_wheel_is_package_extractor(self) -> None:
        assert isinstance(WheelExtractor(), PackageExtractor)

    def test_dir_is_package_extractor(self) -> None:
        assert isinstance(DirExtractor(), PackageExtractor)


# ── CLI integration: --dso-only and --keep-extracted ─────────────────────


class TestCompareReleaseDsoOnly:
    def test_dso_only_flag_accepted(self, tmp_path: Path) -> None:
        """Verify --dso-only flag is accepted by compare-release."""
        from click.testing import CliRunner

        from abicheck.cli import main
        from abicheck.model import AbiSnapshot, Function, Visibility
        from abicheck.serialization import snapshot_to_json

        snap = AbiSnapshot(
            library="libfoo.so", version="1.0",
            functions=[Function(name="foo", mangled="_Z3foov",
                                return_type="int", visibility=Visibility.PUBLIC)],
        )

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        (old_dir / "libfoo.so.json").write_text(snapshot_to_json(snap))
        (new_dir / "libfoo.so.json").write_text(snapshot_to_json(snap))

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_dir), str(new_dir),
            "--format", "json", "--dso-only",
        ])
        # With --dso-only, JSON snapshots are not ELF DSOs, so no pairs found
        # but the command should still succeed
        assert result.exit_code == 0, f"Exit {result.exit_code}: {result.output}"


class TestKeepExtractedActuallyKeeps:
    def test_temp_dirs_survive_with_keep_extracted(self, tmp_path: Path) -> None:
        """Verify --keep-extracted actually preserves temp dirs after command exits."""
        from click.testing import CliRunner

        from abicheck.cli import main
        from abicheck.model import AbiSnapshot, Function, Visibility
        from abicheck.serialization import snapshot_to_json

        snap = AbiSnapshot(
            library="libfoo.so", version="1.0",
            functions=[Function(name="foo", mangled="_Z3foov",
                                return_type="int", visibility=Visibility.PUBLIC)],
        )

        archive = tmp_path / "pkg.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            data = snapshot_to_json(snap).encode()
            info = tarfile.TarInfo(name="libfoo.so.json")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(archive), str(archive),
            "--format", "json", "--keep-extracted",
        ])
        assert result.exit_code == 0, f"Exit {result.exit_code}: {result.output}"
        # Check output mentions kept dirs
        assert "Extracted files kept in:" in result.output


# ── RpmExtractor post_validate tests ─────────────────────────────────────


class TestRpmPostValidate:
    def test_post_validate_clean_dir(self, tmp_path: Path) -> None:
        """Post-validation passes on a clean directory."""
        (tmp_path / "usr" / "lib").mkdir(parents=True)
        (tmp_path / "usr" / "lib" / "libfoo.so").write_bytes(b"data")
        # Should not raise
        RpmExtractor._post_validate(tmp_path)

    def test_post_validate_with_safe_symlink(self, tmp_path: Path) -> None:
        """Post-validation passes with symlinks that stay within root."""
        lib_dir = tmp_path / "usr" / "lib"
        lib_dir.mkdir(parents=True)
        (lib_dir / "libfoo.so.1").write_bytes(b"data")
        (lib_dir / "libfoo.so").symlink_to("libfoo.so.1")
        RpmExtractor._post_validate(tmp_path)

    def test_post_validate_escaping_symlink(self, tmp_path: Path) -> None:
        """Post-validation catches symlinks pointing outside root."""
        lib_dir = tmp_path / "usr" / "lib"
        lib_dir.mkdir(parents=True)
        evil = lib_dir / "evil.so"
        evil.symlink_to("/etc/passwd")
        with pytest.raises(ExtractionSecurityError, match="escapes extraction root|symlink target"):
            RpmExtractor._post_validate(tmp_path)


# ── Discover shared libraries: symlink edge cases ───────────────────────


class TestDiscoverSymlinkEdgeCases:
    def test_symlink_oserror_skipped(self, tmp_path: Path) -> None:
        """Symlink that raises OSError on resolve is skipped."""
        lib_dir = tmp_path / "usr" / "lib"
        lib_dir.mkdir(parents=True)
        broken = lib_dir / "libfoo.so"
        # Create a symlink to a non-existent target
        broken.symlink_to("/nonexistent/does_not_exist")
        result = discover_shared_libraries(tmp_path)
        # Should not crash, broken symlink is skipped
        assert len(result) == 0


# ── Conda v2 extraction (mocked zstandard) ──────────────────────────────


class TestCondaV2Extraction:
    def test_extract_v2_no_zstandard_no_zstd(self, tmp_path: Path) -> None:
        """Conda v2 raises RuntimeError when neither zstandard nor zstd is available."""
        # Create a minimal .conda (zip) with a pkg-*.tar.zst file
        conda_pkg = tmp_path / "test.conda"
        with zipfile.ZipFile(conda_pkg, "w") as zf:
            zf.writestr("metadata.json", '{"name":"test"}')
            zf.writestr("pkg-test-abc.tar.zst", b"fake zstd data")

        out = tmp_path / "output"
        out.mkdir()

        with mock.patch.dict("sys.modules", {"zstandard": None}):
            with mock.patch("abicheck.package.shutil.which", return_value=None):
                with pytest.raises(RuntimeError, match="Cannot extract .tar.zst"):
                    CondaExtractor().extract(conda_pkg, out)


# ── Deb extractor: no data.tar error ────────────────────────────────────


class TestDebExtractorNoDataTar:
    def test_deb_no_data_tar(self, tmp_path: Path) -> None:
        """DebExtractor raises when deb has no data.tar.* member."""
        # We can't easily create a real ar archive without `ar`, but we can test
        # the missing data.tar detection by mocking ar execution
        f = tmp_path / "test.deb"
        f.write_bytes(b"!<arch>\n" + b"\x00" * 100)
        out = tmp_path / "output"
        out.mkdir()

        def fake_run(*args, **kwargs):
            # Simulate ar extracting but not producing data.tar.*
            staging = Path(kwargs.get("cwd", "."))
            (staging / "control.tar.gz").write_bytes(b"control")
            return mock.Mock(returncode=0)

        with mock.patch("abicheck.package.shutil.which", return_value="/usr/bin/ar"):
            with mock.patch("abicheck.package.subprocess.run", side_effect=fake_run):
                with pytest.raises(RuntimeError, match="No data.tar"):
                    DebExtractor().extract(f, out)


# ── Device/FIFO rejection in tar extraction ──────────────────────────────


class TestTarDeviceFifoRejection:
    def test_char_device_rejected(self, tmp_path: Path) -> None:
        """Tar archive containing a character device is rejected."""
        archive = tmp_path / "evil.tar"
        with tarfile.open(archive, "w") as tf:
            info = tarfile.TarInfo(name="dev/evil_chr")
            info.type = tarfile.CHRTYPE
            info.devmajor = 1
            info.devminor = 3
            tf.addfile(info)

        out = tmp_path / "output"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError, match="device or FIFO"):
            TarExtractor().extract(archive, out)

    def test_block_device_rejected(self, tmp_path: Path) -> None:
        """Tar archive containing a block device is rejected."""
        archive = tmp_path / "evil.tar"
        with tarfile.open(archive, "w") as tf:
            info = tarfile.TarInfo(name="dev/evil_blk")
            info.type = tarfile.BLKTYPE
            info.devmajor = 8
            info.devminor = 0
            tf.addfile(info)

        out = tmp_path / "output"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError, match="device or FIFO"):
            TarExtractor().extract(archive, out)

    def test_fifo_rejected(self, tmp_path: Path) -> None:
        """Tar archive containing a FIFO is rejected."""
        archive = tmp_path / "evil.tar"
        with tarfile.open(archive, "w") as tf:
            info = tarfile.TarInfo(name="tmp/evil_fifo")
            info.type = tarfile.FIFOTYPE
            tf.addfile(info)

        out = tmp_path / "output"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError, match="device or FIFO"):
            TarExtractor().extract(archive, out)

    def test_regular_files_accepted(self, tmp_path: Path) -> None:
        """Normal files in tar should still extract fine (regression check)."""
        archive = tmp_path / "normal.tar.gz"
        _make_tar(archive, {"usr/lib/libfoo.so": b"data"})
        out = tmp_path / "output"
        out.mkdir()
        TarExtractor().extract(archive, out)
        assert (out / "usr/lib/libfoo.so").exists()


# ── _post_validate directory entry coverage ──────────────────────────────


class TestRpmPostValidateDirectories:
    def test_directory_symlink_escaping(self, tmp_path: Path) -> None:
        """Post-validation catches directory symlinks pointing outside root."""
        lib_dir = tmp_path / "usr" / "lib"
        lib_dir.mkdir(parents=True)
        # Create a directory symlink pointing outside the extraction root
        external_dir = tmp_path.parent / "external_target"
        external_dir.mkdir(exist_ok=True)
        evil_dir = lib_dir / "evil_dir"
        evil_dir.symlink_to(external_dir)
        with pytest.raises(ExtractionSecurityError, match="escapes extraction root|symlink target"):
            RpmExtractor._post_validate(tmp_path)

    def test_nested_directory_safe(self, tmp_path: Path) -> None:
        """Legitimate nested directories pass validation."""
        (tmp_path / "usr" / "lib" / "subdir").mkdir(parents=True)
        (tmp_path / "usr" / "lib" / "subdir" / "libfoo.so").write_bytes(b"data")
        RpmExtractor._post_validate(tmp_path)


# ── resolve_debug_info disambiguation ────────────────────────────────────


class TestResolveDebugInfoDisambiguation:
    def test_multiple_candidates_path_similarity(self, tmp_path: Path) -> None:
        """When multiple .debug files match, prefer the one with better path overlap."""
        binary = tmp_path / "extract" / "usr" / "lib64" / "libfoo.so"
        binary.parent.mkdir(parents=True)
        _make_minimal_elf_so(binary)

        debug_dir = tmp_path / "debug"

        # Create two candidates: one with matching path, one without
        good = debug_dir / "usr" / "lib64" / "libfoo.so.debug"
        good.parent.mkdir(parents=True)
        good.write_bytes(b"good debug")

        bad = debug_dir / "other" / "path" / "libfoo.so.debug"
        bad.parent.mkdir(parents=True)
        bad.write_bytes(b"bad debug")

        result = resolve_debug_info(binary, debug_dir)
        assert result is not None
        # The good candidate shares more path components (usr, lib64)
        assert result == good

    def test_single_candidate_returned_directly(self, tmp_path: Path) -> None:
        """When exactly one .debug file matches, it's returned without disambiguation."""
        binary = tmp_path / "libbar.so"
        _make_minimal_elf_so(binary)

        debug_dir = tmp_path / "debug"
        only = debug_dir / "libbar.so.debug"
        only.parent.mkdir(parents=True)
        only.write_bytes(b"debug data")

        result = resolve_debug_info(binary, debug_dir)
        assert result == only

    def test_path_mirror_strategy(self, tmp_path: Path) -> None:
        """Debug file found by path mirroring (binary path mirrored under debug_dir)."""
        binary = tmp_path / "extract" / "usr" / "lib64" / "libfoo.so"
        binary.parent.mkdir(parents=True)
        _make_minimal_elf_so(binary)

        debug_dir = tmp_path / "debug"
        mirrored = debug_dir / "usr" / "lib64" / "libfoo.so.debug"
        mirrored.parent.mkdir(parents=True)
        mirrored.write_bytes(b"debug data")

        result = resolve_debug_info(binary, debug_dir)
        assert result is not None
        assert result == mirrored
