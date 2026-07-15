# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Tests for ADR-004: Report Filtering, Deduplication, and Leaf-Change Mode."""

from __future__ import annotations

import json

import pytest

from abicheck.checker import (
    Change,
    ChangeKind,
    DiffResult,
    Verdict,
    _filter_redundant,
    _match_root_type,
)
from abicheck.diff_filtering import (
    _filter_reserved_field_renames,
    _root_type_name,
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

def _make_result(
    changes: list[Change] | None = None,
    redundant_changes: list[Change] | None = None,
    verdict: Verdict = Verdict.BREAKING,
    policy: str = "strict_abi",
) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so",
        changes=changes or [],
        verdict=verdict,
        policy=policy,
        redundant_changes=redundant_changes or [],
        redundant_count=len(redundant_changes) if redundant_changes else 0,
    )


# ---------------------------------------------------------------------------
# _filter_redundant tests
# ---------------------------------------------------------------------------

class TestFilterRedundant:
    def test_no_root_types_returns_all(self):
        """When no root type changes exist, all changes are kept."""
        changes = [
            Change(ChangeKind.FUNC_REMOVED, "foo", "removed"),
            Change(ChangeKind.FUNC_ADDED, "bar", "added"),
        ]
        kept, redundant = _filter_redundant(changes)
        assert len(kept) == 2
        assert len(redundant) == 0

    def test_type_change_causes_func_params_redundancy(self):
        """FUNC_PARAMS_CHANGED referencing a root type is redundant."""
        changes = [
            Change(ChangeKind.TYPE_SIZE_CHANGED, "Config", "size changed from 64 to 72 bytes"),
            Change(
                ChangeKind.FUNC_PARAMS_CHANGED, "config_init",
                "parameter type changed in config_init(Config*)",
                old_value="Config (64 bytes)",
                new_value="Config (72 bytes)",
            ),
        ]
        kept, redundant = _filter_redundant(changes)
        assert len(kept) == 1
        assert kept[0].kind == ChangeKind.TYPE_SIZE_CHANGED
        assert len(redundant) == 1
        assert redundant[0].kind == ChangeKind.FUNC_PARAMS_CHANGED
        assert redundant[0].caused_by_type == "Config"

    def test_root_annotated_with_caused_count(self):
        """Root change gets caused_count and derived symbols."""
        changes = [
            Change(ChangeKind.TYPE_SIZE_CHANGED, "Point", "size changed"),
            Change(
                ChangeKind.FUNC_PARAMS_CHANGED, "draw",
                "parameter type changed: Point",
                old_value="Point (8 bytes)",
            ),
            Change(
                ChangeKind.FUNC_RETURN_CHANGED, "get_point",
                "return type changed: Point",
                new_value="Point (16 bytes)",
            ),
        ]
        kept, redundant = _filter_redundant(changes)
        root = kept[0]
        assert root.kind == ChangeKind.TYPE_SIZE_CHANGED
        assert root.caused_count == 2
        assert len(redundant) == 2

    def test_func_removed_always_independent(self):
        """FUNC_REMOVED is never redundant even if type changed."""
        changes = [
            Change(ChangeKind.TYPE_SIZE_CHANGED, "Config", "size changed"),
            Change(ChangeKind.FUNC_REMOVED, "config_init", "removed", old_value="Config*"),
        ]
        kept, redundant = _filter_redundant(changes)
        assert len(kept) == 2
        assert len(redundant) == 0

    def test_unrelated_func_params_not_redundant(self):
        """FUNC_PARAMS_CHANGED not referencing root type is kept."""
        changes = [
            Change(ChangeKind.TYPE_SIZE_CHANGED, "Config", "size changed"),
            Change(
                ChangeKind.FUNC_PARAMS_CHANGED, "process",
                "parameter count changed",
                old_value="int",
                new_value="int, int",
            ),
        ]
        kept, redundant = _filter_redundant(changes)
        assert len(kept) == 2
        assert len(redundant) == 0

    def test_nested_type_field_type_changed_redundancy(self):
        """TYPE_FIELD_TYPE_CHANGED referencing a root type is redundant."""
        changes = [
            Change(ChangeKind.TYPE_SIZE_CHANGED, "Point", "size changed"),
            Change(
                ChangeKind.TYPE_FIELD_TYPE_CHANGED, "Container::pos",
                "field type changed: Point",
                old_value="Point (8 bytes)",
                new_value="Point (16 bytes)",
            ),
        ]
        kept, redundant = _filter_redundant(changes)
        assert len(kept) == 1
        assert len(redundant) == 1

    def test_elf_changes_always_independent(self):
        """ELF-level changes are never redundant."""
        changes = [
            Change(ChangeKind.TYPE_SIZE_CHANGED, "Config", "size changed"),
            Change(ChangeKind.SONAME_CHANGED, "libfoo.so", "SONAME changed"),
            Change(ChangeKind.NEEDED_ADDED, "libbar.so", "new dep added"),
        ]
        kept, redundant = _filter_redundant(changes)
        assert len(kept) == 3
        assert len(redundant) == 0

    def test_var_type_changed_redundancy(self):
        """VAR_TYPE_CHANGED referencing a root type is redundant."""
        changes = [
            Change(ChangeKind.ENUM_MEMBER_REMOVED, "Status", "member removed"),
            Change(
                ChangeKind.VAR_TYPE_CHANGED, "current_status",
                "variable type changed: Status",
                old_value="Status",
                new_value="Status",
            ),
        ]
        kept, redundant = _filter_redundant(changes)
        assert len(kept) == 1
        assert len(redundant) == 1

    def test_qualified_symbol_matches_root(self):
        """Root type with :: qualifier matches base type name."""
        changes = [
            Change(ChangeKind.TYPE_FIELD_REMOVED, "Container::flags", "field removed"),
            Change(
                ChangeKind.FUNC_PARAMS_CHANGED, "process",
                "param type changed: Container",
                old_value="Container (64)",
            ),
        ]
        kept, redundant = _filter_redundant(changes)
        # Container::flags root type name is "Container"
        assert len(kept) == 1
        assert len(redundant) == 1


