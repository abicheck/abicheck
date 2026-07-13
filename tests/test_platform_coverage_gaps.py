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

"""Tests targeting uncovered lines across platform-specific modules.

Covers gaps in:
  - pdb_utils.py (lines 47, 52, 94, 105, 116-120, 127-129, 214-215, 222-232)
  - macho_metadata.py (lines 175, 178, 199-200, 227-232, 263-268, 275, 291, 295-298)
  - pdb_metadata.py (lines 87-88, 95-96, 100-101, 105-106, 110-111, 147-161, 163, 201, 210, 219, 269, 296)
  - pe_metadata.py (lines 116-117, 144-148, 173-175, 180-199)
  - binder.py (lines 83, 101, 131-136, 159, 255-261, 272, 286, 309-312)
"""
from __future__ import annotations

import os
import stat
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ============================================================================
# pdb_utils.py — coverage gaps
# ============================================================================
from abicheck.pdb_utils import (
    _extract_pdb_path_from_pe,
    _is_network_path,
    _resolve_embedded_pdb,
    locate_pdb,
)


class TestIsNetworkPathExtended:
    """Cover lines 47 (\\?\\UNC prefix) and 52 (PureWindowsPath anchor fallback)."""

    def test_unc_extended_prefix(self):
        """Line 47: \\?\\UNC\\ prefix is detected."""
        assert _is_network_path("\\\\?\\UNC\\server\\share") is True

    def test_unc_extended_prefix_forward_slashes(self):
        """\\?\\UNC\\ with forward slashes normalised to backslashes."""
        assert _is_network_path("//?/UNC/server/share") is True

    def test_non_network_drive_letter(self):
        assert _is_network_path("D:\\build\\output.pdb") is False

    def test_non_network_relative(self):
        assert _is_network_path("subdir/foo.pdb") is False

    def test_path_object(self):
        assert _is_network_path(Path("/local/file.pdb")) is False

    def test_unc_simple_backslash(self):
        assert _is_network_path("\\\\myserver\\myshare\\file.pdb") is True


class TestResolveEmbeddedPdb:
    """Cover lines 94, 105, 116-120, 127-129 in _resolve_embedded_pdb."""

    def test_no_debug_directory_returns_none(self, tmp_path):
        """When PE has no CodeView entry, _resolve_embedded_pdb returns None."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"not a PE")
        with patch("abicheck.pdb_utils._extract_pdb_path_from_pe", return_value=None):
            result = _resolve_embedded_pdb(dll, allow_network=True)
        assert result is None

    def test_network_path_blocked_with_local_fallback(self, tmp_path):
        """Line 94: network path blocked, but filename-only fallback exists locally."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"PE content")
        pdb = tmp_path / "debug.pdb"
        pdb.write_text("pdb data")

        with patch(
            "abicheck.pdb_utils._extract_pdb_path_from_pe",
            return_value="\\\\server\\share\\debug.pdb",
        ):
            result = _resolve_embedded_pdb(dll, allow_network=False)
        assert result == pdb

    def test_network_path_blocked_no_local_fallback(self, tmp_path):
        """Line 95: network path blocked and no local fallback => None."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"PE content")

        with patch(
            "abicheck.pdb_utils._extract_pdb_path_from_pe",
            return_value="\\\\server\\share\\nonexistent.pdb",
        ):
            result = _resolve_embedded_pdb(dll, allow_network=False)
        assert result is None

    def test_different_drive_letter_skips(self, tmp_path):
        """Line 105: embedded PDB on different drive is skipped, falls to local."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"PE content")
        pdb = tmp_path / "test.pdb"
        pdb.write_text("pdb data")

        # Embedded path claims drive D: while DLL resolves to current drive
        with patch(
            "abicheck.pdb_utils._extract_pdb_path_from_pe",
            return_value="D:\\other_drive\\test.pdb",
        ):
            result = _resolve_embedded_pdb(dll, allow_network=False)
        # Should fall back to local filename match
        assert result == pdb

    def test_absolute_path_no_drive_candidate(self, tmp_path):
        """Line 116: pwin.drive is falsy but pwin.is_absolute() — pure rooted path."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"PE content")

        # A rooted Windows path without a drive letter, e.g. "\\build\\foo.pdb"
        # PureWindowsPath("\\build\\foo.pdb").drive == "" but .is_absolute() == True
        with patch(
            "abicheck.pdb_utils._extract_pdb_path_from_pe",
            return_value="\\build\\foo.pdb",
        ):
            result = _resolve_embedded_pdb(dll, allow_network=False)
        # Path("\\build\\foo.pdb") won't exist, so result is None (falls through)
        assert result is None

    def test_relative_path_traversal_blocked(self, tmp_path):
        """Lines 127-129: relative path with '..' traversal attempt."""
        dll = tmp_path / "subdir" / "test.dll"
        dll.parent.mkdir(parents=True, exist_ok=True)
        dll.write_bytes(b"PE content")
        # Create a PDB outside the DLL's directory
        escape_pdb = tmp_path / "escaped.pdb"
        escape_pdb.write_text("pdb data")

        with patch(
            "abicheck.pdb_utils._extract_pdb_path_from_pe",
            return_value="..\\escaped.pdb",
        ):
            result = _resolve_embedded_pdb(dll, allow_network=False)
        # Traversal should be blocked; no local fallback named "escaped.pdb"
        # in the DLL directory so result is None
        assert result is None

    def test_relative_path_valid_same_dir(self, tmp_path):
        """Lines 126-127: relative path that resolves within DLL directory."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"PE content")
        pdb = tmp_path / "symbols" / "test.pdb"
        pdb.parent.mkdir(parents=True, exist_ok=True)
        pdb.write_text("pdb data")

        with patch(
            "abicheck.pdb_utils._extract_pdb_path_from_pe",
            return_value="symbols\\test.pdb",
        ):
            result = _resolve_embedded_pdb(dll, allow_network=False)
        assert result == pdb

    def test_absolute_path_same_drive_with_drive_prefix(self, tmp_path):
        """Lines 112-118: absolute path with drive letter, same drive as DLL."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"PE content")

        # Construct a path that has a drive letter matching the DLL's resolved drive.
        # On Linux, PureWindowsPath(str(dll.resolve())).drive will be empty, so
        # the code will skip the drive mismatch check and try the candidate.
        # We construct an embedded path whose filename exists locally.
        pdb = tmp_path / "test.pdb"
        pdb.write_text("pdb data")

        with patch(
            "abicheck.pdb_utils._extract_pdb_path_from_pe",
            return_value="C:\\build\\test.pdb",
        ):
            result = _resolve_embedded_pdb(dll, allow_network=False)
        # On Linux, dll_drive is "" and emb_drive is "C:", condition at line 104
        # will be (dll_drive="" is falsy), so falls through to filename-only fallback
        assert result == pdb


class TestLocatePdb:
    """Cover locate_pdb edge cases."""

    def test_explicit_override_nonexistent(self, tmp_path):
        """Line 161-162: explicit pdb_path_override that does not exist."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"PE content")
        override = tmp_path / "nonexistent.pdb"
        result = locate_pdb(dll, pdb_path_override=override)
        assert result is None

    def test_fallback_to_pdb_extension(self, tmp_path):
        """Lines 169-171: no embedded PDB, fallback to .pdb extension."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"PE content")
        pdb = tmp_path / "test.pdb"
        pdb.write_text("pdb data")

        with patch("abicheck.pdb_utils._resolve_embedded_pdb", return_value=None):
            result = locate_pdb(dll)
        assert result == pdb

    def test_no_pdb_found(self, tmp_path):
        """Lines 173: nothing found at all."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"PE content")

        with patch("abicheck.pdb_utils._resolve_embedded_pdb", return_value=None):
            result = locate_pdb(dll)
        assert result is None


