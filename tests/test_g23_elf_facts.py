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

"""G23 Phase A — Linux ELF artifact-fact detectors.

Covers static-TLS drift (A1), .note.gnu.property CET/BTI hardening (A2), ELF
identity / ABI-flags guard (A3), and STB_GNU_UNIQUE binding transitions (A4).
All tests use synthetic ``ElfMetadata`` — no real binaries required.
"""
from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import AbiSnapshot


def _snap(elf: ElfMetadata) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version="1.0",
        functions=[],
        variables=[],
        types=[],
        enums=[],
        typedefs={},
        elf=elf,
        elf_only_mode=True,
    )


def _kinds(result) -> set[ChangeKind]:
    return {c.kind for c in result.changes}


def _tls_sym(name: str = "tls_var") -> ElfSymbol:
    return ElfSymbol(name=name, binding=SymbolBinding.GLOBAL, sym_type=SymbolType.TLS)


def _sym_obj(name: str) -> ElfSymbol:
    return ElfSymbol(name=name, binding=SymbolBinding.GLOBAL, sym_type=SymbolType.OBJECT)


def _uniq_obj(name: str) -> ElfSymbol:
    return ElfSymbol(name=name, binding=SymbolBinding.UNIQUE, sym_type=SymbolType.OBJECT)


def _elf(**kwargs) -> ElfMetadata:
    # The A1/A2 detectors are gated on both sides having captured ELF identity
    # (a real parse always sets machine); default it so these fixtures aren't
    # mistaken for legacy/empty snapshots.
    kwargs.setdefault("machine", "EM_X86_64")
    return ElfMetadata(**kwargs)


# ── A1: static-TLS drift ────────────────────────────────────────────────────

class TestStaticTls:
    def test_introduced_with_tls_symbols(self):
        old = _elf(symbols=[_tls_sym()], has_tls_symbols=True)
        new = _elf(symbols=[_tls_sym()], has_tls_symbols=True, has_static_tls=True)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.STATIC_TLS_INTRODUCED in _kinds(r)

    def test_introduced_suppressed_when_no_tls_symbols(self):
        # DF_STATIC_TLS set but the library participates in no TLS at all → not a
        # dlopen hazard, so no finding (the suppression guard).
        old = _elf(has_tls_symbols=False)
        new = _elf(has_tls_symbols=False, has_static_tls=True)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.STATIC_TLS_INTRODUCED not in _kinds(r)

    def test_introduced_fires_on_import_only_tls(self):
        # An initial-exec reference to an *external* __thread var sets
        # DF_STATIC_TLS with no local TLS definitions; has_tls_symbols is still
        # True (set from any STT_TLS entry, defined or undefined).
        old = _elf(has_tls_symbols=True)
        new = _elf(has_tls_symbols=True, has_static_tls=True)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.STATIC_TLS_INTRODUCED in _kinds(r)

    def test_removed_is_compatible(self):
        old = _elf(symbols=[_tls_sym()], has_tls_symbols=True, has_static_tls=True)
        new = _elf(symbols=[_tls_sym()], has_tls_symbols=True)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.STATIC_TLS_REMOVED in _kinds(r)

    def test_no_change_when_stable(self):
        old = _elf(has_tls_symbols=True, has_static_tls=True)
        new = _elf(has_tls_symbols=True, has_static_tls=True)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.STATIC_TLS_INTRODUCED not in _kinds(r)
        assert ChangeKind.STATIC_TLS_REMOVED not in _kinds(r)

    def test_legacy_baseline_without_identity_never_introduces(self):
        # A legacy baseline serialized before the G23 fields existed has
        # machine="" and rehydrates has_static_tls=False (unknown, not "absent").
        # Comparing it to a fresh DSO that already had DF_STATIC_TLS must NOT
        # emit static_tls_introduced (which the security policy could fail on).
        legacy = ElfMetadata()  # no machine, no G23 fields captured
        fresh = _elf(has_tls_symbols=True, has_static_tls=True)
        r = compare(_snap(legacy), _snap(fresh))
        assert ChangeKind.STATIC_TLS_INTRODUCED not in _kinds(r)


# ── A2: .note.gnu.property CET / branch protection ──────────────────────────

