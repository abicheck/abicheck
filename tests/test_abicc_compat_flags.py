"""Tests for ABICC compat mode flags.

Verifies that abicheck compat command:
- Accepts all major ABICC-equivalent CLI flags
- -s/-strict promotes COMPATIBLE → BREAKING exit code
- -source/-src/-api filters ELF-only changes out
- -skip-symbols / -skip-types build suppression correctly
- -v1/-v2 override version labels
- -stdout prints report to stdout
- _filter_source_only removes BINARY_ONLY_KINDS
- _build_skip_suppression handles exact names and patterns
"""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.checker_policy import API_BREAK_KINDS as _API_BREAK_KINDS
from abicheck.cli import main
from abicheck.compat.cli import (
    _BINARY_ONLY_KINDS,
    _apply_strict,
    _build_skip_suppression,
    _filter_source_only,
)

# ── helpers ──────────────────────────────────────────────────────────────────

def _result(verdict: Verdict, kinds: list[ChangeKind]) -> DiffResult:
    changes = [
        Change(kind=k, symbol=f"_sym_{i}", description=k.value)
        for i, k in enumerate(kinds)
    ]
    return DiffResult(
        old_version="1.0", new_version="2.0",
        library="libtest.so.1",
        changes=changes,
        verdict=verdict,
    )


# ── _apply_strict ────────────────────────────────────────────────────────────

class TestApplyStrict:
    def test_pure_addition_stays_compatible_in_strict(self):
        # ABICC 2.3 semantics: pure additions stay COMPATIBLE even under -strict.
        r = _result(Verdict.COMPATIBLE, [ChangeKind.FUNC_ADDED])
        promoted = _apply_strict(r)
        assert promoted.verdict == Verdict.COMPATIBLE

    def test_promotes_compatible_non_addition_to_breaking(self):
        # Non-addition compatible changes ARE promoted under strict mode.
        r = _result(Verdict.COMPATIBLE, [ChangeKind.FUNC_REMOVED_ELF_ONLY])
        promoted = _apply_strict(r)
        assert promoted.verdict == Verdict.BREAKING

    def test_promotes_source_break_to_breaking(self):
        r = _result(Verdict.API_BREAK, [ChangeKind.FUNC_PARAMS_CHANGED])
        promoted = _apply_strict(r)
        assert promoted.verdict == Verdict.BREAKING

    def test_no_change_stays(self):
        r = _result(Verdict.NO_CHANGE, [])
        promoted = _apply_strict(r)
        assert promoted.verdict == Verdict.NO_CHANGE

    def test_breaking_stays(self):
        r = _result(Verdict.BREAKING, [ChangeKind.FUNC_REMOVED])
        promoted = _apply_strict(r)
        assert promoted.verdict == Verdict.BREAKING

    def test_api_mode_compatible_stays_compatible(self):
        # mode='api': COMPATIBLE result should NOT be promoted to BREAKING
        r = _result(Verdict.COMPATIBLE, [ChangeKind.FUNC_ADDED])
        result = _apply_strict(r, mode="api")
        assert result.verdict == Verdict.COMPATIBLE

    def test_api_mode_promotes_api_break(self):
        # mode='api': API_BREAK should still be promoted to BREAKING.
        # Use PARAM_RENAMED which is in _API_BREAK_KINDS (not BREAKING),
        # so the fixture accurately represents an API_BREAK scenario.
        r = _result(Verdict.API_BREAK, [ChangeKind.PARAM_RENAMED])
        result = _apply_strict(r, mode="api")
        assert result.verdict == Verdict.BREAKING

    def test_full_mode_pure_addition_stays_compatible(self):
        # mode='full' (explicit): pure additions stay COMPATIBLE (ABICC parity)
        r = _result(Verdict.COMPATIBLE, [ChangeKind.FUNC_ADDED])
        result = _apply_strict(r, mode="full")
        assert result.verdict == Verdict.COMPATIBLE

    def test_full_mode_promotes_non_addition_compatible(self):
        # mode='full': non-addition compatible changes ARE promoted
        r = _result(Verdict.COMPATIBLE, [ChangeKind.FUNC_REMOVED_ELF_ONLY])
        result = _apply_strict(r, mode="full")
        assert result.verdict == Verdict.BREAKING


# ── _filter_source_only ───────────────────────────────────────────────────────

