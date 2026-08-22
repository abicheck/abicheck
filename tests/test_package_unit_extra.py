"""Additional unit tests for abicheck.package — RPM extraction, zstd tar, build-ID resolution."""
from __future__ import annotations

import subprocess
import tarfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from abicheck.errors import ExtractionSecurityError
from abicheck.package import (
    CondaExtractor,
    RpmExtractor,
    _read_build_id,
    resolve_debug_info,
)

# ---------------------------------------------------------------------------
# RPM extraction
# ---------------------------------------------------------------------------


class TestRpmExtractionTimeout:
    """RPM extraction pipeline timeout handling (lines 219-261)."""

    @patch("shutil.which", side_effect=lambda cmd: f"/usr/bin/{cmd}")
    @patch("subprocess.Popen")
    def test_cpio_communicate_timeout_kills_both(self, mock_popen, _mock_which, tmp_path):
        rpm2cpio_proc = MagicMock()
        rpm2cpio_proc.stdout = MagicMock()
        cpio_proc = MagicMock()
        cpio_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="cpio", timeout=120)

        mock_popen.side_effect = [rpm2cpio_proc, cpio_proc]

        with pytest.raises(RuntimeError, match="timed out"):
            RpmExtractor._rpm_extract(tmp_path / "pkg.rpm", tmp_path)

        cpio_proc.kill.assert_called_once()
        rpm2cpio_proc.kill.assert_called_once()

    @patch("shutil.which", side_effect=lambda cmd: f"/usr/bin/{cmd}")
    @patch("subprocess.Popen")
    def test_rpm2cpio_wait_timeout_kills(self, mock_popen, _mock_which, tmp_path):
        rpm2cpio_proc = MagicMock()
        rpm2cpio_proc.stdout = MagicMock()
        # First call (with timeout) raises; second call (cleanup after kill) succeeds.
        rpm2cpio_proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="rpm2cpio", timeout=120),
            0,
        ]

        cpio_proc = MagicMock()
        cpio_proc.communicate.return_value = (b"", b"")
        cpio_proc.returncode = 0

        mock_popen.side_effect = [rpm2cpio_proc, cpio_proc]

        with pytest.raises(RuntimeError, match="rpm2cpio timed out"):
            RpmExtractor._rpm_extract(tmp_path / "pkg.rpm", tmp_path)

        rpm2cpio_proc.kill.assert_called_once()


class TestRpmExtractionFailures:
    """RPM extraction subprocess failure paths."""

    @patch("shutil.which", side_effect=lambda cmd: f"/usr/bin/{cmd}")
    @patch("subprocess.Popen")
    def test_rpm2cpio_nonzero_raises(self, mock_popen, _mock_which, tmp_path):
        rpm2cpio_proc = MagicMock()
        rpm2cpio_proc.stdout = MagicMock()
        rpm2cpio_proc.returncode = 1
        rpm2cpio_proc.wait.return_value = 1

        cpio_proc = MagicMock()
        cpio_proc.communicate.return_value = (b"", b"")
        cpio_proc.returncode = 0

        mock_popen.side_effect = [rpm2cpio_proc, cpio_proc]

        with pytest.raises(RuntimeError, match="rpm2cpio failed"):
            RpmExtractor._rpm_extract(tmp_path / "pkg.rpm", tmp_path)

    @patch("shutil.which", side_effect=lambda cmd: f"/usr/bin/{cmd}")
    @patch("subprocess.Popen")
    def test_cpio_nonzero_raises_with_stderr(self, mock_popen, _mock_which, tmp_path):
        rpm2cpio_proc = MagicMock()
        rpm2cpio_proc.stdout = MagicMock()
        rpm2cpio_proc.returncode = 0
        rpm2cpio_proc.wait.return_value = 0

        cpio_proc = MagicMock()
        cpio_proc.communicate.return_value = (b"", b"cpio: bad magic")
        cpio_proc.returncode = 1

        mock_popen.side_effect = [rpm2cpio_proc, cpio_proc]

        with pytest.raises(RuntimeError, match="cpio extraction failed: cpio: bad magic"):
            RpmExtractor._rpm_extract(tmp_path / "pkg.rpm", tmp_path)


