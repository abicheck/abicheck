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

"""Unit tests for pe_metadata — dataclass construction, magic detection, serialization, and parsing."""
from __future__ import annotations

import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

from abicheck.pe_metadata import (
    PeExport,
    PeMetadata,
    PeSymbolType,
    is_pe,
    parse_pe_metadata,
)

# ── PeMetadata dataclass ────────────────────────────────────────────────

class TestPeMetadataDataclass:
    def test_default_construction(self):
        meta = PeMetadata()
        assert meta.machine == ""
        assert meta.characteristics == 0
        assert meta.dll_characteristics == 0
        assert meta.exports == []
        assert meta.imports == {}
        assert meta.file_version == ""
        assert meta.product_version == ""

    def test_export_map_cached_property(self):
        e1 = PeExport(name="foo", ordinal=1)
        e2 = PeExport(name="bar", ordinal=2)
        e3 = PeExport(name="", ordinal=3)  # nameless export
        meta = PeMetadata(exports=[e1, e2, e3])
        em = meta.export_map
        assert em == {"foo": e1, "bar": e2}
        # Cached — same object
        assert meta.export_map is em

    def test_pe_export_defaults(self):
        exp = PeExport(name="test")
        assert exp.ordinal == 0
        assert exp.sym_type == PeSymbolType.EXPORTED
        assert exp.forwarder == ""

    def test_pe_export_forwarded(self):
        exp = PeExport(name="func", ordinal=5, sym_type=PeSymbolType.FORWARDED,
                       forwarder="NTDLL.RtlFoo")
        assert exp.sym_type == PeSymbolType.FORWARDED
        assert exp.forwarder == "NTDLL.RtlFoo"


# ── is_pe magic detection ───────────────────────────────────────────────

def _make_pe_file(tmp_path: Path, pe_offset: int = 0x80) -> Path:
    """Create a minimal file with valid PE magic bytes."""
    p = tmp_path / "test.dll"
    data = bytearray(pe_offset + 4)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, pe_offset)
    data[pe_offset:pe_offset + 4] = b"PE\x00\x00"
    p.write_bytes(bytes(data))
    return p


