# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Coverage-extension Mach-O detectors: loader facts, weak flips, export trie.

Covers filetype drift, linkage-flag flips, LC_RPATH drift, deployment-floor
raises, current_version downgrades, re-export repoints, strong↔weak export
flips, arm64e slice naming, and the dyld export-trie walker. All tests use
synthetic ``MachoMetadata`` — no real binaries required.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from macholib.mach_o import LC_DYLD_INFO  # type: ignore[import-untyped]

from abicheck.checker import ChangeKind, compare
from abicheck.macho_metadata import (
    _LC_DYLD_EXPORTS_TRIE,
    MachoExport,
    MachoMetadata,
    MachoSymbolType,
    _cpu_slice_name,
    _parse,
    _parse_export_trie,
    _read_uleb128,
    _walk_export_trie,
)
from abicheck.model import AbiSnapshot


def _snap(macho: MachoMetadata) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.dylib",
        version="1.0",
        functions=[],
        variables=[],
        types=[],
        enums=[],
        typedefs={},
        macho=macho,
        elf_only_mode=True,
    )


def _kinds(result) -> set[ChangeKind]:
    return {c.kind for c in result.changes}


def _macho(**kwargs) -> MachoMetadata:
    # Loader-fact detectors are gated on both sides carrying Mach-O identity.
    kwargs.setdefault("cpu_type", "ARM64")
    return MachoMetadata(**kwargs)


# ── Filetype ─────────────────────────────────────────────────────────────────

class TestFiletype:
    def test_dylib_to_bundle_is_breaking(self):
        r = compare(_snap(_macho(filetype="MH_DYLIB")), _snap(_macho(filetype="MH_BUNDLE")))
        assert ChangeKind.MACHO_FILETYPE_CHANGED in _kinds(r)

    def test_uncaptured_side_skipped(self):
        r = compare(_snap(_macho(filetype="")), _snap(_macho(filetype="MH_BUNDLE")))
        assert ChangeKind.MACHO_FILETYPE_CHANGED not in _kinds(r)


# ── Linkage flags ────────────────────────────────────────────────────────────

class TestLinkageFlags:
    def test_twolevel_dropped(self):
        old = _macho(flags=0x80)   # MH_TWOLEVEL
        new = _macho(flags=0x0)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.MACHO_LINKAGE_FLAGS_CHANGED in _kinds(r)
        change = next(c for c in r.changes if c.kind == ChangeKind.MACHO_LINKAGE_FLAGS_CHANGED)
        assert "-MH_TWOLEVEL" in change.description

    def test_unrelated_flag_bits_ignored(self):
        # e.g. MH_DYLDLINK (0x4) churn is not a linkage-semantics change.
        r = compare(_snap(_macho(flags=0x4)), _snap(_macho(flags=0x0)))
        assert ChangeKind.MACHO_LINKAGE_FLAGS_CHANGED not in _kinds(r)

    def test_identity_gate(self):
        old = MachoMetadata(cpu_type="", flags=0x80)
        new = _macho(flags=0x0)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.MACHO_LINKAGE_FLAGS_CHANGED not in _kinds(r)


# ── LC_RPATH ─────────────────────────────────────────────────────────────────

class TestMachoRpath:
    def test_changed(self):
        old = _macho(rpaths=["@loader_path/../lib"])
        new = _macho(rpaths=["@loader_path/../lib64"])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.RPATH_CHANGED in _kinds(r)

    def test_absent_both_sides_no_finding(self):
        r = compare(_snap(_macho()), _snap(_macho()))
        assert ChangeKind.RPATH_CHANGED not in _kinds(r)

    def test_uncaptured_legacy_side_skipped(self):
        # rpaths=None (legacy snapshot, LC_RPATH never captured) is unknown,
        # not "verified no rpaths" — no fabricated finding.
        old = _macho()  # rpaths defaults to None
        new = _macho(rpaths=["@loader_path/../lib"])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.RPATH_CHANGED not in _kinds(r)

    def test_captured_empty_side_is_evidence(self):
        # A parsed Mach-O with zero LC_RPATH commands ([]) is real evidence;
        # gaining a first rpath reports.
        old = _macho(rpaths=[])
        new = _macho(rpaths=["@loader_path/../lib"])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.RPATH_CHANGED in _kinds(r)


