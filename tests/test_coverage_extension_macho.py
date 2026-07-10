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

from abicheck.checker import ChangeKind, compare
from abicheck.macho_metadata import (
    MachoExport,
    MachoMetadata,
    MachoSymbolType,
    _cpu_slice_name,
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
        import pytest

        with pytest.raises(ValueError):
            _walk_export_trie(b"\x80")  # truncated ULEB128