class TestMatchRootType:
    def test_matches_in_old_value(self):
        c = Change(ChangeKind.FUNC_PARAMS_CHANGED, "foo", "desc", old_value="Config*")
        assert _match_root_type(c, {"Config": Change(ChangeKind.TYPE_SIZE_CHANGED, "Config", "")}) == "Config"

    def test_matches_in_description(self):
        c = Change(ChangeKind.FUNC_RETURN_CHANGED, "bar", "return type changed: Config")
        assert _match_root_type(c, {"Config": Change(ChangeKind.TYPE_SIZE_CHANGED, "Config", "")}) == "Config"

    def test_no_match_returns_none(self):
        c = Change(ChangeKind.FUNC_PARAMS_CHANGED, "foo", "param count changed", old_value="int")
        assert _match_root_type(c, {"Config": Change(ChangeKind.TYPE_SIZE_CHANGED, "Config", "")}) is None

    def test_with_precompiled_patterns(self):
        """Pre-compiled patterns dict produces same results as without."""
        import re
        root_types = {"Config": Change(ChangeKind.TYPE_SIZE_CHANGED, "Config", "")}
        compiled = {
            name: re.compile(r'(?<![A-Za-z0-9_])' + re.escape(name) + r'(?![A-Za-z0-9_])')
            for name in root_types
        }
        c = Change(ChangeKind.FUNC_PARAMS_CHANGED, "foo", "desc", old_value="Config*")
        assert _match_root_type(c, root_types, compiled) == "Config"

    def test_without_compiled_patterns_fallback(self):
        """When compiled_patterns is None, function still works (fallback)."""
        c = Change(ChangeKind.FUNC_PARAMS_CHANGED, "foo", "desc", new_value="Config*")
        assert _match_root_type(c, {"Config": Change(ChangeKind.TYPE_SIZE_CHANGED, "Config", "")}, None) == "Config"

    def test_compiled_patterns_empty_dict(self):
        """Empty compiled_patterns dict triggers fallback compilation per type."""
        c = Change(ChangeKind.FUNC_RETURN_CHANGED, "bar", "return type changed: Config")
        # Empty dict — type_name not in compiled_patterns, should compile on the fly
        assert _match_root_type(c, {"Config": Change(ChangeKind.TYPE_SIZE_CHANGED, "Config", "")}, {}) == "Config"


# ---------------------------------------------------------------------------
# ShowOnlyFilter tests
# ---------------------------------------------------------------------------

