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

"""G23 Phase D — ecosystem detectors: unnamed types (D3), long double (D2),
Module.symvers kABI (D1)."""
from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import AbiSnapshot
from abicheck.symvers_metadata import (
    KabiMetadata,
    looks_like_symvers,
    parse_symvers,
)


def _sym(name: str) -> ElfSymbol:
    return ElfSymbol(name=name, binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC)


def _elf_snap(*names: str) -> AbiSnapshot:
    return AbiSnapshot(
        library="l.so.1", version="1", functions=[], variables=[], types=[],
        enums=[], typedefs={},
        elf=ElfMetadata(symbols=[_sym(n) for n in names], machine="EM_X86_64"),
        elf_only_mode=True,
    )


def _kabi_snap(entries_text: str) -> AbiSnapshot:
    return AbiSnapshot(
        library="Module.symvers", version="1", functions=[], variables=[],
        types=[], enums=[], typedefs={}, kabi=parse_symvers(entries_text),
    )


def _kinds(r) -> set[ChangeKind]:
    return {c.kind for c in r.changes}


# ── D3: unnamed-type leakage ────────────────────────────────────────────────

class TestUnnamedTypeLeak:
    def test_newly_leaked_lambda_is_risk(self):
        # _ZNK4g_cbMUliE_clEi = g_cb::{lambda(int)#1}::operator()(int) const
        old = _elf_snap("_Z3fooi")
        new = _elf_snap("_Z3fooi", "_ZNK4g_cbMUliE_clEi")
        r = compare(old, new)
        assert ChangeKind.UNNAMED_TYPE_IN_PUBLIC_ABI in _kinds(r)

    def test_unnamed_struct_marker(self):
        # An unnamed struct/enum mangles as Ut<n>_.
        old = _elf_snap("_Z3fooi")
        new = _elf_snap("_Z3fooi", "_Z3barP3FooIUt_E")
        r = compare(old, new)
        assert ChangeKind.UNNAMED_TYPE_IN_PUBLIC_ABI in _kinds(r)

    def test_preexisting_leak_not_reported(self):
        # Only *newly introduced* leaks are flagged (single-snapshot anti-pattern).
        both = "_ZNK4g_cbMUliE_clEi"
        r = compare(_elf_snap(both), _elf_snap(both))
        assert ChangeKind.UNNAMED_TYPE_IN_PUBLIC_ABI not in _kinds(r)

    def test_ordinary_symbol_not_flagged(self):
        old = _elf_snap("_Z3fooi")
        new = _elf_snap("_Z3fooi", "_Z3bazv")
        assert ChangeKind.UNNAMED_TYPE_IN_PUBLIC_ABI not in _kinds(compare(old, new))


# ── D2: long-double ABI transition ──────────────────────────────────────────

class TestLongDoubleAbi:
    def test_long_double_to_float128_is_breaking(self):
        # _Z4areae = area(long double), _Z4areag = area(__float128)
        old = _elf_snap("_Z4areae")
        new = _elf_snap("_Z4areag")
        r = compare(old, new)
        assert ChangeKind.LONG_DOUBLE_ABI_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_collapses_the_remove_add_pair(self):
        old = _elf_snap("_Z4areae")
        new = _elf_snap("_Z4areag")
        ks = _kinds(compare(old, new))
        assert ChangeKind.FUNC_REMOVED not in ks
        assert ChangeKind.FUNC_REMOVED_ELF_ONLY not in ks
        assert ChangeKind.FUNC_ADDED not in ks

    def test_unrelated_remove_add_not_paired(self):
        # A genuine removal + a genuine addition that both lack long-double types
        # must not be mistaken for a long-double transition.
        old = _elf_snap("_Z3fooi")
        new = _elf_snap("_Z3bard")  # bar(double) — not long double
        assert ChangeKind.LONG_DOUBLE_ABI_CHANGED not in _kinds(compare(old, new))


# ── D1: Module.symvers kABI ─────────────────────────────────────────────────

class TestSymversParser:
    def test_five_field_with_namespace(self):
        meta = parse_symvers("0x1\tksym\tvmlinux\tEXPORT_SYMBOL_NS\tDRM\n")
        e = meta.entries["ksym"]
        assert e.crc == "0x1"
        assert e.export_type == "EXPORT_SYMBOL_NS"
        assert e.namespace == "DRM"

    def test_four_field_pre_5_4(self):
        meta = parse_symvers("0x2\tksym\tvmlinux\tEXPORT_SYMBOL\n")
        assert meta.entries["ksym"].namespace == ""

    def test_looks_like_symvers(self):
        assert looks_like_symvers("0xdeadbeef\tfoo\tvmlinux\tEXPORT_SYMBOL\t")
        assert not looks_like_symvers("this is not\ta symvers file at all\n")


class TestKabiDiff:
    _OLD = (
        "0x11\tkmalloc\tvmlinux\tEXPORT_SYMBOL\t\n"
        "0x22\tkfree\tvmlinux\tEXPORT_SYMBOL_GPL\t\n"
        "0x33\tdrv_reg\tvmlinux\tEXPORT_SYMBOL\tDRM\n"
        "0x44\told\tvmlinux\tEXPORT_SYMBOL\t\n"
    )

    def test_removed_symbol_is_breaking(self):
        new = self._OLD.replace("0x44\told\tvmlinux\tEXPORT_SYMBOL\t\n", "")
        r = compare(_kabi_snap(self._OLD), _kabi_snap(new))
        assert ChangeKind.KABI_SYMBOL_REMOVED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_crc_change_is_breaking(self):
        new = self._OLD.replace("0x11\tkmalloc", "0x1199\tkmalloc")
        r = compare(_kabi_snap(self._OLD), _kabi_snap(new))
        assert ChangeKind.KABI_CRC_CHANGED in _kinds(r)

    def test_gpl_to_nongpl_is_api_break(self):
        new = self._OLD.replace("EXPORT_SYMBOL_GPL", "EXPORT_SYMBOL")
        r = compare(_kabi_snap(self._OLD), _kabi_snap(new))
        assert ChangeKind.KABI_EXPORT_TYPE_CHANGED in _kinds(r)

    def test_namespace_gained_is_flagged(self):
        old = "0x33\tdrv_reg\tvmlinux\tEXPORT_SYMBOL\t\n"
        new = "0x33\tdrv_reg\tvmlinux\tEXPORT_SYMBOL_NS\tDRM\n"
        r = compare(_kabi_snap(old), _kabi_snap(new))
        assert ChangeKind.KABI_SYMBOL_NAMESPACE_CHANGED in _kinds(r)

    def test_added_symbol_is_compatible(self):
        new = self._OLD + "0x55\tbrand_new\tvmlinux\tEXPORT_SYMBOL\t\n"
        r = compare(_kabi_snap(self._OLD), _kabi_snap(new))
        assert ChangeKind.KABI_SYMBOL_ADDED in _kinds(r)

    def test_roundtrip_serialization(self):
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

        snap = _kabi_snap(self._OLD)
        back = snapshot_from_dict(snapshot_to_dict(snap))
        assert isinstance(back.kabi, KabiMetadata)
        assert back.kabi.entries["kfree"].export_type == "EXPORT_SYMBOL_GPL"