class TestRpmExtractionSuccess:
    """RPM extraction pipeline success path."""

    @patch("shutil.which", side_effect=lambda cmd: f"/usr/bin/{cmd}")
    @patch("subprocess.Popen")
    def test_successful_pipeline(self, mock_popen, _mock_which, tmp_path):
        rpm2cpio_proc = MagicMock()
        rpm2cpio_proc.stdout = MagicMock()
        rpm2cpio_proc.returncode = 0
        rpm2cpio_proc.wait.return_value = 0

        cpio_proc = MagicMock()
        cpio_proc.communicate.return_value = (b"", b"")
        cpio_proc.returncode = 0

        mock_popen.side_effect = [rpm2cpio_proc, cpio_proc]

        # Should not raise
        RpmExtractor._rpm_extract(tmp_path / "pkg.rpm", tmp_path)

        assert mock_popen.call_count == 2
        rpm2cpio_proc.stdout.close.assert_called_once()


# ---------------------------------------------------------------------------
# Zstandard tar extraction (lines 440-490)
# ---------------------------------------------------------------------------


def _make_tar_bytes(members: list[tuple[str, str, int | None]]) -> bytes:
    """Build an in-memory tar archive.

    Each member is (name, type, size) where type is one of:
    'file', 'sym', 'chr', 'blk', 'fifo', 'lnk'.
    """
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, kind, _size in members:
            info = tarfile.TarInfo(name=name)
            if kind == "file":
                info.type = tarfile.REGTYPE
                info.size = 0
            elif kind == "sym":
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
            elif kind == "chr":
                info.type = tarfile.CHRTYPE
            elif kind == "blk":
                info.type = tarfile.BLKTYPE
            elif kind == "fifo":
                info.type = tarfile.FIFOTYPE
            elif kind == "lnk":
                info.type = tarfile.LNKTYPE
                info.linkname = name  # hardlink to self (valid)
            tf.addfile(info)
    return buf.getvalue()


class TestZstTarSecurityChecks:
    """Zstandard tar extraction security validation (lines 440-468)."""

    def _extract_with_zstd_mock(self, tar_bytes: bytes, target_dir: Path):
        """Run CondaExtractor._extract_zst_tar with mocked zstandard."""
        import sys

        mock_zstd = MagicMock()
        mock_dctx = MagicMock()
        mock_zstd.ZstdDecompressor.return_value = mock_dctx

        # stream_reader context manager returns a BytesIO wrapping our tar data.
        # The code opens the zst file twice (validate pass + extract pass),
        # and calls stream_reader each time — provide fresh BytesIO each time.
        def make_reader(fobj):
            stream = BytesIO(tar_bytes)

            class FakeReader:
                def __enter__(self_):
                    return stream
                def __exit__(self_, *args):
                    pass

            return FakeReader()

        mock_dctx.stream_reader = make_reader

        # Create the .tar.zst file so that open() succeeds
        zst_path = target_dir / "test.tar.zst"
        zst_path.write_bytes(b"dummy")

        with patch.dict(sys.modules, {"zstandard": mock_zstd}):
            CondaExtractor._extract_zst_tar(zst_path, target_dir)

    def test_device_chr_raises(self, tmp_path):
        tar_bytes = _make_tar_bytes([("evil_device", "chr", None)])
        with pytest.raises(ExtractionSecurityError, match="device or FIFO"):
            self._extract_with_zstd_mock(tar_bytes, tmp_path)

    def test_device_blk_raises(self, tmp_path):
        tar_bytes = _make_tar_bytes([("evil_block", "blk", None)])
        with pytest.raises(ExtractionSecurityError, match="device or FIFO"):
            self._extract_with_zstd_mock(tar_bytes, tmp_path)

    def test_fifo_raises(self, tmp_path):
        tar_bytes = _make_tar_bytes([("evil_fifo", "fifo", None)])
        with pytest.raises(ExtractionSecurityError, match="device or FIFO"):
            self._extract_with_zstd_mock(tar_bytes, tmp_path)

    def test_symlink_escaping_root_raises(self, tmp_path):
        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo(name="escape_link")
            info.type = tarfile.SYMTYPE
            info.linkname = "../../../../etc/passwd"
            tf.addfile(info)
        tar_bytes = buf.getvalue()
        with pytest.raises(ExtractionSecurityError, match="symlink target"):
            self._extract_with_zstd_mock(tar_bytes, tmp_path)

    def test_hardlink_validated_as_member_path(self, tmp_path):
        """Hardlinks are validated via _validate_member_path (not symlink check)."""
        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            # Add a regular file first, then a hardlink to it
            info = tarfile.TarInfo(name="realfile.txt")
            info.type = tarfile.REGTYPE
            info.size = 0
            tf.addfile(info)
            lnk = tarfile.TarInfo(name="hardlink.txt")
            lnk.type = tarfile.LNKTYPE
            lnk.linkname = "realfile.txt"
            tf.addfile(lnk)
        tar_bytes = buf.getvalue()
        # Should not raise since both paths are within root
        self._extract_with_zstd_mock(tar_bytes, tmp_path)