class TestShowOnlyFilter:
    def test_parse_severity_tokens(self):
        f = ShowOnlyFilter.parse("breaking,api-break")
        assert f.severities == frozenset({"breaking", "api-break"})
        assert not f.elements
        assert not f.actions

    def test_parse_mixed_tokens(self):
        f = ShowOnlyFilter.parse("breaking,functions,removed")
        assert f.severities == frozenset({"breaking"})
        assert f.elements == frozenset({"functions"})
        assert f.actions == frozenset({"removed"})

    def test_parse_unknown_token_raises(self):
        with pytest.raises(ValueError, match="Unknown --show-only token"):
            ShowOnlyFilter.parse("bogus")

    def test_severity_filter_breaking(self):
        f = ShowOnlyFilter.parse("breaking")
        c_break = Change(ChangeKind.FUNC_REMOVED, "foo", "removed")
        c_compat = Change(ChangeKind.FUNC_ADDED, "bar", "added")
        assert f.matches(c_break)
        assert not f.matches(c_compat)

    def test_element_filter_functions(self):
        f = ShowOnlyFilter.parse("functions")
        c_func = Change(ChangeKind.FUNC_REMOVED, "foo", "removed")
        c_type = Change(ChangeKind.TYPE_SIZE_CHANGED, "T", "size changed")
        assert f.matches(c_func)
        assert not f.matches(c_type)

    def test_action_filter_removed(self):
        f = ShowOnlyFilter.parse("removed")
        c_removed = Change(ChangeKind.FUNC_REMOVED, "foo", "removed")
        c_added = Change(ChangeKind.FUNC_ADDED, "bar", "added")
        c_changed = Change(ChangeKind.FUNC_PARAMS_CHANGED, "baz", "changed")
        assert f.matches(c_removed)
        assert not f.matches(c_added)
        assert not f.matches(c_changed)

    def test_cross_dimension_and(self):
        """breaking AND functions → only breaking function changes."""
        f = ShowOnlyFilter.parse("breaking,functions")
        c_func_removed = Change(ChangeKind.FUNC_REMOVED, "foo", "removed")
        c_type_changed = Change(ChangeKind.TYPE_SIZE_CHANGED, "T", "size changed")
        c_func_added = Change(ChangeKind.FUNC_ADDED, "bar", "added")  # not breaking
        assert f.matches(c_func_removed)
        assert not f.matches(c_type_changed)
        assert not f.matches(c_func_added)

    def test_within_dimension_or(self):
        """types,enums → types OR enums."""
        f = ShowOnlyFilter.parse("types,enums")
        c_type = Change(ChangeKind.TYPE_SIZE_CHANGED, "T", "size")
        c_enum = Change(ChangeKind.ENUM_MEMBER_REMOVED, "E", "member")
        c_func = Change(ChangeKind.FUNC_REMOVED, "f", "removed")
        assert f.matches(c_type)
        assert f.matches(c_enum)
        assert not f.matches(c_func)


class TestApplyShowOnly:
    def test_filters_changes(self):
        changes = [
            Change(ChangeKind.FUNC_REMOVED, "foo", "removed"),
            Change(ChangeKind.FUNC_ADDED, "bar", "added"),
            Change(ChangeKind.TYPE_SIZE_CHANGED, "T", "size changed"),
        ]
        result = apply_show_only(changes, "functions")
        assert len(result) == 2
        assert all(c.kind.value.startswith("func_") for c in result)

    def test_kind_level_policy_override_respected_without_policy_file(self):
        """Without policy_file/kind_sets, a kind-level PolicyFile override is
        invisible to the severity dimension — verified defect (P0 finding 1):
        a func_removed demoted to COMPATIBLE by policy still counted as
        'breaking' for --show-only purposes."""
        from abicheck.policy_file import PolicyFile

        c = Change(ChangeKind.FUNC_REMOVED, "foo", "removed: foo")
        pf = PolicyFile(overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE})
        result = DiffResult(
            old_version="1.0", new_version="2.0", library="libtest.so",
            changes=[c], verdict=Verdict.COMPATIBLE, policy_file=pf,
        )

        # Without threading kind_sets/policy_file, the override is invisible.
        assert apply_show_only([c], "breaking") == [c]
        assert apply_show_only([c], "compatible") == []

        # Threading the effective kind_sets/policy_file (as every report
        # renderer now does) makes --show-only agree with the effective
        # verdict the rest of the report uses.
        kind_sets = result._effective_kind_sets()
        assert apply_show_only(
            [c], "breaking", kind_sets=kind_sets, policy_file=pf,
        ) == []
        assert apply_show_only(
            [c], "compatible", kind_sets=kind_sets, policy_file=pf,
        ) == [c]

    def test_per_finding_effective_verdict_still_respected(self):
        """A per-change effective_verdict override (A4 modulation / frozen
        namespace) keeps working once kind_sets/policy_file are threaded
        through, since severity.effective_verdict_for_change checks it
        before falling back to kind-level classification."""
        c = Change(ChangeKind.FUNC_REMOVED, "foo", "removed: foo")
        c.effective_verdict = Verdict.COMPATIBLE
        assert apply_show_only([c], "breaking") == []
        assert apply_show_only([c], "compatible") == [c]


