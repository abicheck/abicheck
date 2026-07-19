"""Tests for fixes identified during the scan-accuracy review.

Covers:
- canonicalize_type_name edge cases (C3: const-reorder, multi-word, templates)
- Parameter type canonicalization in function signature checks (C1)
- Confidence computation with both snapshots and variables (C2)
- SuppressionList.audit() (H1)
- PolicyFile.validate_overrides() (H1)
- Confidence enum (H2)
"""
from __future__ import annotations

import copy
from datetime import date, timedelta

from abicheck.checker import Change, ChangeKind, compare
from abicheck.checker_policy import Confidence, Verdict
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    ParamKind,
    RecordType,
    TypeField,
    Variable,
    Visibility,
    canonicalize_type_name,
)
from abicheck.policy_file import PolicyFile

# ─── canonicalize_type_name ──────────────────────────────────────────────────


class TestCanonicalizeTypeName:
    """Direct unit tests for canonicalize_type_name."""

    def test_strip_struct_prefix(self):
        assert canonicalize_type_name("struct Foo") == "Foo"

    def test_strip_class_prefix(self):
        assert canonicalize_type_name("class Bar") == "Bar"

    def test_strip_union_prefix(self):
        assert canonicalize_type_name("union Baz") == "Baz"

    def test_strip_enum_prefix(self):
        assert canonicalize_type_name("enum Color") == "Color"

    def test_east_const_simple(self):
        assert canonicalize_type_name("const int") == "int const"

    def test_east_const_pointer(self):
        assert canonicalize_type_name("const int *") == "int const *"

    def test_east_const_multi_word(self):
        """const unsigned long long should move const after full base type."""
        assert canonicalize_type_name("const unsigned long long") == "unsigned long long const"

    def test_east_const_volatile(self):
        """const volatile int should move const after the full base."""
        assert canonicalize_type_name("const volatile int") == "volatile int const"

    def test_east_const_multi_word_pointer(self):
        assert canonicalize_type_name("const unsigned int *") == "unsigned int const *"

    def test_template_type_preserved(self):
        """Template types with angle brackets should not be reordered."""
        assert canonicalize_type_name("const std::vector<int>") == "const std::vector<int>"

    def test_already_east_const(self):
        assert canonicalize_type_name("int const") == "int const"

    def test_pointer_to_const(self):
        """int const * should be left alone (const is already east)."""
        assert canonicalize_type_name("int const *") == "int const *"

    def test_whitespace_collapse(self):
        # Leading whitespace prevents struct-prefix regex from matching (anchored at ^)
        assert canonicalize_type_name("class   Bar") == "Bar"
        assert canonicalize_type_name("int    *") == "int *"

    def test_identity(self):
        assert canonicalize_type_name("int") == "int"

    def test_empty_string(self):
        assert canonicalize_type_name("") == ""

    def test_anonymous_enum_location_stripped(self):
        """clang's embedded 'at <path>:<line>:<col>' must not affect identity
        (pvxs acceptance spike FP: old/new checkout roots differ, same decl)."""
        old = "enum (unnamed enum at /tmp/old/include/pvxs/unittest.h:56:5)"
        new = "enum (unnamed enum at /tmp/new/include/pvxs/unittest.h:56:5)"
        assert canonicalize_type_name(old) == canonicalize_type_name(new)
        assert canonicalize_type_name(old) == "(unnamed enum)"

    def test_anonymous_struct_location_stripped(self):
        old = "struct (unnamed struct at /a/foo.h:10:3)"
        new = "struct (unnamed struct at /b/foo.h:10:3)"
        assert canonicalize_type_name(old) == canonicalize_type_name(new)

    def test_anonymous_location_different_line_still_differs_only_by_marker(self):
        """A genuinely different line/col still canonicalizes to the same bare
        marker — same-file line renumbering from an unrelated edit shouldn't
        trip a field-type-changed finding on its own."""
        a = "enum (unnamed enum at /x/foo.h:56:5)"
        b = "enum (unnamed enum at /x/foo.h:60:2)"
        assert canonicalize_type_name(a) == canonicalize_type_name(b)


# ─── Parameter type canonicalization (C1) ────────────────────────────────────