class TestExtractPdbPathFromPe:
    """Cover lines 214-215 (NB10 raw), 222-228 (RSDS pefile parsed), 232 (NB10 pefile parsed)."""

    def test_nb10_raw_data(self, tmp_path):
        """Lines 214-215: NB10 signature with raw data fallback."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"fake")

        # Build a mock PE with a debug entry that has data=None but raw NB10 data
        nb10_raw = b"NB10" + b"\x00" * 12 + b"test.pdb\x00"

        mock_dbg = MagicMock()
        mock_dbg.struct.Type = 2  # IMAGE_DEBUG_TYPE_CODEVIEW
        mock_dbg.entry = None
        mock_dbg.struct.SizeOfData = len(nb10_raw)
        mock_dbg.struct.AddressOfRawData = 0x1000

        mock_pe = MagicMock()
        mock_pe.DIRECTORY_ENTRY_DEBUG = [mock_dbg]
        mock_pe.get_data.return_value = nb10_raw

        with patch("abicheck.pdb_utils.pefile.PE", return_value=mock_pe):
            result = _extract_pdb_path_from_pe(dll)
        assert result == "test.pdb"

    def test_rsds_pefile_parsed_bytes(self, tmp_path):
        """Lines 222-225: pefile-parsed CodeView with RSDS sig, PdbFileName as bytes."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"fake")

        mock_data = MagicMock()
        mock_data.CvSignature = struct.unpack("<I", b"RSDS")[0]
        mock_data.PdbFileName = b"C:\\build\\output.pdb\x00"

        mock_dbg = MagicMock()
        mock_dbg.struct.Type = 2
        mock_dbg.entry = mock_data

        mock_pe = MagicMock()
        mock_pe.DIRECTORY_ENTRY_DEBUG = [mock_dbg]

        with patch("abicheck.pdb_utils.pefile.PE", return_value=mock_pe):
            result = _extract_pdb_path_from_pe(dll)
        assert result == "C:\\build\\output.pdb"

    def test_rsds_pefile_parsed_str(self, tmp_path):
        """Line 226: pefile-parsed CodeView with RSDS sig, PdbFileName as str."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"fake")

        mock_data = MagicMock()
        mock_data.CvSignature = struct.unpack("<I", b"RSDS")[0]
        mock_data.PdbFileName = "C:\\build\\string.pdb"

        mock_dbg = MagicMock()
        mock_dbg.struct.Type = 2
        mock_dbg.entry = mock_data

        mock_pe = MagicMock()
        mock_pe.DIRECTORY_ENTRY_DEBUG = [mock_dbg]

        with patch("abicheck.pdb_utils.pefile.PE", return_value=mock_pe):
            result = _extract_pdb_path_from_pe(dll)
        assert result == "C:\\build\\string.pdb"

    def test_nb10_pefile_parsed_bytes(self, tmp_path):
        """Lines 227-231: pefile-parsed CodeView with NB10 sig, PdbFileName as bytes."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"fake")

        mock_data = MagicMock()
        mock_data.CvSignature = struct.unpack("<I", b"NB10")[0]
        mock_data.PdbFileName = b"nb10_output.pdb\x00"

        mock_dbg = MagicMock()
        mock_dbg.struct.Type = 2
        mock_dbg.entry = mock_data

        mock_pe = MagicMock()
        mock_pe.DIRECTORY_ENTRY_DEBUG = [mock_dbg]

        with patch("abicheck.pdb_utils.pefile.PE", return_value=mock_pe):
            result = _extract_pdb_path_from_pe(dll)
        assert result == "nb10_output.pdb"

    def test_nb10_pefile_parsed_str(self, tmp_path):
        """Line 232: pefile-parsed CodeView with NB10 sig, PdbFileName as str."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"fake")

        mock_data = MagicMock()
        mock_data.CvSignature = struct.unpack("<I", b"NB10")[0]
        mock_data.PdbFileName = "nb10_string.pdb"

        mock_dbg = MagicMock()
        mock_dbg.struct.Type = 2
        mock_dbg.entry = mock_data

        mock_pe = MagicMock()
        mock_pe.DIRECTORY_ENTRY_DEBUG = [mock_dbg]

        with patch("abicheck.pdb_utils.pefile.PE", return_value=mock_pe):
            result = _extract_pdb_path_from_pe(dll)
        assert result == "nb10_string.pdb"


# ============================================================================
# macho_metadata.py — coverage gaps
# ============================================================================

from abicheck.macho_metadata import (
    MachoSymbolType,
    _dylib_name_from_cmd,
    _select_header,
    _version_field_to_str,
    _walk_export_trie,
    parse_macho_metadata,
)


class TestDylibNameFromCmd:
    """Cover lines 175, 178 in _dylib_name_from_cmd."""

    def test_empty_data(self):
        """Line 175: empty data returns empty string."""
        assert _dylib_name_from_cmd(b"") == ""

    def test_no_null_terminator(self):
        """Line 178: data without null terminator uses entire length."""
        assert _dylib_name_from_cmd(b"libfoo.dylib") == "libfoo.dylib"

    def test_normal_null_terminated(self):
        assert _dylib_name_from_cmd(b"libbar.dylib\x00extra") == "libbar.dylib"


class TestVersionFieldToStr:
    """Cover _version_field_to_str with mach_version_helper object."""

    def test_raw_integer(self):
        # packed version 1.2.3 = (1 << 16) | (2 << 8) | 3 = 65536 + 512 + 3 = 66051
        assert _version_field_to_str(66051) == "1.2.3"

    def test_mach_version_helper_object(self):
        """Line 162-163: object with _version attribute."""
        helper = SimpleNamespace(_version=66051)
        assert _version_field_to_str(helper) == "1.2.3"

    def test_zero_version(self):
        assert _version_field_to_str(0) == "0.0.0"


class TestSelectHeader:
    """Cover lines 227-232 in _select_header: fat binary with multiple headers."""

    _CPU_TYPE_X86_64 = 0x01000007
    _CPU_TYPE_ARM64 = 0x0100000C

    def _make_header(self, cputype):
        hdr = MagicMock()
        hdr.header.cputype = cputype
        return hdr

    def test_single_header(self):
        macho = MagicMock()
        hdr = self._make_header(self._CPU_TYPE_ARM64)
        macho.headers = [hdr]
        assert _select_header(macho) is hdr

    def test_no_headers(self):
        macho = MagicMock()
        macho.headers = []
        assert _select_header(macho) is None

    def test_multiple_headers_prefer_arm64_on_arm(self):
        """Lines 226-228: prefer arm64 on arm64 host."""
        macho = MagicMock()
        x86_hdr = self._make_header(self._CPU_TYPE_X86_64)
        arm_hdr = self._make_header(self._CPU_TYPE_ARM64)
        macho.headers = [x86_hdr, arm_hdr]

        with patch("abicheck.macho_metadata.platform.machine", return_value="arm64"):
            result = _select_header(macho)
        assert result is arm_hdr

    def test_multiple_headers_prefer_x86_on_x86(self):
        """Lines 226-228: prefer x86_64 on x86_64 host."""
        macho = MagicMock()
        x86_hdr = self._make_header(self._CPU_TYPE_X86_64)
        arm_hdr = self._make_header(self._CPU_TYPE_ARM64)
        macho.headers = [arm_hdr, x86_hdr]

        with patch("abicheck.macho_metadata.platform.machine", return_value="x86_64"):
            result = _select_header(macho)
        assert result is x86_hdr

    def test_multiple_headers_fallback_to_other_arch(self):
        """Lines 229-231: preferred arch not found, fall back to other."""
        macho = MagicMock()
        arm_hdr = self._make_header(self._CPU_TYPE_ARM64)
        macho.headers = [arm_hdr]
        # Actually need >1 header to enter the multi-header path
        other_hdr = self._make_header(0x99999)
        macho.headers = [other_hdr, arm_hdr]

        with patch("abicheck.macho_metadata.platform.machine", return_value="x86_64"):
            # Preferred is x86_64 but not present; fallback is arm64
            result = _select_header(macho)
        assert result is arm_hdr

    def test_multiple_headers_neither_arch_falls_to_first(self):
        """Line 232: neither preferred nor fallback arch found, returns first."""
        macho = MagicMock()
        hdr1 = self._make_header(0xAAAA)
        hdr2 = self._make_header(0xBBBB)
        macho.headers = [hdr1, hdr2]

        with patch("abicheck.macho_metadata.platform.machine", return_value="x86_64"):
            result = _select_header(macho)
        assert result is hdr1


class TestMachoExportTrieBudgets:
    """Export trie traversal refuses inputs that exceed resource budgets."""

    @staticmethod
    def _uleb(value: int) -> bytes:
        out = bytearray()
        while True:
            byte = value & 0x7F
            value >>= 7
            if value:
                byte |= 0x80
            out.append(byte)
            if not value:
                return bytes(out)

    def test_child_entry_budget_is_enforced(self, monkeypatch):
        monkeypatch.setattr("abicheck.macho_metadata._TRIE_MAX_CHILDREN", 1)
        leaf1 = b"\x01\x00\x00"
        leaf2 = b"\x01\x00\x00"
        root_prefix = b"\x00\x02a\x00"
        first_off = len(root_prefix) + 1 + len(b"b\x00") + 1
        root = root_prefix + self._uleb(first_off) + b"b\x00" + self._uleb(first_off + len(leaf1))

        with pytest.raises(ValueError, match="child-entry budget"):
            _walk_export_trie(root + leaf1 + leaf2)

    def test_result_budget_is_enforced(self, monkeypatch):
        monkeypatch.setattr("abicheck.macho_metadata._TRIE_MAX_RESULTS", 1)
        root = b"\x01\x00\x01a\x00" + self._uleb(6)
        leaf = b"\x01\x00\x00"

        with pytest.raises(ValueError, match="result budget"):
            _walk_export_trie(root + leaf)

    def test_node_budget_is_enforced_before_visiting_child(self, monkeypatch):
        monkeypatch.setattr("abicheck.macho_metadata._TRIE_MAX_NODES", 1)
        root = b"\x00\x01a\x00" + self._uleb(5)
        leaf = b"\x01\x00\x00"

        with pytest.raises(ValueError, match="node budget"):
            _walk_export_trie(root + leaf)

    def test_cumulative_symbol_byte_budget_is_enforced(self, monkeypatch):
        monkeypatch.setattr("abicheck.macho_metadata._TRIE_MAX_SYMBOL_BYTES", 3)
        root = b"\x00\x01ab\x00" + self._uleb(6)
        middle = b"\x00\x01cd\x00" + self._uleb(12)
        leaf = b"\x01\x00\x00"

        with pytest.raises(ValueError, match="symbol data budget"):
            _walk_export_trie(root + middle + leaf)

    def test_cumulative_symbol_byte_budget_allows_exact_boundary(self, monkeypatch):
        monkeypatch.setattr("abicheck.macho_metadata._TRIE_MAX_SYMBOL_BYTES", 4)
        root = b"\x00\x01ab\x00" + self._uleb(6)
        middle = b"\x00\x01cd\x00" + self._uleb(12)
        leaf = b"\x01\x00\x00"

        assert _walk_export_trie(root + middle + leaf) == [("abcd", 0)]

    def test_symbol_name_length_budget_is_enforced(self, monkeypatch):
        monkeypatch.setattr("abicheck.macho_metadata._TRIE_MAX_SYMBOL_LENGTH", 1)
        root = b"\x00\x01ab\x00" + self._uleb(6)
        leaf = b"\x01\x00\x00"

        with pytest.raises(ValueError, match="symbol-name budget"):
            _walk_export_trie(root + leaf)


class TestParseMachoMetadataNonRegularFile:
    """Cover lines 199-200: parse_macho_metadata with non-regular file."""

    def test_non_regular_file_returns_empty(self, tmp_path):
        """Lines 199-200: fstat reports non-regular file."""
        fake_path = tmp_path / "fake_device"
        fake_path.write_bytes(b"data")

        # Mock os.fstat to return a non-regular file mode (e.g. S_IFIFO)
        fake_stat = os.stat_result((stat.S_IFIFO | 0o644, 0, 0, 0, 0, 0, 0, 0, 0, 0))
        with patch("abicheck.macho_metadata.os.fstat", return_value=fake_stat):
            result = parse_macho_metadata(fake_path)
        assert result.cpu_type == ""
        assert result.exports == []


class TestParseMachoSymbolTableFailure:
    """Cover line 301-303: SymbolTable raises, exports stay empty."""

    def test_symtab_failure_still_returns_metadata(self, tmp_path):
        """When SymbolTable constructor raises, we still get header/load-cmd metadata."""
        from macholib.mach_o import (
            LC_BUILD_VERSION,
            LC_ID_DYLIB,
            LC_LOAD_DYLIB,
            LC_REEXPORT_DYLIB,
        )

        dylib_path = tmp_path / "libtest.dylib"
        dylib_path.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 100)

        # Build a mock MachO object
        mock_header = MagicMock()
        mock_header.header.cputype = 0x0100000C  # ARM64
        mock_header.header.filetype = 6  # MH_DYLIB
        mock_header.header.flags = 0

        # Create load commands with proper data
        lc_id = MagicMock()
        lc_id.cmd = LC_ID_DYLIB
        cmd_id = MagicMock()
        cmd_id.current_version = 66051  # 1.2.3
        cmd_id.compatibility_version = 65536  # 1.0.0

        lc_load = MagicMock()
        lc_load.cmd = LC_LOAD_DYLIB

        lc_reexport = MagicMock()
        lc_reexport.cmd = LC_REEXPORT_DYLIB

        lc_build = MagicMock()
        lc_build.cmd = LC_BUILD_VERSION
        cmd_build = MagicMock()
        cmd_build.minos = (11 << 16) | (0 << 8) | 0  # 11.0.0

        mock_header.commands = [
            (lc_id, cmd_id, b"/usr/lib/libtest.dylib\x00"),
            (lc_load, MagicMock(), b"/usr/lib/libSystem.B.dylib\x00"),
            (lc_reexport, MagicMock(), b"/usr/lib/libreexported.dylib\x00"),
            (lc_build, cmd_build, b""),
        ]

        mock_macho = MagicMock()
        mock_macho.headers = [mock_header]

        with patch("abicheck.macho_metadata.MachO", return_value=mock_macho), \
             patch("abicheck.macho_metadata.SymbolTable", side_effect=Exception("no symtab")):
            result = parse_macho_metadata(dylib_path)

        assert result.cpu_type  # should be populated
        assert result.filetype == "MH_DYLIB"
        assert result.install_name == "/usr/lib/libtest.dylib"
        assert "/usr/lib/libSystem.B.dylib" in result.dependent_libs
        assert "/usr/lib/libreexported.dylib" in result.reexported_libs
        assert result.min_os_version == "11.0.0"
        assert result.exports == []  # SymbolTable failed


class TestParseMachoWeakSymbols:
    """Cover lines 291, 295-298: symbol with N_WEAK_DEF and underscore stripping."""

    def test_weak_exported_symbol_and_underscore_strip(self, tmp_path):
        from macholib.mach_o import N_EXT, N_WEAK_DEF

        dylib_path = tmp_path / "libweak.dylib"
        dylib_path.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 100)

        mock_header = MagicMock()
        mock_header.header.cputype = 0x01000007  # X86_64
        mock_header.header.filetype = 6
        mock_header.header.flags = 0
        mock_header.commands = []

        mock_macho = MagicMock()
        mock_macho.headers = [mock_header]

        # n_type must have N_EXT set AND (n_type & N_TYPE) != N_UNDF (0)
        # N_SECT = 0xe (defined in a section), N_EXT = 0x1
        # So use 0xf = N_EXT | 0xe (defined external)
        defined_ext = N_EXT | 0xe  # 0xf: external, defined in section

        nlist_normal = MagicMock()
        nlist_normal.n_type = defined_ext
        nlist_normal.n_desc = 0

        nlist_weak = MagicMock()
        nlist_weak.n_type = defined_ext
        nlist_weak.n_desc = N_WEAK_DEF

        mock_symtab = MagicMock()
        mock_symtab.extdefsyms = [
            (nlist_normal, b"_my_func"),
            (nlist_weak, b"_weak_func"),
        ]

        with patch("abicheck.macho_metadata.MachO", return_value=mock_macho), \
             patch("abicheck.macho_metadata.SymbolTable", return_value=mock_symtab):
            result = parse_macho_metadata(dylib_path)

        names = {e.name for e in result.exports}
        assert "my_func" in names
        assert "weak_func" in names
        weak_exp = [e for e in result.exports if e.name == "weak_func"][0]
        assert weak_exp.is_weak is True
        assert weak_exp.sym_type == MachoSymbolType.WEAK


# ============================================================================
# pdb_metadata.py — coverage gaps
# ============================================================================

from abicheck.dwarf_advanced import AdvancedDwarfMetadata
from abicheck.dwarf_metadata import DwarfMetadata
from abicheck.pdb_metadata import (
    _extract_enums,
    _extract_struct_layouts,
    _extract_toolchain_info,
    parse_pdb_debug_info,
)


class TestPdbMetadataNoTpi:
    """Cover lines 87-88: pdb.types is None."""

    def test_no_tpi_stream(self, tmp_path):
        pdb_path = tmp_path / "empty.pdb"
        pdb_path.write_bytes(b"fake")

        mock_pdb = MagicMock()
        mock_pdb.types = None
        mock_pdb.dbi = None

        with patch("abicheck.pdb_metadata.parse_pdb", return_value=mock_pdb):
            meta, adv = parse_pdb_debug_info(pdb_path)
        assert not meta.has_dwarf
        assert not adv.has_dwarf


class TestPdbMetadataPhaseExceptions:
    """Cover lines 95-96, 100-101, 105-106, 110-111: each phase can fail independently."""

    def _make_mock_pdb(self):
        mock_types = MagicMock()
        mock_pdb = MagicMock()
        mock_pdb.types = mock_types
        mock_pdb.dbi = None
        return mock_pdb

    def test_struct_extraction_fails(self, tmp_path):
        """Lines 95-96: _extract_struct_layouts raises."""
        pdb_path = tmp_path / "test.pdb"
        pdb_path.write_bytes(b"fake")
        mock_pdb = self._make_mock_pdb()

        with patch("abicheck.pdb_metadata.parse_pdb", return_value=mock_pdb), \
             patch("abicheck.pdb_metadata._extract_struct_layouts", side_effect=RuntimeError("boom")), \
             patch("abicheck.pdb_metadata._extract_enums"), \
             patch("abicheck.pdb_metadata._extract_toolchain_info"):
            meta, adv = parse_pdb_debug_info(pdb_path)
        assert meta.has_dwarf

    def test_enum_extraction_fails(self, tmp_path):
        """Lines 100-101: _extract_enums raises."""
        pdb_path = tmp_path / "test.pdb"
        pdb_path.write_bytes(b"fake")
        mock_pdb = self._make_mock_pdb()

        with patch("abicheck.pdb_metadata.parse_pdb", return_value=mock_pdb), \
             patch("abicheck.pdb_metadata._extract_struct_layouts"), \
             patch("abicheck.pdb_metadata._extract_enums", side_effect=RuntimeError("boom")), \
             patch("abicheck.pdb_metadata._extract_toolchain_info"):
            meta, adv = parse_pdb_debug_info(pdb_path)
        assert meta.has_dwarf

    def test_toolchain_info_extraction_fails(self, tmp_path):
        """Lines 110-111: _extract_toolchain_info raises."""
        pdb_path = tmp_path / "test.pdb"
        pdb_path.write_bytes(b"fake")
        mock_pdb = self._make_mock_pdb()

        with patch("abicheck.pdb_metadata.parse_pdb", return_value=mock_pdb), \
             patch("abicheck.pdb_metadata._extract_struct_layouts"), \
             patch("abicheck.pdb_metadata._extract_enums"), \
             patch("abicheck.pdb_metadata._extract_toolchain_info", side_effect=RuntimeError("boom")):
            meta, adv = parse_pdb_debug_info(pdb_path)
        assert meta.has_dwarf


class TestPdbMetadataStructLayouts:
    """Cover lines 147-161, 163 in _extract_struct_layouts."""

    def test_forward_ref_skipped(self):
        """Struct with is_forward_ref=True is skipped."""
        mock_types = MagicMock()
        fwd_struct = MagicMock()
        fwd_struct.is_forward_ref = True
        fwd_struct.name = "ForwardDeclared"
        mock_types.all_structs.return_value = {0x1000: fwd_struct}

        meta = DwarfMetadata(has_dwarf=True)
        _extract_struct_layouts(mock_types, meta)
        assert "ForwardDeclared" not in meta.structs

    def test_unnamed_struct_skipped(self):
        """Struct with empty name is skipped."""
        mock_types = MagicMock()
        unnamed = MagicMock()
        unnamed.is_forward_ref = False
        unnamed.name = ""
        mock_types.all_structs.return_value = {0x1000: unnamed}

        meta = DwarfMetadata(has_dwarf=True)
        _extract_struct_layouts(mock_types, meta)
        assert len(meta.structs) == 0

    def test_internal_name_skipped(self):
        """Struct starting with '<' or '__' is skipped."""
        mock_types = MagicMock()
        s1 = MagicMock()
        s1.is_forward_ref = False
        s1.name = "<lambda_1>"
        s2 = MagicMock()
        s2.is_forward_ref = False
        s2.name = "__compiler_internal"
        mock_types.all_structs.return_value = {0x1000: s1, 0x1001: s2}

        meta = DwarfMetadata(has_dwarf=True)
        _extract_struct_layouts(mock_types, meta)
        assert len(meta.structs) == 0

    def test_valid_struct_extracted(self):
        """Lines 131-148: valid struct with fields is extracted."""
        from abicheck.pdb_parser import CvMember

        mock_types = MagicMock()
        cv_struct = MagicMock()
        cv_struct.is_forward_ref = False
        cv_struct.name = "MyStruct"
        cv_struct.byte_size = 16
        cv_struct.field_list_ti = 0x1001
        cv_struct.is_union = False
        cv_struct.is_packed = False

        member = CvMember(name="x", type_ti=0x74, offset=0, access=3)
        mock_types.all_structs.return_value = {0x1000: cv_struct}
        mock_types.get_fieldlist.return_value = [member]
        mock_types.type_name.return_value = "int"
        mock_types.type_size.return_value = 4
        mock_types._bitfields = {}

        meta = DwarfMetadata(has_dwarf=True)
        _extract_struct_layouts(mock_types, meta)
        assert "MyStruct" in meta.structs
        layout = meta.structs["MyStruct"]
        assert layout.byte_size == 16
        assert len(layout.fields) == 1
        assert layout.fields[0].name == "x"

    def test_odr_first_definition_wins(self):
        """Line 147-148: ODR — keep first complete definition."""
        mock_types = MagicMock()
        s1 = MagicMock()
        s1.is_forward_ref = False
        s1.name = "Dup"
        s1.byte_size = 8
        s1.field_list_ti = 0
        s1.is_union = False
        s1.is_packed = False

        s2 = MagicMock()
        s2.is_forward_ref = False
        s2.name = "Dup"
        s2.byte_size = 16
        s2.field_list_ti = 0
        s2.is_union = False
        s2.is_packed = False

        mock_types.all_structs.return_value = {0x1000: s1, 0x1001: s2}
        mock_types.get_fieldlist.return_value = []
        mock_types._bitfields = {}

        meta = DwarfMetadata(has_dwarf=True)
        _extract_struct_layouts(mock_types, meta)
        assert meta.structs["Dup"].byte_size == 8


class TestPdbMetadataEnums:
    """Cover lines 201, 210, 219 in _extract_enums."""

    def test_enum_forward_ref_skipped(self):
        mock_types = MagicMock()
        cv_enum = MagicMock()
        cv_enum.is_forward_ref = True
        cv_enum.name = "FwdEnum"
        mock_types.all_enums.return_value = {0x1000: cv_enum}

        meta = DwarfMetadata(has_dwarf=True)
        _extract_enums(mock_types, meta)
        assert "FwdEnum" not in meta.enums

    def test_enum_empty_name_skipped(self):
        mock_types = MagicMock()
        cv_enum = MagicMock()
        cv_enum.is_forward_ref = False
        cv_enum.name = ""
        mock_types.all_enums.return_value = {0x1000: cv_enum}

        meta = DwarfMetadata(has_dwarf=True)
        _extract_enums(mock_types, meta)
        assert len(meta.enums) == 0

    def test_valid_enum_with_members(self):
        """Lines 205-220: valid enum extracted with members."""
        from abicheck.pdb_parser import CvEnumerator

        mock_types = MagicMock()
        cv_enum = MagicMock()
        cv_enum.is_forward_ref = False
        cv_enum.name = "Color"
        cv_enum.underlying_type_ti = 0x74
        cv_enum.field_list_ti = 0x1001

        mock_types.all_enums.return_value = {0x1000: cv_enum}
        mock_types.type_size.return_value = 4
        mock_types.get_fieldlist.return_value = [
            CvEnumerator(name="RED", value=0),
            CvEnumerator(name="GREEN", value=1),
            CvEnumerator(name="BLUE", value=2),
        ]

        meta = DwarfMetadata(has_dwarf=True)
        _extract_enums(mock_types, meta)
        assert "Color" in meta.enums
        assert meta.enums["Color"].members == {"RED": 0, "GREEN": 1, "BLUE": 2}

    def test_enum_odr_first_wins(self):
        """Line 219: first definition wins for enums too."""
        mock_types = MagicMock()
        e1 = MagicMock()
        e1.is_forward_ref = False
        e1.name = "DupEnum"
        e1.underlying_type_ti = 0x74
        e1.field_list_ti = 0x1001

        e2 = MagicMock()
        e2.is_forward_ref = False
        e2.name = "DupEnum"
        e2.underlying_type_ti = 0x75
        e2.field_list_ti = 0x1002

        mock_types.all_enums.return_value = {0x1000: e1, 0x1001: e2}
        mock_types.type_size.side_effect = lambda ti: 4 if ti == 0x74 else 8
        mock_types.get_fieldlist.return_value = []

        meta = DwarfMetadata(has_dwarf=True)
        _extract_enums(mock_types, meta)
        assert meta.enums["DupEnum"].underlying_byte_size == 4


class TestPdbMetadataPackedStructs:
    """Cover packed struct detection in _extract_struct_layouts."""

    def test_packed_structs_collected(self):
        mock_types = MagicMock()
        s1 = MagicMock()
        s1.is_forward_ref = False
        s1.name = "PackedStruct"
        s1.is_packed = True
        s1.byte_size = 8
        s1.is_union = False
        s1.field_list_ti = 0

        s2 = MagicMock()
        s2.is_forward_ref = False
        s2.name = "NormalStruct"
        s2.is_packed = False
        s2.byte_size = 16
        s2.is_union = False
        s2.field_list_ti = 0

        mock_types.all_structs.return_value = {0x1000: s1, 0x1001: s2}
        mock_types.get_fieldlist.return_value = []

        meta = DwarfMetadata(has_dwarf=True)
        adv = AdvancedDwarfMetadata(has_dwarf=True)
        _extract_struct_layouts(mock_types, meta, adv)
        assert "PackedStruct" in adv.packed_structs
        assert "NormalStruct" not in adv.packed_structs
        assert "PackedStruct" in adv.all_struct_names
        assert "NormalStruct" in adv.all_struct_names


class TestPdbMetadataToolchainInfo:
    """Cover line 296 in _extract_toolchain_info."""

    def test_msvc_version_from_module_path(self):
        """Lines 293-303: extract detailed MSVC version from module obj path."""
        mock_pdb = MagicMock()
        mock_header = MagicMock()
        mock_header.machine = 0x8664  # AMD64
        mock_header.build_number = (14 << 8) | 36  # major=14, minor=36
        mock_header.flags = 0
        mock_pdb.dbi.header = mock_header

        mod = MagicMock()
        mod.obj_file_name = "C:\\Program Files\\MSVC\\14.36.32532\\lib\\x64\\libcmt.lib"
        mock_pdb.dbi.modules = [mod]

        adv = AdvancedDwarfMetadata(has_dwarf=True)
        _extract_toolchain_info(mock_pdb, adv)
        assert adv.toolchain.version == "14.36.32532"
        assert "14.36.32532" in adv.toolchain.producer_string

    def test_no_dbi_returns_early(self):
        """Line 258-259: pdb.dbi is None."""
        mock_pdb = MagicMock()
        mock_pdb.dbi = None

        adv = AdvancedDwarfMetadata(has_dwarf=True)
        original_toolchain = adv.toolchain
        _extract_toolchain_info(mock_pdb, adv)
        # Toolchain should be unchanged (not populated)
        assert adv.toolchain is original_toolchain

    def test_incremental_linking_flag(self):
        """Line 282-283: flags & 0x01 sets /INCREMENTAL."""
        mock_pdb = MagicMock()
        mock_header = MagicMock()
        mock_header.machine = 0x014C  # x86
        mock_header.build_number = (12 << 8) | 0
        mock_header.flags = 0x01  # incremental
        mock_pdb.dbi.header = mock_header
        mock_pdb.dbi.modules = []

        adv = AdvancedDwarfMetadata(has_dwarf=True)
        _extract_toolchain_info(mock_pdb, adv)
        assert "/INCREMENTAL" in adv.toolchain.abi_flags
        assert "-m32" in adv.toolchain.abi_flags


# ============================================================================
# pe_metadata.py — coverage gaps
# ============================================================================

from abicheck.pe_metadata import parse_pe_metadata


class TestPeMetadataNonRegularFile:
    """Cover lines 116-117: non-regular file."""

    def test_pipe_returns_empty(self, tmp_path):
        fake_path = tmp_path / "fake_device"
        fake_path.write_bytes(b"data")

        fake_stat = os.stat_result((stat.S_IFIFO | 0o644, 0, 0, 0, 0, 0, 0, 0, 0, 0))
        with patch("abicheck.pe_metadata.os.fstat", return_value=fake_stat):
            result = parse_pe_metadata(fake_path)
        assert result.machine == ""
        assert result.exports == []


class TestPeMetadataNoExports:
    """Cover lines 144-148: PE with no DIRECTORY_ENTRY_EXPORT."""

    def test_no_exports_no_optional_header(self, tmp_path):
        """Lines 144-148: PE without OPTIONAL_HEADER and without exports."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"fake")

        mock_pe = MagicMock()
        mock_pe.FILE_HEADER.Machine = 0x8664
        mock_pe.FILE_HEADER.Characteristics = 0x2022
        # No OPTIONAL_HEADER
        del mock_pe.OPTIONAL_HEADER
        # No DIRECTORY_ENTRY_EXPORT
        del mock_pe.DIRECTORY_ENTRY_EXPORT
        # No DIRECTORY_ENTRY_IMPORT
        del mock_pe.DIRECTORY_ENTRY_IMPORT
        # No VS_FIXEDFILEINFO
        del mock_pe.VS_FIXEDFILEINFO

        with patch("abicheck.pe_metadata.pefile.PE", return_value=mock_pe), \
             patch("abicheck.pe_metadata.os.fstat") as mock_fstat:
            mock_stat = MagicMock()
            mock_stat.st_mode = stat.S_IFREG | 0o644
            mock_fstat.return_value = mock_stat
            result = parse_pe_metadata(dll)

        assert result.machine  # should have machine type
        assert result.exports == []
        assert result.imports == {}