class TestOperationForKind:
    """Codex review on #557: operation_for_kind() (shared by --show-only's
    added/removed/changed tokens and the JSON `operation` field, schema 2.3)
    only matched a fixed suffix list, misclassifying kinds that are still
    semantically an addition/removal but spelled differently."""

    def test_ordinary_added_removed_modified(self):
        from abicheck.reporter_markdown import operation_for_kind

        assert operation_for_kind("func_added") == "added"
        assert operation_for_kind("func_removed") == "removed"
        assert operation_for_kind("func_params_changed") == "modified"

    def test_non_suffix_addition_kind(self):
        from abicheck.reporter_markdown import operation_for_kind

        assert operation_for_kind("symbol_version_required_added_compat") == "added"

    def test_non_suffix_removal_kinds(self):
        from abicheck.reporter_markdown import operation_for_kind

        assert operation_for_kind("experimental_removed_without_replacement") == "removed"
        assert operation_for_kind("func_deleted_dwarf") == "removed"
        assert operation_for_kind("cpu_dispatch_isa_dropped") == "removed"

    def test_trait_change_on_persisting_entity_is_modified_not_removed(self):
        """A '_lost_*'/'_introduced' kind describes a property gained/lost on
        an entity that still exists — that's "modified", not "added"/
        "removed" (which name the entity itself appearing/disappearing)."""
        from abicheck.reporter_markdown import operation_for_kind

        assert operation_for_kind("field_lost_const") == "modified"
        assert operation_for_kind("func_lost_inline") == "modified"
        assert operation_for_kind("vptr_introduced") == "modified"
        assert operation_for_kind("static_tls_introduced") == "modified"

    def test_no_remaining_add_remove_synonym_misses(self):
        """Systematic sweep: every ChangeKind whose name contains an add/
        remove synonym must classify as "added"/"removed", unless it is a
        known trait-change-on-persisting-entity exception (see the
        docstring on _OPERATION_OVERRIDES)."""
        import re

        from abicheck.checker_policy import ChangeKind
        from abicheck.reporter_markdown import operation_for_kind

        trait_change_exceptions = {"python_abi3_dropped"}
        synonym = re.compile(
            r"drop|excis|eliminat|vanish|disappear|revok|discontinu"
            r"|withdraw|retract|purge|delet|remov"
            r"|gain|introduc|new_|appear|creat"
        )
        for kind in ChangeKind:
            v = kind.value
            # "_lost_*"/"_introduced" name a trait gained/lost on an entity
            # that still exists, not the entity itself appearing/
            # disappearing — see the docstring on _OPERATION_OVERRIDES.
            if v in trait_change_exceptions or "_lost_" in v or v.endswith("_introduced"):
                continue
            if synonym.search(v) and "_added" not in v and "_removed" not in v:
                # Not already covered by the plain suffix rule — must be a
                # deliberate override, not a silent "modified" miss.
                op = operation_for_kind(v)
                assert op in ("added", "removed"), (
                    f"{v!r} looks like an add/remove kind but "
                    f"operation_for_kind() returned {op!r}"
                )


# ---------------------------------------------------------------------------
# Stat mode tests
# ---------------------------------------------------------------------------

class TestStatMode:
    def test_stat_text_output(self):
        result = _make_result(
            changes=[
                Change(ChangeKind.FUNC_REMOVED, "foo", "removed"),
                Change(ChangeKind.FUNC_ADDED, "bar", "added"),
            ],
        )
        text = to_stat(result)
        assert "BREAKING" in text
        assert "1 breaking" in text
        assert "1 compatible" in text
        assert "2 total" in text

    def test_stat_json_output(self):
        result = _make_result(
            changes=[
                Change(ChangeKind.FUNC_REMOVED, "foo", "removed"),
            ],
        )
        text = to_stat_json(result)
        d = json.loads(text)
        assert d["verdict"] == "BREAKING"
        assert "summary" in d
        assert "changes" not in d  # stat mode: no changes array

    def test_stat_with_redundant_count(self):
        result = _make_result(
            changes=[Change(ChangeKind.TYPE_SIZE_CHANGED, "T", "size")],
            redundant_changes=[Change(ChangeKind.FUNC_PARAMS_CHANGED, "f", "changed")],
        )
        text = to_stat(result)
        assert "1 redundant hidden" in text

    def test_stat_via_to_markdown(self):
        result = _make_result(
            changes=[Change(ChangeKind.FUNC_REMOVED, "foo", "removed")],
        )
        text = to_markdown(result, stat=True)
        assert "BREAKING" in text
        assert "total" in text

    def test_stat_via_to_json(self):
        result = _make_result(
            changes=[Change(ChangeKind.FUNC_REMOVED, "foo", "removed")],
        )
        text = to_json(result, stat=True)
        d = json.loads(text)
        assert "changes" not in d