# ── Deployment floor / versions ──────────────────────────────────────────────

class TestMachoVersions:
    def test_min_os_raised(self):
        old = _macho(min_os_version="11.0.0")
        new = _macho(min_os_version="13.0.0")
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.OS_DEPLOYMENT_FLOOR_RAISED in _kinds(r)

    def test_min_os_lowered_is_fine(self):
        old = _macho(min_os_version="13.0.0")
        new = _macho(min_os_version="11.0.0")
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.OS_DEPLOYMENT_FLOOR_RAISED not in _kinds(r)

    def test_current_version_downgraded(self):
        old = _macho(current_version="2.5.0")
        new = _macho(current_version="2.4.0")
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.LIBRARY_VERSION_DOWNGRADED in _kinds(r)

    def test_current_version_upgrade_is_fine(self):
        old = _macho(current_version="2.5.0")
        new = _macho(current_version="2.6.0")
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.LIBRARY_VERSION_DOWNGRADED not in _kinds(r)


# ── Re-export repoint ────────────────────────────────────────────────────────

class TestReexportRepoint:
    def test_single_repoint(self):
        old = _macho(reexported_libs=["/usr/lib/libold.dylib"])
        new = _macho(reexported_libs=["/usr/lib/libnew.dylib"])
        r = compare(_snap(old), _snap(new))
        kinds = _kinds(r)
        assert ChangeKind.MACHO_REEXPORT_CHANGED in kinds
        assert ChangeKind.NEEDED_REMOVED not in kinds
        assert ChangeKind.NEEDED_ADDED not in kinds

    def test_pure_removal_stays_needed_removed(self):
        old = _macho(reexported_libs=["/usr/lib/libold.dylib"])
        new = _macho(reexported_libs=[])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.NEEDED_REMOVED in _kinds(r)
        assert ChangeKind.MACHO_REEXPORT_CHANGED not in _kinds(r)

    def test_pure_addition_stays_needed_added(self):
        old = _macho(reexported_libs=[])
        new = _macho(reexported_libs=["/usr/lib/libextra.dylib"])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.NEEDED_ADDED in _kinds(r)
        assert ChangeKind.MACHO_REEXPORT_CHANGED not in _kinds(r)


# ── Weak↔strong export flips ─────────────────────────────────────────────────

def _export(name: str, weak: bool = False) -> MachoExport:
    return MachoExport(
        name=name,
        sym_type=MachoSymbolType.WEAK if weak else MachoSymbolType.EXPORTED,
        is_weak=weak,
    )


class TestWeakExportFlips:
    def test_became_weak(self):
        old = _macho(exports=[_export("frob")])
        new = _macho(exports=[_export("frob", weak=True)])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.SYMBOL_BINDING_CHANGED in _kinds(r)

    def test_became_strong(self):
        old = _macho(exports=[_export("frob", weak=True)])
        new = _macho(exports=[_export("frob")])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.SYMBOL_BINDING_STRENGTHENED in _kinds(r)

    def test_stable_no_finding(self):
        old = _macho(exports=[_export("frob", weak=True)])
        new = _macho(exports=[_export("frob", weak=True)])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.SYMBOL_BINDING_CHANGED not in _kinds(r)
        assert ChangeKind.SYMBOL_BINDING_STRENGTHENED not in _kinds(r)


# ── arm64e slice naming ──────────────────────────────────────────────────────

class TestArm64e:
    def test_slice_names(self):
        assert _cpu_slice_name(0x0100000C, 0) == "ARM64"
        assert _cpu_slice_name(0x0100000C, 2) == "ARM64E"
        # High capability bits are masked before the subtype compare.
        assert _cpu_slice_name(0x0100000C, 0x80000002) == "ARM64E"

    def test_arm64_to_arm64e_reports_cpu_type_changed(self):
        old = _macho(cpu_type="ARM64", cpu_types=["ARM64"])
        new = _macho(cpu_type="ARM64E", cpu_types=["ARM64E"])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.MACHO_CPU_TYPE_CHANGED in _kinds(r)