class TestParamTypeCanonicalization:
    """Verify that struct/class prefix differences in param types don't cause false positives."""

    def test_struct_prefix_in_param_no_false_positive(self):
        """'struct stat *' vs 'stat *' should NOT trigger FUNC_PARAMS_CHANGED."""
        old = AbiSnapshot(
            library="libtest.so", version="1.0",
            functions=[Function(
                name="do_stat", mangled="_Z7do_statP4stat",
                return_type="int",
                params=[Param(name="s", type="struct stat *", kind=ParamKind.POINTER)],
                visibility=Visibility.PUBLIC,
            )],
        )
        new = AbiSnapshot(
            library="libtest.so", version="2.0",
            functions=[Function(
                name="do_stat", mangled="_Z7do_statP4stat",
                return_type="int",
                params=[Param(name="s", type="stat *", kind=ParamKind.POINTER)],
                visibility=Visibility.PUBLIC,
            )],
        )
        result = compare(old, new)
        param_changes = [c for c in result.changes if c.kind == ChangeKind.FUNC_PARAMS_CHANGED]
        assert len(param_changes) == 0, "struct prefix difference should not be a param change"

    def test_const_reorder_in_param_no_false_positive(self):
        """'const int' vs 'int const' in params should NOT trigger FUNC_PARAMS_CHANGED."""
        old = AbiSnapshot(
            library="libtest.so", version="1.0",
            functions=[Function(
                name="get", mangled="_Z3geti",
                return_type="void",
                params=[Param(name="x", type="const int", kind=ParamKind.VALUE)],
                visibility=Visibility.PUBLIC,
            )],
        )
        new = AbiSnapshot(
            library="libtest.so", version="2.0",
            functions=[Function(
                name="get", mangled="_Z3geti",
                return_type="void",
                params=[Param(name="x", type="int const", kind=ParamKind.VALUE)],
                visibility=Visibility.PUBLIC,
            )],
        )
        result = compare(old, new)
        param_changes = [c for c in result.changes if c.kind == ChangeKind.FUNC_PARAMS_CHANGED]
        assert len(param_changes) == 0


# ─── Confidence computation (C2) ────────────────────────────────────────────


class TestConfidenceComputation:
    """Verify confidence checks both snapshots and includes variables."""

    def test_confidence_is_enum(self):
        old = AbiSnapshot(library="lib.so", version="1.0")
        new = AbiSnapshot(library="lib.so", version="2.0")
        result = compare(old, new)
        assert isinstance(result.confidence, Confidence)

    def test_confidence_str_comparison_still_works(self):
        """Confidence enum is str-based so string comparisons keep working."""
        assert Confidence.HIGH == "high"
        assert Confidence.MEDIUM == "medium"
        assert Confidence.LOW == "low"

    def test_empty_old_populated_new_has_headers(self):
        """When old is empty but new has functions, has_headers should be True."""
        old = AbiSnapshot(library="lib.so", version="1.0")
        new = AbiSnapshot(
            library="lib.so", version="2.0",
            functions=[Function(
                name="foo", mangled="_Z3foov",
                return_type="void", visibility=Visibility.PUBLIC,
            )],
        )
        result = compare(old, new)
        assert "header" in result.evidence_tiers

    def test_variables_only_detected_as_headers(self):
        """Snapshots with only variables should still be detected as having header data."""
        old = AbiSnapshot(
            library="lib.so", version="1.0",
            variables=[Variable(
                name="ver", mangled="_Z3verv", type="int",
                visibility=Visibility.PUBLIC,
            )],
        )
        new = copy.deepcopy(old)
        new.version = "2.0"
        result = compare(old, new)
        assert "header" in result.evidence_tiers


# ─── SuppressionList.audit() ────────────────────────────────────────────────