class TestGnuProperty:
    def test_cet_weakened(self):
        old = _elf(gnu_properties=frozenset({"IBT", "SHSTK"}))
        new = _elf(gnu_properties=frozenset({"SHSTK"}))
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.CET_PROTECTION_WEAKENED in _kinds(r)

    def test_cet_fully_dropped(self):
        old = _elf(gnu_properties=frozenset({"IBT", "SHSTK"}))
        new = _elf(gnu_properties=frozenset())
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.CET_PROTECTION_WEAKENED in _kinds(r)

    def test_cet_improved(self):
        old = _elf(gnu_properties=frozenset())
        new = _elf(gnu_properties=frozenset({"IBT", "SHSTK"}))
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.CET_PROTECTION_IMPROVED in _kinds(r)

    def test_branch_protection_weakened(self):
        old = _elf(gnu_properties=frozenset({"BTI", "PAC"}))
        new = _elf(gnu_properties=frozenset({"PAC"}))
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.BRANCH_PROTECTION_WEAKENED in _kinds(r)

    def test_branch_protection_improved(self):
        old = _elf(gnu_properties=frozenset({"PAC"}))
        new = _elf(gnu_properties=frozenset({"BTI", "PAC"}))
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.BRANCH_PROTECTION_IMPROVED in _kinds(r)

    def test_cet_and_branch_independent(self):
        # Dropping CET while gaining BTI reports one of each direction.
        old = _elf(gnu_properties=frozenset({"IBT"}))
        new = _elf(gnu_properties=frozenset({"BTI"}))
        r = compare(_snap(old), _snap(new))
        ks = _kinds(r)
        assert ChangeKind.CET_PROTECTION_WEAKENED in ks
        assert ChangeKind.BRANCH_PROTECTION_IMPROVED in ks

    def test_no_change_when_stable(self):
        props = frozenset({"IBT", "SHSTK", "BTI"})
        r = compare(_snap(_elf(gnu_properties=props)), _snap(_elf(gnu_properties=props)))
        ks = _kinds(r)
        assert ChangeKind.CET_PROTECTION_WEAKENED not in ks
        assert ChangeKind.BRANCH_PROTECTION_WEAKENED not in ks


# ── A3: ELF identity / ABI-flags guard ──────────────────────────────────────