class TestIsPe:
    def test_valid_pe_file(self, tmp_path):
        p = _make_pe_file(tmp_path)
        assert is_pe(p) is True

    def test_non_pe_file(self, tmp_path):
        p = tmp_path / "notpe.bin"
        p.write_bytes(b"\x00" * 256)
        assert is_pe(p) is False

    def test_mz_without_pe_signature(self, tmp_path):
        p = tmp_path / "fakemz.bin"
        data = bytearray(256)
        data[0:2] = b"MZ"
        struct.pack_into("<I", data, 0x3C, 0x80)
        # No PE\0\0 at offset 0x80
        p.write_bytes(bytes(data))
        assert is_pe(p) is False

    def test_truncated_file(self, tmp_path):
        p = tmp_path / "short.bin"
        p.write_bytes(b"MZ")
        assert is_pe(p) is False

    def test_nonexistent_file(self, tmp_path):
        p = tmp_path / "nope.dll"
        assert is_pe(p) is False

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.dll"
        p.write_bytes(b"")
        assert is_pe(p) is False

    def test_elf_file_not_pe(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        assert is_pe(p) is False


# ── Serialization round-trip ─────────────────────────────────────────────

class TestPeSerialization:
    def test_snapshot_roundtrip(self):
        from abicheck.model import AbiSnapshot
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

        pe = PeMetadata(
            machine="IMAGE_FILE_MACHINE_AMD64",
            characteristics=0x2022,
            dll_characteristics=0x8160,
            exports=[
                PeExport(name="init", ordinal=1),
                PeExport(name="fwd", ordinal=2, sym_type=PeSymbolType.FORWARDED,
                         forwarder="OTHER.init"),
            ],
            imports={"KERNEL32.dll": ["LoadLibraryA", "GetProcAddress"]},
            file_version="1.0.0.0",
            product_version="1.0.0.0",
        )
        snap = AbiSnapshot(library="test.dll", version="1.0", pe=pe)
        d = snapshot_to_dict(snap)
        assert d["pe"]["machine"] == "IMAGE_FILE_MACHINE_AMD64"
        assert len(d["pe"]["exports"]) == 2
        assert d["pe"]["exports"][1]["sym_type"] == "forwarded"

        snap2 = snapshot_from_dict(d)
        assert snap2.pe is not None
        assert snap2.pe.machine == "IMAGE_FILE_MACHINE_AMD64"
        assert len(snap2.pe.exports) == 2
        assert snap2.pe.exports[1].sym_type == PeSymbolType.FORWARDED
        assert snap2.pe.exports[1].forwarder == "OTHER.init"
        assert snap2.pe.imports == {"KERNEL32.dll": ["LoadLibraryA", "GetProcAddress"]}


# ── Checker diff_pe ──────────────────────────────────────────────────────

class TestDiffPe:
    def test_removed_export(self):
        from abicheck.checker import ChangeKind, _diff_pe
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="test.dll", version="1.0", pe=PeMetadata(
            exports=[PeExport(name="foo"), PeExport(name="bar")],
        ))
        new = AbiSnapshot(library="test.dll", version="2.0", pe=PeMetadata(
            exports=[PeExport(name="foo")],
        ))
        changes = _diff_pe(old, new)
        removed = [c for c in changes if c.kind == ChangeKind.FUNC_REMOVED]
        assert len(removed) == 1
        assert removed[0].symbol == "bar"

    def test_added_export(self):
        from abicheck.checker import ChangeKind, _diff_pe
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="test.dll", version="1.0", pe=PeMetadata(
            exports=[PeExport(name="foo")],
        ))
        new = AbiSnapshot(library="test.dll", version="2.0", pe=PeMetadata(
            exports=[PeExport(name="foo"), PeExport(name="baz")],
        ))
        changes = _diff_pe(old, new)
        added = [c for c in changes if c.kind == ChangeKind.FUNC_ADDED]
        assert len(added) == 1
        assert added[0].symbol == "baz"

    def test_import_dependency_changes(self):
        from abicheck.checker import ChangeKind, _diff_pe
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="test.dll", version="1.0", pe=PeMetadata(
            exports=[PeExport(name="x")],
            imports={"KERNEL32.dll": ["LoadLibraryA"], "USER32.dll": ["MessageBoxA"]},
        ))
        new = AbiSnapshot(library="test.dll", version="2.0", pe=PeMetadata(
            exports=[PeExport(name="x")],
            imports={"KERNEL32.dll": ["LoadLibraryA"], "ADVAPI32.dll": ["RegOpenKeyA"]},
        ))
        changes = _diff_pe(old, new)
        removed = [c for c in changes if c.kind == ChangeKind.NEEDED_REMOVED]
        added = [c for c in changes if c.kind == ChangeKind.NEEDED_ADDED]
        assert len(removed) == 1
        assert removed[0].symbol == "USER32.dll"
        assert len(added) == 1
        assert added[0].symbol == "ADVAPI32.dll"

    def test_no_changes(self):
        from abicheck.checker import _diff_pe
        from abicheck.model import AbiSnapshot

        pe = PeMetadata(exports=[PeExport(name="foo")])
        old = AbiSnapshot(library="test.dll", version="1.0", pe=pe)
        new = AbiSnapshot(library="test.dll", version="2.0", pe=PeMetadata(
            exports=[PeExport(name="foo")],
        ))
        assert _diff_pe(old, new) == []

    def test_empty_pe_metadata(self):
        from abicheck.checker import _diff_pe
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="test.dll", version="1.0", pe=PeMetadata())
        new = AbiSnapshot(library="test.dll", version="2.0", pe=PeMetadata())
        assert _diff_pe(old, new) == []

    def test_ordinal_only_export_removed(self):
        """Ordinal-only exports (no name) should still be tracked."""
        from abicheck.checker import ChangeKind, _diff_pe
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="test.dll", version="1.0", pe=PeMetadata(
            exports=[PeExport(name="", ordinal=42), PeExport(name="named")],
        ))
        new = AbiSnapshot(library="test.dll", version="2.0", pe=PeMetadata(
            exports=[PeExport(name="named")],
        ))
        changes = _diff_pe(old, new)
        removed = [c for c in changes if c.kind == ChangeKind.FUNC_REMOVED]
        assert len(removed) == 1
        assert removed[0].symbol == "ordinal:42"

    def test_ordinal_only_export_added(self):
        from abicheck.checker import ChangeKind, _diff_pe
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="test.dll", version="1.0", pe=PeMetadata(
            exports=[PeExport(name="named")],
        ))
        new = AbiSnapshot(library="test.dll", version="2.0", pe=PeMetadata(
            exports=[PeExport(name="named"), PeExport(name="", ordinal=99)],
        ))
        changes = _diff_pe(old, new)
        added = [c for c in changes if c.kind == ChangeKind.FUNC_ADDED]
        assert len(added) == 1
        assert added[0].symbol == "ordinal:99"

    def test_export_not_duplicated_when_in_functions(self):
        """Symbols already in snapshot.functions must not be re-emitted by _diff_pe."""
        from abicheck.checker import ChangeKind, _diff_pe
        from abicheck.model import AbiSnapshot, Function

        fn = Function(name="bar", mangled="_bar", return_type="void")
        old = AbiSnapshot(library="test.dll", version="1.0",
                          functions=[fn],
                          pe=PeMetadata(exports=[PeExport(name="foo"), PeExport(name="bar")]))
        new = AbiSnapshot(library="test.dll", version="2.0",
                          functions=[fn],
                          pe=PeMetadata(exports=[PeExport(name="foo")]))
        changes = _diff_pe(old, new)
        removed = [c for c in changes if c.kind == ChangeKind.FUNC_REMOVED]
        # "bar" is already in old.functions → must be deduplicated
        assert all(c.symbol != "bar" for c in removed)

    def test_export_removed_not_in_functions_still_emitted(self):
        """Symbols in PE exports but NOT in functions must still be reported."""
        from abicheck.checker import ChangeKind, _diff_pe
        from abicheck.model import AbiSnapshot, Function

        fn = Function(name="foo", mangled="_foo", return_type="void")
        old = AbiSnapshot(library="test.dll", version="1.0",
                          functions=[fn],
                          pe=PeMetadata(exports=[PeExport(name="foo"), PeExport(name="baz")]))
        new = AbiSnapshot(library="test.dll", version="2.0",
                          functions=[fn],
                          pe=PeMetadata(exports=[PeExport(name="foo")]))
        changes = _diff_pe(old, new)
        removed = [c for c in changes if c.kind == ChangeKind.FUNC_REMOVED]
        # "baz" is not in functions → must still be emitted
        assert any(c.symbol == "baz" for c in removed)