class TestPeMetadataOrdinalOnlyExports:
    """Cover lines 173-175: ordinal-only imports."""

    def test_ordinal_only_import(self, tmp_path):
        """Lines 173-175: import by ordinal, no name."""
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"fake")

        mock_imp_entry = MagicMock()
        mock_imp_entry.name = None
        mock_imp_entry.import_by_ordinal = True
        mock_imp_entry.ordinal = 42

        mock_import = MagicMock()
        mock_import.dll = b"KERNEL32.dll"
        mock_import.imports = [mock_imp_entry]

        mock_pe = MagicMock()
        mock_pe.FILE_HEADER.Machine = 0x8664
        mock_pe.FILE_HEADER.Characteristics = 0x2022
        mock_pe.OPTIONAL_HEADER.DllCharacteristics = 0x8160
        del mock_pe.DIRECTORY_ENTRY_EXPORT
        mock_pe.DIRECTORY_ENTRY_IMPORT = [mock_import]
        del mock_pe.VS_FIXEDFILEINFO

        with patch("abicheck.pe_metadata.pefile.PE", return_value=mock_pe), \
             patch("abicheck.pe_metadata.os.fstat") as mock_fstat:
            mock_stat = MagicMock()
            mock_stat.st_mode = stat.S_IFREG | 0o644
            mock_fstat.return_value = mock_stat
            result = parse_pe_metadata(dll)

        assert "KERNEL32.dll" in result.imports
        assert "ordinal:42" in result.imports["KERNEL32.dll"]