class TestElfIdentity:
    def test_machine_changed_is_breaking(self):
        old = ElfMetadata(machine="EM_X86_64")
        new = ElfMetadata(machine="EM_AARCH64")
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.ELF_MACHINE_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_machine_change_subsumes_flags(self):
        # A cross-arch pair should not also emit ABI-flag drift (per-arch flags
        # are not comparable across machines).
        old = ElfMetadata(machine="EM_ARM", abi_flags=frozenset({"float-hard"}))
        new = ElfMetadata(machine="EM_AARCH64", abi_flags=frozenset({"float-soft"}))
        r = compare(_snap(old), _snap(new))
        ks = _kinds(r)
        assert ChangeKind.ELF_MACHINE_CHANGED in ks
        assert ChangeKind.ELF_ABI_FLAGS_CHANGED not in ks

    def test_class_changed_is_breaking(self):
        old = ElfMetadata(machine="EM_X86_64", elf_class=64)
        new = ElfMetadata(machine="EM_X86_64", elf_class=32)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.ELF_CLASS_CHANGED in _kinds(r)

    def test_abi_flags_float_change_is_breaking(self):
        old = ElfMetadata(machine="EM_ARM", abi_flags=frozenset({"float-hard", "eabi5"}))
        new = ElfMetadata(machine="EM_ARM", abi_flags=frozenset({"float-soft", "eabi5"}))
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.ELF_ABI_FLAGS_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_osabi_changed_is_risk(self):
        # A genuinely different OS ABI (SYSV → FreeBSD) is flagged.
        old = ElfMetadata(machine="EM_X86_64", osabi="ELFOSABI_SYSV")
        new = ElfMetadata(machine="EM_X86_64", osabi="ELFOSABI_FREEBSD")
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.ELF_OSABI_CHANGED in _kinds(r)

    def test_sysv_to_gnu_osabi_is_benign(self):
        # The GNU toolchain stamps ELFOSABI_GNU/LINUX when a GNU extension (IFUNC,
        # STB_GNU_UNIQUE, …) is used, so SYSV↔GNU rides along with compatible
        # changes and must NOT be flagged (regression: case29_ifunc_transition).
        old = ElfMetadata(machine="EM_X86_64", osabi="ELFOSABI_SYSV")
        new = ElfMetadata(machine="EM_X86_64", osabi="ELFOSABI_LINUX")
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.ELF_OSABI_CHANGED not in _kinds(r)

    def test_no_change_when_identity_stable(self):
        elf = ElfMetadata(
            machine="EM_X86_64", elf_class=64, osabi="ELFOSABI_SYSV",
            abi_flags=frozenset(),
        )
        r = compare(_snap(elf), _snap(elf))
        ks = _kinds(r)
        assert ChangeKind.ELF_MACHINE_CHANGED not in ks
        assert ChangeKind.ELF_ABI_FLAGS_CHANGED not in ks
        assert ChangeKind.ELF_OSABI_CHANGED not in ks

    def test_empty_identity_side_never_fabricates(self):
        # An in-memory snapshot with no ELF identity captured (all defaults on
        # one side) must not produce a machine/osabi finding.
        old = ElfMetadata()  # machine="" — unknown
        new = ElfMetadata(machine="EM_X86_64", osabi="ELFOSABI_SYSV")
        r = compare(_snap(old), _snap(new))
        ks = _kinds(r)
        assert ChangeKind.ELF_MACHINE_CHANGED not in ks
        assert ChangeKind.ELF_OSABI_CHANGED not in ks

    def test_missing_elf_side_does_not_fabricate_class_change(self):
        # A metadata-less side keeps the elf_class=64 default; comparing it to a
        # real 32-bit ELF must NOT emit elf_class_changed (machine is unknown on
        # the empty side, so no identity is compared at all).
        old = ElfMetadata()  # header-only / parse-failed: machine="", elf_class=64
        new = ElfMetadata(machine="EM_386", elf_class=32)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.ELF_CLASS_CHANGED not in _kinds(r)

    def test_undecoded_arch_raw_eflags_diff_is_breaking(self):
        # PPC64 encodes its ELFv1/ELFv2 ABI version in e_flags, which the
        # metadata parser does not decode into abi_flags — the raw e_flags diff
        # must still surface as elf_abi_flags_changed.
        old = ElfMetadata(machine="EM_PPC64", e_flags=1)  # ELFv1
        new = ElfMetadata(machine="EM_PPC64", e_flags=2)  # ELFv2
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.ELF_ABI_FLAGS_CHANGED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_undecoded_arch_same_eflags_no_change(self):
        old = ElfMetadata(machine="EM_PPC64", e_flags=2)
        new = ElfMetadata(machine="EM_PPC64", e_flags=2)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.ELF_ABI_FLAGS_CHANGED not in _kinds(r)

    def test_decoded_tokens_equal_but_raw_eflags_differ(self):
        # A partially-decoded arch (MIPS) can keep the same decoded ABI token
        # while an undecoded bit in e_flags flips (e.g. arch level). The raw
        # e_flags fallback must still surface the drift.
        old = ElfMetadata(machine="EM_MIPS", abi_flags=frozenset({"mips-abi-0x1000"}), e_flags=0x1000)
        new = ElfMetadata(machine="EM_MIPS", abi_flags=frozenset({"mips-abi-0x1000"}), e_flags=0x9000)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.ELF_ABI_FLAGS_CHANGED in _kinds(r)

    def test_decoded_tokens_equal_and_raw_eflags_equal_no_change(self):
        old = ElfMetadata(machine="EM_ARM", abi_flags=frozenset({"float-hard"}), e_flags=0x400)
        new = ElfMetadata(machine="EM_ARM", abi_flags=frozenset({"float-hard"}), e_flags=0x400)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.ELF_ABI_FLAGS_CHANGED not in _kinds(r)


class TestStaticTlsHiddenTls:
    def test_pt_tls_segment_counts_as_tls_participation(self):
        # A hidden/local __thread variable produces a PT_TLS segment but no
        # dynamic STT_TLS symbol; has_tls_symbols set from PT_TLS must let
        # static_tls_introduced fire (not suppressed).
        old = _elf(has_tls_symbols=True)
        new = _elf(has_tls_symbols=True, has_static_tls=True)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.STATIC_TLS_INTRODUCED in _kinds(r)


# ── A4: STB_GNU_UNIQUE binding transitions ──────────────────────────────────

