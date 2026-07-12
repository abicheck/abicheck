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

"""Composition-compatibility detectors (Wave A gap-analysis follow-up).

Covers checks that only fire when independently-valid artifacts are combined
at runtime rather than a single library's own declaration diff:

* runtime symbol-binding rebound (``stack_binding_diff.py``)
* ordered DT_NEEDED / DT_SYMBOLIC / DF_TEXTREL loader contract
  (``diff_platform_elf_dynamic.py``)
* consumer-aware PE ordinal retargeting and eager/delay import transitions
  (``appcompat.py`` / ``diff_platform.py``)
* the -fshort-wchar data-model flag (``dwarf_advanced.py``)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from abicheck.appcompat import AppRequirements, _check_pe_ordinal_imports
from abicheck.binder import BindingStatus, SymbolBinding
from abicheck.checker import ChangeKind, compare
from abicheck.dwarf_advanced import AdvancedDwarfMetadata, ToolchainInfo
from abicheck.elf_metadata import ElfMetadata
from abicheck.model import AbiSnapshot
from abicheck.pe_metadata import PeExport, PeMetadata
from abicheck.resolver import DependencyGraph, ResolvedDSO
from abicheck.stack_binding_diff import diff_runtime_bindings
from abicheck.stack_checker import StackVerdict, _compute_abi_risk


def _elf_snap(elf: ElfMetadata) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1", version="1.0",
        functions=[], variables=[], types=[], enums=[], typedefs={},
        elf=elf, elf_only_mode=True,
    )


def _elf(**kwargs) -> ElfMetadata:
    kwargs.setdefault("machine", "EM_X86_64")
    return ElfMetadata(**kwargs)


def _pe_snap(pe: PeMetadata) -> AbiSnapshot:
    return AbiSnapshot(
        library="foo.dll", version="1.0",
        functions=[], variables=[], types=[], enums=[], typedefs={},
        pe=pe,
    )


def _kinds(result) -> set[ChangeKind]:
    return {c.kind for c in result.changes}


# ── needed_order_changed ─────────────────────────────────────────────────────

class TestNeededOrderChanged:
    def test_reorder_with_same_set_flags(self):
        old = _elf(needed=["liba.so", "libb.so"])
        new = _elf(needed=["libb.so", "liba.so"])
        r = compare(_elf_snap(old), _elf_snap(new))
        assert ChangeKind.NEEDED_ORDER_CHANGED in _kinds(r)

    def test_unchanged_order_not_flagged(self):
        old = _elf(needed=["liba.so", "libb.so"])
        new = _elf(needed=["liba.so", "libb.so"])
        r = compare(_elf_snap(old), _elf_snap(new))
        assert ChangeKind.NEEDED_ORDER_CHANGED not in _kinds(r)

    def test_pure_add_not_flagged_as_reorder(self):
        old = _elf(needed=["liba.so"])
        new = _elf(needed=["liba.so", "libb.so"])
        r = compare(_elf_snap(old), _elf_snap(new))
        kinds = _kinds(r)
        assert ChangeKind.NEEDED_ORDER_CHANGED not in kinds
        assert ChangeKind.NEEDED_ADDED in kinds


# ── symbolic_binding_mode_changed / text_relocation_* ────────────────────────

class TestSymbolicAndTextrel:
    def test_symbolic_introduced(self):
        old = _elf(is_symbolic=False)
        new = _elf(is_symbolic=True)
        r = compare(_elf_snap(old), _elf_snap(new))
        assert ChangeKind.SYMBOLIC_BINDING_MODE_CHANGED in _kinds(r)

    def test_symbolic_unchanged_not_flagged(self):
        old = _elf(is_symbolic=True)
        new = _elf(is_symbolic=True)
        r = compare(_elf_snap(old), _elf_snap(new))
        assert ChangeKind.SYMBOLIC_BINDING_MODE_CHANGED not in _kinds(r)

    def test_textrel_introduced_is_breaking(self):
        from abicheck.checker_policy import BREAKING_KINDS

        old = _elf(has_textrel=False)
        new = _elf(has_textrel=True)
        r = compare(_elf_snap(old), _elf_snap(new))
        assert ChangeKind.TEXT_RELOCATION_INTRODUCED in _kinds(r)
        assert ChangeKind.TEXT_RELOCATION_INTRODUCED in BREAKING_KINDS

    def test_textrel_removed_is_improvement(self):
        old = _elf(has_textrel=True)
        new = _elf(has_textrel=False)
        r = compare(_elf_snap(old), _elf_snap(new))
        assert ChangeKind.TEXT_RELOCATION_REMOVED in _kinds(r)

    def test_legacy_snapshot_not_flagged(self):
        # machine="" on one side means "not captured" — must never fabricate
        # a finding just because the boolean defaults differ from the other side.
        old = ElfMetadata(machine="", has_textrel=False, is_symbolic=False)
        new = _elf(has_textrel=True, is_symbolic=True)
        r = compare(_elf_snap(old), _elf_snap(new))
        kinds = _kinds(r)
        assert ChangeKind.TEXT_RELOCATION_INTRODUCED not in kinds
        assert ChangeKind.SYMBOLIC_BINDING_MODE_CHANGED not in kinds


# ── wchar_model_changed ──────────────────────────────────────────────────────

class TestWcharModelChanged:
    def _dwarf_snap(self, meta: AdvancedDwarfMetadata) -> AbiSnapshot:
        return AbiSnapshot(
            library="libtest.so.1", version="1.0",
            functions=[], variables=[], types=[], enums=[], typedefs={},
            dwarf_advanced=meta,
        )

    def test_short_wchar_introduced(self):
        old = AdvancedDwarfMetadata(has_dwarf=True, toolchain=ToolchainInfo(producer_string="GNU C++"))
        new = AdvancedDwarfMetadata(
            has_dwarf=True,
            toolchain=ToolchainInfo(producer_string="GNU C++", wchar_flags={"-fshort-wchar"}),
        )
        r = compare(self._dwarf_snap(old), self._dwarf_snap(new))
        assert ChangeKind.WCHAR_MODEL_CHANGED in _kinds(r)

    def test_unchanged_not_flagged(self):
        old = AdvancedDwarfMetadata(has_dwarf=True, toolchain=ToolchainInfo(wchar_flags={"-fshort-wchar"}))
        new = AdvancedDwarfMetadata(has_dwarf=True, toolchain=ToolchainInfo(wchar_flags={"-fshort-wchar"}))
        r = compare(self._dwarf_snap(old), self._dwarf_snap(new))
        assert ChangeKind.WCHAR_MODEL_CHANGED not in _kinds(r)

    def test_no_dwarf_skips_detector(self):
        old = AdvancedDwarfMetadata(has_dwarf=False)
        new = AdvancedDwarfMetadata(has_dwarf=True, toolchain=ToolchainInfo(wchar_flags={"-fshort-wchar"}))
        r = compare(self._dwarf_snap(old), self._dwarf_snap(new))
        assert ChangeKind.WCHAR_MODEL_CHANGED not in _kinds(r)


# ── pe_import_load_mode_changed ──────────────────────────────────────────────

class TestPeImportLoadModeChanged:
    def test_eager_to_delay(self):
        old = PeMetadata(
            machine="IMAGE_FILE_MACHINE_AMD64",
            imports={"KERNELBASE.dll": ["Foo"]}, delay_imports={},
        )
        new = PeMetadata(
            machine="IMAGE_FILE_MACHINE_AMD64",
            imports={}, delay_imports={"KERNELBASE.dll": ["Foo"]},
        )
        r = compare(_pe_snap(old), _pe_snap(new))
        assert ChangeKind.PE_IMPORT_LOAD_MODE_CHANGED in _kinds(r)

    def test_delay_to_eager(self):
        old = PeMetadata(
            machine="IMAGE_FILE_MACHINE_AMD64",
            imports={}, delay_imports={"KERNELBASE.dll": ["Foo"]},
        )
        new = PeMetadata(
            machine="IMAGE_FILE_MACHINE_AMD64",
            imports={"KERNELBASE.dll": ["Foo"]}, delay_imports={},
        )
        r = compare(_pe_snap(old), _pe_snap(new))
        assert ChangeKind.PE_IMPORT_LOAD_MODE_CHANGED in _kinds(r)

    def test_unchanged_import_mode_not_flagged(self):
        old = PeMetadata(
            machine="IMAGE_FILE_MACHINE_AMD64",
            imports={"KERNELBASE.dll": ["Foo"]}, delay_imports={},
        )
        new = PeMetadata(
            machine="IMAGE_FILE_MACHINE_AMD64",
            imports={"KERNELBASE.dll": ["Foo"]}, delay_imports={},
        )
        r = compare(_pe_snap(old), _pe_snap(new))
        assert ChangeKind.PE_IMPORT_LOAD_MODE_CHANGED not in _kinds(r)

    def test_legacy_snapshot_delay_imports_none_skipped(self):
        old = PeMetadata(machine="IMAGE_FILE_MACHINE_AMD64", imports={"KERNELBASE.dll": ["Foo"]})
        new = PeMetadata(machine="IMAGE_FILE_MACHINE_AMD64", imports={}, delay_imports={"KERNELBASE.dll": ["Foo"]})
        assert old.delay_imports is None
        r = compare(_pe_snap(old), _pe_snap(new))
        assert ChangeKind.PE_IMPORT_LOAD_MODE_CHANGED not in _kinds(r)


# ── pe_ordinal_retargeted ────────────────────────────────────────────────────

class _FakePeMeta:
    def __init__(self, exports):
        self.exports = exports


class TestPeOrdinalRetargeted:
    def test_ordinal_retargeted_to_different_function(self):
        with patch("abicheck.appcompat._detect_app_format", return_value="pe"), \
             patch("abicheck.pe_metadata.parse_pe_metadata") as mock_parse:
            mock_parse.side_effect = [
                _FakePeMeta([PeExport(name="Foo", ordinal=17)]),
                _FakePeMeta([PeExport(name="Bar", ordinal=17)]),
            ]
            reqs = AppRequirements(undefined_symbols={"ordinal:17"})
            resolved, retargeted = _check_pe_ordinal_imports(Path("old.dll"), Path("new.dll"), reqs)

        assert resolved == {"ordinal:17"}
        assert len(retargeted) == 1
        assert retargeted[0].kind == ChangeKind.PE_ORDINAL_RETARGETED

    def test_ordinal_unchanged_resolved_no_retarget(self):
        with patch("abicheck.appcompat._detect_app_format", return_value="pe"), \
             patch("abicheck.pe_metadata.parse_pe_metadata") as mock_parse:
            mock_parse.side_effect = [
                _FakePeMeta([PeExport(name="Foo", ordinal=17)]),
                _FakePeMeta([PeExport(name="Foo", ordinal=17)]),
            ]
            reqs = AppRequirements(undefined_symbols={"ordinal:17"})
            resolved, retargeted = _check_pe_ordinal_imports(Path("old.dll"), Path("new.dll"), reqs)

        assert resolved == {"ordinal:17"}
        assert retargeted == []

    def test_ordinal_dropped_stays_unresolved(self):
        with patch("abicheck.appcompat._detect_app_format", return_value="pe"), \
             patch("abicheck.pe_metadata.parse_pe_metadata") as mock_parse:
            mock_parse.side_effect = [
                _FakePeMeta([PeExport(name="Foo", ordinal=17)]),
                _FakePeMeta([]),
            ]
            reqs = AppRequirements(undefined_symbols={"ordinal:17"})
            resolved, retargeted = _check_pe_ordinal_imports(Path("old.dll"), Path("new.dll"), reqs)

        assert resolved == set()
        assert retargeted == []

    def test_no_ordinal_requirements_short_circuits(self):
        reqs = AppRequirements(undefined_symbols={"NamedFunc"})
        resolved, retargeted = _check_pe_ordinal_imports(Path("old.dll"), Path("new.dll"), reqs)
        assert resolved == set()
        assert retargeted == []


# ── Runtime symbol-binding rebound ───────────────────────────────────────────

def _node(path: str, soname: str) -> ResolvedDSO:
    return ResolvedDSO(
        path=Path(path), soname=soname, needed=[], rpath="", runpath="",
        resolution_reason="root", depth=0,
    )


class TestRuntimeBindingDiff:
    def test_provider_changed_across_environments(self):
        base_graph = DependencyGraph(root="/app", nodes={
            "/base/app": _node("/base/app", "app"),
            "/base/liba.so": _node("/base/liba.so", "liba.so.1"),
        })
        cand_graph = DependencyGraph(root="/app", nodes={
            "/cand/app": _node("/cand/app", "app"),
            "/cand/libb.so": _node("/cand/libb.so", "libb.so.1"),
        })
        base_bindings = [
            SymbolBinding(consumer="/base/app", symbol="process", version="",
                          provider="/base/liba.so", status=BindingStatus.RESOLVED_OK, explanation=""),
        ]
        cand_bindings = [
            SymbolBinding(consumer="/cand/app", symbol="process", version="",
                          provider="/cand/libb.so", status=BindingStatus.RESOLVED_OK, explanation=""),
        ]
        changes = diff_runtime_bindings(base_graph, cand_graph, base_bindings, cand_bindings)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.RUNTIME_SYMBOL_PROVIDER_CHANGED
        assert changes[0].old_value == "liba.so.1"
        assert changes[0].new_value == "libb.so.1"

    def test_same_provider_not_flagged(self):
        base_graph = DependencyGraph(root="/app", nodes={
            "/base/app": _node("/base/app", "app"),
            "/base/liba.so": _node("/base/liba.so", "liba.so.1"),
        })
        cand_graph = DependencyGraph(root="/app", nodes={
            "/cand/app": _node("/cand/app", "app"),
            "/cand/liba.so": _node("/cand/liba.so", "liba.so.1"),
        })
        binding = SymbolBinding(consumer="/base/app", symbol="process", version="",
                                 provider="/base/liba.so", status=BindingStatus.RESOLVED_OK, explanation="")
        cand_binding = SymbolBinding(consumer="/cand/app", symbol="process", version="",
                                      provider="/cand/liba.so", status=BindingStatus.RESOLVED_OK, explanation="")
        changes = diff_runtime_bindings(base_graph, cand_graph, [binding], [cand_binding])
        assert changes == []

    def test_weak_resolution_changed(self):
        base_graph = DependencyGraph(root="/app", nodes={"/base/app": _node("/base/app", "app")})
        cand_graph = DependencyGraph(root="/app", nodes={
            "/cand/app": _node("/cand/app", "app"),
            "/cand/libb.so": _node("/cand/libb.so", "libb.so.1"),
        })
        base_bindings = [
            SymbolBinding(consumer="/base/app", symbol="opt_feature", version="",
                          provider=None, status=BindingStatus.WEAK_UNRESOLVED, explanation=""),
        ]
        cand_bindings = [
            SymbolBinding(consumer="/cand/app", symbol="opt_feature", version="",
                          provider="/cand/libb.so", status=BindingStatus.RESOLVED_OK, explanation=""),
        ]
        changes = diff_runtime_bindings(base_graph, cand_graph, base_bindings, cand_bindings)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.RUNTIME_WEAK_RESOLUTION_CHANGED

    def test_consumer_only_on_one_side_ignored(self):
        # A binding whose consumer doesn't exist in the other environment
        # (a DSO added/removed) must not be compared — that's a different,
        # already-covered event.
        base_graph = DependencyGraph(root="/app", nodes={"/base/app": _node("/base/app", "app")})
        cand_graph = DependencyGraph(root="/app", nodes={"/cand/app2": _node("/cand/app2", "app2")})
        base_bindings = [
            SymbolBinding(consumer="/base/app", symbol="process", version="",
                          provider=None, status=BindingStatus.MISSING, explanation=""),
        ]
        cand_bindings = [
            SymbolBinding(consumer="/cand/app2", symbol="process", version="",
                          provider=None, status=BindingStatus.MISSING, explanation=""),
        ]
        changes = diff_runtime_bindings(base_graph, cand_graph, base_bindings, cand_bindings)
        assert changes == []


class TestComputeAbiRiskWithBindingChanges:
    def test_risk_binding_change_warns(self):
        from abicheck.checker_types import Change

        change = Change(
            kind=ChangeKind.RUNTIME_SYMBOL_PROVIDER_CHANGED,
            symbol="process", description="moved provider",
        )
        assert _compute_abi_risk([], [change]) == StackVerdict.WARN

    def test_no_binding_changes_passes(self):
        assert _compute_abi_risk([], []) == StackVerdict.PASS