class TestPeMetadataVersionResource:
    """Cover lines 180-199: VS_FIXEDFILEINFO version resource."""

    def test_version_resource_extracted(self, tmp_path):
        dll = tmp_path / "test.dll"
        dll.write_bytes(b"fake")

        mock_finfo = MagicMock()
        mock_finfo.FileVersionMS = (10 << 16) | 0
        mock_finfo.FileVersionLS = (19041 << 16) | 1
        mock_finfo.ProductVersionMS = (10 << 16) | 0
        mock_finfo.ProductVersionLS = (19041 << 16) | 1

        mock_pe = MagicMock()
        mock_pe.FILE_HEADER.Machine = 0x8664
        mock_pe.FILE_HEADER.Characteristics = 0
        mock_pe.OPTIONAL_HEADER.DllCharacteristics = 0
        del mock_pe.DIRECTORY_ENTRY_EXPORT
        del mock_pe.DIRECTORY_ENTRY_IMPORT
        mock_pe.VS_FIXEDFILEINFO = [mock_finfo]

        with patch("abicheck.pe_metadata.pefile.PE", return_value=mock_pe), \
             patch("abicheck.pe_metadata.os.fstat") as mock_fstat:
            mock_stat = MagicMock()
            mock_stat.st_mode = stat.S_IFREG | 0o644
            mock_fstat.return_value = mock_stat
            result = parse_pe_metadata(dll)

        assert result.file_version == "10.0.19041.1"
        assert result.product_version == "10.0.19041.1"