class TestZstFallbackToExternalCommand:
    """Fallback to system zstd command (lines 480-490)."""

    @patch("abicheck.package.TarExtractor._safe_extract")
    @patch("subprocess.Popen")
    @patch("shutil.which", return_value="/usr/bin/zstd")
    def test_uses_subprocess_when_no_python_zstandard(
        self, mock_which, mock_popen, mock_safe_extract, tmp_path
    ):
        # Streamed via Popen + a chunked read loop, not subprocess.run(),
        # since the decompression-bomb fix needs to bound total output
        # size incrementally rather than let the CLI write its own
        # decoded output straight to disk unbounded (Codex review, fresh
        # evidence) -- see TestTarExtractorZstDecompressionBomb in
        # test_package.py for the real end-to-end bomb-rejection coverage.
        import sys

        zst_path = tmp_path / "test.tar.zst"
        zst_path.touch()

        proc = MagicMock()
        proc.stdout = BytesIO(b"")
        proc.returncode = 0
        proc.communicate.return_value = (b"", b"")
        proc.poll.return_value = 0
        mock_popen.return_value = proc

        # Make zstandard import fail
        with patch.dict(sys.modules, {"zstandard": None}):
            with patch("builtins.__import__", side_effect=_import_blocker("zstandard")):
                CondaExtractor._extract_zst_tar(zst_path, tmp_path)

        mock_popen.assert_called_once()
        args = mock_popen.call_args
        assert args[0][0][0] == "/usr/bin/zstd"
        assert "-dc" in args[0][0]
        assert any(a.startswith("--memory=") for a in args[0][0])
        mock_safe_extract.assert_called_once()


class TestZstNotAvailable:
    """Neither zstandard nor zstd command available."""

    @patch("shutil.which", return_value=None)
    def test_raises_runtime_error(self, _mock_which, tmp_path):
        import sys

        zst_path = tmp_path / "test.tar.zst"
        zst_path.touch()

        with patch.dict(sys.modules, {"zstandard": None}):
            with patch("builtins.__import__", side_effect=_import_blocker("zstandard")):
                with pytest.raises(RuntimeError, match="install.*zstandard.*zstd"):
                    CondaExtractor._extract_zst_tar(zst_path, tmp_path)


def _import_blocker(blocked_name: str):
    """Return an __import__ side_effect that blocks one module."""
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def _blocker(name, *args, **kwargs):
        if name == blocked_name:
            raise ImportError(f"Mocked: no module named '{blocked_name}'")
        return real_import(name, *args, **kwargs)

    return _blocker


# ---------------------------------------------------------------------------
# Build-ID resolution (lines 660-767)
# ---------------------------------------------------------------------------


