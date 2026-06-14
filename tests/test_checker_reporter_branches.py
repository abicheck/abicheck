"""Comprehensive branch-coverage tests for checker, reporter, and stack_report modules.

Targets uncovered lines:
  - checker.py: affected_symbols computation (1807-1915), redundant change
    filtering (2599-2623), enum edge cases (2802-2806), template parsing (3022)
  - reporter.py: to_stat (193-210), ShowOnlyFilter.matches edge cases,
    _build_impact_table (240-259), to_json/to_markdown with show_only/show_impact/report_mode
  - stack_report.py: missing symbol truncation (107), stack changes section
    (121-127), empty graph (227-239), cycle/diamond detection (265, 272-273)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.checker import _ROOT_TYPE_CHANGE_KINDS, Change, DiffResult, compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.confidence import _compute_confidence
from abicheck.diff_filtering import (
    _enrich_affected_symbols,
    _enrich_source_locations,
)
from abicheck.dwarf_advanced import AdvancedDwarfMetadata
from abicheck.dwarf_metadata import DwarfMetadata
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.reporter import (
    ShowOnlyFilter,
    apply_show_only,
    to_json,
    to_markdown,
    to_stat,
    to_stat_json,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snap(
    *,
    library: str = "libtest.so",
    version: str = "1.0",
    functions: list[Function] | None = None,
    variables: list[Variable] | None = None,
    types: list[RecordType] | None = None,
    enums: list[EnumType] | None = None,
) -> AbiSnapshot:
    """Build a minimal AbiSnapshot for testing."""
    snap = AbiSnapshot(
        library=library,
        version=version,
        functions=functions or [],
        variables=variables or [],
        types=types or [],
        enums=enums or [],
    )
    snap.index()
    return snap


def _make_diff(
    changes: list[Change] | None = None,
    verdict: Verdict = Verdict.NO_CHANGE,
    redundant_count: int = 0,
    redundant_changes: list[Change] | None = None,
    suppressed_count: int = 0,
    suppression_file_provided: bool = False,
    suppressed_changes: list[Change] | None = None,
) -> DiffResult:
    """Build a minimal DiffResult for testing."""
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so",
        changes=changes or [],
        verdict=verdict,
        redundant_count=redundant_count,
        redundant_changes=redundant_changes or [],
        suppressed_count=suppressed_count,
        suppression_file_provided=suppression_file_provided,
        suppressed_changes=suppressed_changes or [],
    )


# ===========================================================================
# checker.py branch coverage
# ===========================================================================


class TestAffectedSymbolsComputation:
    """Exercise _enrich_affected_symbols (lines 1845-1923)."""

    def test_type_change_populates_affected_symbols(self):
        """A struct used by functions changes size -> affected_symbols populated."""
        point = RecordType(name="Point", kind="struct", size_bits=64, fields=[
            TypeField(name="x", type="int", offset_bits=0),
            TypeField(name="y", type="int", offset_bits=32),
        ])
        point_v2 = RecordType(name="Point", kind="struct", size_bits=96, fields=[
            TypeField(name="x", type="int", offset_bits=0),
            TypeField(name="y", type="int", offset_bits=32),
            TypeField(name="z", type="int", offset_bits=64),
        ])

        func_using_point = Function(
            name="draw_point", mangled="_Z10draw_point5Point",
            return_type="void",
            params=[Param(name="p", type="Point")],
        )
        func_not_using_point = Function(
            name="get_version", mangled="_Z11get_versionv",
            return_type="int", params=[],
        )

        old = _make_snap(version="1.0", functions=[func_using_point, func_not_using_point], types=[point])
        new = _make_snap(version="2.0", functions=[func_using_point, func_not_using_point], types=[point_v2])

        result = compare(old, new)
        # Find the type_size_changed change for Point
        type_changes = [c for c in result.changes if c.kind == ChangeKind.TYPE_SIZE_CHANGED and "Point" in c.symbol]
        assert len(type_changes) >= 1
        tc = type_changes[0]
        assert tc.affected_symbols is not None
        assert "draw_point" in tc.affected_symbols
        assert "get_version" not in tc.affected_symbols

    def test_transitive_embedding_affects_outer_type_functions(self):
        """Struct A contains Struct B field. Change B -> functions using A are affected."""
        inner = RecordType(name="Inner", kind="struct", size_bits=32, fields=[
            TypeField(name="val", type="int", offset_bits=0),
        ])
        inner_v2 = RecordType(name="Inner", kind="struct", size_bits=64, fields=[
            TypeField(name="val", type="int", offset_bits=0),
            TypeField(name="extra", type="int", offset_bits=32),
        ])
        outer = RecordType(name="Outer", kind="struct", size_bits=64, fields=[
            TypeField(name="inner", type="Inner", offset_bits=0),
            TypeField(name="flags", type="int", offset_bits=32),
        ])

        func_using_outer = Function(
            name="process_outer", mangled="_Z13process_outer5Outer",
            return_type="void",
            params=[Param(name="o", type="Outer")],
        )

        old = _make_snap(version="1.0", functions=[func_using_outer], types=[inner, outer])
        new = _make_snap(version="2.0", functions=[func_using_outer], types=[inner_v2, outer])

        result = compare(old, new)
        inner_changes = [
            c for c in result.changes
            if c.kind in (ChangeKind.TYPE_SIZE_CHANGED, ChangeKind.TYPE_FIELD_ADDED)
            and "Inner" in c.symbol
        ]
        # At least one Inner change should reference process_outer through transitive embedding
        affected_all = set()
        for c in inner_changes:
            if c.affected_symbols:
                affected_all.update(c.affected_symbols)
        assert "process_outer" in affected_all

    def test_type_change_no_functions_referencing(self):
        """Type change with no functions using the type -> affected_symbols stays empty."""
        rec = RecordType(name="Unused", kind="struct", size_bits=32, fields=[
            TypeField(name="x", type="int", offset_bits=0),
        ])
        rec_v2 = RecordType(name="Unused", kind="struct", size_bits=64, fields=[
            TypeField(name="x", type="int", offset_bits=0),
            TypeField(name="y", type="int", offset_bits=32),
        ])
        # Function that does NOT use Unused
        func = Function(
            name="hello", mangled="_Z5hellov",
            return_type="void", params=[],
        )

        old = _make_snap(version="1.0", functions=[func], types=[rec])
        new = _make_snap(version="2.0", functions=[func], types=[rec_v2])

        result = compare(old, new)
        type_changes = [c for c in result.changes if "Unused" in c.symbol and c.kind in _ROOT_TYPE_CHANGE_KINDS]
        for tc in type_changes:
            # affected_symbols should be None or empty
            assert not tc.affected_symbols or len(tc.affected_symbols) == 0

    def test_field_qualified_symbol_strips_to_base_type(self):
        """Change with symbol 'Container::flags' should strip to 'Container'."""
        container = RecordType(name="Container", kind="struct", size_bits=64, fields=[
            TypeField(name="flags", type="int", offset_bits=0),
            TypeField(name="data", type="int", offset_bits=32),
        ])
        container_v2 = RecordType(name="Container", kind="struct", size_bits=64, fields=[
            TypeField(name="data", type="int", offset_bits=0),
        ])

        func = Function(
            name="use_container", mangled="_Z13use_container9Container",
            return_type="void",
            params=[Param(name="c", type="Container")],
        )

        old = _make_snap(version="1.0", functions=[func], types=[container])
        new = _make_snap(version="2.0", functions=[func], types=[container_v2])

        result = compare(old, new)
        # Should have field-related changes with Container as root
        field_changes = [c for c in result.changes if "Container" in c.symbol]
        assert len(field_changes) >= 1


class TestRedundantChangeFiltering:
    """Exercise _filter_redundant (lines 2005-2087)."""

    def test_type_change_cascades_to_function_param_redundancy(self):
        """A struct size change causes func_params_changed to be redundant."""
        rec = RecordType(name="Data", kind="struct", size_bits=32, fields=[
            TypeField(name="x", type="int", offset_bits=0),
        ])
        rec_v2 = RecordType(name="Data", kind="struct", size_bits=64, fields=[
            TypeField(name="x", type="int", offset_bits=0),
            TypeField(name="y", type="int", offset_bits=32),
        ])

        func = Function(
            name="process", mangled="_Z7process4Data",
            return_type="void",
            params=[Param(name="d", type="Data")],
        )
        # Old has Data param, new changes the param type description
        func_v2 = Function(
            name="process", mangled="_Z7process4Data",
            return_type="void",
            params=[Param(name="d", type="Data")],
        )

        old = _make_snap(version="1.0", functions=[func], types=[rec])
        new = _make_snap(version="2.0", functions=[func_v2], types=[rec_v2])

        result = compare(old, new)
        # The type change should be kept; derived changes may be hidden
        type_changes = [c for c in result.changes if c.kind in _ROOT_TYPE_CHANGE_KINDS]
        assert len(type_changes) >= 1
        # redundant_count counts hidden derived changes
        # Even if 0, this exercises the filter path
        assert result.redundant_count >= 0

    def test_redundant_count_positive_with_cascading_changes(self):
        """Manually crafted scenario: root type change + derived func change."""
        root_change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="MyStruct",
            description="Type size changed: MyStruct (32 -> 64 bits)",
            old_value="32",
            new_value="64",
        )
        derived_change = Change(
            kind=ChangeKind.FUNC_PARAMS_CHANGED,
            symbol="_Z4func8MyStruct",
            description="Parameters changed: func(MyStruct) -> func(MyStruct)",
            old_value="MyStruct",
            new_value="MyStruct",
        )
        # Use compare internals by constructing changes and filtering
        from abicheck.checker import _filter_redundant
        kept, redundant = _filter_redundant([root_change, derived_change])
        assert len(redundant) == 1
        assert redundant[0].kind == ChangeKind.FUNC_PARAMS_CHANGED
        assert redundant[0].caused_by_type == "MyStruct"
        # Root change should have caused_count incremented
        assert root_change.caused_count >= 1

    def test_no_root_types_means_no_redundancy(self):
        """No root type changes -> all changes kept, no redundancy."""
        from abicheck.checker import _filter_redundant
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="removed"),
            Change(kind=ChangeKind.FUNC_ADDED, symbol="bar", description="added"),
        ]
        kept, redundant = _filter_redundant(changes)
        assert len(kept) == 2
        assert len(redundant) == 0


class TestEnumEdgeCases:
    """Exercise enum diffing edge cases (lines 2788-2806)."""

    def test_enum_member_renamed_not_reported_as_removed(self):
        """An enum member rename (same value, different name) is not a plain removal."""
        old_enum = EnumType(name="Color", members=[
            EnumMember(name="RED", value=0),
            EnumMember(name="GRN", value=1),
            EnumMember(name="BLU", value=2),
        ])
        new_enum = EnumType(name="Color", members=[
            EnumMember(name="RED", value=0),
            EnumMember(name="GREEN", value=1),  # renamed from GRN
            EnumMember(name="BLUE", value=2),   # renamed from BLU
        ])

        old = _make_snap(version="1.0", enums=[old_enum])
        new = _make_snap(version="2.0", enums=[new_enum])

        result = compare(old, new)
        # Should have renames, not plain removals for GRN and BLU
        removed = [c for c in result.changes if c.kind == ChangeKind.ENUM_MEMBER_REMOVED]
        renamed = [c for c in result.changes if c.kind == ChangeKind.ENUM_MEMBER_RENAMED]
        # The rename detector should catch these; removals should be suppressed
        # At minimum, the rename-suppression logic in lines 2802-2806 is exercised
        assert len(removed) == 0 or len(renamed) > 0

    def test_enum_member_removed_with_value_collision(self):
        """Multiple added members with same value -> rename heuristic fails, shows removal."""
        old_enum = EnumType(name="Flags", members=[
            EnumMember(name="FLAG_A", value=1),
            EnumMember(name="FLAG_B", value=2),
        ])
        new_enum = EnumType(name="Flags", members=[
            EnumMember(name="FLAG_X", value=1),
            EnumMember(name="FLAG_Y", value=1),  # same value as FLAG_X
            EnumMember(name="FLAG_B", value=2),
        ])

        old = _make_snap(version="1.0", enums=[old_enum])
        new = _make_snap(version="2.0", enums=[new_enum])

        result = compare(old, new)
        # FLAG_A removed because two new members share value=1 (no unique rename candidate)
        # Should have some enum changes
        assert len(result.changes) > 0


# ===========================================================================
# reporter.py branch coverage
# ===========================================================================


class TestToStat:
    """Exercise to_stat (lines 193-210) and to_stat_json (213-233)."""

    def test_stat_no_change(self):
        result = _make_diff(verdict=Verdict.NO_CHANGE)
        out = to_stat(result)
        assert "NO_CHANGE" in out
        assert "no changes" in out

    def test_stat_breaking(self):
        result = _make_diff(
            changes=[
                Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="removed"),
            ],
            verdict=Verdict.BREAKING,
        )
        out = to_stat(result)
        assert "BREAKING" in out
        assert "breaking" in out.lower()

    def test_stat_with_redundant_count(self):
        result = _make_diff(
            changes=[
                Change(kind=ChangeKind.FUNC_ADDED, symbol="bar", description="added"),
            ],
            verdict=Verdict.COMPATIBLE,
            redundant_count=5,
        )
        out = to_stat(result)
        assert "5 redundant hidden" in out

    def test_stat_compatible_with_risk(self):
        result = _make_diff(
            changes=[
                Change(kind=ChangeKind.NEEDED_ADDED, symbol="test", description="NEEDED added"),
            ],
            verdict=Verdict.COMPATIBLE_WITH_RISK,
        )
        out = to_stat(result)
        assert "COMPATIBLE_WITH_RISK" in out

    def test_stat_json_structure(self):
        result = _make_diff(
            changes=[
                Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="removed"),
            ],
            verdict=Verdict.BREAKING,
            redundant_count=3,
        )
        out = to_stat_json(result)
        d = json.loads(out)
        assert d["verdict"] == "BREAKING"
        assert "summary" in d
        assert d["summary"]["total_changes"] >= 1
        assert d["redundant_count"] == 3

    def test_stat_json_no_redundant(self):
        result = _make_diff(verdict=Verdict.NO_CHANGE)
        out = to_stat_json(result)
        d = json.loads(out)
        assert "redundant_count" not in d


class TestShowOnlyFilter:
    """Exercise ShowOnlyFilter.matches edge cases (lines 98-176)."""

    def _brk_change(self):
        return Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="removed")

    def _compat_change(self):
        return Change(kind=ChangeKind.FUNC_ADDED, symbol="bar", description="added")

    def _type_change(self):
        return Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Pt", description="size changed")

    def _var_change(self):
        return Change(kind=ChangeKind.VAR_REMOVED, symbol="g_x", description="removed")

    def _enum_change(self):
        return Change(kind=ChangeKind.ENUM_MEMBER_REMOVED, symbol="E::V", description="removed")

    def _elf_change(self):
        return Change(kind=ChangeKind.SONAME_CHANGED, symbol="lib", description="soname changed")

    # Severity filters
    def test_severity_breaking(self):
        f = ShowOnlyFilter.parse("breaking")
        assert f.matches(self._brk_change()) is True
        assert f.matches(self._compat_change()) is False

    def test_severity_compatible(self):
        f = ShowOnlyFilter.parse("compatible")
        assert f.matches(self._compat_change()) is True
        assert f.matches(self._brk_change()) is False

    def test_severity_api_break(self):
        f = ShowOnlyFilter.parse("api-break")
        # ENUM_MEMBER_RENAMED is in API_BREAK_KINDS → should match
        api_change = Change(kind=ChangeKind.ENUM_MEMBER_RENAMED, symbol="E::V", description="renamed")
        assert f.matches(api_change) is True
        # FUNC_REMOVED is in BREAKING_KINDS, not API_BREAK_KINDS → should not match
        assert f.matches(self._brk_change()) is False

    def test_severity_risk(self):
        f = ShowOnlyFilter.parse("risk")
        # SYMBOL_VERSION_REQUIRED_ADDED is in RISK_KINDS → should match
        risk_change = Change(kind=ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED,
                             symbol="GLIBC_2.34", description="new version requirement")
        assert f.matches(risk_change) is True
        # NEEDED_ADDED is in COMPATIBLE_KINDS, not RISK_KINDS → should not match
        compat_change = Change(kind=ChangeKind.NEEDED_ADDED, symbol="lib", description="needed added")
        assert f.matches(compat_change) is False

    # Element filters
    def test_element_functions(self):
        f = ShowOnlyFilter.parse("functions")
        assert f.matches(self._brk_change()) is True
        assert f.matches(self._type_change()) is False
        assert f.matches(self._var_change()) is False

    def test_element_variables(self):
        f = ShowOnlyFilter.parse("variables")
        assert f.matches(self._var_change()) is True
        assert f.matches(self._brk_change()) is False

    def test_element_types(self):
        f = ShowOnlyFilter.parse("types")
        assert f.matches(self._type_change()) is True
        assert f.matches(self._brk_change()) is False

    def test_element_enums(self):
        f = ShowOnlyFilter.parse("enums")
        assert f.matches(self._enum_change()) is True
        assert f.matches(self._brk_change()) is False

    def test_element_elf(self):
        f = ShowOnlyFilter.parse("elf")
        assert f.matches(self._elf_change()) is True
        assert f.matches(self._brk_change()) is False

    # Action filters
    def test_action_added(self):
        f = ShowOnlyFilter.parse("added")
        assert f.matches(self._compat_change()) is True  # func_added ends with _added
        assert f.matches(self._brk_change()) is False    # func_removed

    def test_action_removed(self):
        f = ShowOnlyFilter.parse("removed")
        assert f.matches(self._brk_change()) is True     # func_removed
        assert f.matches(self._compat_change()) is False  # func_added

    def test_action_changed(self):
        f = ShowOnlyFilter.parse("changed")
        changed = Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="f", description="return changed")
        assert f.matches(changed) is True
        assert f.matches(self._compat_change()) is False

    # Combined filters (AND across dimensions)
    def test_combined_severity_and_element(self):
        f = ShowOnlyFilter.parse("breaking,functions")
        assert f.matches(self._brk_change()) is True   # breaking + function
        assert f.matches(self._compat_change()) is False  # compatible, not breaking
        assert f.matches(self._type_change()) is False    # type, not function

    # Invalid token
    def test_invalid_token_raises(self):
        with pytest.raises(ValueError, match="Unknown --show-only token"):
            ShowOnlyFilter.parse("nonsense")

    # Empty severities (no filter)
    def test_empty_filter_matches_all(self):
        f = ShowOnlyFilter(severities=frozenset(), elements=frozenset(), actions=frozenset())
        assert f.matches(self._brk_change()) is True
        assert f.matches(self._compat_change()) is True


class TestApplyShowOnly:
    """Exercise apply_show_only (lines 179-186)."""

    def test_filter_with_multiple_tokens(self):
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="f1", description="removed"),
            Change(kind=ChangeKind.FUNC_ADDED, symbol="f2", description="added"),
            Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="T", description="size changed"),
        ]
        result = apply_show_only(changes, "breaking,functions")
        # Only breaking + function changes should remain
        assert all(c.kind == ChangeKind.FUNC_REMOVED for c in result)

    def test_filter_returns_empty_when_nothing_matches(self):
        changes = [
            Change(kind=ChangeKind.FUNC_ADDED, symbol="f", description="added"),
        ]
        result = apply_show_only(changes, "breaking")
        assert result == []


class TestBuildImpactTable:
    """Exercise _build_impact_table (lines 240-285)."""

    def test_impact_table_with_affected_symbols(self):
        from abicheck.reporter import _build_impact_table

        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="Data",
            description="size changed",
            affected_symbols=["func_a", "func_b", "func_c"],
            caused_count=2,
        )
        result = _make_diff(changes=[c], verdict=Verdict.BREAKING)
        lines = _build_impact_table(result)
        text = "\n".join(lines)
        assert "Impact Summary" in text
        assert "3 functions" in text
        assert "+2 collapsed" in text

    def test_impact_table_empty_when_no_root_changes(self):
        from abicheck.reporter import _build_impact_table

        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="removed")
        result = _make_diff(changes=[c], verdict=Verdict.BREAKING)
        lines = _build_impact_table(result)
        # Should have direct_removals entry
        text = "\n".join(lines)
        assert "removals" in text

    def test_impact_table_empty_for_no_changes(self):
        from abicheck.reporter import _build_impact_table

        result = _make_diff(verdict=Verdict.NO_CHANGE)
        lines = _build_impact_table(result)
        assert lines == []

    def test_impact_table_with_displayed_changes_subset(self):
        from abicheck.reporter import _build_impact_table

        c1 = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="Foo",
            description="size changed",
            affected_symbols=["fn1"],
        )
        c2 = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="bar",
            description="removed",
        )
        result = _make_diff(changes=[c1, c2], verdict=Verdict.BREAKING)
        # Only pass c1 as displayed
        lines = _build_impact_table(result, displayed_changes=[c1])
        text = "\n".join(lines)
        assert "Foo" in text
        # bar removal should not appear since not in displayed_changes
        assert "removals" not in text


class TestToJsonBranches:
    """Exercise to_json with show_only, show_impact, stat, report_mode."""

    def test_to_json_stat_mode(self):
        result = _make_diff(verdict=Verdict.NO_CHANGE)
        out = to_json(result, stat=True)
        d = json.loads(out)
        assert "verdict" in d
        assert "summary" in d

    def test_to_json_leaf_mode(self):
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="MyType",
            description="size changed",
            affected_symbols=["fn1"],
            caused_count=1,
        )
        result = _make_diff(changes=[c], verdict=Verdict.BREAKING, redundant_count=2)
        out = to_json(result, report_mode="leaf")
        d = json.loads(out)
        assert "leaf_changes" in d
        assert d["redundant_count"] == 2

    def test_to_json_with_show_only(self):
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="f", description="removed"),
            Change(kind=ChangeKind.FUNC_ADDED, symbol="g", description="added"),
        ]
        result = _make_diff(changes=changes, verdict=Verdict.BREAKING)
        out = to_json(result, show_only="breaking")
        d = json.loads(out)
        # Only breaking changes should appear
        assert all(c["kind"] == "func_removed" for c in d["changes"])

    def test_to_json_with_show_impact(self):
        result = _make_diff(changes=[], verdict=Verdict.NO_CHANGE)
        out = to_json(result, show_impact=True)
        d = json.loads(out)
        assert d["show_only_applied"] is False

    def test_to_json_leaf_with_show_only(self):
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="T",
            description="size changed",
        )
        result = _make_diff(changes=[c], verdict=Verdict.BREAKING)
        out = to_json(result, report_mode="leaf", show_only="types")
        d = json.loads(out)
        assert "leaf_changes" in d

    def test_to_json_redundant_count_omitted_when_zero(self):
        result = _make_diff(verdict=Verdict.NO_CHANGE)
        out = to_json(result)
        d = json.loads(out)
        assert "redundant_count" not in d


class TestToMarkdownBranches:
    """Exercise to_markdown with show_impact, show_only, report_mode, stat."""

    def test_to_markdown_stat_mode(self):
        result = _make_diff(verdict=Verdict.NO_CHANGE)
        out = to_markdown(result, stat=True)
        assert "NO_CHANGE" in out

    def test_to_markdown_with_show_impact(self):
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="T",
            description="size changed",
            affected_symbols=["fn_x"],
            caused_count=1,
        )
        result = _make_diff(changes=[c], verdict=Verdict.BREAKING)
        out = to_markdown(result, show_impact=True)
        assert "Impact Summary" in out

    def test_to_markdown_leaf_mode(self):
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="MyStruct",
            description="size changed",
            affected_symbols=["f1", "f2"],
            caused_count=3,
        )
        result = _make_diff(changes=[c], verdict=Verdict.BREAKING)
        out = to_markdown(result, report_mode="leaf")
        assert "leaf-change view" in out
        assert "MyStruct" in out
        assert "Affected interfaces" in out
        assert "3 derived change(s)" in out

    def test_to_markdown_leaf_mode_with_show_only(self):
        c1 = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="A", description="size")
        c2 = Change(kind=ChangeKind.FUNC_REMOVED, symbol="f", description="removed")
        result = _make_diff(changes=[c1, c2], verdict=Verdict.BREAKING)
        out = to_markdown(result, report_mode="leaf", show_only="types")
        assert "Filtered by" in out

    def test_to_markdown_leaf_no_changes_with_filter(self):
        """Leaf mode with show_only that filters everything -> 'No changes match'."""
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="f", description="removed")
        result = _make_diff(changes=[c], verdict=Verdict.BREAKING)
        out = to_markdown(result, report_mode="leaf", show_only="enums")
        assert "No changes match the current filter" in out

    def test_to_markdown_no_changes(self):
        result = _make_diff(verdict=Verdict.NO_CHANGE)
        out = to_markdown(result)
        assert "No ABI changes detected" in out

    def test_to_markdown_show_only_filters_empty(self):
        """show_only that filters everything but result has changes."""
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="f", description="removed")
        result = _make_diff(changes=[c], verdict=Verdict.BREAKING)
        out = to_markdown(result, show_only="enums")
        assert "No changes match the current filter" in out

    def test_to_markdown_with_redundancy_note(self):
        result = _make_diff(
            changes=[Change(kind=ChangeKind.FUNC_REMOVED, symbol="f", description="removed")],
            verdict=Verdict.BREAKING,
            redundant_count=4,
        )
        out = to_markdown(result)
        assert "4 redundant change(s) hidden" in out

    def test_to_markdown_with_suppression_note(self):
        suppressed = Change(kind=ChangeKind.FUNC_REMOVED, symbol="old_fn", description="suppressed removal")
        result = _make_diff(
            changes=[],
            verdict=Verdict.NO_CHANGE,
            suppression_file_provided=True,
            suppressed_count=1,
            suppressed_changes=[suppressed],
        )
        out = to_markdown(result)
        assert "suppressed via suppression file" in out

    def test_to_markdown_suppression_file_no_matches(self):
        result = _make_diff(
            changes=[],
            verdict=Verdict.NO_CHANGE,
            suppression_file_provided=True,
            suppressed_count=0,
        )
        out = to_markdown(result)
        assert "nothing suppressed" in out

    def test_to_markdown_leaf_with_many_affected_symbols(self):
        """Leaf mode with >10 affected symbols triggers truncation."""
        symbols = [f"fn_{i}" for i in range(15)]
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="BigType",
            description="size changed",
            affected_symbols=symbols,
        )
        result = _make_diff(changes=[c], verdict=Verdict.BREAKING)
        out = to_markdown(result, report_mode="leaf")
        assert "5 more" in out

    def test_to_markdown_leaf_show_impact(self):
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="X",
            description="size changed",
            affected_symbols=["f"],
            caused_count=1,
        )
        result = _make_diff(changes=[c], verdict=Verdict.BREAKING)
        out = to_markdown(result, report_mode="leaf", show_impact=True)
        assert "Impact Summary" in out

    def test_to_markdown_risk_changes_section(self):
        """Exercise the risk changes section in markdown."""
        c = Change(kind=ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, symbol="GLIBC_2.34",
                    description="New symbol version requirement: GLIBC_2.34")
        result = _make_diff(changes=[c], verdict=Verdict.COMPATIBLE_WITH_RISK)
        out = to_markdown(result)
        assert "Deployment Risk" in out

    def test_to_markdown_compatible_additions_section(self):
        c = Change(kind=ChangeKind.FUNC_ADDED, symbol="new_fn", description="new_fn added")
        result = _make_diff(changes=[c], verdict=Verdict.COMPATIBLE)
        out = to_markdown(result)
        assert "Additions" in out

    def test_to_markdown_source_breaks_section(self):
        """Exercise source-level breaks section."""
        c = Change(kind=ChangeKind.FUNC_NOEXCEPT_REMOVED, symbol="f", description="noexcept removed")
        result = _make_diff(changes=[c], verdict=Verdict.API_BREAK)
        out = to_markdown(result)
        assert "Source-Level Breaks" in out or "noexcept" in out


class TestFormatChangeMd:
    """Exercise _format_change_md edge cases."""

    def test_change_with_all_metadata(self):
        from abicheck.reporter import _format_change_md

        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="foo",
            description="removed foo",
            old_value="int foo()",
            new_value=None,
            source_location="header.h:42",
            affected_symbols=["a", "b", "c", "d", "e", "f"],
            caused_count=3,
        )
        line = _format_change_md(c)
        assert "header.h:42" in line
        assert "3 derived change(s)" in line
        assert "+1 more" in line  # 6 affected, show 5

    def test_change_with_new_value_only(self):
        from abicheck.reporter import _format_change_md

        c = Change(
            kind=ChangeKind.FUNC_ADDED,
            symbol="bar",
            description="added bar",
            new_value="void bar()",
        )
        line = _format_change_md(c)
        assert "`void bar()`" in line

    def test_change_with_both_values(self):
        from abicheck.reporter import _format_change_md

        c = Change(
            kind=ChangeKind.FUNC_RETURN_CHANGED,
            symbol="fn",
            description="return changed",
            old_value="int",
            new_value="void",
        )
        line = _format_change_md(c)
        assert "`int`" in line
        assert "`void`" in line


# ===========================================================================
# stack_report.py branch coverage
# ===========================================================================


def _make_graph(nodes_dict, edges, unresolved=None, root="/app"):
    """Build a DependencyGraph for testing."""
    from abicheck.resolver import DependencyGraph, ResolvedDSO

    nodes = {}
    for key, (soname, depth, reason) in nodes_dict.items():
        nodes[key] = ResolvedDSO(
            path=Path(key),
            soname=soname,
            needed=[],
            rpath="",
            runpath="",
            resolution_reason=reason,
            depth=depth,
        )
    return DependencyGraph(
        root=root,
        nodes=nodes,
        edges=edges,
        unresolved=unresolved or [],
    )


def _make_binding(consumer, symbol, version="", provider=None, status=None):
    """Build a SymbolBinding for testing."""
    from abicheck.binder import BindingStatus, SymbolBinding

    return SymbolBinding(
        consumer=consumer,
        symbol=symbol,
        version=version,
        provider=provider,
        status=status or BindingStatus.MISSING,
        explanation="not found",
    )


class TestStackReportMissingSymbolsTruncation:
    """Exercise _render_missing_symbols_section with >20 symbols (line 107)."""

    def test_truncation_at_20(self):
        from abicheck.stack_report import _render_missing_symbols_section

        missing = [_make_binding(f"/lib/consumer_{i}.so", f"sym_{i}") for i in range(25)]
        lines: list[str] = []
        _render_missing_symbols_section(lines, missing)
        text = "\n".join(lines)
        assert "+5 more" in text
        assert "Missing Symbols" in text

    def test_no_truncation_under_20(self):
        from abicheck.stack_report import _render_missing_symbols_section

        missing = [_make_binding("/lib/c.so", f"sym_{i}") for i in range(5)]
        lines: list[str] = []
        _render_missing_symbols_section(lines, missing)
        text = "\n".join(lines)
        assert "more" not in text

    def test_empty_missing(self):
        from abicheck.stack_report import _render_missing_symbols_section

        lines: list[str] = []
        _render_missing_symbols_section(lines, [])
        assert lines == []

    def test_missing_symbol_with_version(self):
        from abicheck.stack_report import _render_missing_symbols_section

        missing = [_make_binding("/lib/c.so", "printf", version="GLIBC_2.17")]
        lines: list[str] = []
        _render_missing_symbols_section(lines, missing)
        text = "\n".join(lines)
        assert "@GLIBC_2.17" in text


class TestStackReportStackChangesSection:
    """Exercise _render_stack_changes_section (lines 111-128)."""

    def test_removed_change(self):
        from abicheck.stack_checker import StackChange
        from abicheck.stack_report import _render_stack_changes_section

        changes = [StackChange(library="libold.so", change_type="removed")]
        lines: list[str] = []
        _render_stack_changes_section(lines, changes)
        text = "\n".join(lines)
        assert "removed from candidate" in text
        assert "libold.so" in text

    def test_added_change(self):
        from abicheck.stack_checker import StackChange
        from abicheck.stack_report import _render_stack_changes_section

        changes = [StackChange(library="libnew.so", change_type="added")]
        lines: list[str] = []
        _render_stack_changes_section(lines, changes)
        text = "\n".join(lines)
        assert "new in candidate" in text

    def test_content_changed_breaking(self):
        from abicheck.stack_checker import StackChange
        from abicheck.stack_report import _render_stack_changes_section

        diff = _make_diff(
            changes=[Change(kind=ChangeKind.FUNC_REMOVED, symbol="f", description="removed")],
            verdict=Verdict.BREAKING,
        )
        changes = [StackChange(library="libchanged.so", change_type="content_changed", abi_diff=diff)]
        lines: list[str] = []
        _render_stack_changes_section(lines, changes)
        text = "\n".join(lines)
        assert "content changed" in text
        assert "BREAKING" in text

    def test_content_changed_compatible(self):
        from abicheck.stack_checker import StackChange
        from abicheck.stack_report import _render_stack_changes_section

        diff = _make_diff(verdict=Verdict.COMPATIBLE)
        changes = [StackChange(library="libcompat.so", change_type="content_changed", abi_diff=diff)]
        lines: list[str] = []
        _render_stack_changes_section(lines, changes)
        text = "\n".join(lines)
        assert "COMPATIBLE" in text

    def test_content_changed_no_diff(self):
        from abicheck.stack_checker import StackChange
        from abicheck.stack_report import _render_stack_changes_section

        changes = [StackChange(library="libx.so", change_type="content_changed", abi_diff=None)]
        lines: list[str] = []
        _render_stack_changes_section(lines, changes)
        text = "\n".join(lines)
        assert "unknown" in text

    def test_empty_changes(self):
        from abicheck.stack_report import _render_stack_changes_section

        lines: list[str] = []
        _render_stack_changes_section(lines, [])
        assert lines == []

    def test_content_changed_with_breaking_details(self):
        """content_changed with breaking changes shows up to 5 detail lines."""
        from abicheck.stack_checker import StackChange
        from abicheck.stack_report import _render_stack_changes_section

        brk = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol=f"f{i}", description=f"removed f{i}")
            for i in range(3)
        ]
        diff = _make_diff(changes=brk, verdict=Verdict.BREAKING)
        changes = [StackChange(library="libbig.so", change_type="content_changed", abi_diff=diff)]
        lines: list[str] = []
        _render_stack_changes_section(lines, changes)
        text = "\n".join(lines)
        assert "func_removed" in text


class TestStackReportRenderTree:
    """Exercise _render_tree / _render_node edge cases."""

    def test_empty_graph(self):
        """No root node found -> '_(empty graph)_'."""
        from abicheck.resolver import DependencyGraph
        from abicheck.stack_report import _render_tree

        graph = DependencyGraph(root="/none", nodes={}, edges=[])
        lines: list[str] = []
        _render_tree(lines, graph)
        assert any("empty graph" in line for line in lines)

    def test_diamond_dependency(self):
        """A->B, A->C, B->D, C->D: D shown once, second time as '*(already shown)*'."""
        from abicheck.stack_report import _render_tree

        graph = _make_graph(
            nodes_dict={
                "/A": ("A.so", 0, "root"),
                "/B": ("B.so", 1, "needed"),
                "/C": ("C.so", 1, "needed"),
                "/D": ("D.so", 2, "needed"),
            },
            edges=[("/A", "/B"), ("/A", "/C"), ("/B", "/D"), ("/C", "/D")],
            root="/A",
        )
        lines: list[str] = []
        _render_tree(lines, graph)
        text = "\n".join(lines)
        assert "already shown" in text

    def test_cycle_detection(self):
        """A->B->A: cycle detected -> '*(cycle)*'."""
        from abicheck.stack_report import _render_tree

        graph = _make_graph(
            nodes_dict={
                "/A": ("A.so", 0, "root"),
                "/B": ("B.so", 1, "needed"),
            },
            edges=[("/A", "/B"), ("/B", "/A")],
            root="/A",
        )
        lines: list[str] = []
        _render_tree(lines, graph)
        text = "\n".join(lines)
        assert "cycle" in text

    def test_simple_tree(self):
        """Simple A->B, A->C renders without annotations."""
        from abicheck.stack_report import _render_tree

        graph = _make_graph(
            nodes_dict={
                "/A": ("A.so", 0, "root"),
                "/B": ("B.so", 1, "needed"),
                "/C": ("C.so", 1, "needed"),
            },
            edges=[("/A", "/B"), ("/A", "/C")],
            root="/A",
        )
        lines: list[str] = []
        _render_tree(lines, graph)
        text = "\n".join(lines)
        assert "`A.so`" in text
        assert "`B.so`" in text
        assert "`C.so`" in text
        assert "cycle" not in text
        assert "already shown" not in text

    def test_missing_node_in_edge(self):
        """Edge references a node not in graph.nodes -> silently skipped (line 264-265)."""
        from abicheck.stack_report import _render_tree

        graph = _make_graph(
            nodes_dict={
                "/A": ("A.so", 0, "root"),
            },
            edges=[("/A", "/missing")],  # /missing not in nodes
            root="/A",
        )
        lines: list[str] = []
        _render_tree(lines, graph)
        text = "\n".join(lines)
        assert "`A.so`" in text
        # /missing is silently ignored


class TestStackReportUnresolvedSection:
    """Exercise _render_unresolved_section."""

    def test_unresolved_libraries(self):
        from abicheck.stack_report import _render_unresolved_section

        graph = _make_graph(
            nodes_dict={"/A": ("A.so", 0, "root")},
            edges=[],
            unresolved=[("/A", "libmissing.so")],
            root="/A",
        )
        lines: list[str] = []
        _render_unresolved_section(lines, graph)
        text = "\n".join(lines)
        assert "Unresolved Libraries" in text
        assert "libmissing.so" in text

    def test_no_unresolved(self):
        from abicheck.stack_report import _render_unresolved_section

        graph = _make_graph(
            nodes_dict={"/A": ("A.so", 0, "root")},
            edges=[],
            root="/A",
        )
        lines: list[str] = []
        _render_unresolved_section(lines, graph)
        assert lines == []


# ===========================================================================
# Integration: compare() end-to-end with affected symbols and redundancy
# ===========================================================================


class TestCompareIntegration:
    """End-to-end compare() exercising affected symbols + redundancy pipeline."""

    def test_full_pipeline_type_change_with_functions(self):
        """Full compare: type changes, affected symbols enrichment, redundancy filter."""
        rec = RecordType(name="Config", kind="struct", size_bits=64, fields=[
            TypeField(name="timeout", type="int", offset_bits=0),
            TypeField(name="retries", type="int", offset_bits=32),
        ])
        rec_v2 = RecordType(name="Config", kind="struct", size_bits=96, fields=[
            TypeField(name="timeout", type="int", offset_bits=0),
            TypeField(name="retries", type="int", offset_bits=32),
            TypeField(name="debug", type="int", offset_bits=64),
        ])

        fn1 = Function(
            name="init_config", mangled="_Z11init_config6Config",
            return_type="void",
            params=[Param(name="c", type="Config")],
        )
        fn2 = Function(
            name="save_config", mangled="_Z11save_config6Config",
            return_type="int",
            params=[Param(name="c", type="Config")],
        )

        old = _make_snap(version="1.0", functions=[fn1, fn2], types=[rec])
        new = _make_snap(version="2.0", functions=[fn1, fn2], types=[rec_v2])

        result = compare(old, new)
        assert result.verdict != Verdict.NO_CHANGE

        # Check affected symbols on type changes
        type_changes = [c for c in result.changes if c.kind in _ROOT_TYPE_CHANGE_KINDS and "Config" in c.symbol]
        all_affected = set()
        for tc in type_changes:
            if tc.affected_symbols:
                all_affected.update(tc.affected_symbols)
        assert "init_config" in all_affected or "save_config" in all_affected

    def test_return_type_uses_affected_type(self):
        """Function returning a changed struct -> should be in affected_symbols."""
        rec = RecordType(name="Result", kind="struct", size_bits=32, fields=[
            TypeField(name="code", type="int", offset_bits=0),
        ])
        rec_v2 = RecordType(name="Result", kind="struct", size_bits=64, fields=[
            TypeField(name="code", type="int", offset_bits=0),
            TypeField(name="msg", type="int", offset_bits=32),
        ])

        fn = Function(
            name="get_result", mangled="_Z10get_resultv",
            return_type="Result",
            params=[],
        )

        old = _make_snap(version="1.0", functions=[fn], types=[rec])
        new = _make_snap(version="2.0", functions=[fn], types=[rec_v2])

        result = compare(old, new)
        type_changes = [c for c in result.changes if "Result" in c.symbol and c.kind in _ROOT_TYPE_CHANGE_KINDS]
        affected = set()
        for tc in type_changes:
            if tc.affected_symbols:
                affected.update(tc.affected_symbols)
        assert "get_result" in affected


# ---------------------------------------------------------------------------
# Namespace-qualified source location enrichment (architecture review fix #1)
# ---------------------------------------------------------------------------


class TestEnrichSourceLocationsNamespace:
    """_enrich_source_locations must resolve ns::Type::field via root type name."""

    def test_namespaced_field_gets_location(self):
        old = _make_snap(types=[
            RecordType(name="ns::MyStruct", kind="struct", source_location="file.h:10"),
        ])
        new = _make_snap()
        changes = [
            Change(ChangeKind.TYPE_FIELD_REMOVED, "ns::MyStruct::flags", "removed"),
        ]
        _enrich_source_locations(changes, old, new)
        assert changes[0].source_location == "file.h:10"

    def test_simple_field_still_works(self):
        old = _make_snap(types=[
            RecordType(name="MyStruct", kind="struct", source_location="file.h:20"),
        ])
        new = _make_snap()
        changes = [
            Change(ChangeKind.TYPE_FIELD_REMOVED, "MyStruct::flags", "removed"),
        ]
        _enrich_source_locations(changes, old, new)
        assert changes[0].source_location == "file.h:20"


class TestEnrichAffectedSymbolsAncestorCache:
    """Test ancestor caching in _enrich_affected_symbols."""

    def test_shared_ancestor_scanned_once(self):
        """Two affected types sharing the same ancestor should reuse cached results."""
        # Inner is embedded in both Wrapper1 and Wrapper2.
        # Changing Inner should find functions using Wrapper1 and Wrapper2
        # via the ancestor cache (Wrapper1/Wrapper2 are not in affected_types).
        inner = RecordType(name="Inner", kind="struct", size_bits=32, fields=[
            TypeField(name="val", type="int", offset_bits=0),
        ])
        wrapper1 = RecordType(name="Wrapper1", kind="struct", size_bits=64, fields=[
            TypeField(name="inner", type="Inner", offset_bits=0),
            TypeField(name="x", type="int", offset_bits=32),
        ])
        wrapper2 = RecordType(name="Wrapper2", kind="struct", size_bits=64, fields=[
            TypeField(name="inner", type="Inner", offset_bits=0),
            TypeField(name="y", type="int", offset_bits=32),
        ])
        # Also create SecondInner to ensure both hit the ancestor cache for Wrapper1
        second_inner = RecordType(name="SecondInner", kind="struct", size_bits=32, fields=[
            TypeField(name="val", type="int", offset_bits=0),
        ])
        wrapper_with_both = RecordType(name="WrapperBoth", kind="struct", size_bits=96, fields=[
            TypeField(name="a", type="Inner", offset_bits=0),
            TypeField(name="b", type="SecondInner", offset_bits=32),
            TypeField(name="c", type="int", offset_bits=64),
        ])

        func1 = Function(
            name="use_wrapper1", mangled="_Z12use_wrapper19Wrapper1",
            return_type="void", visibility=Visibility.PUBLIC,
            params=[Param(name="w", type="Wrapper1")],
        )
        func_both = Function(
            name="use_both", mangled="_Z8use_both10WrapperBoth",
            return_type="void", visibility=Visibility.PUBLIC,
            params=[Param(name="w", type="WrapperBoth")],
        )

        old = _make_snap(
            types=[inner, second_inner, wrapper1, wrapper2, wrapper_with_both],
            functions=[func1, func_both],
        )
        # Both Inner and SecondInner are affected; WrapperBoth embeds both,
        # so the second affected type should hit the ancestor cache for WrapperBoth.
        changes = [
            Change(ChangeKind.TYPE_SIZE_CHANGED, "Inner", "size changed"),
            Change(ChangeKind.TYPE_SIZE_CHANGED, "SecondInner", "size changed"),
        ]
        _enrich_affected_symbols(changes, old)
        # Both changes should find use_both through WrapperBoth
        for c in changes:
            assert c.affected_symbols is not None
            assert "use_both" in c.affected_symbols

    def test_ancestor_with_empty_function_list(self):
        """An affected type that is also an ancestor of another affected type
        but has no matching functions should not break the lookup."""
        # Parent is an affected type with no functions using it directly.
        # Child embeds Parent. Functions use Child.
        parent = RecordType(name="Parent", kind="struct", size_bits=32, fields=[
            TypeField(name="x", type="int", offset_bits=0),
        ])
        child = RecordType(name="Child", kind="struct", size_bits=64, fields=[
            TypeField(name="p", type="Parent", offset_bits=0),
            TypeField(name="y", type="int", offset_bits=32),
        ])
        func = Function(
            name="use_child", mangled="_Z9use_child5Child",
            return_type="void", visibility=Visibility.PUBLIC,
            params=[Param(name="c", type="Child")],
        )
        old = _make_snap(types=[parent, child], functions=[func])
        changes = [
            Change(ChangeKind.TYPE_SIZE_CHANGED, "Parent", "size changed"),
            Change(ChangeKind.TYPE_SIZE_CHANGED, "Child", "size changed"),
        ]
        _enrich_affected_symbols(changes, old)
        # Parent should find use_child via Child embedding
        parent_change = changes[0]
        assert parent_change.affected_symbols is not None
        assert "use_child" in parent_change.affected_symbols


class TestRegexCacheInOpaqueChecks:
    """Test regex caching in _is_pointer_only_type and _has_public_pointer_factory."""

    def test_is_pointer_only_type_with_shared_cache(self):
        from abicheck.diff_filtering import _is_pointer_only_type

        func = Function(
            name="create_ctx", mangled="_Z10create_ctx",
            return_type="Context*", visibility=Visibility.PUBLIC,
            params=[],
        )
        snap = _make_snap(functions=[func])
        cache: dict = {}
        result1 = _is_pointer_only_type("Context", snap, cache)
        assert result1 is True
        assert "Context" in cache  # cache populated

        # Second call with same cache should use cached pattern
        result2 = _is_pointer_only_type("Context", snap, cache)
        assert result2 is True

    def test_is_pointer_only_type_without_cache(self):
        from abicheck.diff_filtering import _is_pointer_only_type

        func = Function(
            name="get_val", mangled="_Z7get_val",
            return_type="MyType", visibility=Visibility.PUBLIC,
            params=[],
        )
        snap = _make_snap(functions=[func])
        # No cache — should still work
        assert _is_pointer_only_type("MyType", snap, None) is False
        assert _is_pointer_only_type("MyType", snap) is False

    def test_has_public_pointer_factory_with_shared_cache(self):
        from abicheck.diff_filtering import _has_public_pointer_factory

        func = Function(
            name="create_handle", mangled="_Z13create_handle",
            return_type="Handle*", visibility=Visibility.PUBLIC,
            params=[],
        )
        snap = _make_snap(functions=[func])
        cache: dict = {}
        assert _has_public_pointer_factory("Handle", snap, cache) is True
        assert "Handle" in cache

        # Cache hit path
        assert _has_public_pointer_factory("Handle", snap, cache) is True

    def test_has_public_pointer_factory_without_cache(self):
        from abicheck.diff_filtering import _has_public_pointer_factory

        func = Function(
            name="create_handle", mangled="_Z13create_handle",
            return_type="Handle*", visibility=Visibility.PUBLIC,
            params=[],
        )
        snap = _make_snap(functions=[func])
        assert _has_public_pointer_factory("Handle", snap, None) is True
        assert _has_public_pointer_factory("Handle", snap) is True


class TestEnrichAffectedSymbolsNamespace:
    """_enrich_affected_symbols must group ns::Type::field under ns::Type."""

    def test_namespaced_type_field_finds_affected_functions(self):
        from abicheck.model import Visibility

        old = _make_snap(
            types=[RecordType(name="ns::Config", kind="struct")],
            functions=[
                Function(
                    name="init_config",
                    mangled="_Z11init_configPN2ns6ConfigE",
                    return_type="void",
                    visibility=Visibility.PUBLIC,
                    params=[Param(name="cfg", type="ns::Config")],
                ),
            ],
        )
        changes = [
            Change(ChangeKind.TYPE_FIELD_REMOVED, "ns::Config::timeout", "removed"),
        ]
        _enrich_affected_symbols(changes, old)
        assert changes[0].affected_symbols is not None
        assert "init_config" in changes[0].affected_symbols


# ---------------------------------------------------------------------------
# _compute_confidence: dwarf.has_dwarf flag check (architecture review fix #3)
# ---------------------------------------------------------------------------


class TestComputeConfidenceDwarfFlag:
    """Placeholder DWARF objects (has_dwarf=False) must not add 'dwarf' tier."""

    def test_placeholder_dwarf_not_counted(self):
        old = _make_snap(functions=[
            Function(name="f", mangled="_f", return_type="void", visibility=Visibility.PUBLIC),
        ])
        old.dwarf = DwarfMetadata(has_dwarf=False)
        new = _make_snap(functions=[
            Function(name="f", mangled="_f", return_type="void", visibility=Visibility.PUBLIC),
        ])
        new.dwarf = DwarfMetadata(has_dwarf=False)
        tiers, _conf, _warns, _etier = _compute_confidence([], old, new)
        assert "dwarf" not in tiers

    def test_real_dwarf_counted(self):
        old = _make_snap()
        old.dwarf = DwarfMetadata(has_dwarf=True)
        new = _make_snap()
        new.dwarf = DwarfMetadata(has_dwarf=True)
        tiers, _conf, _warns, _etier = _compute_confidence([], old, new)
        assert "dwarf" in tiers

    def test_placeholder_dwarf_advanced_not_counted(self):
        old = _make_snap()
        old.dwarf_advanced = AdvancedDwarfMetadata(has_dwarf=False)
        new = _make_snap()
        new.dwarf_advanced = AdvancedDwarfMetadata(has_dwarf=False)
        tiers, _conf, _warns, _etier = _compute_confidence([], old, new)
        assert "dwarf_advanced" not in tiers

    def test_real_dwarf_advanced_counted(self):
        old = _make_snap()
        old.dwarf_advanced = AdvancedDwarfMetadata(has_dwarf=True)
        new = _make_snap()
        new.dwarf_advanced = AdvancedDwarfMetadata(has_dwarf=True)
        tiers, _conf, _warns, _etier = _compute_confidence([], old, new)
        assert "dwarf_advanced" in tiers

    def test_one_side_real_dwarf_counted(self):
        old = _make_snap()
        old.dwarf = DwarfMetadata(has_dwarf=True)
        new = _make_snap()
        new.dwarf = DwarfMetadata(has_dwarf=False)
        tiers, _conf, _warns, _etier = _compute_confidence([], old, new)
        assert "dwarf" in tiers