# ============================================================================
# binder.py — coverage gaps
# ============================================================================

from abicheck.binder import (
    BindingStatus,
    _compute_load_order,
    compute_bindings,
)
from abicheck.elf_metadata import (
    ElfImport,
    ElfMetadata,
    ElfSymbol,
    SymbolBinding as ElfSymbolBinding,
)
from abicheck.resolver import DependencyGraph, ResolvedDSO


def _make_graph(
    nodes: dict[str, tuple[list[str], list[ElfSymbol], list[ElfImport]]],
    edges: list[tuple[str, str]] | None = None,
    root: str = "/app",
) -> DependencyGraph:
    """Helper to build a DependencyGraph from simplified specs."""
    graph = DependencyGraph(root=root)
    for i, (path, (needed, exports, imports)) in enumerate(nodes.items()):
        meta = ElfMetadata(
            needed=needed,
            symbols=exports,
            imports=imports,
        )
        graph.nodes[path] = ResolvedDSO(
            path=Path(path),
            soname=Path(path).name,
            needed=needed,
            rpath="",
            runpath="",
            resolution_reason="root" if i == 0 else "default",
            depth=0 if i == 0 else 1,
            elf_metadata=meta,
        )
    graph.edges = edges or []
    return graph


def _sym(name: str, version: str = "", is_default: bool = True, vis: str = "default") -> ElfSymbol:
    return ElfSymbol(name=name, version=version, is_default=is_default, visibility=vis)