class TestResolveBuildId:
    """resolve_debug_info strategies."""

    def test_strategy1_build_id_match(self, tmp_path):
        """Strategy 1: build-id directory match."""
        binary = tmp_path / "usr" / "lib64" / "libfoo.so.1"
        binary.parent.mkdir(parents=True)
        binary.touch()

        debug_dir = tmp_path / "debug"
        bid_path = debug_dir / ".build-id" / "ab" / "cdef1234.debug"
        bid_path.parent.mkdir(parents=True)
        bid_path.touch()

        with patch("abicheck.package._read_build_id", return_value="abcdef1234"):
            result = resolve_debug_info(binary, debug_dir)

        assert result == bid_path

    def test_strategy2_path_mirror(self, tmp_path):
        """Strategy 2: path mirror match."""
        binary = tmp_path / "extract" / "usr" / "lib64" / "libfoo.so.1"
        binary.parent.mkdir(parents=True)
        binary.touch()

        debug_dir = tmp_path / "debug"
        mirrored = debug_dir / "usr" / "lib64" / "libfoo.so.1.debug"
        mirrored.parent.mkdir(parents=True)
        mirrored.touch()

        with patch("abicheck.package._read_build_id", return_value=None):
            result = resolve_debug_info(binary, debug_dir)

        assert result == mirrored

    def test_strategy3_single_candidate(self, tmp_path):
        """Strategy 3: single rglob candidate."""
        binary = tmp_path / "extract" / "libbar.so.2"
        binary.parent.mkdir(parents=True)
        binary.touch()

        debug_dir = tmp_path / "debug"
        dbg_file = debug_dir / "some" / "deep" / "path" / "libbar.so.2.debug"
        dbg_file.parent.mkdir(parents=True)
        dbg_file.touch()

        with patch("abicheck.package._read_build_id", return_value=None):
            result = resolve_debug_info(binary, debug_dir)

        assert result == dbg_file

    def test_strategy3_multiple_candidates_build_id_disambiguates(self, tmp_path):
        """Strategy 3: multiple candidates, build-id breaks the tie."""
        binary = tmp_path / "extract" / "usr" / "lib64" / "libfoo.so"
        binary.parent.mkdir(parents=True)
        binary.touch()

        debug_dir = tmp_path / "debug"
        cand1 = debug_dir / "aaa" / "libfoo.so.debug"
        cand2 = debug_dir / "bbb" / "libfoo.so.debug"
        cand1.parent.mkdir(parents=True)
        cand2.parent.mkdir(parents=True)
        cand1.touch()
        cand2.touch()

        # build-id present but no .build-id directory file exists
        build_id = "deadbeef01"

        def fake_read_build_id(path):
            if path == binary:
                return build_id
            if path == cand2:
                return build_id  # matches
            return None

        with patch("abicheck.package._read_build_id", side_effect=fake_read_build_id):
            result = resolve_debug_info(binary, debug_dir)

        assert result == cand2

    def test_strategy3_multiple_candidates_path_similarity(self, tmp_path):
        """Strategy 3: multiple candidates, path similarity breaks the tie."""
        binary = tmp_path / "extract" / "usr" / "lib64" / "libfoo.so"
        binary.parent.mkdir(parents=True)
        binary.touch()

        debug_dir = tmp_path / "debug"
        # cand1 shares more path components with binary ("usr", "lib64")
        cand1 = debug_dir / "usr" / "lib64" / "libfoo.so.debug"
        cand2 = debug_dir / "other" / "path" / "libfoo.so.debug"
        cand1.parent.mkdir(parents=True)
        cand2.parent.mkdir(parents=True)
        cand1.touch()
        cand2.touch()

        # No build-id to disambiguate
        with patch("abicheck.package._read_build_id", return_value=None):
            result = resolve_debug_info(binary, debug_dir)

        assert result == cand1

    def test_no_candidates_returns_none(self, tmp_path):
        """No debug info found at all."""
        binary = tmp_path / "extract" / "libmissing.so"
        binary.parent.mkdir(parents=True)
        binary.touch()

        debug_dir = tmp_path / "debug"
        debug_dir.mkdir()

        with patch("abicheck.package._read_build_id", return_value=None):
            result = resolve_debug_info(binary, debug_dir)

        assert result is None


class TestReadBuildId:
    """_read_build_id with mocked pyelftools."""

    def test_binary_with_build_id(self, tmp_path):
        """Binary with .note.gnu.build-id section returns hex string."""
        binary = tmp_path / "test.so"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 100)

        mock_note = {"n_type": "NT_GNU_BUILD_ID", "n_desc": "abcdef0123456789"}
        mock_section = MagicMock()
        mock_section.name = ".note.gnu.build-id"
        mock_section.iter_notes.return_value = [mock_note]

        mock_elf = MagicMock()
        mock_elf.iter_sections.return_value = [mock_section]

        # _read_build_id does `from elftools.elf.elffile import ELFFile` at call
        # time, so we patch the module-level class in elftools.
        with patch("elftools.elf.elffile.ELFFile", return_value=mock_elf, create=True):
            result = _read_build_id(binary)

        assert result == "abcdef0123456789"

    def test_binary_without_build_id(self, tmp_path):
        """Binary without .note.gnu.build-id returns None."""
        binary = tmp_path / "test.so"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 100)

        mock_section = MagicMock()
        mock_section.name = ".text"

        mock_elf = MagicMock()
        mock_elf.iter_sections.return_value = [mock_section]

        with patch("elftools.elf.elffile.ELFFile", return_value=mock_elf, create=True):
            result = _read_build_id(binary)

        assert result is None

    def test_unreadable_file_returns_none(self, tmp_path):
        """Unreadable file returns None."""
        binary = tmp_path / "nonexistent.so"
        result = _read_build_id(binary)
        assert result is None
