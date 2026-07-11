# pylint: disable=too-many-branches,too-many-statements,too-many-locals,too-many-arguments,too-many-return-statements
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

"""Unit tests for macho_metadata — dataclass construction, magic detection, serialization, and parsing."""
from __future__ import annotations

import struct

from abicheck.macho_metadata import (
    MachoExport,
    MachoMetadata,
    MachoSymbolType,
    _version_field_to_str,
    _version_str,
    is_macho,
    parse_macho_metadata,
)

# ── MachoMetadata dataclass ─────────────────────────────────────────────

class TestMachoMetadataDataclass:
    def test_default_construction(self):
        meta = MachoMetadata()
        assert meta.cpu_type == ""
        assert meta.filetype == ""
        assert meta.flags == 0
        assert meta.install_name == ""
        assert meta.dependent_libs == []
        assert meta.reexported_libs == []
        assert meta.exports == []
        assert meta.current_version == ""
        assert meta.compat_version == ""
        assert meta.min_os_version == ""

    def test_export_map_cached_property(self):
        e1 = MachoExport(name="foo")
        e2 = MachoExport(name="bar")
        e3 = MachoExport(name="")  # nameless
        meta = MachoMetadata(exports=[e1, e2, e3])
        em = meta.export_map
        assert em == {"foo": e1, "bar": e2}
        assert meta.export_map is em

    def test_macho_export_defaults(self):
        exp = MachoExport(name="test")
        assert exp.sym_type == MachoSymbolType.EXPORTED
        assert exp.is_weak is False

    def test_macho_export_weak(self):
        exp = MachoExport(name="weak_fn", sym_type=MachoSymbolType.WEAK, is_weak=True)
        assert exp.sym_type == MachoSymbolType.WEAK
        assert exp.is_weak is True


# ── _version_str ────────────────────────────────────────────────────────

class TestVersionStr:
    def test_simple_version(self):
        # 1.2.3 → major=1, minor=2, patch=3
        packed = (1 << 16) | (2 << 8) | 3
        assert _version_str(packed) == "1.2.3"

    def test_zero_version(self):
        assert _version_str(0) == "0.0.0"

    def test_high_major(self):
        packed = (10 << 16) | (0 << 8) | 0
        assert _version_str(packed) == "10.0.0"

    def test_version_field_to_str_from_int(self):
        packed = (3 << 16) | (4 << 8) | 5
        assert _version_field_to_str(packed) == "3.4.5"

    def test_version_field_to_str_from_helper_object(self):
        class _Helper:
            _version = (7 << 16) | (8 << 8) | 9

        assert _version_field_to_str(_Helper()) == "7.8.9"


# ── is_macho magic detection ────────────────────────────────────────────