def _imp(name: str, version: str = "", binding: ElfSymbolBinding = ElfSymbolBinding.GLOBAL) -> ElfImport:
    return ElfImport(name=name, version=version, binding=binding)


class TestBinderEmptyGraph:
    """Cover line 83 (no metadata), 101 (skip node with no metadata)."""

    def test_empty_graph_no_nodes(self):
        graph = DependencyGraph(root="/app")
        bindings = compute_bindings(graph)
        assert bindings == []

    def test_node_with_no_elf_metadata(self):
        """Line 83, 101: node.elf_metadata is None and metadata dict is empty."""
        graph = DependencyGraph(root="/app")
        graph.nodes["/app"] = ResolvedDSO(
            path=Path("/app"),
            soname="app",
            needed=[],
            rpath="",
            runpath="",
            resolution_reason="root",
            depth=0,
            elf_metadata=None,
        )
        bindings = compute_bindings(graph)
        assert bindings == []


class TestBinderPreload:
    """Cover preload path in compute_bindings."""

    def test_preload_searched_first(self):
        """Preload DSO provides symbol before normal load order."""
        graph = _make_graph(
            {
                "/app": (["libfoo.so"], [], [_imp("my_sym")]),
                "/lib/libfoo.so": ([], [_sym("my_sym")], []),
                "/preload/libpre.so": ([], [_sym("my_sym")], []),
            },
            edges=[("/app", "/lib/libfoo.so")],
        )
        bindings = compute_bindings(graph, preload=["/preload/libpre.so"])
        resolved = [b for b in bindings if b.status == BindingStatus.RESOLVED_OK]
        assert len(resolved) == 1
        assert resolved[0].provider == "/preload/libpre.so"