class TestSuppressionAudit:
    """Tests for SuppressionList.audit()."""

    def test_audit_stale_rules(self):
        from abicheck.suppression import Suppression, SuppressionList

        slist = SuppressionList([
            Suppression(symbol="_Z3foov", reason="test"),
        ])
        # No changes match the suppression
        audit = slist.audit([])
        assert len(audit.stale_rules) == 1
        assert audit.has_issues

    def test_audit_matching_rule_not_stale(self):
        from abicheck.suppression import Suppression, SuppressionList

        slist = SuppressionList([
            Suppression(symbol="_Z3foov", reason="test"),
        ])
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="removed",
        )
        audit = slist.audit([change])
        assert len(audit.stale_rules) == 0
        # FUNC_REMOVED is BREAKING so should appear in high_risk
        assert len(audit.high_risk_matches) == 1

    def test_audit_expired_rules(self):
        from abicheck.suppression import Suppression, SuppressionList

        past = date.today() - timedelta(days=10)
        slist = SuppressionList([
            Suppression(symbol="_Z3foov", reason="expired", expires=past),
        ])
        audit = slist.audit([])
        assert len(audit.expired_rules) == 1
        assert audit.has_issues

    def test_audit_near_expiry(self):
        from abicheck.suppression import Suppression, SuppressionList

        soon = date.today() + timedelta(days=5)
        slist = SuppressionList([
            Suppression(symbol="_Z3foov", reason="expiring", expires=soon),
        ])
        audit = slist.audit([], near_expiry_days=30)
        assert len(audit.near_expiry_rules) == 1

    def test_audit_no_issues(self):
        from abicheck.suppression import Suppression, SuppressionList

        slist = SuppressionList([
            Suppression(symbol="_Z3foov", reason="test"),
        ])
        change = Change(
            kind=ChangeKind.FUNC_ADDED,
            symbol="_Z3foov",
            description="added",
        )
        audit = slist.audit([change])
        # FUNC_ADDED is not BREAKING, rule matched, no expiry
        assert len(audit.stale_rules) == 0
        assert len(audit.high_risk_matches) == 0
        assert not audit.has_issues

    def test_audit_summary_output(self):
        from abicheck.suppression import Suppression, SuppressionList

        slist = SuppressionList([
            Suppression(symbol="_Z3foov", reason="test"),
        ])
        audit = slist.audit([])
        summary = audit.summary()
        assert "stale" in summary.lower() or "matched nothing" in summary.lower()

    def test_audit_summary_no_issues(self):
        from abicheck.suppression import Suppression, SuppressionList

        slist = SuppressionList([
            Suppression(symbol="_Z3foov", reason="test"),
        ])
        change = Change(
            kind=ChangeKind.FUNC_ADDED,
            symbol="_Z3foov",
            description="added",
        )
        audit = slist.audit([change])
        summary = audit.summary()
        assert "No issues found" in summary

    def test_audit_summary_high_risk(self):
        from abicheck.suppression import Suppression, SuppressionList

        slist = SuppressionList([
            Suppression(symbol="_Z3foov", reason="test"),
        ])
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="removed",
        )
        audit = slist.audit([change])
        summary = audit.summary()
        assert "BREAKING" in summary

    def test_audit_summary_expired(self):
        from abicheck.suppression import Suppression, SuppressionList

        past = date.today() - timedelta(days=10)
        slist = SuppressionList([
            Suppression(symbol="_Z3foov", reason="old", expires=past),
        ])
        audit = slist.audit([])
        summary = audit.summary()
        assert "expired" in summary.lower()

    def test_audit_summary_near_expiry(self):
        from abicheck.suppression import Suppression, SuppressionList

        soon = date.today() + timedelta(days=5)
        slist = SuppressionList([
            Suppression(symbol="_Z3foov", reason="soon", expires=soon),
        ])
        audit = slist.audit([], near_expiry_days=30)
        summary = audit.summary()
        assert "expiring soon" in summary.lower()

    def test_audit_match_counts(self):
        from abicheck.suppression import Suppression, SuppressionList

        slist = SuppressionList([
            Suppression(symbol="_Z3foov", reason="test"),
            Suppression(symbol="_Z3barv", reason="test2"),
        ])
        c1 = Change(kind=ChangeKind.FUNC_ADDED, symbol="_Z3foov", description="added")
        c2 = Change(kind=ChangeKind.FUNC_ADDED, symbol="_Z3foov", description="added2")
        audit = slist.audit([c1, c2])
        assert audit.match_counts[0] == 2  # first rule matched twice
        assert audit.match_counts[1] == 0  # second rule matched nothing
        assert audit.total_rules == 2


# ─── PolicyFile.validate_overrides() ─────────────────────────────────────────