class TestFilterSourceOnly:
    def test_removes_binary_only_soname(self):
        r = _result(Verdict.BREAKING, [ChangeKind.SONAME_CHANGED, ChangeKind.FUNC_REMOVED])
        filtered = _filter_source_only(r)
        kinds = {c.kind for c in filtered.changes}
        assert ChangeKind.SONAME_CHANGED not in kinds
        assert ChangeKind.FUNC_REMOVED in kinds

    def test_removes_symbol_binding_changed(self):
        r = _result(Verdict.BREAKING, [ChangeKind.SYMBOL_BINDING_CHANGED])
        filtered = _filter_source_only(r)
        assert filtered.changes == []
        assert filtered.verdict == Verdict.NO_CHANGE

    def test_keeps_func_params_changed(self):
        r = _result(Verdict.BREAKING, [ChangeKind.FUNC_PARAMS_CHANGED])
        filtered = _filter_source_only(r)
        assert len(filtered.changes) == 1
        assert filtered.verdict == Verdict.BREAKING

    def test_verdict_recalculated_to_no_change(self):
        r = _result(Verdict.BREAKING, [ChangeKind.TOOLCHAIN_FLAG_DRIFT])
        filtered = _filter_source_only(r)
        assert filtered.verdict == Verdict.NO_CHANGE

    def test_all_binary_kinds_removable(self):
        """All BINARY_ONLY_KINDS must exist in ChangeKind enum."""
        for kind in _BINARY_ONLY_KINDS:
            assert kind in ChangeKind

    def test_internal_symbol_size_change_is_binary_only(self):
        """symbol_size_changed_internal is a pure-ELF signal — filtered from
        source-only views just like symbol_size_changed (Codex review)."""
        assert ChangeKind.SYMBOL_SIZE_CHANGED_INTERNAL in _BINARY_ONLY_KINDS
        from abicheck.compat.cli import _filter_source_only
        r = _result(Verdict.COMPATIBLE_WITH_RISK,
                    [ChangeKind.SYMBOL_SIZE_CHANGED_INTERNAL])
        filtered = _filter_source_only(r)
        assert ChangeKind.SYMBOL_SIZE_CHANGED_INTERNAL not in {c.kind for c in filtered.changes}

    def test_const_object_size_change_is_binary_only(self):
        """symbol_size_changed_const_object is still ELF st_size metadata."""
        assert ChangeKind.SYMBOL_SIZE_CHANGED_CONST_OBJECT in _BINARY_ONLY_KINDS

    def test_g23_phase_a_facts_are_binary_only(self):
        """G23 Phase A ELF facts (machine/class/ABI-flags/OSABI, static-TLS,
        CET/BTI, GNU-unique bindings) have no source-API delta, so
        source-only mode must drop them (Codex review)."""
        for kind in (
            ChangeKind.ELF_MACHINE_CHANGED,
            ChangeKind.ELF_CLASS_CHANGED,
            ChangeKind.ELF_ABI_FLAGS_CHANGED,
            ChangeKind.ELF_OSABI_CHANGED,
            ChangeKind.STATIC_TLS_INTRODUCED,
            ChangeKind.CET_PROTECTION_WEAKENED,
            ChangeKind.BRANCH_PROTECTION_WEAKENED,
            ChangeKind.SYMBOL_BINDING_BECAME_UNIQUE,
        ):
            assert kind in _BINARY_ONLY_KINDS, kind
            filtered = _filter_source_only(_result(Verdict.BREAKING, [kind]))
            assert kind not in {c.kind for c in filtered.changes}

    def test_g23_b1_thunk_kinds_are_not_binary_only(self):
        """The B1 thunk/VTT kinds mirror source-visible vtable/base changes
        (like vtable_slot_count_changed), so they are NOT filtered."""
        for kind in (
            ChangeKind.VTABLE_THUNK_OFFSET_CHANGED,
            ChangeKind.VTABLE_THUNK_SET_CHANGED,
            ChangeKind.VTT_SLOT_COUNT_CHANGED,
        ):
            assert kind not in _BINARY_ONLY_KINDS, kind
        from abicheck.compat.cli import _filter_source_only
        r = _result(Verdict.BREAKING,
                    [ChangeKind.SYMBOL_SIZE_CHANGED_CONST_OBJECT])
        filtered = _filter_source_only(r)
        assert ChangeKind.SYMBOL_SIZE_CHANGED_CONST_OBJECT not in {c.kind for c in filtered.changes}
        assert filtered.verdict == Verdict.NO_CHANGE


# ── _build_skip_suppression ───────────────────────────────────────────────────

