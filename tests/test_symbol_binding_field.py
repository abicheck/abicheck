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

"""Tests for ELF symbol *binding* (linkage) as a model field + suppression
selector: Function/Variable.elf_binding, Change.symbol_binding, and
Suppression's ``binding`` selector.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.diff_symbols import _check_removed_function, _var_removed
from abicheck.dumper_elf_symbols import _populate_elf_visibility
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding
from abicheck.model import AbiSnapshot, FactStatus, Function, Variable
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
from abicheck.suppression import Suppression


def _snapshot_with_binding(func_binding: SymbolBinding) -> AbiSnapshot:
    func = Function(name="f", mangled="_Z1fv", return_type="void")
    var = Variable(name="g", mangled="g", type="int")
    elf = ElfMetadata(
        symbols=[
            ElfSymbol(name="_Z1fv", binding=func_binding),
            ElfSymbol(name="g", binding=func_binding),
        ]
    )
    return AbiSnapshot(
        library="lib.so", version="1.0", functions=[func], variables=[var], elf=elf
    )


class TestModelPopulation:
    def test_populate_elf_visibility_sets_binding_on_functions_and_variables(
        self,
    ) -> None:
        snap = _snapshot_with_binding(SymbolBinding.WEAK)
        _populate_elf_visibility(snap)
        assert snap.functions[0].elf_binding == SymbolBinding.WEAK
        assert snap.variables[0].elf_binding == SymbolBinding.WEAK

    def test_populate_elf_visibility_global_binding(self) -> None:
        snap = _snapshot_with_binding(SymbolBinding.GLOBAL)
        _populate_elf_visibility(snap)
        assert snap.functions[0].elf_binding == SymbolBinding.GLOBAL

    def test_no_elf_metadata_leaves_binding_none(self) -> None:
        func = Function(name="f", mangled="_Z1fv", return_type="void")
        snap = AbiSnapshot(library="lib.so", version="1.0", functions=[func], elf=None)
        _populate_elf_visibility(snap)
        assert snap.functions[0].elf_binding is None

    def test_no_matching_symbol_leaves_binding_none(self) -> None:
        func = Function(name="f", mangled="_Z1fv", return_type="void")
        elf = ElfMetadata(symbols=[])
        snap = AbiSnapshot(library="lib.so", version="1.0", functions=[func], elf=elf)
        _populate_elf_visibility(snap)
        assert snap.functions[0].elf_binding is None


class TestSerializationRoundTrip:
    def test_elf_binding_round_trips(self) -> None:
        snap = _snapshot_with_binding(SymbolBinding.WEAK)
        _populate_elf_visibility(snap)
        d = snapshot_to_dict(snap)
        loaded = snapshot_from_dict(d)
        assert loaded.functions[0].elf_binding == SymbolBinding.WEAK
        assert loaded.variables[0].elf_binding == SymbolBinding.WEAK

    def test_missing_elf_binding_key_backfilled_from_elf_symbols(self) -> None:
        # Simulates an older (pre-this-field) snapshot: the per-declaration
        # elf_binding key is absent, but the elf.symbols block that carries
        # the same underlying fact is still present -- snapshot_from_dict
        # must backfill from it rather than leave an already-known fact as
        # None (Codex review).
        snap = _snapshot_with_binding(SymbolBinding.GLOBAL)
        _populate_elf_visibility(snap)
        d = snapshot_to_dict(snap)
        for f in d["functions"]:
            f.pop("elf_binding", None)
            f.pop("elf_binding_fact", None)
        for v in d["variables"]:
            v.pop("elf_binding", None)
            v.pop("elf_binding_fact", None)
        loaded = snapshot_from_dict(d)
        assert loaded.functions[0].elf_binding == SymbolBinding.GLOBAL
        assert loaded.variables[0].elf_binding == SymbolBinding.GLOBAL
        # ADR-063 Phase 5 (Codex/CodeRabbit review, fresh evidence): the
        # legacy load-time backfill must keep elf_binding_fact in sync with
        # the recovered legacy value too -- previously it left the fact at
        # its __post_init__-derived Fact.not_collected() even though the
        # legacy field itself carried a real, recovered binding, so a
        # reserialize-then-reload round trip silently reverted the
        # recovered evidence back to "not collected".
        assert loaded.functions[0].elf_binding_fact.status is FactStatus.PRESENT
        assert loaded.functions[0].elf_binding_fact.value == SymbolBinding.GLOBAL
        assert loaded.variables[0].elf_binding_fact.status is FactStatus.PRESENT
        assert loaded.variables[0].elf_binding_fact.value == SymbolBinding.GLOBAL

    def test_elf_binding_fact_survives_a_second_round_trip_after_backfill(
        self,
    ) -> None:
        # The bug this closes: backfill_missing_elf_binding recovered
        # elf_binding but left elf_binding_fact stale at NOT_COLLECTED, so
        # re-serializing the backfilled snapshot and loading it again
        # (schema_version now current) silently reset elf_binding to None
        # -- the recovered evidence disappeared on the *second* round trip,
        # not the first.
        snap = _snapshot_with_binding(SymbolBinding.GLOBAL)
        _populate_elf_visibility(snap)
        d = snapshot_to_dict(snap)
        for f in d["functions"]:
            f.pop("elf_binding", None)
            f.pop("elf_binding_fact", None)
        for v in d["variables"]:
            v.pop("elf_binding", None)
            v.pop("elf_binding_fact", None)
        once_loaded = snapshot_from_dict(d)
        twice_loaded = snapshot_from_dict(snapshot_to_dict(once_loaded))
        assert twice_loaded.functions[0].elf_binding == SymbolBinding.GLOBAL
        assert twice_loaded.variables[0].elf_binding == SymbolBinding.GLOBAL

    def test_missing_elf_binding_stays_none_without_elf_metadata(self) -> None:
        # No elf block at all (e.g. a non-ELF-format snapshot, or one
        # genuinely missing the metadata) -- nothing to backfill from, so
        # the ordinary missing-key default applies.
        func = Function(name="f", mangled="_Z1fv", return_type="void")
        snap = AbiSnapshot(library="lib.so", version="1.0", functions=[func], elf=None)
        d = snapshot_to_dict(snap)
        d["functions"][0].pop("elf_binding", None)
        loaded = snapshot_from_dict(d)
        assert loaded.functions[0].elf_binding is None

    def test_missing_elf_binding_stays_none_when_no_matching_symbol(self) -> None:
        # elf block present, but no .dynsym entry for this declaration's
        # mangled name -- nothing to backfill from either. Covers the
        # variable branch too (`backfill_missing_elf_binding` walks
        # functions and variables separately) -- previously exercised only
        # for functions.
        func = Function(name="f", mangled="_Z1fv", return_type="void")
        var = Variable(name="g", mangled="g", type="int")
        snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            functions=[func],
            variables=[var],
            elf=ElfMetadata(symbols=[]),
        )
        d = snapshot_to_dict(snap)
        d["functions"][0].pop("elf_binding", None)
        d["variables"][0].pop("elf_binding", None)
        loaded = snapshot_from_dict(d)
        assert loaded.functions[0].elf_binding is None
        assert loaded.variables[0].elf_binding is None

    def test_explicit_elf_binding_is_not_overwritten_by_backfill(self) -> None:
        # An already-serialized non-None value must be preserved untouched,
        # never recomputed -- even if a differently-keyed elf.symbols entry
        # would otherwise suggest something else (backfill only ever fills
        # a None). The elf.symbols binding is deliberately set to a
        # *different* value here so this test can't pass by coincidence
        # (CodeRabbit nit).
        snap = _snapshot_with_binding(SymbolBinding.WEAK)
        _populate_elf_visibility(snap)
        d = snapshot_to_dict(snap)
        d["elf"]["symbols"][0]["binding"] = "global"
        loaded = snapshot_from_dict(d)
        assert loaded.functions[0].elf_binding == SymbolBinding.WEAK


class TestChangeSymbolBindingStamp:
    def test_func_removed_stamps_symbol_binding(self) -> None:
        f_old = Function(
            name="f",
            mangled="_Z1fv",
            return_type="void",
            elf_binding=SymbolBinding.WEAK,
        )
        change = _check_removed_function("_Z1fv", f_old, {}, elf_only_mode=False)
        assert change.kind == ChangeKind.FUNC_REMOVED
        assert change.symbol_binding == "weak"

    def test_func_removed_no_binding_captured_is_none(self) -> None:
        f_old = Function(name="f", mangled="_Z1fv", return_type="void")
        change = _check_removed_function("_Z1fv", f_old, {}, elf_only_mode=False)
        assert change.symbol_binding is None

    def test_func_visibility_changed_stamps_symbol_binding(self) -> None:
        # Codex review, upstream ask #1: FUNC_VISIBILITY_CHANGED (a public
        # export demoted to HIDDEN, not a removal) is the other common
        # export-loss shape a `binding:` selector needs to scope, alongside
        # the four removal kinds -- an unstamped visibility-hidden finding
        # forced oneDAL's own reclassify rules to fall back to a kind-global
        # override instead of a binding-scoped one.
        from abicheck.model import Visibility

        f_old = Function(
            name="f",
            mangled="_Z1fv",
            return_type="void",
            visibility=Visibility.PUBLIC,
            elf_binding=SymbolBinding.WEAK,
        )
        f_hidden = Function(
            name="f",
            mangled="_Z1fv",
            return_type="void",
            visibility=Visibility.HIDDEN,
        )
        change = _check_removed_function(
            "_Z1fv", f_old, {"_Z1fv": f_hidden}, elf_only_mode=False
        )
        assert change.kind == ChangeKind.FUNC_VISIBILITY_CHANGED
        assert change.symbol_binding == "weak"

    def test_func_visibility_changed_no_binding_captured_is_none(self) -> None:
        from abicheck.model import Visibility

        f_old = Function(
            name="f", mangled="_Z1fv", return_type="void", visibility=Visibility.PUBLIC
        )
        f_hidden = Function(
            name="f", mangled="_Z1fv", return_type="void", visibility=Visibility.HIDDEN
        )
        change = _check_removed_function(
            "_Z1fv", f_old, {"_Z1fv": f_hidden}, elf_only_mode=False
        )
        assert change.symbol_binding is None

    def test_var_removed_stamps_symbol_binding(self) -> None:
        v_old = Variable(
            name="g", mangled="g", type="int", elf_binding=SymbolBinding.GLOBAL
        )
        changes = _var_removed("g", v_old)
        assert changes[0].symbol_binding == "global"

    def test_elf_deleted_fallback_stamps_symbol_binding(self) -> None:
        from abicheck.diff_platform import _diff_elf_deleted_fallback

        f_old = Function(
            name="f",
            mangled="_Z1fv",
            return_type="void",
            elf_binding=SymbolBinding.WEAK,
        )
        f_new = Function(name="f", mangled="_Z1fv", return_type="void")
        old = AbiSnapshot(
            library="lib.so",
            version="1.0",
            functions=[f_old],
            elf=ElfMetadata(
                symbols=[ElfSymbol(name="_Z1fv", binding=SymbolBinding.WEAK)]
            ),
        )
        new = AbiSnapshot(
            library="lib.so",
            version="2.0",
            functions=[f_new],
            elf=ElfMetadata(symbols=[]),
        )
        changes = _diff_elf_deleted_fallback(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.FUNC_DELETED_ELF_FALLBACK
        assert changes[0].symbol_binding == "weak"


class TestSuppressionBindingSelector:
    def _make_change(self, binding: str | None) -> object:
        from abicheck.checker_types import Change

        return Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z1fv",
            description="removed",
            symbol_binding=binding,
        )

    @pytest.mark.parametrize("binding", [item.value for item in SymbolBinding])
    def test_binding_selector_matches_known_binding(self, binding: str) -> None:
        # Parametrized over every SymbolBinding value, not just "weak"
        # (CodeRabbit nit).
        sup = Suppression(symbol="_Z1fv", binding=binding, reason="binding-specific")
        assert sup.matches(self._make_change(binding))

    def test_binding_selector_does_not_match_global(self) -> None:
        sup = Suppression(
            symbol="_Z1fv", binding="weak", reason="tolerate weak demotions"
        )
        assert not sup.matches(self._make_change("global"))

    def test_binding_selector_does_not_match_unknown_binding(self) -> None:
        sup = Suppression(
            symbol="_Z1fv", binding="weak", reason="tolerate weak demotions"
        )
        assert not sup.matches(self._make_change(None))

    def test_invalid_binding_value_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid binding"):
            Suppression(symbol="_Z1fv", binding="bogus", reason="x")

    def test_non_string_binding_raises_value_error_not_type_error(self) -> None:
        # Regression (Codex review, fresh evidence): a YAML value the
        # `str | None` annotation doesn't enforce at runtime (e.g. a list
        # from `binding: [weak]`) must not escape as an unhandled TypeError
        # from the frozenset membership check -- SuppressionList.load/the
        # CLI/the service layer only translate ValueError.
        with pytest.raises(ValueError, match="Invalid binding"):
            Suppression(symbol="_Z1fv", binding=["weak"], reason="x")

    def test_no_binding_selector_is_unaffected(self) -> None:
        sup = Suppression(symbol="_Z1fv", reason="unrelated")
        assert sup.matches(self._make_change("global"))
        assert sup.matches(self._make_change("weak"))


class TestSuppressionYamlLoading:
    def test_binding_key_loads_from_yaml(self, tmp_path: Path) -> None:
        from abicheck.suppression import SuppressionList

        p = tmp_path / "suppressions.yml"
        p.write_text(
            "version: 1\n"
            "suppressions:\n"
            '  - symbol: "_Z1fv"\n'
            "    change_kind: func_removed\n"
            "    binding: weak\n"
            '    reason: "weak COMDAT demotion, every consumer has its own copy"\n'
        )
        loaded = SuppressionList.load(p)
        assert loaded._suppressions[0].binding == "weak"

    def test_unknown_key_still_rejected(self, tmp_path: Path) -> None:
        from abicheck.suppression import SuppressionList

        p = tmp_path / "suppressions.yml"
        p.write_text(
            "version: 1\n"
            "suppressions:\n"
            '  - symbol: "_Z1fv"\n'
            "    bindingx: weak\n"
            '    reason: "typo\'d key"\n'
        )
        with pytest.raises(ValueError, match="unknown key"):
            SuppressionList.load(p)


class TestReportSerialization:
    """symbol_binding surfaces in the two primary machine-integration
    outputs (JSON via reporter.py, SARIF via sarif.py) -- Codex review:
    without this, weak-COMDAT and strong-export removals were
    indistinguishable in structured output even though the `binding:`
    suppression selector can already tell them apart at match time.
    """

    def _change(self, binding: str | None) -> object:
        from abicheck.checker_types import Change

        return Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z1fv",
            description="removed",
            symbol_binding=binding,
        )

    def test_json_report_includes_symbol_binding(self) -> None:
        from abicheck.reporter import _change_annotation_fields

        fields = _change_annotation_fields(self._change("weak"))
        assert fields["symbol_binding"] == "weak"

    def test_json_report_omits_symbol_binding_when_unset(self) -> None:
        from abicheck.reporter import _change_annotation_fields

        fields = _change_annotation_fields(self._change(None))
        assert "symbol_binding" not in fields

    def test_sarif_includes_symbol_binding(self) -> None:
        from abicheck.sarif import _change_detail_properties

        props = _change_detail_properties(self._change("global"))
        assert props["symbolBinding"] == "global"

    def test_sarif_omits_symbol_binding_when_unset(self) -> None:
        from abicheck.sarif import _change_detail_properties

        props = _change_detail_properties(self._change(None))
        assert "symbolBinding" not in props

    def test_scan_baseline_finding_dicts_includes_symbol_binding(self) -> None:
        # scan --against shares the same --suppress surface as compare, so
        # its own finding projector needs the field too (Codex review,
        # fresh evidence).
        from abicheck.cli_scan_baseline import _baseline_finding_dicts

        entries = _baseline_finding_dicts([self._change("weak")], "breaking")
        assert entries[0]["symbol_binding"] == "weak"

    def test_scan_baseline_finding_dicts_omits_symbol_binding_when_unset(
        self,
    ) -> None:
        from abicheck.cli_scan_baseline import _baseline_finding_dicts

        entries = _baseline_finding_dicts([self._change(None)], "breaking")
        assert "symbol_binding" not in entries[0]

    def test_suppressed_change_entry_includes_symbol_binding(self) -> None:
        # A separate projector from _change_annotation_fields, used for
        # suppression.suppressed_changes[] -- the audit trail for *why* a
        # binding-scoped rule matched needs the field too (Codex review,
        # fresh evidence, schema 2.31).
        from abicheck.reporter import _suppressed_change_entry

        entry = _suppressed_change_entry(self._change("weak"))
        assert entry["symbol_binding"] == "weak"

    def test_suppressed_change_entry_omits_symbol_binding_when_unset(self) -> None:
        from abicheck.reporter import _suppressed_change_entry

        entry = _suppressed_change_entry(self._change(None))
        assert "symbol_binding" not in entry
