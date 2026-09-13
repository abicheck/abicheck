# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for PDB utility functions (pdb_utils.py)."""

from __future__ import annotations

import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from abicheck.pdb_utils import (
    _extract_pdb_path_from_pe,
    _is_network_path,
    locate_pdb,
)

# ---------------------------------------------------------------------------
# Tests: _is_network_path
# ---------------------------------------------------------------------------


class TestIsNetworkPath:
    def test_unc_backslash(self) -> None:
        assert _is_network_path("\\\\server\\share") is True

    def test_unc_forward_slash(self) -> None:
        assert _is_network_path("//server/share") is True

    def test_regular_windows_path(self) -> None:
        assert _is_network_path("C:\\foo\\bar.pdb") is False

    def test_regular_unix_path(self) -> None:
        assert _is_network_path("/home/user/foo.pdb") is False

    def test_relative_path(self) -> None:
        assert _is_network_path("foo.pdb") is False

    def test_empty_string(self) -> None:
        assert _is_network_path("") is False

    def test_path_object(self, tmp_path: Path) -> None:
        assert _is_network_path(tmp_path / "foo.pdb") is False

    def test_exception_handling(self) -> None:
        """PureWindowsPath parsing exception should be logged and return False."""
        with patch("abicheck.pdb_utils.PureWindowsPath") as mock_pwp:
            mock_pwp.side_effect = TypeError("test error")
            # Should not raise, returns False
            assert _is_network_path("normal") is False


# ---------------------------------------------------------------------------
# Tests: locate_pdb
# ---------------------------------------------------------------------------


class TestLocatePdb:
    def test_override_exists(self, tmp_path: Path) -> None:
        pdb = tmp_path / "override.pdb"
        pdb.write_bytes(b"fake pdb")
        dll = tmp_path / "test.dll"
        result = locate_pdb(dll, pdb_path_override=pdb)
        assert result == pdb

    def test_override_missing(self, tmp_path: Path) -> None:
        pdb = tmp_path / "nonexistent.pdb"
        dll = tmp_path / "test.dll"
        result = locate_pdb(dll, pdb_path_override=pdb)
        assert result is None

    def test_stem_fallback(self, tmp_path: Path) -> None:
        """Should find DLL_name.pdb in same directory."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"MZ")
        pdb = tmp_path / "test.pdb"
        pdb.write_bytes(b"fake pdb")
        with patch("abicheck.pdb_utils._extract_pdb_path_from_pe", return_value=None):
            result = locate_pdb(dll)
        assert result == pdb

    def test_no_pdb_found(self, tmp_path: Path) -> None:
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"MZ")
        with patch("abicheck.pdb_utils._extract_pdb_path_from_pe", return_value=None):
            result = locate_pdb(dll)
        assert result is None

    def test_embedded_path_local_fallback(self, tmp_path: Path) -> None:
        """Embedded path doesn't exist, but filename in DLL dir does."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"MZ")
        pdb = tmp_path / "foo.pdb"
        pdb.write_bytes(b"fake")
        # Use forward slashes so Path.name works correctly on Linux
        with patch(
            "abicheck.pdb_utils._extract_pdb_path_from_pe",
            return_value="/nonexistent/build/foo.pdb",
        ):
            result = locate_pdb(dll)
        assert result == pdb

    def test_embedded_network_path_skipped(self, tmp_path: Path) -> None:
        """Network path in PE should be skipped when allow_network=False."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"MZ")
        with patch(
            "abicheck.pdb_utils._extract_pdb_path_from_pe",
            return_value="\\\\server\\share\\foo.pdb",
        ):
            result = locate_pdb(dll, allow_network=False)
        assert result is None

    def test_embedded_network_path_allowed(self, tmp_path: Path) -> None:
        """Network path in PE should be tried when allow_network=True."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"MZ")
        with patch(
            "abicheck.pdb_utils._extract_pdb_path_from_pe",
            return_value="\\\\server\\share\\foo.pdb",
        ):
            # The network path won't exist, so returns None
            result = locate_pdb(dll, allow_network=True)
        assert result is None


# ---------------------------------------------------------------------------
# Tests: _extract_pdb_path_from_pe
# ---------------------------------------------------------------------------