class TestBuildSkipSuppression:
    def test_exact_symbol_match(self, tmp_path):
        f = tmp_path / "skip.txt"
        f.write_text("_Z3foov\n_Z3barv\n")
        sup = _build_skip_suppression(f, None)
        assert len(sup._suppressions) == 2
        assert sup._suppressions[0].symbol == "_Z3foov"

    def test_pattern_symbol_match(self, tmp_path):
        f = tmp_path / "skip.txt"
        f.write_text("_Z.*foo.*\n")
        sup = _build_skip_suppression(f, None)
        assert sup._suppressions[0].symbol_pattern == "_Z.*foo.*"

    def test_skip_types_file(self, tmp_path):
        f = tmp_path / "types.txt"
        f.write_text("MyStruct\nOtherType\n")
        sup = _build_skip_suppression(None, f)
        assert len(sup._suppressions) == 2
        assert sup._suppressions[0].symbol == "MyStruct"

    def test_both_none_returns_empty(self):
        sup = _build_skip_suppression(None, None)
        assert sup._suppressions == []

    def test_comments_skipped(self, tmp_path):
        f = tmp_path / "skip.txt"
        f.write_text("# comment\n_Z3foov\n  \n")
        sup = _build_skip_suppression(f, None)
        assert len(sup._suppressions) == 1

    def test_missing_file_raises_oserror(self, tmp_path):
        """Non-existent file raises OSError (caller handles with sys.exit(2))."""
        with pytest.raises(OSError):
            _build_skip_suppression(tmp_path / "nonexistent.txt", None)

    def test_merge_combines_suppressions(self, tmp_path):
        """SuppressionList.merge() combines rules from both lists."""
        from abicheck.suppression import Suppression, SuppressionList
        a = SuppressionList(suppressions=[Suppression(symbol="_sym1")])
        b = SuppressionList(suppressions=[Suppression(symbol="_sym2")])
        merged = SuppressionList.merge(a, b)
        assert len(merged._suppressions) == 2


# ── CLI flag parsing ──────────────────────────────────────────────────────────

class TestCompatCliFlags:
    """Verify all ABICC-equivalent flags are accepted by the CLI parser."""

    def _invoke_help(self, *args):
        runner = CliRunner()
        return runner.invoke(main, ["compat", "check", "--help"])

    def test_help_contains_strict_flag(self):
        result = self._invoke_help()
        assert "-s" in result.output or "strict" in result.output

    def test_help_contains_show_retval(self):
        result = self._invoke_help()
        assert "show-retval" in result.output

    def test_help_contains_source_flag(self):
        result = self._invoke_help()
        assert "source" in result.output

    def test_help_contains_headers_only(self):
        result = self._invoke_help()
        assert "headers-only" in result.output

    def test_help_contains_skip_symbols(self):
        result = self._invoke_help()
        assert "skip-symbols" in result.output

    def test_help_contains_v1_v2(self):
        result = self._invoke_help()
        assert "v1" in result.output or "vnum1" in result.output

    def test_help_contains_stdout(self):
        result = self._invoke_help()
        assert "stdout" in result.output

    def test_d1_d2_aliases_accepted(self):
        """Verify -d1/-d2 aliases for -old/-new are registered."""
        runner = CliRunner()
        result = runner.invoke(main, ["compat", "check", "--help"])
        assert "-d1" in result.output or "d1" in result.output


# ── API_BREAK_KINDS / BINARY_ONLY_KINDS completeness ──────────────────────

class TestKindSets:
    def test_no_overlap_between_source_and_binary(self):
        overlap = _API_BREAK_KINDS & _BINARY_ONLY_KINDS
        assert overlap == frozenset(), f"Overlap: {overlap}"

    def test_soname_in_binary_only(self):
        assert ChangeKind.SONAME_CHANGED in _BINARY_ONLY_KINDS

    def test_param_renamed_in_api_break(self):
        assert ChangeKind.PARAM_RENAMED in _API_BREAK_KINDS

    def test_filter_source_only_source_break_verdict(self):
        """_filter_source_only: API_BREAK_KINDS changes → correct verdict + filtering."""
        from abicheck.compat.cli import _filter_source_only
        r = _result(Verdict.BREAKING, [ChangeKind.SONAME_CHANGED, ChangeKind.FUNC_PARAMS_CHANGED])
        filtered = _filter_source_only(r)
        # SONAME removed, FUNC_PARAMS_CHANGED stays
        assert ChangeKind.SONAME_CHANGED not in {c.kind for c in filtered.changes}
        assert ChangeKind.FUNC_PARAMS_CHANGED in {c.kind for c in filtered.changes}
        # FUNC_PARAMS_CHANGED is in _API_BREAK_KINDS → verdict API_BREAK
        assert filtered.verdict in (Verdict.BREAKING, Verdict.API_BREAK)