# ---------------------------------------------------------------------------
# Leaf mode tests
# ---------------------------------------------------------------------------

class TestLeafMode:
    def test_leaf_markdown(self):
        result = _make_result(
            changes=[
                Change(ChangeKind.TYPE_SIZE_CHANGED, "Config", "size changed from 64 to 72 bytes",
                       affected_symbols=["config_init", "config_load"]),
                Change(ChangeKind.FUNC_REMOVED, "old_api", "function removed: old_api"),
            ],
        )
        text = to_markdown(result, report_mode="leaf")
        assert "leaf-change view" in text
        assert "Config" in text
        assert "config_init" in text
        assert "Non-Type Changes" in text
        assert "old_api" in text

    def test_leaf_json(self):
        result = _make_result(
            changes=[
                Change(ChangeKind.TYPE_SIZE_CHANGED, "Config", "size changed",
                       affected_symbols=["f1", "f2"]),
                Change(ChangeKind.FUNC_REMOVED, "old_api", "removed"),
            ],
        )
        text = to_json(result, report_mode="leaf")
        d = json.loads(text)
        assert "leaf_changes" in d
        assert "non_type_changes" in d
        assert len(d["leaf_changes"]) == 1
        assert d["leaf_changes"][0]["symbol"] == "Config"
        assert d["leaf_changes"][0]["affected_count"] == 2
        assert len(d["non_type_changes"]) == 1

    def test_leaf_markdown_carries_severity_summary(self):
        """report_mode="leaf" returned before the severity summary section
        was ever built, so it silently had no severity info even when a
        caller passed severity_config through to_markdown."""
        from abicheck.severity import PRESET_DEFAULT

        result = _make_result(
            changes=[Change(ChangeKind.FUNC_ADDED, "new_api", "new function: new_api")],
        )
        text = to_markdown(result, report_mode="leaf", severity_config=PRESET_DEFAULT)
        assert "Severity Configuration" in text


# ---------------------------------------------------------------------------
# Show-impact tests
# ---------------------------------------------------------------------------

class TestShowImpact:
    def test_impact_in_markdown(self):
        root = Change(ChangeKind.TYPE_SIZE_CHANGED, "Config", "size changed",
                      affected_symbols=["f1", "f2", "f3"])
        root.caused_count = 3
        result = _make_result(changes=[root])
        text = to_markdown(result, show_impact=True)
        assert "Impact Summary" in text
        assert "Config" in text

    def test_impact_in_json(self):
        result = _make_result(
            changes=[Change(ChangeKind.TYPE_SIZE_CHANGED, "Config", "size changed",
                           affected_symbols=["f1"])],
        )
        text = to_json(result, show_impact=True)
        d = json.loads(text)
        assert "show_only_applied" in d  # impact-related key


# ---------------------------------------------------------------------------
# Show-only in markdown/json
# ---------------------------------------------------------------------------