class TestGnuUniqueBinding:
    def test_became_unique(self):
        old = _elf(symbols=[
            ElfSymbol(name="inst", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.OBJECT)])
        new = _elf(symbols=[
            ElfSymbol(name="inst", binding=SymbolBinding.UNIQUE, sym_type=SymbolType.OBJECT)])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.SYMBOL_BINDING_BECAME_UNIQUE in _kinds(r)

    def test_lost_unique(self):
        old = _elf(symbols=[
            ElfSymbol(name="inst", binding=SymbolBinding.UNIQUE, sym_type=SymbolType.OBJECT)])
        new = _elf(symbols=[
            ElfSymbol(name="inst", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.OBJECT)])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.SYMBOL_BINDING_LOST_UNIQUE in _kinds(r)

    def test_newly_added_unique_export_flagged_at_library_level(self):
        # When a release first gains GNU_UNIQUE exports (e.g. turns on
        # -fgnu-unique), the added unique symbol isn't a both-sides transition,
        # but the library newly becomes non-unloadable — reported once.
        old = ElfMetadata(machine="EM_X86_64", symbols=[
            _sym_obj("plain")])
        new = ElfMetadata(machine="EM_X86_64", symbols=[
            _sym_obj("plain"), _uniq_obj("inst1"), _uniq_obj("inst2")])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.SYMBOL_BINDING_BECAME_UNIQUE in _kinds(r)

    def test_added_unique_not_flagged_when_already_non_unloadable(self):
        # If the old side already had a unique export, adding more doesn't change
        # the library's unloadability → no new finding.
        old = ElfMetadata(machine="EM_X86_64", symbols=[_uniq_obj("inst0")])
        new = ElfMetadata(machine="EM_X86_64", symbols=[
            _uniq_obj("inst0"), _uniq_obj("inst1")])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.SYMBOL_BINDING_BECAME_UNIQUE not in _kinds(r)

    def test_empty_baseline_symbol_table_not_flagged(self):
        # An old side with no captured symbols (header-only / parse-failed) leaves
        # the old binding unknown, not proven-absent: a new GNU_UNIQUE export must
        # not be reported as newly introduced.
        from abicheck.diff_platform_elf_symbols import _check_gained_gnu_unique

        new_syms = {"inst": _uniq_obj("inst")}
        assert _check_gained_gnu_unique({}, new_syms) == []

    def test_unique_does_not_emit_generic_binding_change(self):
        old = _elf(symbols=[
            ElfSymbol(name="inst", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.OBJECT)])
        new = _elf(symbols=[
            ElfSymbol(name="inst", binding=SymbolBinding.UNIQUE, sym_type=SymbolType.OBJECT)])
        ks = _kinds(compare(_snap(old), _snap(new)))
        assert ChangeKind.SYMBOL_BINDING_CHANGED not in ks
        assert ChangeKind.SYMBOL_BINDING_STRENGTHENED not in ks


class TestLegacySnapshotDeserialization:
    def test_missing_elf_class_derived_from_pointer_size_32(self):
        # A legacy snapshot (no elf_class key) with 32-bit pointer_size must
        # rehydrate as 32-bit, not the hard-coded 64 default, so it does not
        # false-positive elf_class_changed against a real 32-bit binary.
        from abicheck.serialization import _elf_from_dict

        elf = _elf_from_dict({"pointer_size": 4})
        assert elf.elf_class == 32

    def test_missing_elf_class_defaults_to_64_for_64bit(self):
        from abicheck.serialization import _elf_from_dict

        elf = _elf_from_dict({"pointer_size": 8})
        assert elf.elf_class == 64

    def test_new_fields_roundtrip(self):
        from abicheck.serialization import _elf_from_dict

        elf = _elf_from_dict({
            "machine": "EM_ARM",
            "elf_class": 32,
            "osabi": "ELFOSABI_LINUX",
            "abi_flags": ["float-hard", "eabi5"],
            "has_static_tls": True,
            "has_tls_symbols": True,
            "gnu_properties": ["BTI"],
        })
        assert elf.machine == "EM_ARM"
        assert elf.elf_class == 32
        assert elf.abi_flags == frozenset({"float-hard", "eabi5"})
        assert elf.has_static_tls is True
        assert elf.gnu_properties == frozenset({"BTI"})


def test_all_a_phase_kinds_are_partitioned():
    # Sanity: every new kind resolves to exactly one verdict band.
    from abicheck.checker_policy import (
        BREAKING_KINDS,
        COMPATIBLE_KINDS,
        RISK_KINDS,
    )

    expected = {
        ChangeKind.STATIC_TLS_INTRODUCED: RISK_KINDS,
        ChangeKind.STATIC_TLS_REMOVED: COMPATIBLE_KINDS,
        ChangeKind.CET_PROTECTION_WEAKENED: RISK_KINDS,
        ChangeKind.BRANCH_PROTECTION_WEAKENED: RISK_KINDS,
        ChangeKind.CET_PROTECTION_IMPROVED: COMPATIBLE_KINDS,
        ChangeKind.BRANCH_PROTECTION_IMPROVED: COMPATIBLE_KINDS,
        ChangeKind.ELF_MACHINE_CHANGED: BREAKING_KINDS,
        ChangeKind.ELF_CLASS_CHANGED: BREAKING_KINDS,
        ChangeKind.ELF_ABI_FLAGS_CHANGED: BREAKING_KINDS,
        ChangeKind.ELF_OSABI_CHANGED: RISK_KINDS,
        ChangeKind.SYMBOL_BINDING_BECAME_UNIQUE: RISK_KINDS,
        ChangeKind.SYMBOL_BINDING_LOST_UNIQUE: RISK_KINDS,
    }
    for kind, band in expected.items():
        assert kind in band, f"{kind} not in expected verdict band"