class TestIsMacho:
    def test_macho_64_le(self, tmp_path):
        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 100)
        assert is_macho(p) is True

    def test_macho_64_be(self, tmp_path):
        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xcf" + b"\x00" * 100)
        assert is_macho(p) is True

    def test_macho_32_le(self, tmp_path):
        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xce\xfa\xed\xfe" + b"\x00" * 100)
        assert is_macho(p) is True

    def test_macho_32_be(self, tmp_path):
        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        assert is_macho(p) is True

    def test_fat_binary(self, tmp_path):
        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xca\xfe\xba\xbe" + b"\x00" * 100)
        assert is_macho(p) is True

    def test_fat_binary_swapped(self, tmp_path):
        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xbe\xba\xfe\xca" + b"\x00" * 100)
        assert is_macho(p) is True

    def test_fat64_binary(self, tmp_path):
        """FAT_MAGIC_64 (fat64 universal binary) must be recognized."""
        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xca\xfe\xba\xbf" + b"\x00" * 100)
        assert is_macho(p) is True

    def test_fat64_binary_swapped(self, tmp_path):
        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xbf\xba\xfe\xca" + b"\x00" * 100)
        assert is_macho(p) is True

    def test_elf_not_macho(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        assert is_macho(p) is False

    def test_pe_not_macho(self, tmp_path):
        p = tmp_path / "test.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        assert is_macho(p) is False

    def test_nonexistent_file(self, tmp_path):
        p = tmp_path / "nope.dylib"
        assert is_macho(p) is False

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.dylib"
        p.write_bytes(b"")
        assert is_macho(p) is False

    def test_short_file(self, tmp_path):
        p = tmp_path / "short.dylib"
        p.write_bytes(b"\xcf\xfa")
        assert is_macho(p) is False


# ── Serialization round-trip ─────────────────────────────────────────────

class TestMachoSerialization:
    def test_snapshot_roundtrip(self):
        from abicheck.model import AbiSnapshot
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

        macho = MachoMetadata(
            cpu_type="ARM64",
            filetype="MH_DYLIB",
            flags=0x00200085,
            install_name="/usr/lib/libfoo.1.dylib",
            dependent_libs=["/usr/lib/libSystem.B.dylib"],
            reexported_libs=[],
            exports=[
                MachoExport(name="foo_init"),
                MachoExport(name="foo_weak", sym_type=MachoSymbolType.WEAK, is_weak=True),
            ],
            current_version="1.2.3",
            compat_version="1.0.0",
            min_os_version="11.0.0",
        )
        snap = AbiSnapshot(library="libfoo.dylib", version="1.2.3", macho=macho)
        d = snapshot_to_dict(snap)
        assert d["macho"]["cpu_type"] == "ARM64"
        assert len(d["macho"]["exports"]) == 2
        assert d["macho"]["exports"][1]["sym_type"] == "weak"

        snap2 = snapshot_from_dict(d)
        assert snap2.macho is not None
        assert snap2.macho.cpu_type == "ARM64"
        assert len(snap2.macho.exports) == 2
        assert snap2.macho.exports[1].sym_type == MachoSymbolType.WEAK
        assert snap2.macho.exports[1].is_weak is True
        assert snap2.macho.install_name == "/usr/lib/libfoo.1.dylib"
        assert snap2.macho.dependent_libs == ["/usr/lib/libSystem.B.dylib"]


# ── Checker diff_macho ───────────────────────────────────────────────────

class TestDiffMacho:
    def test_removed_export(self):
        from abicheck.checker import ChangeKind, _diff_macho
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="libfoo.dylib", version="1.0", macho=MachoMetadata(
            exports=[MachoExport(name="foo"), MachoExport(name="bar")],
        ))
        new = AbiSnapshot(library="libfoo.dylib", version="2.0", macho=MachoMetadata(
            exports=[MachoExport(name="foo")],
        ))
        changes = _diff_macho(old, new)
        removed = [c for c in changes if c.kind == ChangeKind.FUNC_REMOVED]
        assert len(removed) == 1
        assert removed[0].symbol == "bar"

    def test_added_export(self):
        from abicheck.checker import ChangeKind, _diff_macho
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="libfoo.dylib", version="1.0", macho=MachoMetadata(
            exports=[MachoExport(name="foo")],
        ))
        new = AbiSnapshot(library="libfoo.dylib", version="2.0", macho=MachoMetadata(
            exports=[MachoExport(name="foo"), MachoExport(name="baz")],
        ))
        changes = _diff_macho(old, new)
        added = [c for c in changes if c.kind == ChangeKind.FUNC_ADDED]
        assert len(added) == 1
        assert added[0].symbol == "baz"

    def test_install_name_change(self):
        from abicheck.checker import ChangeKind, _diff_macho
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="libfoo.dylib", version="1.0", macho=MachoMetadata(
            exports=[MachoExport(name="foo")],
            install_name="/usr/lib/libfoo.1.dylib",
        ))
        new = AbiSnapshot(library="libfoo.dylib", version="2.0", macho=MachoMetadata(
            exports=[MachoExport(name="foo")],
            install_name="/usr/lib/libfoo.2.dylib",
        ))
        changes = _diff_macho(old, new)
        soname = [c for c in changes if c.kind == ChangeKind.SONAME_CHANGED
                  and c.symbol == "LC_ID_DYLIB"]
        assert len(soname) == 1
        assert soname[0].old_value == "/usr/lib/libfoo.1.dylib"
        assert soname[0].new_value == "/usr/lib/libfoo.2.dylib"

    def test_compat_version_change(self):
        from abicheck.checker import ChangeKind, _diff_macho
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="libfoo.dylib", version="1.0", macho=MachoMetadata(
            exports=[MachoExport(name="foo")],
            compat_version="1.0.0",
        ))
        new = AbiSnapshot(library="libfoo.dylib", version="2.0", macho=MachoMetadata(
            exports=[MachoExport(name="foo")],
            compat_version="2.0.0",
        ))
        changes = _diff_macho(old, new)
        compat = [c for c in changes if c.symbol == "compat_version"]
        assert len(compat) == 1
        assert compat[0].kind == ChangeKind.COMPAT_VERSION_CHANGED

    def test_dependency_changes(self):
        from abicheck.checker import ChangeKind, _diff_macho
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="libfoo.dylib", version="1.0", macho=MachoMetadata(
            exports=[MachoExport(name="foo")],
            dependent_libs=["/usr/lib/libSystem.B.dylib", "/usr/lib/libz.1.dylib"],
        ))
        new = AbiSnapshot(library="libfoo.dylib", version="2.0", macho=MachoMetadata(
            exports=[MachoExport(name="foo")],
            dependent_libs=["/usr/lib/libSystem.B.dylib", "/usr/lib/libc++.1.dylib"],
        ))
        changes = _diff_macho(old, new)
        removed = [c for c in changes if c.kind == ChangeKind.NEEDED_REMOVED]
        added = [c for c in changes if c.kind == ChangeKind.NEEDED_ADDED]
        assert len(removed) == 1
        assert "/usr/lib/libz.1.dylib" in removed[0].symbol
        assert len(added) == 1
        assert "/usr/lib/libc++.1.dylib" in added[0].symbol

    def test_no_changes(self):
        from abicheck.checker import _diff_macho
        from abicheck.model import AbiSnapshot

        macho = MachoMetadata(exports=[MachoExport(name="foo")])
        old = AbiSnapshot(library="libfoo.dylib", version="1.0", macho=macho)
        new = AbiSnapshot(library="libfoo.dylib", version="2.0", macho=MachoMetadata(
            exports=[MachoExport(name="foo")],
        ))
        assert _diff_macho(old, new) == []

    def test_empty_macho_metadata(self):
        from abicheck.checker import _diff_macho
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="libfoo.dylib", version="1.0", macho=MachoMetadata())
        new = AbiSnapshot(library="libfoo.dylib", version="2.0", macho=MachoMetadata())
        assert _diff_macho(old, new) == []

    def test_metadata_diff_without_exports(self):
        """Install name and dependency changes detected even with no exports."""
        from abicheck.checker import ChangeKind, _diff_macho
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="libfoo.dylib", version="1.0", macho=MachoMetadata(
            install_name="/usr/lib/libfoo.1.dylib",
            dependent_libs=["/usr/lib/libSystem.B.dylib"],
        ))
        new = AbiSnapshot(library="libfoo.dylib", version="2.0", macho=MachoMetadata(
            install_name="/usr/lib/libfoo.2.dylib",
            dependent_libs=["/usr/lib/libSystem.B.dylib", "/usr/lib/libc++.1.dylib"],
        ))
        changes = _diff_macho(old, new)
        assert any(c.kind == ChangeKind.SONAME_CHANGED for c in changes)
        assert any(c.kind == ChangeKind.NEEDED_ADDED for c in changes)

    def test_export_not_duplicated_when_in_functions(self):
        """Dylib exports already in snapshot.functions must not be re-emitted."""
        from abicheck.checker import ChangeKind, _diff_macho
        from abicheck.model import AbiSnapshot, Function

        fn = Function(name="bar", mangled="_bar", return_type="void")
        old = AbiSnapshot(library="libfoo.dylib", version="1.0",
                          functions=[fn],
                          macho=MachoMetadata(exports=[MachoExport(name="foo"), MachoExport(name="bar")]))
        new = AbiSnapshot(library="libfoo.dylib", version="2.0",
                          functions=[fn],
                          macho=MachoMetadata(exports=[MachoExport(name="foo")]))
        changes = _diff_macho(old, new)
        removed = [c for c in changes if c.kind == ChangeKind.FUNC_REMOVED]
        # "bar" is already in old.functions → must be deduplicated
        assert all(c.symbol != "bar" for c in removed)

    def test_export_removed_not_in_functions_still_emitted(self):
        """Dylib exports NOT in functions must still be reported."""
        from abicheck.checker import ChangeKind, _diff_macho
        from abicheck.model import AbiSnapshot, Function

        fn = Function(name="foo", mangled="_foo", return_type="void")
        old = AbiSnapshot(library="libfoo.dylib", version="1.0",
                          functions=[fn],
                          macho=MachoMetadata(exports=[MachoExport(name="foo"), MachoExport(name="baz")]))
        new = AbiSnapshot(library="libfoo.dylib", version="2.0",
                          functions=[fn],
                          macho=MachoMetadata(exports=[MachoExport(name="foo")]))
        changes = _diff_macho(old, new)
        removed = [c for c in changes if c.kind == ChangeKind.FUNC_REMOVED]
        # "baz" is not in functions → must still be emitted
        assert any(c.symbol == "baz" for c in removed)

    def test_reexported_lib_removed_and_added(self):
        """A single re-export removed+added pair collapses to the repoint kind;
        deltas with no replacement keep the NEEDED_REMOVED/ADDED reporting."""
        from abicheck.checker import ChangeKind, _diff_macho
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="libfoo.dylib", version="1.0", macho=MachoMetadata(
            reexported_libs=["/usr/lib/libA.dylib", "/usr/lib/libB.dylib"],
        ))
        new = AbiSnapshot(library="libfoo.dylib", version="2.0", macho=MachoMetadata(
            reexported_libs=["/usr/lib/libA.dylib", "/usr/lib/libC.dylib"],
        ))
        changes = _diff_macho(old, new)
        repointed = [c for c in changes if c.kind == ChangeKind.MACHO_REEXPORT_CHANGED]
        assert len(repointed) == 1
        assert repointed[0].old_value == "/usr/lib/libB.dylib"
        assert repointed[0].new_value == "/usr/lib/libC.dylib"

        # Removals with no replacement fall back to per-lib reporting.
        new_two_gone = AbiSnapshot(
            library="libfoo.dylib", version="2.0", macho=MachoMetadata()
        )
        changes = _diff_macho(old, new_two_gone)
        removed = [c for c in changes if c.kind == ChangeKind.NEEDED_REMOVED]
        assert any("/usr/lib/libA.dylib" in c.symbol for c in removed)
        assert any("/usr/lib/libB.dylib" in c.symbol for c in removed)