class TestShowOnlyInReporters:
    def test_show_only_in_markdown(self):
        result = _make_result(
            changes=[
                Change(ChangeKind.FUNC_REMOVED, "foo", "removed"),
                Change(ChangeKind.FUNC_ADDED, "bar", "added"),
                Change(ChangeKind.TYPE_SIZE_CHANGED, "T", "size"),
            ],
        )
        text = to_markdown(result, show_only="functions")
        assert "Filtered by" in text
        assert "2 of 3 changes shown" in text

    def test_severity_summary_exit_impact_ignores_show_only_filter(self):
        """Codex review on #549: the severity summary table's "Exit Impact"
        column was computed from the --show-only-filtered changes, so
        filtering out the finding that actually gates CI made the table
        claim "no exit impact" for a category that still fails the gate.
        `--show-only compatible` hides the breaking FUNC_REMOVED, but the
        default severity preset still exits nonzero on it."""
        from abicheck.severity import PRESET_DEFAULT

        result = _make_result(
            changes=[
                Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed"),
                Change(ChangeKind.FUNC_ADDED, "_Z3barv", "added"),
            ],
        )
        text = to_markdown(
            result, show_only="compatible", severity_config=PRESET_DEFAULT
        )
        assert "0 of 2 changes shown" not in text  # sanity: filter is active
        assert "| ABI/API Incompatibilities |" in text
        lines = [ln for ln in text.splitlines() if "ABI/API Incompatibilities" in ln]
        assert len(lines) == 1
        assert "causes non-zero exit" in lines[0]

    def test_leaf_severity_summary_exit_impact_ignores_show_only_filter(self):
        """Same fix as above, for the report_mode="leaf" summary table."""
        from abicheck.severity import PRESET_DEFAULT

        result = _make_result(
            changes=[
                Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed"),
                Change(ChangeKind.FUNC_ADDED, "_Z3barv", "added"),
            ],
        )
        text = to_markdown(
            result,
            report_mode="leaf",
            show_only="compatible",
            severity_config=PRESET_DEFAULT,
        )
        lines = [ln for ln in text.splitlines() if "ABI/API Incompatibilities" in ln]
        assert len(lines) == 1
        assert "causes non-zero exit" in lines[0]

    def test_show_only_in_json(self):
        result = _make_result(
            changes=[
                Change(ChangeKind.FUNC_REMOVED, "foo", "removed"),
                Change(ChangeKind.TYPE_SIZE_CHANGED, "T", "size"),
            ],
        )
        text = to_json(result, show_only="breaking")
        d = json.loads(text)
        # Both FUNC_REMOVED and TYPE_SIZE_CHANGED are breaking
        assert len(d["changes"]) == 2


# ---------------------------------------------------------------------------
# Redundancy note in markdown
# ---------------------------------------------------------------------------

class TestRedundancyNote:
    def test_redundancy_note_shown(self):
        result = _make_result(
            changes=[Change(ChangeKind.TYPE_SIZE_CHANGED, "T", "size")],
            redundant_changes=[Change(ChangeKind.FUNC_PARAMS_CHANGED, "f", "changed")],
        )
        text = to_markdown(result)
        assert "1 redundant change(s) hidden" in text
        assert "--show-redundant" in text

    def test_no_redundancy_note_when_zero(self):
        result = _make_result(
            changes=[Change(ChangeKind.FUNC_REMOVED, "foo", "removed")],
        )
        text = to_markdown(result)
        assert "redundant" not in text


# ---------------------------------------------------------------------------
# Change model new fields
# ---------------------------------------------------------------------------

class TestChangeModelFields:
    def test_caused_by_type_default_none(self):
        c = Change(ChangeKind.FUNC_REMOVED, "foo", "removed")
        assert c.caused_by_type is None
        assert c.caused_count == 0

    def test_caused_count_in_json(self):
        c = Change(ChangeKind.TYPE_SIZE_CHANGED, "T", "size")
        c.caused_count = 5
        result = _make_result(changes=[c])
        text = to_json(result)
        d = json.loads(text)
        assert d["changes"][0]["caused_count"] == 5


# ---------------------------------------------------------------------------
# DiffResult new fields
# ---------------------------------------------------------------------------

class TestDiffResultFields:
    def test_redundant_changes_default_empty(self):
        result = DiffResult(
            old_version="1", new_version="2", library="lib.so",
        )
        assert result.redundant_changes == []
        assert result.redundant_count == 0

    def test_redundant_count_in_json(self):
        result = _make_result(
            changes=[Change(ChangeKind.TYPE_SIZE_CHANGED, "T", "size")],
            redundant_changes=[Change(ChangeKind.FUNC_PARAMS_CHANGED, "f", "changed")],
        )
        text = to_json(result)
        d = json.loads(text)
        assert d["redundant_count"] == 1


# ---------------------------------------------------------------------------
# Caused-count in markdown format
# ---------------------------------------------------------------------------

class TestCausedCountInMarkdown:
    def test_caused_count_shown(self):
        c = Change(ChangeKind.TYPE_SIZE_CHANGED, "Config", "size changed 64 -> 72")
        c.caused_count = 12
        c.affected_symbols = ["f1", "f2"]
        result = _make_result(changes=[c])
        text = to_markdown(result)
        assert "12 derived change(s) collapsed" in text


# ---------------------------------------------------------------------------
# SARIF format support
# ---------------------------------------------------------------------------

