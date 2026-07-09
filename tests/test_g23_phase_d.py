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


def _ld_dwarf_snap(*names: str, ld_size: int) -> AbiSnapshot:
    """ELF snapshot carrying a DWARF `long double` base type of *ld_size* bytes."""
    from abicheck.dwarf_metadata import DwarfMetadata

    return AbiSnapshot(
        library="l.so.1", version="1", functions=[], variables=[], types=[],
        enums=[], typedefs={},
        elf=ElfMetadata(symbols=[_sym(n) for n in names], machine="EM_X86_64"),
        dwarf=DwarfMetadata(has_dwarf=True, base_types={"long double": ld_size}),
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

    def test_ul_substring_without_lambda_not_flagged(self):
        # A length-prefixed source name starting "Ul" (here "6Ulci003v") is not a
        # closure type: the scanner skips the whole identifier, so its "Ul" is
        # never seen at a structural position.
        from abicheck.diff_unnamed_types import _unnamed_kind

        assert _unnamed_kind("_Z6Ulci003v") is None

    def test_tokens_inside_source_name_not_flagged(self):
        # `aUt_()` mangles as `_Z4aUt_v`; `Ut_` sits inside the 4-char identifier,
        # not at an Itanium type-production boundary, so it must not be flagged.
        from abicheck.diff_unnamed_types import _unnamed_kind

        assert _unnamed_kind("_Z4aUt_v") is None
        assert _unnamed_kind("_Z6UlciE_v") is None

    def test_ordinary_ut_name_export_not_flagged_end_to_end(self):
        old = _elf_snap("_Z3fooi")
        new = _elf_snap("_Z3fooi", "_Z4aUt_v")  # exported aUt_() — not a leak
        assert ChangeKind.UNNAMED_TYPE_IN_PUBLIC_ABI not in _kinds(compare(old, new))

    def test_lambda_detection_is_demangler_independent(self):
        # Regression: lambda closures must be caught from the mangled `Ul…E_`
        # token, not the platform demangler's `{lambda` spelling (macOS libc++abi
        # differs from libstdc++), so detection is stable across platforms.
        from abicheck.diff_unnamed_types import _unnamed_kind

        assert _unnamed_kind("_ZNK4g_cbMUliE_clEi") == "lambda closure"
        assert _unnamed_kind("_ZN3FooMUliE0_clEi") == "lambda closure"  # numbered

    def test_exported_names_empty_without_elf(self):
        from abicheck.diff_unnamed_types import _exported_symbol_names

        assert _exported_symbol_names(AbiSnapshot(library="x", version="1")) == set()

    def test_captured_empty_baseline_flags_new_lambda(self):
        # Old side captured ELF and genuinely exported nothing: a lambda in the
        # new binary IS newly introduced against that proven-empty surface.
        old = _elf_snap()  # captured, no exported symbols
        new = _elf_snap("_ZNK4g_cbMUliE_clEi")
        assert ChangeKind.UNNAMED_TYPE_IN_PUBLIC_ABI in _kinds(compare(old, new))

    def test_header_only_old_not_flagged(self):
        # Old side never captured ELF (elf=None): requires_support disables the
        # detector, so a pre-existing leak is not mistaken for a new one.
        old = AbiSnapshot(library="l.so.1", version="1")  # no elf
        new = _elf_snap("_ZNK4g_cbMUliE_clEi")
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

    def test_same_mangling_width_change_from_dwarf(self):
        # -mlong-double-64 keeps _Z4aread... wait, long double mangles to `e`.
        # The symbol name is identical on both sides; only the DWARF `long
        # double` base-type size reveals the 80-bit → 64-bit shrink.
        old = _ld_dwarf_snap("_Z4areae", ld_size=16)
        new = _ld_dwarf_snap("_Z4areae", ld_size=8)
        r = compare(old, new)
        assert ChangeKind.LONG_DOUBLE_ABI_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_same_mangling_no_size_change_not_flagged(self):
        old = _ld_dwarf_snap("_Z4areae", ld_size=16)
        new = _ld_dwarf_snap("_Z4areae", ld_size=16)
        assert ChangeKind.LONG_DOUBLE_ABI_CHANGED not in _kinds(compare(old, new))

    def test_same_mangling_non_ld_symbol_not_flagged(self):
        # Size differs but the persisting symbol has no long-double parameter.
        old = _ld_dwarf_snap("_Z3fooi", ld_size=16)
        new = _ld_dwarf_snap("_Z3fooi", ld_size=8)
        assert ChangeKind.LONG_DOUBLE_ABI_CHANGED not in _kinds(compare(old, new))

    def test_non_ld_removal_alongside_transition(self):
        # A plain (non-LD) removal coexists with a real LD transition: the
        # non-LD symbol is skipped, the LD pair is still collapsed to one finding.
        old = _elf_snap("_Z3fooi", "_Z4areae")   # foo(int) + area(long double)
        new = _elf_snap("_Z4areag")              # area(__float128)
        ks = _kinds(compare(old, new))
        assert ChangeKind.LONG_DOUBLE_ABI_CHANGED in ks

    def test_ld_removal_without_matching_signature(self):
        # Two LD symbols with different function names do not pair.
        old = _elf_snap("_Z4areae")   # area(long double)
        new = _elf_snap("_Z3fooe")    # foo(long double) — different function
        assert ChangeKind.LONG_DOUBLE_ABI_CHANGED not in _kinds(compare(old, new))

    def test_return_only_ld_width_change_flagged(self):
        # `long double f()` mangles as `_Z1fv`: the return type is absent from the
        # symbol name, so the demangled string ("f()") never mentions long double.
        # The recorded return type must still surface the return-only width break.
        from abicheck.dwarf_metadata import DwarfMetadata
        from abicheck.model import Function

        def _snap(ld_size: int) -> AbiSnapshot:
            return AbiSnapshot(
                library="l.so.1", version="1",
                functions=[Function(name="f()", mangled="_Z1fv", return_type="long double")],
                variables=[], types=[], enums=[], typedefs={},
                elf=ElfMetadata(symbols=[_sym("_Z1fv")], machine="EM_X86_64"),
                dwarf=DwarfMetadata(has_dwarf=True, base_types={"long double": ld_size}),
            )

        r = compare(_snap(16), _snap(8))
        assert ChangeKind.LONG_DOUBLE_ABI_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_return_only_non_ld_not_flagged(self):
        # A non-long-double return type must not be swept up by the width change.
        from abicheck.dwarf_metadata import DwarfMetadata
        from abicheck.model import Function

        def _snap(ld_size: int) -> AbiSnapshot:
            return AbiSnapshot(
                library="l.so.1", version="1",
                functions=[Function(name="f()", mangled="_Z1fv", return_type="double")],
                variables=[], types=[], enums=[], typedefs={},
                elf=ElfMetadata(symbols=[_sym("_Z1fv")], machine="EM_X86_64"),
                dwarf=DwarfMetadata(has_dwarf=True, base_types={"long double": ld_size}),
            )

        assert ChangeKind.LONG_DOUBLE_ABI_CHANGED not in _kinds(compare(_snap(16), _snap(8)))

    def test_exported_empty_without_elf(self):
        from abicheck.diff_long_double import _exported

        assert _exported(AbiSnapshot(library="x", version="1")) == set()


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

    def test_looks_like_symvers_skips_comments_and_short_lines(self):
        # A leading comment and a too-short line are skipped, then a real record hits.
        text = "# generated\nshortline\n0x1\tsym\tvmlinux\tEXPORT_SYMBOL_GPL\t\n"
        assert looks_like_symvers(text)

    def test_looks_like_symvers_rejects_tabbed_non_record(self):
        # A 4-field tabbed line that is not a symvers record → not symvers.
        assert not looks_like_symvers("a\tb\tc\td\n")

    def test_malformed_and_blank_lines_skipped(self):
        text = (
            "\n"                                      # blank → skipped
            "0x1\tsym\tvmlinux\n"                     # <4 fields → skipped
            "0x2\t\tvmlinux\tEXPORT_SYMBOL\t\n"       # empty symbol → skipped
            "0x3\tgood\tvmlinux\tEXPORT_SYMBOL\t\n"   # valid
        )
        meta = parse_symvers(text)
        assert set(meta.entries) == {"good"}

    def test_parse_symvers_file(self, tmp_path):
        from abicheck.symvers_metadata import parse_symvers_file

        p = tmp_path / "Module.symvers"
        p.write_text("0x9\tksym\tvmlinux\tEXPORT_SYMBOL_GPL\tCORE\n")
        meta = parse_symvers_file(p)
        assert meta.entries["ksym"].namespace == "CORE"

    def test_parse_symvers_file_missing_is_empty(self, tmp_path):
        from abicheck.symvers_metadata import parse_symvers_file

        assert parse_symvers_file(tmp_path / "nope").entries == {}

    def test_parse_symvers_file_directory_is_empty(self, tmp_path):
        from abicheck.symvers_metadata import parse_symvers_file

        # A non-regular file (directory) yields empty metadata, not a crash.
        assert parse_symvers_file(tmp_path).entries == {}


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

    def test_nongpl_to_gpl_is_api_break(self):
        # Restricting the license class (EXPORT_SYMBOL → EXPORT_SYMBOL_GPL) locks
        # out proprietary modules — an availability break for that consumer class.
        new = self._OLD.replace(
            "0x11\tkmalloc\tvmlinux\tEXPORT_SYMBOL\t",
            "0x11\tkmalloc\tvmlinux\tEXPORT_SYMBOL_GPL\t",
        )
        r = compare(_kabi_snap(self._OLD), _kabi_snap(new))
        assert ChangeKind.KABI_EXPORT_TYPE_CHANGED in _kinds(r)

    def test_gpl_to_nongpl_is_relaxation(self):
        # EXPORT_SYMBOL_GPL → EXPORT_SYMBOL widens availability; it is not a
        # break and must not be flagged as an export-type change.
        new = self._OLD.replace("EXPORT_SYMBOL_GPL", "EXPORT_SYMBOL")
        r = compare(_kabi_snap(self._OLD), _kabi_snap(new))
        assert ChangeKind.KABI_EXPORT_TYPE_CHANGED not in _kinds(r)

    def test_namespace_gained_is_flagged(self):
        old = "0x33\tdrv_reg\tvmlinux\tEXPORT_SYMBOL\t\n"
        new = "0x33\tdrv_reg\tvmlinux\tEXPORT_SYMBOL_NS\tDRM\n"
        r = compare(_kabi_snap(old), _kabi_snap(new))
        assert ChangeKind.KABI_SYMBOL_NAMESPACE_CHANGED in _kinds(r)
        # A namespace gain keeps the GPL class (EXPORT_SYMBOL → EXPORT_SYMBOL_NS),
        # so it must not double-report as an export-type change.
        assert ChangeKind.KABI_EXPORT_TYPE_CHANGED not in _kinds(r)

    def test_ns_gpl_to_ns_nongpl_not_flagged(self):
        # Dropping _GPL while keeping the namespace is still a relaxation.
        old = "0x33\tdrv_reg\tvmlinux\tEXPORT_SYMBOL_NS_GPL\tDRM\n"
        new = "0x33\tdrv_reg\tvmlinux\tEXPORT_SYMBOL_NS\tDRM\n"
        r = compare(_kabi_snap(old), _kabi_snap(new))
        assert ChangeKind.KABI_EXPORT_TYPE_CHANGED not in _kinds(r)

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