# ── Synthetic Mach-O binary builder ──────────────────────────────────────

def _build_macho_64_le(
    *,
    cputype: int = 0x0100000C,  # ARM64
    filetype: int = 6,          # MH_DYLIB
    flags: int = 0x00200085,
    install_name: str = "",
    dependent_libs: list[str] | None = None,
    reexported_libs: list[str] | None = None,
    min_os_version: int = 0,
    symbols: list[tuple[str, int, int]] | None = None,  # (name, n_type, n_desc)
) -> bytes:
    """Build a minimal 64-bit little-endian Mach-O binary for testing."""
    endian = "<"
    magic = b"\xcf\xfa\xed\xfe"

    load_cmds = bytearray()
    ncmds = 0

    def _dylib_cmd(cmd_id: int, name: str, cur_ver: int = 0x10000, compat_ver: int = 0x10000) -> bytes:
        name_bytes = name.encode("utf-8") + b"\x00"
        # dylib_command: cmd(4), cmdsize(4), name_offset(4), timestamp(4), cur_ver(4), compat_ver(4)
        # name_offset = 24 (after the fixed part)
        name_offset = 24
        total = name_offset + len(name_bytes)
        # Pad to 8-byte alignment
        total = (total + 7) & ~7
        buf = struct.pack(f"{endian}IIIIII", cmd_id, total, name_offset, 0, cur_ver, compat_ver)
        buf += name_bytes
        buf += b"\x00" * (total - len(buf))
        return buf

    if install_name:
        cmd = _dylib_cmd(0xD, install_name, cur_ver=(1 << 16) | (2 << 8) | 3, compat_ver=(1 << 16))
        load_cmds += cmd
        ncmds += 1

    for dep in (dependent_libs or []):
        load_cmds += _dylib_cmd(0xC, dep)
        ncmds += 1

    for dep in (reexported_libs or []):
        load_cmds += _dylib_cmd(0x8000001F, dep)
        ncmds += 1

    if min_os_version:
        # LC_VERSION_MIN_MACOSX: cmd(4), cmdsize(4), version(4), sdk(4)
        load_cmds += struct.pack(f"{endian}IIII", 0x24, 16, min_os_version, 0)
        ncmds += 1

    # Build symbol table
    symtab_entries = symbols or []
    if symtab_entries:
        # We'll compute offsets after placing the symtab load command
        # LC_SYMTAB: cmd(4), cmdsize(4), symoff(4), nsyms(4), stroff(4), strsize(4)
        symtab_cmd_size = 24
        symtab_placeholder_pos = len(load_cmds)
        load_cmds += b"\x00" * symtab_cmd_size
        ncmds += 1
    else:
        symtab_placeholder_pos = -1

    # Header: magic(4) + cputype(4), cpusubtype(4), filetype(4), ncmds(4), sizeofcmds(4), flags(4), reserved(4)
    hdr_size = 32
    sizeofcmds = len(load_cmds)

    # After header + load_cmds, place symtab data
    data_start = hdr_size + sizeofcmds

    if symtab_entries:
        # Build string table
        strtab = bytearray(b"\x00")  # first byte is null
        str_indices: list[int] = []
        for name, _, _ in symtab_entries:
            str_indices.append(len(strtab))
            strtab += b"_" + name.encode("utf-8") + b"\x00"  # Mach-O C symbols have leading _

        # Build nlist_64 entries
        symtab_data = bytearray()
        for i, (name, n_type, n_desc) in enumerate(symtab_entries):
            # nlist_64: n_strx(4), n_type(1), n_sect(1), n_desc(2), n_value(8)
            symtab_data += struct.pack(f"{endian}IBBHQ", str_indices[i], n_type, 1, n_desc, 0)

        symoff = data_start
        nsyms = len(symtab_entries)
        stroff = data_start + len(symtab_data)
        strsize = len(strtab)

        # Fill in LC_SYMTAB command
        symtab_cmd = struct.pack(f"{endian}IIIIII", 0x2, symtab_cmd_size, symoff, nsyms, stroff, strsize)
        load_cmds[symtab_placeholder_pos:symtab_placeholder_pos + symtab_cmd_size] = symtab_cmd

        # Recalculate sizeofcmds since load_cmds length may have changed
        sizeofcmds = len(load_cmds)
        # Update offsets after header recalculation
        data_start = hdr_size + sizeofcmds
        symoff = data_start
        stroff = data_start + len(symtab_data)
        symtab_cmd = struct.pack(f"{endian}IIIIII", 0x2, symtab_cmd_size, symoff, nsyms, stroff, strsize)
        load_cmds[symtab_placeholder_pos:symtab_placeholder_pos + symtab_cmd_size] = symtab_cmd

        trailing = bytes(symtab_data) + bytes(strtab)
    else:
        trailing = b""

    hdr = magic + struct.pack(f"{endian}IIiIIII", cputype, 0, filetype, ncmds, len(load_cmds), flags, 0)
    return bytes(hdr) + bytes(load_cmds) + trailing


