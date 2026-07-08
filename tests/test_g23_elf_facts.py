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


# ── A1: static-TLS drift ────────────────────────────────────────────────────

class TestStaticTls:
    def test_introduced_with_tls_symbols(self):
        old = ElfMetadata(symbols=[_tls_sym()], has_tls_symbols=True)
        new = ElfMetadata(symbols=[_tls_sym()], has_tls_symbols=True, has_static_tls=True)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.STATIC_TLS_INTRODUCED in _kinds(r)

    def test_introduced_suppressed_when_no_tls_symbols(self):
        # DF_STATIC_TLS set but the library participates in no TLS at all → not a
        # dlopen hazard, so no finding (the suppression guard).
        old = ElfMetadata(has_tls_symbols=False)
        new = ElfMetadata(has_tls_symbols=False, has_static_tls=True)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.STATIC_TLS_INTRODUCED not in _kinds(r)

    def test_introduced_fires_on_import_only_tls(self):
        # An initial-exec reference to an *external* __thread var sets
        # DF_STATIC_TLS with no local TLS definitions; has_tls_symbols is still
        # True (set from any STT_TLS entry, defined or undefined).
        old = ElfMetadata(has_tls_symbols=True)
        new = ElfMetadata(has_tls_symbols=True, has_static_tls=True)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.STATIC_TLS_INTRODUCED in _kinds(r)

    def test_removed_is_compatible(self):
        old = ElfMetadata(symbols=[_tls_sym()], has_tls_symbols=True, has_static_tls=True)
        new = ElfMetadata(symbols=[_tls_sym()], has_tls_symbols=True)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.STATIC_TLS_REMOVED in _kinds(r)

    def test_no_change_when_stable(self):
        old = ElfMetadata(has_tls_symbols=True, has_static_tls=True)
        new = ElfMetadata(has_tls_symbols=True, has_static_tls=True)
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.STATIC_TLS_INTRODUCED not in _kinds(r)
        assert ChangeKind.STATIC_TLS_REMOVED not in _kinds(r)


# ── A2: .note.gnu.property CET / branch protection ──────────────────────────

class TestGnuProperty:
    def test_cet_weakened(self):
        old = ElfMetadata(gnu_properties=frozenset({"IBT", "SHSTK"}))
        new = ElfMetadata(gnu_properties=frozenset({"SHSTK"}))
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.CET_PROTECTION_WEAKENED in _kinds(r)

    def test_cet_fully_dropped(self):
        old = ElfMetadata(gnu_properties=frozenset({"IBT", "SHSTK"}))
        new = ElfMetadata(gnu_properties=frozenset())
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.CET_PROTECTION_WEAKENED in _kinds(r)

    def test_cet_improved(self):
        old = ElfMetadata(gnu_properties=frozenset())
        new = ElfMetadata(gnu_properties=frozenset({"IBT", "SHSTK"}))
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.CET_PROTECTION_IMPROVED in _kinds(r)

    def test_branch_protection_weakened(self):
        old = ElfMetadata(gnu_properties=frozenset({"BTI", "PAC"}))
        new = ElfMetadata(gnu_properties=frozenset({"PAC"}))
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.BRANCH_PROTECTION_WEAKENED in _kinds(r)

    def test_branch_protection_improved(self):
        old = ElfMetadata(gnu_properties=frozenset({"PAC"}))
        new = ElfMetadata(gnu_properties=frozenset({"BTI", "PAC"}))
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.BRANCH_PROTECTION_IMPROVED in _kinds(r)

    def test_cet_and_branch_independent(self):
        # Dropping CET while gaining BTI reports one of each direction.
        old = ElfMetadata(gnu_properties=frozenset({"IBT"}))
        new = ElfMetadata(gnu_properties=frozenset({"BTI"}))
        r = compare(_snap(old), _snap(new))
        ks = _kinds(r)
        assert ChangeKind.CET_PROTECTION_WEAKENED in ks
        assert ChangeKind.BRANCH_PROTECTION_IMPROVED in ks

    def test_no_change_when_stable(self):
        props = frozenset({"IBT", "SHSTK", "BTI"})
        r = compare(_snap(ElfMetadata(gnu_properties=props)), _snap(ElfMetadata(gnu_properties=props)))
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
        old = ElfMetadata(machine="EM_X86_64", osabi="ELFOSABI_SYSV")
        new = ElfMetadata(machine="EM_X86_64", osabi="ELFOSABI_LINUX")
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.ELF_OSABI_CHANGED in _kinds(r)

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


# ── A4: STB_GNU_UNIQUE binding transitions ──────────────────────────────────

class TestGnuUniqueBinding:
    def test_became_unique(self):
        old = ElfMetadata(symbols=[
            ElfSymbol(name="inst", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.OBJECT)])
        new = ElfMetadata(symbols=[
            ElfSymbol(name="inst", binding=SymbolBinding.UNIQUE, sym_type=SymbolType.OBJECT)])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.SYMBOL_BINDING_BECAME_UNIQUE in _kinds(r)

    def test_lost_unique(self):
        old = ElfMetadata(symbols=[
            ElfSymbol(name="inst", binding=SymbolBinding.UNIQUE, sym_type=SymbolType.OBJECT)])
        new = ElfMetadata(symbols=[
            ElfSymbol(name="inst", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.OBJECT)])
        r = compare(_snap(old), _snap(new))
        assert ChangeKind.SYMBOL_BINDING_LOST_UNIQUE in _kinds(r)

    def test_unique_does_not_emit_generic_binding_change(self):
        old = ElfMetadata(symbols=[
            ElfSymbol(name="inst", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.OBJECT)])
        new = ElfMetadata(symbols=[
            ElfSymbol(name="inst", binding=SymbolBinding.UNIQUE, sym_type=SymbolType.OBJECT)])
        ks = _kinds(compare(_snap(old), _snap(new)))
        assert ChangeKind.SYMBOL_BINDING_CHANGED not in ks
        assert ChangeKind.SYMBOL_BINDING_STRENGTHENED not in ks


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