# ── parse_pe_metadata ───────────────────────────────────────────────────

class TestParsePeMetadata:
    def test_nonexistent_file_returns_empty(self):
        meta = parse_pe_metadata(Path("/nonexistent/fake.dll"))
        assert isinstance(meta, PeMetadata)
        assert meta.exports == []

    def test_directory_returns_empty(self, tmp_path):
        meta = parse_pe_metadata(tmp_path)
        assert isinstance(meta, PeMetadata)
        assert meta.exports == []

    def test_parse_with_mock_pefile(self, tmp_path):
        """Exercise _parse via a mocked pefile module."""
        f = tmp_path / "test.dll"
        f.write_bytes(b"MZ" + b"\x00" * 256)

        # Mock PE object
        mock_pe = MagicMock()
        mock_pe.FILE_HEADER.Machine = 0x8664
        mock_pe.FILE_HEADER.Characteristics = 0x2022
        mock_pe.OPTIONAL_HEADER.DllCharacteristics = 0x8160

        # Exports
        exp1 = MagicMock()
        exp1.name = b"my_func"
        exp1.ordinal = 1
        exp1.forwarder = None
        exp2 = MagicMock()
        exp2.name = b"fwd_func"
        exp2.ordinal = 2
        exp2.forwarder = b"OTHER.init"
        mock_pe.DIRECTORY_ENTRY_EXPORT.symbols = [exp1, exp2]

        # Imports
        imp_entry = MagicMock()
        imp_entry.dll = b"KERNEL32.dll"
        imp_func = MagicMock()
        imp_func.name = b"LoadLibraryA"
        imp_entry.imports = [imp_func]
        mock_pe.DIRECTORY_ENTRY_IMPORT = [imp_entry]

        # No version resource, no delay-load import directory
        del mock_pe.VS_FIXEDFILEINFO
        del mock_pe.DIRECTORY_ENTRY_DELAY_IMPORT

        mock_pefile = MagicMock()
        mock_pefile.PE.return_value = mock_pe
        mock_pefile.PEFormatError = Exception
        mock_pefile.MACHINE_TYPE = {0x8664: "IMAGE_FILE_MACHINE_AMD64"}
        mock_pefile.DIRECTORY_ENTRY = {
            "IMAGE_DIRECTORY_ENTRY_EXPORT": 0,
            "IMAGE_DIRECTORY_ENTRY_IMPORT": 1,
            "IMAGE_DIRECTORY_ENTRY_RESOURCE": 2,
        }

        with patch("abicheck.pe_metadata.pefile", mock_pefile):
            meta = parse_pe_metadata(f)

        assert meta.machine == "IMAGE_FILE_MACHINE_AMD64"
        assert meta.characteristics == 0x2022
        assert len(meta.exports) == 2
        assert meta.exports[0].name == "my_func"
        assert meta.exports[1].sym_type == PeSymbolType.FORWARDED
        assert meta.exports[1].forwarder == "OTHER.init"
        assert meta.imports == {"KERNEL32.dll": ["LoadLibraryA"]}
        mock_pe.close.assert_called_once()

    def test_parse_with_version_resource(self, tmp_path):
        """Exercise VS_FIXEDFILEINFO parsing."""
        f = tmp_path / "test.dll"
        f.write_bytes(b"MZ" + b"\x00" * 256)

        mock_pe = MagicMock()
        mock_pe.FILE_HEADER.Machine = 0x14C
        mock_pe.FILE_HEADER.Characteristics = 0
        mock_pe.OPTIONAL_HEADER.DllCharacteristics = 0
        # No exports/imports
        del mock_pe.DIRECTORY_ENTRY_EXPORT
        del mock_pe.DIRECTORY_ENTRY_IMPORT

        # Version resource
        vinfo = MagicMock()
        vinfo.FileVersionMS = (10 << 16) | 0
        vinfo.FileVersionLS = (19041 << 16) | 1
        vinfo.ProductVersionMS = (10 << 16) | 0
        vinfo.ProductVersionLS = (19041 << 16) | 1
        mock_pe.VS_FIXEDFILEINFO = [vinfo]

        mock_pefile = MagicMock()
        mock_pefile.PE.return_value = mock_pe
        mock_pefile.PEFormatError = Exception
        mock_pefile.MACHINE_TYPE = {0x14C: "IMAGE_FILE_MACHINE_I386"}
        mock_pefile.DIRECTORY_ENTRY = {
            "IMAGE_DIRECTORY_ENTRY_EXPORT": 0,
            "IMAGE_DIRECTORY_ENTRY_IMPORT": 1,
            "IMAGE_DIRECTORY_ENTRY_RESOURCE": 2,
        }

        with patch("abicheck.pe_metadata.pefile", mock_pefile):
            meta = parse_pe_metadata(f)

        assert meta.file_version == "10.0.19041.1"
        assert meta.product_version == "10.0.19041.1"

    def test_parse_format_error(self, tmp_path):
        """PEFormatError should be caught, return empty metadata."""
        f = tmp_path / "bad.dll"
        f.write_bytes(b"MZ" + b"\x00" * 256)

        mock_pefile = MagicMock()
        mock_pefile.PEFormatError = type("PEFormatError", (Exception,), {})
        mock_pefile.PE.side_effect = mock_pefile.PEFormatError("bad")
        mock_pefile.DIRECTORY_ENTRY = {
            "IMAGE_DIRECTORY_ENTRY_EXPORT": 0,
            "IMAGE_DIRECTORY_ENTRY_IMPORT": 1,
            "IMAGE_DIRECTORY_ENTRY_RESOURCE": 2,
        }

        with patch("abicheck.pe_metadata.pefile", mock_pefile):
            meta = parse_pe_metadata(f)

        assert isinstance(meta, PeMetadata)
        assert meta.exports == []


    def test_parse_delay_imports_and_missing_subsystem_version(self, tmp_path):
        """Exercise DIRECTORY_ENTRY_DELAY_IMPORT parsing (named, ordinal-only,
        and nameless non-ordinal entries) and the absent-subsystem-version
        branch (an OPTIONAL_HEADER without Major/MinorSubsystemVersion must
        leave the field uncaptured)."""
        from types import SimpleNamespace

        f = tmp_path / "test.dll"
        f.write_bytes(b"MZ" + b"\x00" * 256)

        mock_pe = MagicMock()
        mock_pe.FILE_HEADER.Machine = 0x8664
        mock_pe.FILE_HEADER.Characteristics = 0x2022
        mock_pe.OPTIONAL_HEADER = SimpleNamespace(DllCharacteristics=0x0100)
        del mock_pe.DIRECTORY_ENTRY_EXPORT
        del mock_pe.DIRECTORY_ENTRY_IMPORT
        del mock_pe.VS_FIXEDFILEINFO

        named = MagicMock()
        named.name = b"MiniDumpWriteDump"
        by_ordinal = MagicMock()
        by_ordinal.name = None
        by_ordinal.import_by_ordinal = True
        by_ordinal.ordinal = 42
        nameless = MagicMock()
        nameless.name = None
        nameless.import_by_ordinal = False

        entry = MagicMock()
        entry.dll = b"DBGHELP.dll"
        entry.imports = [named, by_ordinal, nameless]
        mock_pe.DIRECTORY_ENTRY_DELAY_IMPORT = [entry]

        mock_pefile = MagicMock()
        mock_pefile.PE.return_value = mock_pe
        mock_pefile.PEFormatError = Exception
        mock_pefile.MACHINE_TYPE = {0x8664: "IMAGE_FILE_MACHINE_AMD64"}
        mock_pefile.DIRECTORY_ENTRY = {
            "IMAGE_DIRECTORY_ENTRY_EXPORT": 0,
            "IMAGE_DIRECTORY_ENTRY_IMPORT": 1,
            "IMAGE_DIRECTORY_ENTRY_RESOURCE": 2,
        }

        with patch("abicheck.pe_metadata.pefile", mock_pefile):
            meta = parse_pe_metadata(f)

        assert meta.delay_imports == {
            "DBGHELP.dll": ["MiniDumpWriteDump", "ordinal:42"]
        }
        assert meta.subsystem_version == ""