# ── parse_macho_metadata ─────────────────────────────────────────────────

class TestParseMachoMetadata:
    def test_nonexistent_file_returns_empty(self):
        from pathlib import Path
        meta = parse_macho_metadata(Path("/nonexistent/fake.dylib"))
        assert isinstance(meta, MachoMetadata)
        assert meta.exports == []

    def test_directory_returns_empty(self, tmp_path):
        meta = parse_macho_metadata(tmp_path)
        assert isinstance(meta, MachoMetadata)
        assert meta.exports == []

    def test_non_macho_file_returns_empty(self, tmp_path):
        f = tmp_path / "bad.dylib"
        f.write_bytes(b"not a macho file" + b"\x00" * 100)
        meta = parse_macho_metadata(f)
        assert isinstance(meta, MachoMetadata)

    def test_truncated_header_returns_empty(self, tmp_path):
        f = tmp_path / "trunc.dylib"
        f.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 4)
        meta = parse_macho_metadata(f)
        assert isinstance(meta, MachoMetadata)

    def test_parse_minimal_dylib(self, tmp_path):
        """Parse a synthetic Mach-O 64-bit LE dylib with basic metadata."""
        data = _build_macho_64_le(
            install_name="/usr/lib/libtest.1.dylib",
            dependent_libs=["/usr/lib/libSystem.B.dylib"],
        )
        f = tmp_path / "libtest.dylib"
        f.write_bytes(data)
        meta = parse_macho_metadata(f)
        assert meta.cpu_type == "ARM64"
        assert meta.filetype == "MH_DYLIB"
        assert meta.install_name == "/usr/lib/libtest.1.dylib"
        assert "/usr/lib/libSystem.B.dylib" in meta.dependent_libs
        assert meta.current_version == "1.2.3"
        assert meta.compat_version == "1.0.0"

    def test_parse_with_exports(self, tmp_path):
        """Parse a synthetic Mach-O with exported symbols."""
        N_EXT = 0x01
        N_SECT = 0x0E
        N_WEAK_DEF = 0x0080
        data = _build_macho_64_le(
            symbols=[
                ("my_func", N_EXT | N_SECT, 0),           # normal export
                ("weak_func", N_EXT | N_SECT, N_WEAK_DEF),  # weak export
                ("internal", N_SECT, 0),                    # not exported (no N_EXT)
            ],
        )
        f = tmp_path / "libtest.dylib"
        f.write_bytes(data)
        meta = parse_macho_metadata(f)
        names = [e.name for e in meta.exports]
        assert "my_func" in names
        assert "weak_func" in names
        assert "internal" not in names
        # Check weak flag
        weak = [e for e in meta.exports if e.name == "weak_func"]
        assert len(weak) == 1
        assert weak[0].is_weak is True
        assert weak[0].sym_type == MachoSymbolType.WEAK

    def test_parse_with_min_os_version(self, tmp_path):
        """Exercise LC_VERSION_MIN_MACOSX parsing."""
        min_ver = (11 << 16) | (0 << 8) | 0
        data = _build_macho_64_le(min_os_version=min_ver)
        f = tmp_path / "libtest.dylib"
        f.write_bytes(data)
        meta = parse_macho_metadata(f)
        assert meta.min_os_version == "11.0.0"

    def test_parse_with_reexported_libs(self, tmp_path):
        data = _build_macho_64_le(
            reexported_libs=["/usr/lib/libreexport.dylib"],
        )
        f = tmp_path / "libtest.dylib"
        f.write_bytes(data)
        meta = parse_macho_metadata(f)
        assert "/usr/lib/libreexport.dylib" in meta.reexported_libs

    def test_parse_fat_binary_returns_metadata(self, tmp_path):
        """Fat binary with single slice should parse that slice."""
        # Build a single-arch Mach-O
        inner = _build_macho_64_le(install_name="/usr/lib/libfat.dylib")
        # Build fat header: magic(4), nfat_arch(4), then fat_arch entry(20)
        fat_offset = 4 + 4 + 20  # after fat header + 1 arch entry
        # Pad to make offset work
        fat_offset = (fat_offset + 7) & ~7
        fat = struct.pack(">II", 0xCAFEBABE, 1)  # FAT_MAGIC, 1 arch
        # fat_arch: cputype(4), cpusubtype(4), offset(4), size(4), align(4)
        fat += struct.pack(">IIIII", 0x0100000C, 0, fat_offset, len(inner), 3)
        fat += b"\x00" * (fat_offset - len(fat))  # pad to offset
        fat += inner
        f = tmp_path / "libfat.dylib"
        f.write_bytes(fat)
        meta = parse_macho_metadata(f)
        assert meta.cpu_type == "ARM64"
        assert meta.install_name == "/usr/lib/libfat.dylib"

    def test_parse_fat_binary_multi_arch_prefers_known_arch(self, tmp_path):
        """Fat binary with x86_64 + arm64 slices: both are parseable (arch selection is deterministic)."""
        inner_x86 = _build_macho_64_le(cputype=0x01000007, install_name="/usr/lib/libfat_x86.dylib")
        inner_arm = _build_macho_64_le(cputype=0x0100000C, install_name="/usr/lib/libfat_arm.dylib")
        # fat header: magic + nfat_arch
        n_arches = 2
        header_size = 4 + 4 + n_arches * 20
        fat_offset_x86 = (header_size + 7) & ~7
        fat_offset_arm = (fat_offset_x86 + len(inner_x86) + 7) & ~7

        fat = struct.pack(">II", 0xCAFEBABE, n_arches)
        # x86_64: cputype=0x01000007
        fat += struct.pack(">IIIII", 0x01000007, 3, fat_offset_x86, len(inner_x86), 3)
        # arm64: cputype=0x0100000C
        fat += struct.pack(">IIIII", 0x0100000C, 0, fat_offset_arm, len(inner_arm), 3)
        fat += b"\x00" * (fat_offset_x86 - len(fat))
        fat += inner_x86
        fat += b"\x00" * (fat_offset_arm - len(fat))
        fat += inner_arm
        f = tmp_path / "libfat_multi.dylib"
        f.write_bytes(fat)
        meta = parse_macho_metadata(f)
        # Verify host-arch preference: arm64/aarch64 runners should get arm slice,
        # x86_64 runners should get x86 slice, not silently always first-wins.
        import platform as _plat
        machine = _plat.machine().lower()
        if machine in ("arm64", "aarch64"):
            assert meta.install_name == "/usr/lib/libfat_arm.dylib", (
                f"Expected arm64 slice on {machine}, got: {meta.install_name}"
            )
        else:
            assert meta.install_name == "/usr/lib/libfat_x86.dylib", (
                f"Expected x86_64 slice on {machine}, got: {meta.install_name}"
            )

    def test_parse_fat_binary_empty_arches(self, tmp_path):
        """Fat binary with 0 arches returns empty metadata."""
        fat = struct.pack(">II", 0xCAFEBABE, 0)  # FAT_MAGIC, 0 arches
        f = tmp_path / "empty_fat.dylib"
        f.write_bytes(fat)
        meta = parse_macho_metadata(f)
        assert isinstance(meta, MachoMetadata)
        assert meta.cpu_type == ""