class TestExtractPdbPath:
    def test_invalid_pe(self, tmp_path: Path) -> None:
        """Non-PE file should return None."""
        f = tmp_path / "bad.exe"
        f.write_bytes(b"not a PE")
        assert _extract_pdb_path_from_pe(f) is None

    def test_rsds_bytes_entry(self) -> None:
        """RSDS entry with PdbFileName as bytes."""
        pdb_name = b"C:\\build\\test.pdb\x00"
        cv_sig = struct.unpack("<I", b"RSDS")[0]

        entry = SimpleNamespace(
            CvSignature=cv_sig,
            PdbFileName=pdb_name,
        )
        dbg_struct = SimpleNamespace(Type=2)
        dbg = SimpleNamespace(struct=dbg_struct, entry=entry)

        mock_pe = MagicMock()
        mock_pe.DIRECTORY_ENTRY_DEBUG = [dbg]

        with patch("abicheck.pdb_utils.pefile") as mock_pefile:
            mock_pefile.PE.return_value = mock_pe
            mock_pefile.DIRECTORY_ENTRY = {"IMAGE_DIRECTORY_ENTRY_DEBUG": 6}
            result = _extract_pdb_path_from_pe(Path("test.dll"))

        assert result == "C:\\build\\test.pdb"

    def test_rsds_string_entry(self) -> None:
        """RSDS entry with PdbFileName as string (not bytes)."""
        cv_sig = struct.unpack("<I", b"RSDS")[0]

        entry = SimpleNamespace(
            CvSignature=cv_sig,
            PdbFileName="C:\\build\\test.pdb",
        )
        dbg_struct = SimpleNamespace(Type=2)
        dbg = SimpleNamespace(struct=dbg_struct, entry=entry)

        mock_pe = MagicMock()
        mock_pe.DIRECTORY_ENTRY_DEBUG = [dbg]

        with patch("abicheck.pdb_utils.pefile") as mock_pefile:
            mock_pefile.PE.return_value = mock_pe
            mock_pefile.DIRECTORY_ENTRY = {"IMAGE_DIRECTORY_ENTRY_DEBUG": 6}
            result = _extract_pdb_path_from_pe(Path("test.dll"))

        assert result == "C:\\build\\test.pdb"

    def test_nb10_entry(self) -> None:
        """NB10 entry should also extract PdbFileName."""
        cv_sig = struct.unpack("<I", b"NB10")[0]

        entry = SimpleNamespace(
            CvSignature=cv_sig,
            PdbFileName=b"old.pdb\x00",
        )
        dbg_struct = SimpleNamespace(Type=2)
        dbg = SimpleNamespace(struct=dbg_struct, entry=entry)

        mock_pe = MagicMock()
        mock_pe.DIRECTORY_ENTRY_DEBUG = [dbg]

        with patch("abicheck.pdb_utils.pefile") as mock_pefile:
            mock_pefile.PE.return_value = mock_pe
            mock_pefile.DIRECTORY_ENTRY = {"IMAGE_DIRECTORY_ENTRY_DEBUG": 6}
            result = _extract_pdb_path_from_pe(Path("test.dll"))

        assert result == "old.pdb"

    def test_no_debug_directory(self) -> None:
        """PE with no DIRECTORY_ENTRY_DEBUG should return None."""
        mock_pe = MagicMock(spec=[])  # no attributes
        mock_pe.parse_data_directories = MagicMock()
        mock_pe.close = MagicMock()

        with patch("abicheck.pdb_utils.pefile") as mock_pefile:
            mock_pefile.PE.return_value = mock_pe
            mock_pefile.DIRECTORY_ENTRY = {"IMAGE_DIRECTORY_ENTRY_DEBUG": 6}
            result = _extract_pdb_path_from_pe(Path("test.dll"))

        assert result is None

    def test_non_codeview_entry_skipped(self) -> None:
        """Non-CodeView debug entries (Type != 2) should be skipped."""
        dbg_struct = SimpleNamespace(Type=9)  # IMAGE_DEBUG_TYPE_BORLAND
        dbg = SimpleNamespace(struct=dbg_struct, entry=None)

        mock_pe = MagicMock()
        mock_pe.DIRECTORY_ENTRY_DEBUG = [dbg]

        with patch("abicheck.pdb_utils.pefile") as mock_pefile:
            mock_pefile.PE.return_value = mock_pe
            mock_pefile.DIRECTORY_ENTRY = {"IMAGE_DIRECTORY_ENTRY_DEBUG": 6}
            result = _extract_pdb_path_from_pe(Path("test.dll"))

        assert result is None

    def test_raw_data_fallback(self) -> None:
        """When entry is None, should fall back to raw data parsing."""
        # Build RSDS raw data: sig(4) + GUID(16) + age(4) + filename
        raw = b"RSDS" + b"\x00" * 16 + struct.pack("<I", 1) + b"raw.pdb\x00"

        dbg_struct = SimpleNamespace(
            Type=2,
            AddressOfRawData=0x1000,
            SizeOfData=len(raw),
        )
        dbg = SimpleNamespace(struct=dbg_struct, entry=None)

        mock_pe = MagicMock()
        mock_pe.DIRECTORY_ENTRY_DEBUG = [dbg]
        mock_pe.get_data.return_value = raw

        with patch("abicheck.pdb_utils.pefile") as mock_pefile:
            mock_pefile.PE.return_value = mock_pe
            mock_pefile.DIRECTORY_ENTRY = {"IMAGE_DIRECTORY_ENTRY_DEBUG": 6}
            result = _extract_pdb_path_from_pe(Path("test.dll"))

        assert result == "raw.pdb"

    def test_raw_data_no_rsds(self) -> None:
        """Raw data that doesn't start with RSDS should be skipped."""
        raw = b"XXXX" + b"\x00" * 20 + b"nope.pdb\x00"

        dbg_struct = SimpleNamespace(
            Type=2,
            AddressOfRawData=0x1000,
            SizeOfData=len(raw),
        )
        dbg = SimpleNamespace(struct=dbg_struct, entry=None)

        mock_pe = MagicMock()
        mock_pe.DIRECTORY_ENTRY_DEBUG = [dbg]
        mock_pe.get_data.return_value = raw

        with patch("abicheck.pdb_utils.pefile") as mock_pefile:
            mock_pefile.PE.return_value = mock_pe
            mock_pefile.DIRECTORY_ENTRY = {"IMAGE_DIRECTORY_ENTRY_DEBUG": 6}
            result = _extract_pdb_path_from_pe(Path("test.dll"))

        assert result is None

    def test_parse_exception(self) -> None:
        """Exception during parsing should return None."""
        mock_pe = MagicMock()
        mock_pe.parse_data_directories.side_effect = RuntimeError("parse fail")

        with patch("abicheck.pdb_utils.pefile") as mock_pefile:
            mock_pefile.PE.return_value = mock_pe
            mock_pefile.DIRECTORY_ENTRY = {"IMAGE_DIRECTORY_ENTRY_DEBUG": 6}
            result = _extract_pdb_path_from_pe(Path("test.dll"))

        assert result is None

    def test_entry_no_cvsignature(self) -> None:
        """Entry without CvSignature attr should be skipped."""
        entry = SimpleNamespace()  # no CvSignature
        dbg_struct = SimpleNamespace(Type=2)
        dbg = SimpleNamespace(struct=dbg_struct, entry=entry)

        mock_pe = MagicMock()
        mock_pe.DIRECTORY_ENTRY_DEBUG = [dbg]

        with patch("abicheck.pdb_utils.pefile") as mock_pefile:
            mock_pefile.PE.return_value = mock_pe
            mock_pefile.DIRECTORY_ENTRY = {"IMAGE_DIRECTORY_ENTRY_DEBUG": 6}
            result = _extract_pdb_path_from_pe(Path("test.dll"))

        assert result is None

    def test_raw_data_no_address(self) -> None:
        """When entry is None and AddressOfRawData is 0, should skip."""
        dbg_struct = SimpleNamespace(
            Type=2,
            AddressOfRawData=0,
            SizeOfData=100,
        )
        dbg = SimpleNamespace(struct=dbg_struct, entry=None)

        mock_pe = MagicMock()
        mock_pe.DIRECTORY_ENTRY_DEBUG = [dbg]

        with patch("abicheck.pdb_utils.pefile") as mock_pefile:
            mock_pefile.PE.return_value = mock_pe
            mock_pefile.DIRECTORY_ENTRY = {"IMAGE_DIRECTORY_ENTRY_DEBUG": 6}
            result = _extract_pdb_path_from_pe(Path("test.dll"))

        assert result is None