# ── dyld export trie ─────────────────────────────────────────────────────────

class TestExportTrie:
    def test_walk_single_weak_symbol(self):
        # Root: terminal_size=0, one child edge "_foo" → node at offset 8.
        # Child: terminal_size=2 (flags=WEAK_DEFINITION, address=0), no children.
        blob = b"\x00\x01_foo\x00\x08" + b"\x02\x04\x00" + b"\x00"
        assert _walk_export_trie(blob) == [("_foo", 0x04)]

    def test_walk_shared_prefix(self):
        # Root → edge "_f" → intermediate node with two children "oo"/"ar".
        blob = (
            b"\x00\x01_f\x00\x06"            # root (0-5): child "_f" at offset 6
            b"\x00\x02oo\x00\x10ar\x00\x14"  # node (6-15): children "oo"@16, "ar"@20
            b"\x02\x00\x00\x00"              # "_foo" (16-19): terminal, flags=0
            b"\x02\x08\x00\x00"              # "_far" (20-23): terminal, flags=REEXPORT
        )
        assert sorted(_walk_export_trie(blob)) == [("_far", 0x08), ("_foo", 0x00)]

    def test_malformed_trie_raises(self):
        with pytest.raises(ValueError):
            _walk_export_trie(b"\x80")  # truncated ULEB128

    def test_overlong_uleb_raises(self):
        # 10 continuation bytes push the shift past 63 bits.
        with pytest.raises(ValueError):
            _read_uleb128(b"\xff" * 10, 0)

    def test_uleb_multibyte(self):
        assert _read_uleb128(b"\x85\x02", 0) == (0x105, 2)

    def test_walk_cycle_is_bounded(self):
        # Root's only child points back at the root — the visited-set guard
        # must terminate the walk instead of recursing forever.
        blob = b"\x00\x01a\x00\x00"
        assert _walk_export_trie(blob) == []

    def test_walk_truncated_after_terminal(self):
        # Node ends right after the (empty) terminal payload — no child count.
        assert _walk_export_trie(b"\x00") == []

    def test_walk_edge_missing_nul(self):
        # Child edge string runs off the end of the blob without a NUL.
        assert _walk_export_trie(b"\x00\x01abc") == []


#: File offset the trie is written at in the fixtures (export_off=0 means
#: "no trie" to the parser, so it must be nonzero).
_TRIE_OFF = 8


def _trie_header(trie: bytes, *, modern: bool = False) -> SimpleNamespace:
    """Fake macholib header exposing an export trie via LC_DYLD_INFO or
    LC_DYLD_EXPORTS_TRIE, mirroring the (lc, cmd, data) command tuples."""
    if modern:
        lc, cmd = (
            SimpleNamespace(cmd=_LC_DYLD_EXPORTS_TRIE),
            SimpleNamespace(dataoff=_TRIE_OFF, datasize=len(trie)),
        )
    else:
        lc, cmd = (
            SimpleNamespace(cmd=LC_DYLD_INFO),
            SimpleNamespace(export_off=_TRIE_OFF, export_size=len(trie)),
        )
    return SimpleNamespace(commands=[(lc, cmd, b"")], offset=0)


#: Trie exporting _plain (flags 0), _weakling (weak-def), _fwd (re-export),
#: plus a bare "_" that must be dropped (empty name after underscore strip).
#: Root (bytes 0-29): no terminal, 4 children.
_TRIE = (
    b"\x00\x04"
    b"_plain\x00\x1e"      # → node at 30
    b"_weakling\x00\x22"   # → node at 34
    b"_fwd\x00\x26"        # → node at 38
    b"_\x00\x2a"           # → node at 42
    b"\x02\x00\x00\x00"    # _plain: terminal, flags=0
    b"\x02\x04\x00\x00"    # _weakling: terminal, flags=WEAK_DEFINITION
    b"\x02\x08\x00\x00"    # _fwd: terminal, flags=REEXPORT
    b"\x02\x00\x00\x00"    # _: terminal — name empties after strip
)