# ── CLI _dump_native_binary / _detect_binary_format ──────────────────────

class TestCliIntegration:
    def test_detect_binary_format_pe(self, tmp_path):
        from abicheck.cli import _detect_binary_format
        p = tmp_path / "test.dll"
        data = bytearray(0x84 + 4)
        data[0:2] = b"MZ"
        struct.pack_into("<I", data, 0x3C, 0x80)
        data[0x80:0x84] = b"PE\x00\x00"
        p.write_bytes(bytes(data))
        assert _detect_binary_format(p) == "pe"

    def test_detect_binary_format_macho(self, tmp_path):
        from abicheck.cli import _detect_binary_format
        data = _build_macho_64_le()
        p = tmp_path / "lib.dylib"
        p.write_bytes(data)
        assert _detect_binary_format(p) == "macho"

    def test_detect_binary_format_elf(self, tmp_path):
        from abicheck.cli import _detect_binary_format
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        assert _detect_binary_format(p) == "elf"

    def test_detect_binary_format_unknown(self, tmp_path):
        from abicheck.cli import _detect_binary_format
        p = tmp_path / "data.txt"
        p.write_text("hello")
        assert _detect_binary_format(p) is None

    def test_dump_native_binary_pe(self, tmp_path):
        """Exercise _dump_native_binary for PE format (mocked parse)."""
        from unittest.mock import patch as mock_patch

        from abicheck.cli import _dump_native_binary
        from abicheck.pe_metadata import PeExport, PeMetadata

        pe_meta = PeMetadata(
            machine="IMAGE_FILE_MACHINE_AMD64",
            exports=[PeExport(name="test_func", ordinal=1)],
        )
        f = tmp_path / "test.dll"
        f.write_bytes(b"fake")

        with mock_patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            snap = _dump_native_binary(f, "pe", [], [], "1.0", "c")

        assert snap.platform == "pe"
        assert snap.elf_only_mode is False
        assert len(snap.functions) == 1
        assert snap.functions[0].name == "test_func"
        assert snap.pe is pe_meta

    def test_dump_native_binary_macho(self, tmp_path):
        """Exercise _dump_native_binary for Mach-O format (mocked parse)."""
        from unittest.mock import patch as mock_patch

        from abicheck.cli import _dump_native_binary
        from abicheck.macho_metadata import MachoExport, MachoMetadata

        macho_meta = MachoMetadata(exports=[MachoExport(name="macho_func")])
        f = tmp_path / "lib.dylib"
        f.write_bytes(b"fake")

        with mock_patch("abicheck.macho_metadata.parse_macho_metadata", return_value=macho_meta):
            snap = _dump_native_binary(f, "macho", [], [], "1.0", "c")

        assert snap.platform == "macho"
        assert snap.elf_only_mode is False
        assert len(snap.functions) == 1
        assert snap.functions[0].name == "macho_func"
        assert snap.macho is macho_meta

    def test_dump_native_binary_pe_empty_exports_raises(self, tmp_path):
        """PE with valid machine but no exports (named or ordinal) raises ClickException."""
        from unittest.mock import patch as mock_patch

        import click
        import pytest

        from abicheck.cli import _dump_native_binary
        from abicheck.pe_metadata import PeMetadata

        f = tmp_path / "empty.dll"
        f.write_bytes(b"fake")
        # machine set (parse succeeded) but no exports at all
        with mock_patch("abicheck.pe_metadata.parse_pe_metadata",
                        return_value=PeMetadata(machine="IMAGE_FILE_MACHINE_AMD64")):
            with pytest.raises(click.ClickException, match="no exports"):
                _dump_native_binary(f, "pe", [], [], "1.0", "c")

    def test_dump_native_binary_macho_empty_exports_raises(self, tmp_path):
        """Mach-O with no exports and no load-command metadata raises ClickException."""
        from unittest.mock import patch as mock_patch

        import click
        import pytest

        from abicheck.cli import _dump_native_binary
        from abicheck.macho_metadata import MachoMetadata

        f = tmp_path / "empty.dylib"
        f.write_bytes(b"fake")
        # Completely empty metadata — no exports, no install_name, no dependent_libs
        with mock_patch("abicheck.macho_metadata.parse_macho_metadata", return_value=MachoMetadata()):
            with pytest.raises(click.ClickException, match="no exports or load-command metadata"):
                _dump_native_binary(f, "macho", [], [], "1.0", "c")

    def test_dump_native_binary_unsupported_format(self, tmp_path):
        """Unsupported binary format raises ClickException."""
        import click
        import pytest

        from abicheck.cli import _dump_native_binary

        f = tmp_path / "test.bin"
        f.write_bytes(b"fake")
        with pytest.raises(click.ClickException, match="Unsupported binary format"):
            _dump_native_binary(f, "unknown", [], [], "1.0", "c")