class TestSarifRedundancy:
    def test_sarif_includes_redundant_count(self):
        from abicheck.sarif import to_sarif

        result = _make_result(
            changes=[Change(ChangeKind.TYPE_SIZE_CHANGED, "T", "size")],
            redundant_changes=[Change(ChangeKind.FUNC_PARAMS_CHANGED, "f", "changed")],
        )
        sarif = to_sarif(result)
        props = sarif["runs"][0]["properties"]
        assert props["redundantCount"] == 1

    def test_sarif_no_redundant_count_when_zero(self):
        from abicheck.sarif import to_sarif

        result = _make_result(
            changes=[Change(ChangeKind.FUNC_REMOVED, "foo", "removed")],
        )
        sarif = to_sarif(result)
        props = sarif["runs"][0]["properties"]
        assert "redundantCount" not in props

    def test_sarif_caused_by_type_in_result(self):
        from abicheck.sarif import to_sarif

        c = Change(ChangeKind.FUNC_PARAMS_CHANGED, "f", "param changed")
        c.caused_by_type = "Config"
        result = _make_result(changes=[c])
        sarif = to_sarif(result)
        result_props = sarif["runs"][0]["results"][0]["properties"]
        assert result_props["causedByType"] == "Config"

    def test_sarif_caused_count_in_result(self):
        from abicheck.sarif import to_sarif

        c = Change(ChangeKind.TYPE_SIZE_CHANGED, "T", "size")
        c.caused_count = 5
        result = _make_result(changes=[c])
        sarif = to_sarif(result)
        result_props = sarif["runs"][0]["results"][0]["properties"]
        assert result_props["causedCount"] == 5

    def test_sarif_show_only_filters(self):
        from abicheck.sarif import to_sarif

        result = _make_result(
            changes=[
                Change(ChangeKind.FUNC_REMOVED, "foo", "removed"),
                Change(ChangeKind.FUNC_ADDED, "bar", "added"),
                Change(ChangeKind.TYPE_SIZE_CHANGED, "T", "size"),
            ],
        )
        sarif = to_sarif(result, show_only="functions")
        # Only func_removed and func_added
        assert len(sarif["runs"][0]["results"]) == 2


# ---------------------------------------------------------------------------
# HTML format support
# ---------------------------------------------------------------------------

class TestHtmlRedundancy:
    def test_html_redundancy_note(self):
        from abicheck.html_report import generate_html_report

        result = _make_result(
            changes=[Change(ChangeKind.TYPE_SIZE_CHANGED, "T", "size")],
            redundant_changes=[Change(ChangeKind.FUNC_PARAMS_CHANGED, "f", "changed")],
        )
        html = generate_html_report(result, lib_name="lib.so")
        assert "1 redundant change(s)" in html
        assert "--show-redundant" in html

    def test_html_caused_count_displayed(self):
        from abicheck.html_report import generate_html_report

        c = Change(ChangeKind.TYPE_SIZE_CHANGED, "Config", "size changed")
        c.caused_count = 7
        result = _make_result(changes=[c])
        html = generate_html_report(result, lib_name="lib.so")
        assert "7 derived change(s) collapsed" in html

    def test_html_show_only_filter(self):
        from abicheck.html_report import generate_html_report

        result = _make_result(
            changes=[
                Change(ChangeKind.FUNC_REMOVED, "foo", "removed"),
                Change(ChangeKind.TYPE_SIZE_CHANGED, "T", "size"),
            ],
        )
        html = generate_html_report(result, lib_name="lib.so", show_only="functions")
        assert "Filtered by" in html
        assert "1 of 2 changes shown" in html

    def test_html_show_impact(self):
        from abicheck.html_report import generate_html_report

        c = Change(ChangeKind.TYPE_SIZE_CHANGED, "Config", "size changed",
                    affected_symbols=["f1", "f2"])
        c.caused_count = 3
        result = _make_result(changes=[c])
        html = generate_html_report(result, lib_name="lib.so", show_impact=True)
        assert "Impact Summary" in html
        assert "Config" in html


# ---------------------------------------------------------------------------
# XML (ABICC compat) format support
# ---------------------------------------------------------------------------

class TestXmlRedundancy:
    def test_xml_redundant_count(self):
        from abicheck.compat.xml_report import generate_xml_report

        result = _make_result(
            changes=[Change(ChangeKind.TYPE_SIZE_CHANGED, "T", "size")],
            redundant_changes=[Change(ChangeKind.FUNC_PARAMS_CHANGED, "f", "changed")],
        )
        xml = generate_xml_report(result, lib_name="lib.so")
        assert "<redundant_changes>1</redundant_changes>" in xml

    def test_xml_caused_by_type(self):
        from abicheck.compat.xml_report import generate_xml_report

        c = Change(ChangeKind.TYPE_SIZE_CHANGED, "Config", "size changed")
        c.caused_count = 3
        result = _make_result(changes=[c])
        xml = generate_xml_report(result, lib_name="lib.so")
        assert "<caused_count>3</caused_count>" in xml