class TestBinderVisibilityBlocked:
    """Cover lines 255-261: visibility_blocked status via _make_not_found_binding."""

    def test_symbol_found_but_all_hidden(self):
        graph = _make_graph(
            {
                "/app": (["libfoo.so"], [], [_imp("hidden_sym")]),
                "/lib/libfoo.so": ([], [_sym("hidden_sym", vis="hidden")], []),
            },
            edges=[("/app", "/lib/libfoo.so")],
        )
        bindings = compute_bindings(graph)
        assert len(bindings) == 1
        assert bindings[0].status == BindingStatus.VISIBILITY_BLOCKED
        assert bindings[0].provider == "/lib/libfoo.so"


class TestBinderVersionMismatch:
    """Cover lines 272: version_mismatch path."""

    def test_version_mismatch_status(self):
        graph = _make_graph(
            {
                "/app": (["libfoo.so"], [], [_imp("sym", version="V2")]),
                "/lib/libfoo.so": ([], [_sym("sym", version="V1")], []),
            },
            edges=[("/app", "/lib/libfoo.so")],
        )
        bindings = compute_bindings(graph)
        assert bindings[0].status == BindingStatus.VERSION_MISMATCH


class TestBinderWeakUnresolved:
    """Cover line 286: weak_unresolved path."""

    def test_weak_symbol_not_found(self):
        graph = _make_graph(
            {
                "/app": ([], [], [_imp("optional", binding=ElfSymbolBinding.WEAK)]),
            },
        )
        bindings = compute_bindings(graph)
        assert bindings[0].status == BindingStatus.WEAK_UNRESOLVED