# ── install_name / compat_version gained coverage ────────────────────────────

class TestDiffMachoGuards:
    """Tests for install_name and compat_version change detection."""

    def test_install_name_gained_reported(self):
        """old=empty, new=set: gaining an install name is reported."""
        from abicheck.checker import ChangeKind, _diff_macho
        from abicheck.model import AbiSnapshot
        old = AbiSnapshot(library="a.dylib", version="1.0", macho=MachoMetadata(install_name=""))
        new = AbiSnapshot(library="a.dylib", version="2.0", macho=MachoMetadata(install_name="/usr/lib/a.dylib"))
        changes = _diff_macho(old, new)
        assert any(c.kind == ChangeKind.SONAME_CHANGED for c in changes)

    def test_compat_version_gained_reported(self):
        """old=empty, new=set: gaining a compat version is reported."""
        from abicheck.checker import ChangeKind, _diff_macho
        from abicheck.model import AbiSnapshot
        old = AbiSnapshot(library="a.dylib", version="1.0", macho=MachoMetadata(compat_version=""))
        new = AbiSnapshot(library="a.dylib", version="2.0", macho=MachoMetadata(compat_version="1.0.0"))
        changes = _diff_macho(old, new)
        assert any(c.kind == ChangeKind.COMPAT_VERSION_CHANGED for c in changes)