class TestParseExportTrie:
    def _run(self, tmp_path, meta: MachoMetadata, *, modern: bool = False) -> MachoMetadata:
        f = tmp_path / "lib.dylib"
        f.write_bytes(b"\x00" * _TRIE_OFF + _TRIE)
        _parse_export_trie(f, _trie_header(_TRIE, modern=modern), meta)
        return meta

    def test_trie_only_exports_merged(self, tmp_path):
        meta = self._run(tmp_path, MachoMetadata())
        by_name = {e.name: e for e in meta.exports}
        assert set(by_name) == {"plain", "weakling", "fwd"}
        assert by_name["plain"].sym_type == MachoSymbolType.EXPORTED
        assert by_name["weakling"].sym_type == MachoSymbolType.WEAK
        assert by_name["weakling"].is_weak is True
        assert by_name["fwd"].sym_type == MachoSymbolType.REEXPORT

    def test_existing_exports_upgraded_not_duplicated(self, tmp_path):
        meta = MachoMetadata(exports=[
            MachoExport(name="weakling"),
            MachoExport(name="fwd"),
        ])
        self._run(tmp_path, meta)
        by_name = {e.name: e for e in meta.exports}
        assert len(meta.exports) == 3  # only "plain" added
        assert by_name["weakling"].is_weak is True
        assert by_name["weakling"].sym_type == MachoSymbolType.WEAK
        assert by_name["fwd"].sym_type == MachoSymbolType.REEXPORT

    def test_modern_exports_trie_command(self, tmp_path):
        meta = self._run(tmp_path, MachoMetadata(), modern=True)
        assert {e.name for e in meta.exports} == {"plain", "weakling", "fwd"}

    def test_no_trie_command_is_noop(self, tmp_path):
        meta = MachoMetadata()
        header = SimpleNamespace(commands=[], offset=0)
        _parse_export_trie(tmp_path / "missing.dylib", header, meta)
        assert meta.exports == []

    def test_unreadable_file_is_noop(self, tmp_path):
        meta = MachoMetadata()
        _parse_export_trie(tmp_path / "missing.dylib", _trie_header(_TRIE), meta)
        assert meta.exports == []

    def test_malformed_trie_is_noop(self, tmp_path):
        bad = b"\x80"  # truncated ULEB128
        f = tmp_path / "lib.dylib"
        f.write_bytes(b"\x00" * _TRIE_OFF + bad)
        meta = MachoMetadata()
        _parse_export_trie(f, _trie_header(bad), meta)
        assert meta.exports == []


# ── LC_RPATH collection in _parse ────────────────────────────────────────────

class TestParseRpaths:
    def test_rpaths_collected(self, tmp_path, monkeypatch):
        from abicheck import macho_metadata as mm

        hdr = SimpleNamespace(cputype=0x0100000C, cpusubtype=0, filetype=6, flags=0)
        commands = [
            (SimpleNamespace(cmd=mm.LC_RPATH), SimpleNamespace(), b"@loader_path/../lib\x00"),
            (SimpleNamespace(cmd=mm.LC_RPATH), SimpleNamespace(), b"\x00"),  # empty → dropped
            # A segment with one section exercises the ordinal → segment map.
            (
                SimpleNamespace(cmd=mm.LC_SEGMENT_64),
                SimpleNamespace(segname=b"__DATA\x00"),
                [SimpleNamespace()],
            ),
        ]
        header = SimpleNamespace(header=hdr, commands=commands, offset=0)
        monkeypatch.setattr(
            mm, "MachO", lambda path: SimpleNamespace(headers=[header])
        )
        meta = _parse(tmp_path / "lib.dylib")
        assert meta.rpaths == ["@loader_path/../lib"]