# ---------------------------------------------------------------------------
# _root_type_name: namespace preservation (architecture review fix #1)
# ---------------------------------------------------------------------------


class TestRootTypeNameNamespace:
    """Verify _root_type_name uses rsplit (not split) to preserve namespaces."""

    def test_simple_field(self):
        c = Change(ChangeKind.TYPE_FIELD_REMOVED, "Container::flags", "removed")
        assert _root_type_name(c) == "Container"

    def test_namespaced_field(self):
        """ns::Container::flags → ns::Container (not just 'ns')."""
        c = Change(ChangeKind.TYPE_FIELD_REMOVED, "ns::Container::flags", "removed")
        assert _root_type_name(c) == "ns::Container"

    def test_deeply_nested_namespace_field(self):
        c = Change(ChangeKind.STRUCT_FIELD_REMOVED, "a::b::Container::flags", "removed")
        assert _root_type_name(c) == "a::b::Container"

    def test_non_field_kind_preserves_full_symbol(self):
        c = Change(ChangeKind.TYPE_SIZE_CHANGED, "ns::Container", "size changed")
        assert _root_type_name(c) == "ns::Container"

    def test_no_namespace(self):
        c = Change(ChangeKind.TYPE_SIZE_CHANGED, "Container", "size changed")
        assert _root_type_name(c) == "Container"


# ---------------------------------------------------------------------------
# Reserved-field suppression: exact field-name match (review fix #2)
# ---------------------------------------------------------------------------


class TestReservedFieldExactMatch:
    """_filter_reserved_field_renames must not substring-match field names."""

    def test_exact_old_field_suppresses(self):
        changes = [
            Change(
                ChangeKind.USED_RESERVED_FIELD, "S",
                "Reserved field put into use: S::__reserved0 → active",
                old_value="__reserved0", new_value="active",
            ),
            Change(
                ChangeKind.STRUCT_FIELD_REMOVED, "S::__reserved0",
                "Struct field removed: S::__reserved0", old_value="uint32_t",
            ),
        ]
        result = _filter_reserved_field_renames(changes)
        kinds = [c.kind for c in result]
        assert ChangeKind.STRUCT_FIELD_REMOVED not in kinds

    def test_substring_old_field_not_suppressed(self):
        """'__reserved0_extra' must NOT be suppressed for '__reserved0'."""
        changes = [
            Change(
                ChangeKind.USED_RESERVED_FIELD, "S",
                "Reserved field put into use: S::__reserved0 → active",
                old_value="__reserved0", new_value="active",
            ),
            Change(
                ChangeKind.STRUCT_FIELD_REMOVED, "S::__reserved0_extra",
                "Struct field removed: S::__reserved0_extra", old_value="uint32_t",
            ),
        ]
        result = _filter_reserved_field_renames(changes)
        kinds = [c.kind for c in result]
        assert ChangeKind.STRUCT_FIELD_REMOVED in kinds

    def test_exact_new_field_suppresses_added(self):
        changes = [
            Change(
                ChangeKind.USED_RESERVED_FIELD, "S",
                "Reserved: S::__pad → active",
                old_value="__pad", new_value="active",
            ),
            Change(
                ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE, "S::active",
                "Struct field added: S::active", new_value="uint32_t",
            ),
        ]
        result = _filter_reserved_field_renames(changes)
        kinds = [c.kind for c in result]
        assert ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE not in kinds

    def test_substring_new_field_not_suppressed(self):
        """'active_flags' must NOT be suppressed when new_field is 'active'."""
        changes = [
            Change(
                ChangeKind.USED_RESERVED_FIELD, "S",
                "Reserved: S::__pad → active",
                old_value="__pad", new_value="active",
            ),
            Change(
                ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE, "S::active_flags",
                "Struct field added: S::active_flags", new_value="uint32_t",
            ),
        ]
        result = _filter_reserved_field_renames(changes)
        kinds = [c.kind for c in result]
        assert ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE in kinds

    def test_flag_does_not_match_flags(self):
        """Regression: 'flag' must not match 'flags'."""
        changes = [
            Change(
                ChangeKind.USED_RESERVED_FIELD, "S",
                "Reserved: S::__pad → flag",
                old_value="__pad", new_value="flag",
            ),
            Change(
                ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE, "S::flags",
                "Struct field added: S::flags", new_value="uint32_t",
            ),
        ]
        result = _filter_reserved_field_renames(changes)
        kinds = [c.kind for c in result]
        assert ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE in kinds
