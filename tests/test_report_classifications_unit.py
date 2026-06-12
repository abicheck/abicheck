"""Unit tests for abicheck.report_classifications module."""
from __future__ import annotations

import pytest

from abicheck.report_classifications import (
    ADDED_KINDS,
    BINARY_ONLY_KINDS,
    BREAKING_KINDS,
    CHANGED_BREAKING_KINDS,
    REMOVED_KINDS,
    category,
    is_breaking,
    is_symbol_problem,
    is_type_problem,
    kind_str,
    severity,
)

# ---------------------------------------------------------------------------
# Frozenset constants are non-empty
# ---------------------------------------------------------------------------

class TestConstants:
    def test_removed_kinds_contains_expected_members(self):
        assert isinstance(REMOVED_KINDS, frozenset)
        assert "func_removed" in REMOVED_KINDS
        assert "var_removed" in REMOVED_KINDS
        assert "type_removed" in REMOVED_KINDS

    def test_added_kinds_contains_expected_members(self):
        assert isinstance(ADDED_KINDS, frozenset)
        assert "func_added" in ADDED_KINDS
        assert "var_added" in ADDED_KINDS
        assert "type_added" in ADDED_KINDS

    def test_binary_only_kinds_contains_expected_members(self):
        assert isinstance(BINARY_ONLY_KINDS, frozenset)
        assert "soname_changed" in BINARY_ONLY_KINDS
        assert "symbol_type_changed" in BINARY_ONLY_KINDS
        assert "calling_convention_changed" in BINARY_ONLY_KINDS

    def test_breaking_kinds_contains_expected_members(self):
        assert isinstance(BREAKING_KINDS, frozenset)
        assert "func_removed" in BREAKING_KINDS
        assert "type_size_changed" in BREAKING_KINDS
        assert "var_removed" in BREAKING_KINDS

    def test_changed_breaking_kinds_contains_expected_members(self):
        assert isinstance(CHANGED_BREAKING_KINDS, frozenset)
        assert "func_params_changed" in CHANGED_BREAKING_KINDS
        assert "func_return_changed" in CHANGED_BREAKING_KINDS
        assert "type_field_offset_changed" in CHANGED_BREAKING_KINDS


# ---------------------------------------------------------------------------
# category()
# ---------------------------------------------------------------------------

class TestCategory:
    @pytest.mark.parametrize("kind_s, expected", [
        ("func_removed", "Functions"),
        ("var_added", "Variables"),
        ("type_size_changed", "Types"),
        ("struct_field_removed", "Types"),
        ("union_field_type_changed", "Types"),
        ("field_bitfield_changed", "Types"),
        ("typedef_removed", "Types"),
        ("enum_member_added", "Enums"),
        ("soname_changed", "ELF / DWARF"),
        ("symbol_type_changed", "ELF / DWARF"),
        ("needed_added", "ELF / DWARF"),
        ("rpath_changed", "ELF / DWARF"),
        ("runpath_changed", "ELF / DWARF"),
        ("ifunc_introduced", "ELF / DWARF"),
        ("common_symbol_risk", "ELF / DWARF"),
        ("dwarf_info_missing", "ELF / DWARF"),
    ])
    def test_known_categories(self, kind_s, expected):
        assert category(kind_s) == expected

    @pytest.mark.parametrize("kind_s", [
        "calling_convention_changed",
        "unknown_kind",
    ])
    def test_other_category(self, kind_s):
        assert category(kind_s) == "Other"


# ---------------------------------------------------------------------------
# severity()
# ---------------------------------------------------------------------------

class TestSeverity:
    def test_high_severity(self):
        assert severity("func_removed") == "High"

    def test_medium_severity_return_changed(self):
        assert severity("func_return_changed") == "Medium"

    def test_medium_severity_calling_convention(self):
        assert severity("calling_convention_changed") == "Medium"

    def test_medium_severity_struct_return_convention(self) -> None:
        # A register<->sret flip is a silent-corruption break — not Low.
        assert severity("struct_return_convention_changed") == "Medium"

    def test_low_severity_added(self):
        assert severity("func_added") == "Low"

    def test_low_severity_unknown(self):
        assert severity("totally_unknown") == "Low"


# ---------------------------------------------------------------------------
# is_type_problem()
# ---------------------------------------------------------------------------

class TestIsTypeProblem:
    @pytest.mark.parametrize("kind_s", [
        "type_size_changed",
        "struct_field_removed",
        "union_field_type_changed",
        "field_bitfield_changed",
        "typedef_base_changed",
        "enum_member_added",
        "base_class_position_changed",
    ])
    def test_true_for_type_kinds(self, kind_s):
        assert is_type_problem(kind_s) is True

    @pytest.mark.parametrize("kind_s", [
        "func_removed",
        "var_added",
    ])
    def test_false_for_non_type_kinds(self, kind_s):
        assert is_type_problem(kind_s) is False


# ---------------------------------------------------------------------------
# is_symbol_problem()
# ---------------------------------------------------------------------------

class TestIsSymbolProblem:
    @pytest.mark.parametrize("kind_s", [
        "func_removed",
        "func_added",
        "var_removed",
        "var_type_changed",
    ])
    def test_true_for_symbol_kinds(self, kind_s):
        assert is_symbol_problem(kind_s) is True

    @pytest.mark.parametrize("kind_s", [
        "type_size_changed",
        "soname_changed",
    ])
    def test_false_for_non_symbol_kinds(self, kind_s):
        assert is_symbol_problem(kind_s) is False


# ---------------------------------------------------------------------------
# kind_str()
# ---------------------------------------------------------------------------

class TestKindStr:
    def test_kind_with_value_attr(self):
        class FakeKind:
            value = "func_removed"

        class FakeChange:
            kind = FakeKind()

        assert kind_str(FakeChange()) == "func_removed"

    def test_kind_is_none(self):
        class FakeChange:
            kind = None

        assert kind_str(FakeChange()) == "None"

    def test_kind_without_value_attr(self):
        class FakeChange:
            kind = 42

        assert kind_str(FakeChange()) == "42"

    def test_kind_string_no_value_attr(self):
        class FakeChange:
            kind = "some_string_kind"

        assert kind_str(FakeChange()) == "some_string_kind"


# ---------------------------------------------------------------------------
# is_breaking()
# ---------------------------------------------------------------------------

class TestIsBreaking:
    def test_breaking_kind(self):
        # Pick a kind known to be in BREAKING_KINDS
        breaking_kind = next(iter(BREAKING_KINDS))

        class FakeKind:
            value = breaking_kind

        class FakeChange:
            kind = FakeKind()

        assert is_breaking(FakeChange()) is True

    def test_non_breaking_kind(self):
        class FakeKind:
            value = "absolutely_not_a_real_breaking_kind_xyz"

        class FakeChange:
            kind = FakeKind()

        assert is_breaking(FakeChange()) is False