class TestBinderInterposed:
    """Cover lines 286-297: interposition detected."""

    def test_interposition_with_versioned_symbol(self):
        """Two providers export the same version; the later one is INTERPOSED."""
        graph = _make_graph(
            {
                "/app": (
                    ["libA.so", "libB.so"],
                    [],
                    [_imp("func", version="V1")],
                ),
                "/lib/libA.so": ([], [_sym("func", version="V1")], []),
                "/lib/libB.so": ([], [_sym("func", version="V1")], []),
            },
            edges=[("/app", "/lib/libA.so"), ("/app", "/lib/libB.so")],
        )
        bindings = compute_bindings(graph)
        # /app's import of func@V1 resolves from libA.so (first in load order)
        assert bindings[0].status == BindingStatus.RESOLVED_OK
        assert bindings[0].provider == "/lib/libA.so"


class TestBinderNoVersionRequired:
    """Cover lines 309-312: unversioned import matches any default."""

    def test_unversioned_import_matches_default_version(self):
        graph = _make_graph(
            {
                "/app": (["libfoo.so"], [], [_imp("func")]),
                "/lib/libfoo.so": ([], [_sym("func", version="FOO_1", is_default=True)], []),
            },
            edges=[("/app", "/lib/libfoo.so")],
        )
        bindings = compute_bindings(graph)
        assert bindings[0].status == BindingStatus.RESOLVED_OK

    def test_unversioned_import_matches_unversioned_export(self):
        graph = _make_graph(
            {
                "/app": (["libfoo.so"], [], [_imp("func")]),
                "/lib/libfoo.so": ([], [_sym("func", version="")], []),
            },
            edges=[("/app", "/lib/libfoo.so")],
        )
        bindings = compute_bindings(graph)
        assert bindings[0].status == BindingStatus.RESOLVED_OK


class TestComputeLoadOrder:
    """Cover lines 131-136, 159 in _compute_load_order."""

    def test_empty_graph(self):
        """Line 121-122: empty graph returns empty list."""
        graph = DependencyGraph(root="/app")
        assert _compute_load_order(graph) == []

    def test_unreachable_nodes_appended(self):
        """Line 157-159: nodes not reachable from root are appended at end."""
        graph = _make_graph(
            {
                "/app": ([], [], []),
                "/lib/libfoo.so": ([], [], []),
                "/lib/libbar.so": ([], [], []),
            },
            edges=[],  # no edges — libfoo and libbar are unreachable from root
        )
        order = _compute_load_order(graph)
        assert order[0] == "/app"
        # The unreachable nodes should still appear
        assert set(order) == {"/app", "/lib/libfoo.so", "/lib/libbar.so"}

    def test_root_not_matching_key(self):
        """Lines 131-136: graph.root doesn't match any key, fall back to depth=0."""
        graph = DependencyGraph(root="/nonexistent")
        graph.nodes["/app"] = ResolvedDSO(
            path=Path("/app"),
            soname="app",
            needed=[],
            rpath="",
            runpath="",
            resolution_reason="root",
            depth=0,
            elf_metadata=None,
        )
        graph.edges = []
        order = _compute_load_order(graph)
        assert "/app" in order

    def test_no_root_match_returns_all_keys(self):
        """Lines 135-136: no root match and no depth=0 node."""
        graph = DependencyGraph(root="/nonexistent")
        graph.nodes["/lib/a.so"] = ResolvedDSO(
            path=Path("/lib/a.so"),
            soname="a.so",
            needed=[],
            rpath="",
            runpath="",
            resolution_reason="default",
            depth=1,
            elf_metadata=None,
        )
        graph.edges = []
        order = _compute_load_order(graph)
        assert "/lib/a.so" in order