class TestPolicyFileValidateOverrides:
    """Tests for PolicyFile.validate_overrides()."""

    def test_no_overrides_no_warnings(self):
        pf = PolicyFile()
        assert pf.validate_overrides() == []

    def test_critical_kind_to_ignore_warns(self):
        pf = PolicyFile(overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE})
        warnings = pf.validate_overrides()
        assert len(warnings) == 1
        assert "HIGH RISK" in warnings[0]

    def test_critical_kind_to_risk_warns(self):
        pf = PolicyFile(overrides={ChangeKind.TYPE_SIZE_CHANGED: Verdict.COMPATIBLE_WITH_RISK})
        warnings = pf.validate_overrides()
        assert len(warnings) == 1
        assert "RISK" in warnings[0]

    def test_breaking_kind_to_ignore_warns(self):
        pf = PolicyFile(overrides={ChangeKind.FUNC_VIRTUAL_ADDED: Verdict.COMPATIBLE})
        warnings = pf.validate_overrides()
        assert len(warnings) == 1
        assert "BREAKING" in warnings[0]

    def test_safe_override_no_warning(self):
        pf = PolicyFile(overrides={ChangeKind.ENUM_MEMBER_RENAMED: Verdict.COMPATIBLE})
        warnings = pf.validate_overrides()
        assert len(warnings) == 0

    def test_critical_kind_to_warn_no_warning(self):
        """Downgrading critical BREAKING to API_BREAK (warn) should not warn."""
        pf = PolicyFile(overrides={ChangeKind.FUNC_REMOVED: Verdict.API_BREAK})
        warnings = pf.validate_overrides()
        assert len(warnings) == 0

    def test_multiple_overrides_multiple_warnings(self):
        pf = PolicyFile(overrides={
            ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE,
            ChangeKind.VAR_REMOVED: Verdict.COMPATIBLE,
            ChangeKind.SONAME_CHANGED: Verdict.COMPATIBLE_WITH_RISK,
        })
        warnings = pf.validate_overrides()
        assert len(warnings) == 3

    def test_validate_uses_base_policy_breaking_kinds(self):
        """validate_overrides should use the base policy's breaking set."""
        # Under plugin_abi, CALLING_CONVENTION_CHANGED is downgraded to COMPATIBLE.
        # Under strict_abi, it's BREAKING. So overriding it to 'ignore' under
        # strict_abi should warn, but the kind itself is still in BREAKING_KINDS
        # for strict_abi.
        pf = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.CALLING_CONVENTION_CHANGED: Verdict.COMPATIBLE},
        )
        warnings = pf.validate_overrides()
        assert len(warnings) == 1
        assert "BREAKING" in warnings[0]


# ─── Namespace-qualified canonicalization ─────────────────────────────────────


class TestCanonicalizeNamespaceTypes:
    """Verify canonicalize_type_name handles scoped/qualified identifiers."""

    def test_const_namespace_type_pointer(self):
        assert canonicalize_type_name("const ns::Type *") == "ns::Type const *"

    def test_const_namespace_type_reference(self):
        assert canonicalize_type_name("const ns::Type &") == "ns::Type const &"

    def test_const_nested_namespace(self):
        assert canonicalize_type_name("const a::b::C") == "a::b::C const"

    def test_leading_whitespace_struct(self):
        """Leading whitespace should not prevent struct-prefix stripping."""
        assert canonicalize_type_name("  struct Foo") == "Foo"

    def test_const_struct_combined(self):
        """const struct Foo * should canonicalize to Foo const *."""
        assert canonicalize_type_name("const struct Foo *") == "Foo const *"


# ─── Union field type canonicalization ────────────────────────────────────────


class TestUnionFieldCanonicalization:
    """Verify union field type comparison uses canonicalize_type_name."""

    def test_union_field_struct_prefix_no_false_positive(self):
        """'struct X' vs 'X' in union field types should NOT trigger UNION_FIELD_TYPE_CHANGED."""
        old = AbiSnapshot(
            library="libtest.so", version="1.0",
            types=[RecordType(
                name="MyUnion", kind="union", is_union=True,
                fields=[TypeField(name="data", type="struct Inner")],
            )],
        )
        new = AbiSnapshot(
            library="libtest.so", version="2.0",
            types=[RecordType(
                name="MyUnion", kind="union", is_union=True,
                fields=[TypeField(name="data", type="Inner")],
            )],
        )
        result = compare(old, new)
        union_changes = [c for c in result.changes if c.kind == ChangeKind.UNION_FIELD_TYPE_CHANGED]
        assert len(union_changes) == 0, "struct prefix difference should not be a union field type change"


# ---------------------------------------------------------------------------
# render_output: ValidationError for unsupported format (review fix #6)
# ---------------------------------------------------------------------------


class TestRenderOutputValidation:
    """render_output must raise ValidationError for unknown format strings."""

    def _make_result(self) -> object:
        from abicheck.checker import DiffResult, Verdict

        return DiffResult(
            old_version="1.0", new_version="2.0", library="libtest.so",
            changes=[], verdict=Verdict.NO_CHANGE, policy="strict_abi",
        )

    def _make_snap(self) -> AbiSnapshot:
        return AbiSnapshot(library="libtest.so", version="1.0")

    def test_unsupported_format_raises(self):
        import pytest

        from abicheck.errors import ValidationError
        from abicheck.service import render_output

        with pytest.raises(ValidationError, match="Unsupported output format"):
            render_output("xml", self._make_result(), self._make_snap())

    def test_unsupported_format_csv_raises(self):
        import pytest

        from abicheck.errors import ValidationError
        from abicheck.service import render_output

        with pytest.raises(ValidationError, match="Unsupported output format"):
            render_output("csv", self._make_result(), self._make_snap())

    def test_supported_formats_do_not_raise(self):
        from abicheck.service import render_output

        for fmt in ("json", "markdown", "md"):
            output = render_output(fmt, self._make_result(), self._make_snap())
            assert isinstance(output, str)


# ---------------------------------------------------------------------------
# service.py imports from .compat.abicc_dump_import (review fix #7)
# ---------------------------------------------------------------------------


class TestServiceImportPaths:
    """Verify that service.py correctly imports from compat subpackage."""

    def test_looks_like_perl_dump_importable(self):
        from abicheck.compat.abicc_dump_import import looks_like_perl_dump

        assert callable(looks_like_perl_dump)

    def test_import_abicc_perl_dump_importable(self):
        from abicheck.compat.abicc_dump_import import import_abicc_perl_dump

        assert callable(import_abicc_perl_dump)

    def test_service_sniff_does_not_trigger_deprecation(self):
        """Verify service module doesn't trigger the deprecation warning."""
        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            from abicheck import service  # noqa: F811

            _ = service.sniff_text_format
            deprecation_warnings = [
                x for x in w
                if issubclass(x.category, DeprecationWarning)
                and "abicc_dump_import" in str(x.message)
            ]
            assert len(deprecation_warnings) == 0


# ─── FIX-I: type/enum dedup debug logging throttling ─────────────────────────


class TestTypeDedupThrottling:
    """FIX-I: _DwarfSnapshotBuilder logs duplicate types/enums once per name."""

    def _make_builder(self):
        """Create a minimal _DwarfSnapshotBuilder with empty ELF metadata."""
        from unittest.mock import MagicMock

        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = MagicMock()
        elf_meta.symbols = []
        return _DwarfSnapshotBuilder(elf_path="/dev/null", elf_meta=elf_meta)

    def _make_die(self, tag="DW_TAG_structure_type", byte_size=8):
        """Create a minimal mock DIE for builder methods."""
        from unittest.mock import MagicMock

        die = MagicMock()
        die.tag = tag
        # attr_int / attr_bool read die.attributes[attr].value
        size_attr = MagicMock()
        size_attr.value = byte_size
        die.attributes = {"DW_AT_byte_size": size_attr}
        die.iter_children.return_value = []
        return die

    def test_type_dup_logged_once(self, caplog):
        """First duplicate logs debug message; subsequent duplicates are silent."""
        import logging

        builder = self._make_builder()
        die = self._make_die(tag="DW_TAG_structure_type", byte_size=8)
        cu = None  # CU is unused for the duplicate-check path

        # Seed "MyStruct" as already seen so subsequent calls are duplicates.
        builder._seen_type_names.add("MyStruct")

        with caplog.at_level(logging.DEBUG, logger="abicheck.dwarf_snapshot"):
            for _ in range(3):
                builder._process_record_type_named(die, cu, "MyStruct")

        dup_msgs = [r for r in caplog.records if "Duplicate type" in r.message]
        assert len(dup_msgs) == 1
        assert "MyStruct" in dup_msgs[0].message

    def test_enum_dup_logged_once(self, caplog):
        """First duplicate enum logs debug message; repeats are throttled."""
        import logging

        builder = self._make_builder()
        die = self._make_die(tag="DW_TAG_enumeration_type", byte_size=4)
        cu = None

        # Seed "Color" as already seen so subsequent calls are duplicates.
        builder._seen_enum_names.add("Color")

        with caplog.at_level(logging.DEBUG, logger="abicheck.dwarf_snapshot"):
            for _ in range(3):
                builder._process_enum_named(die, cu, "Color")

        dup_msgs = [r for r in caplog.records if "Duplicate enum" in r.message]
        assert len(dup_msgs) == 1
        assert "Color" in dup_msgs[0].message

    def test_no_dup_no_log(self, caplog):
        """No duplicate encountered → no debug log emitted."""
        import logging

        builder = self._make_builder()
        # _seen_type_names is empty, so "NewType" is not a duplicate
        die = self._make_die(tag="DW_TAG_structure_type", byte_size=8)
        cu = None
        with caplog.at_level(logging.DEBUG, logger="abicheck.dwarf_snapshot"):
            builder._process_record_type_named(die, cu, "NewType")

        dup_msgs = [r for r in caplog.records if "Duplicate" in r.message]
        assert len(dup_msgs) == 0
