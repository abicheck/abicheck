"""Tests for JUnit XML output.

Unit tests for the ``junit_report`` module, plus CLI integration tests that
exercise the full ``abicheck compare --format junit`` pipeline using JSON
snapshot files.
"""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from defusedxml.ElementTree import fromstring as xml_fromstring

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.junit_report import to_junit_xml, to_junit_xml_multi
from abicheck.model import AbiSnapshot, EnumType, Function, RecordType, Variable
from abicheck.serialization import snapshot_to_json

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    changes: list[Change],
    verdict: Verdict = Verdict.BREAKING,
    library: str = "libfoo.so.1",
    old: str = "1.0",
    new: str = "2.0",
) -> DiffResult:
    return DiffResult(
        old_version=old,
        new_version=new,
        library=library,
        changes=changes,
        verdict=verdict,
    )


def _make_snapshot(
    library: str = "libfoo.so.1",
    version: str = "1.0",
    functions: list[Function] | None = None,
    types: list[RecordType] | None = None,
    variables: list[Variable] | None = None,
    enums: list[EnumType] | None = None,
) -> AbiSnapshot:
    s = AbiSnapshot(library=library, version=version)
    if functions:
        s.functions = functions
    if types:
        s.types = types
    if variables:
        s.variables = variables
    if enums:
        s.enums = enums
    return s


def _parse(xml_str: str):  # noqa: ANN201
    return xml_fromstring(xml_str)


def _write_snapshot(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


# ===========================================================================
# UNIT TESTS
# ===========================================================================


# ---------------------------------------------------------------------------
# Schema structure tests
# ---------------------------------------------------------------------------

class TestJUnitSchema:
    def test_top_level_element(self) -> None:
        xml = to_junit_xml(_make_result([]))
        root = _parse(xml)
        assert root.tag == "testsuites"
        assert root.get("name") == "abicheck"

    def test_single_testsuite(self) -> None:
        xml = to_junit_xml(_make_result([]))
        root = _parse(xml)
        suites = root.findall("testsuite")
        assert len(suites) == 1
        assert suites[0].get("name") == "libfoo.so.1"

    def test_errors_always_zero(self) -> None:
        xml = to_junit_xml(_make_result([]))
        root = _parse(xml)
        assert root.get("errors") == "0"
        assert root.find("testsuite").get("errors") == "0"

    def test_xml_declaration(self) -> None:
        xml = to_junit_xml(_make_result([]))
        assert xml.startswith("<?xml version='1.0' encoding='UTF-8'?>")


# ---------------------------------------------------------------------------
# No changes → all pass
# ---------------------------------------------------------------------------

class TestNoChanges:
    def test_no_changes_no_snapshot(self) -> None:
        xml = to_junit_xml(
            _make_result([], verdict=Verdict.NO_CHANGE),
        )
        root = _parse(xml)
        assert root.get("tests") == "0"
        assert root.get("failures") == "0"

    def test_no_changes_with_snapshot(self) -> None:
        snap = _make_snapshot(functions=[
            Function(name="foo::bar", mangled="_ZN3foo3barEv", return_type="void"),
            Function(name="foo::baz", mangled="_ZN3foo3bazEi", return_type="int"),
        ])
        result = _make_result([], verdict=Verdict.NO_CHANGE)
        xml = to_junit_xml(result, snap)
        root = _parse(xml)
        ts = root.find("testsuite")
        assert ts.get("tests") == "2"
        assert ts.get("failures") == "0"
        # All testcases pass (no <failure> children)
        for tc in ts.findall("testcase"):
            assert tc.find("failure") is None


# ---------------------------------------------------------------------------
# Breaking changes → failures
# ---------------------------------------------------------------------------

class TestBreakingChanges:
    def test_func_removed_is_failure(self) -> None:
        changes = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="_ZN3foo6legacyEv",
                description="Function foo::legacy() was removed",
            ),
        ]
        xml = to_junit_xml(_make_result(changes))
        root = _parse(xml)
        ts = root.find("testsuite")
        assert ts.get("failures") == "1"
        tc = ts.find("testcase")
        assert tc.get("name") == "_ZN3foo6legacyEv"
        assert tc.get("classname") == "functions"
        fail = tc.find("failure")
        assert fail is not None
        assert "BREAKING" in fail.get("type")
        assert "func_removed" in fail.get("message")

    def test_type_size_changed_is_failure(self) -> None:
        changes = [
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="struct foo::Config",
                description="size changed from 16 to 24 bytes",
                old_value="16",
                new_value="24",
                source_location="include/foo.h:42",
            ),
        ]
        xml = to_junit_xml(_make_result(changes))
        root = _parse(xml)
        tc = root.find(".//testcase[@name='struct foo::Config']")
        assert tc is not None
        assert tc.get("classname") == "types"
        fail = tc.find("failure")
        assert fail is not None
        assert "Source: include/foo.h:42" in fail.text

    def test_func_added_passes(self) -> None:
        changes = [
            Change(
                kind=ChangeKind.FUNC_ADDED,
                symbol="_ZN3foo9new_thingEv",
                description="Function foo::new_thing() was added",
            ),
        ]
        xml = to_junit_xml(_make_result(changes, verdict=Verdict.COMPATIBLE))
        root = _parse(xml)
        ts = root.find("testsuite")
        assert ts.get("failures") == "0"
        tc = ts.find("testcase")
        assert tc.find("failure") is None

    def test_mixed_changes(self) -> None:
        changes = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="_ZN3foo6legacyEv",
                description="Function removed",
            ),
            Change(
                kind=ChangeKind.FUNC_ADDED,
                symbol="_ZN3foo9new_thingEv",
                description="Function added",
            ),
        ]
        xml = to_junit_xml(_make_result(changes))
        root = _parse(xml)
        ts = root.find("testsuite")
        assert ts.get("tests") == "2"
        assert ts.get("failures") == "1"

    def test_api_break_is_failure(self) -> None:
        """API_BREAK changes (e.g. enum member renamed) should be failures."""
        changes = [
            Change(
                kind=ChangeKind.ENUM_MEMBER_RENAMED,
                symbol="Status",
                description="Enum member renamed from OK to SUCCESS",
            ),
        ]
        xml = to_junit_xml(_make_result(changes, verdict=Verdict.API_BREAK))
        root = _parse(xml)
        ts = root.find("testsuite")
        assert ts.get("failures") == "1"
        fail = root.find(".//failure")
        assert fail.get("type") == "API_BREAK"


# ---------------------------------------------------------------------------
# COMPATIBLE_WITH_RISK handling
# ---------------------------------------------------------------------------

class TestCompatibleWithRisk:
    def test_risk_change_default_severity_passes(self) -> None:
        """COMPATIBLE_WITH_RISK changes with severity 'warning' should pass."""
        changes = [
            Change(
                kind=ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED,
                symbol="libc.so.6",
                description="New GLIBC_2.34 version requirement added",
            ),
        ]
        result = _make_result(changes, verdict=Verdict.COMPATIBLE_WITH_RISK)
        xml = to_junit_xml(result)
        root = _parse(xml)
        ts = root.find("testsuite")
        # Default severity for RISK_KINDS is "warning", not "error" — passes
        assert ts.get("failures") == "0"
        tc = ts.find("testcase")
        assert tc.find("failure") is None

    def test_risk_change_with_severity_preset_escalation_fails(self) -> None:
        """COMPATIBLE_WITH_RISK changes fail when a severity preset escalates
        ``potential_breaking`` to 'error' — the real, supported mechanism.
        RISK_KINDS have no per-kind severity in the policy registry (they are
        all "warning" by construction); only a SeverityConfig can escalate
        them to a JUnit failure without changing the finding's verdict.
        """
        from abicheck.severity import SeverityConfig, SeverityLevel

        changes = [
            Change(
                kind=ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED,
                symbol="libc.so.6",
                description="New GLIBC_2.34 version requirement added",
            ),
        ]
        result = _make_result(changes, verdict=Verdict.COMPATIBLE_WITH_RISK)
        severity_config = SeverityConfig(potential_breaking=SeverityLevel.ERROR)
        xml = to_junit_xml(result, severity_config=severity_config)
        root = _parse(xml)
        ts = root.find("testsuite")
        assert ts.get("failures") == "1"
        fail = root.find(".//failure")
        assert fail is not None
        assert fail.get("type") == "COMPATIBLE_WITH_RISK"

    def test_risk_change_escalated_via_policy_file_fails(self) -> None:
        """A PolicyFile override that promotes a RISK-kind's *verdict* to
        BREAKING must fail in JUnit — even with no severity_config.

        Regression test: JUnit's failure classification used to fall back to
        ``policy_for(change.kind).severity`` for a kind not in the (override-
        adjusted) breaking/api_break/risk sets, which silently ignored a
        PolicyFile override that had moved the kind's *effective verdict*
        without also being reflected in that per-kind severity lookup.
        """
        from abicheck.policy_file import PolicyFile

        changes = [
            Change(
                kind=ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED,
                symbol="libc.so.6",
                description="New GLIBC_2.34 version requirement added",
            ),
        ]
        result = _make_result(changes, verdict=Verdict.COMPATIBLE_WITH_RISK)
        result.policy_file = PolicyFile(
            overrides={ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED: Verdict.BREAKING}
        )
        xml = to_junit_xml(result)
        root = _parse(xml)
        ts = root.find("testsuite")
        assert ts.get("failures") == "1"
        fail = root.find(".//failure")
        assert fail is not None
        assert fail.get("type") == "BREAKING"


# ---------------------------------------------------------------------------
# Suppressed changes → pass
# ---------------------------------------------------------------------------

class TestSuppressedChanges:
    def test_suppressed_symbols_appear_as_passing(self) -> None:
        """Symbols that were suppressed (not in changes list) should still
        appear as passing test cases when the old snapshot is provided."""
        snap = _make_snapshot(functions=[
            Function(name="foo::bar", mangled="_ZN3foo3barEv", return_type="void"),
            Function(name="foo::suppressed", mangled="_ZN3foo10suppressedEv", return_type="void"),
        ])
        # Only one change — the suppressed one is not in the changes list
        changes = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="_ZN3foo3barEv",
                description="Function removed",
            ),
        ]
        result = _make_result(changes)
        xml = to_junit_xml(result, snap)
        root = _parse(xml)
        ts = root.find("testsuite")
        assert ts.get("tests") == "2"
        assert ts.get("failures") == "1"
        # The suppressed symbol passes
        suppressed_tc = ts.find(".//testcase[@name='_ZN3foo10suppressedEv']")
        assert suppressed_tc is not None
        assert suppressed_tc.find("failure") is None


# ---------------------------------------------------------------------------
# XML escaping of C++ mangled names with templates
# ---------------------------------------------------------------------------

class TestXmlEscaping:
    def test_template_symbol_escaping(self) -> None:
        """C++ mangled names with angle brackets must be properly escaped."""
        changes = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="_ZN3foo3barINS_3BazIiEEEEvT_",
                description="Function foo::bar<foo::Baz<int>>() removed",
            ),
        ]
        xml = to_junit_xml(_make_result(changes))
        # This should parse without error — if escaping is wrong, xml_fromstring fails
        root = _parse(xml)
        tc = root.find(".//testcase")
        assert tc.get("name") == "_ZN3foo3barINS_3BazIiEEEEvT_"

    def test_description_with_angle_brackets(self) -> None:
        changes = [
            Change(
                kind=ChangeKind.FUNC_RETURN_CHANGED,
                symbol="_ZN3foo3barEv",
                description="Return type changed from std::vector<int> to std::vector<long>",
                old_value="std::vector<int>",
                new_value="std::vector<long>",
            ),
        ]
        xml = to_junit_xml(_make_result(changes))
        root = _parse(xml)
        fail = root.find(".//failure")
        import xml.etree.ElementTree
        assert "std::vector<int>" in fail.text or "std::vector&lt;int&gt;" in xml.etree.ElementTree.tostring(fail, encoding="unicode")

    def test_ampersand_in_symbol(self) -> None:
        """Symbol names or descriptions containing & must be escaped."""
        changes = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="foo&bar",
                description="Function foo&bar() removed",
            ),
        ]
        xml = to_junit_xml(_make_result(changes))
        root = _parse(xml)  # Would raise if escaping failed
        tc = root.find(".//testcase")
        assert tc.get("name") == "foo&bar"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_description_uses_kind(self) -> None:
        """When description is empty, the failure message should still be useful."""
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="f", description=""),
        ]
        xml = to_junit_xml(_make_result(changes))
        root = _parse(xml)
        fail = root.find(".//failure")
        assert "func_removed" in fail.get("message")
        # Body should use kind-derived text when description is empty
        assert fail.text is not None and len(fail.text) > 0

    def test_none_old_value_none_new_value(self) -> None:
        """When both old_value and new_value are None, no (? → ?) line appears."""
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="f", description="removed"),
        ]
        xml = to_junit_xml(_make_result(changes))
        root = _parse(xml)
        fail = root.find(".//failure")
        assert "→" not in fail.text

    def test_old_value_is_empty_string(self) -> None:
        """Empty string old_value should still emit the (? → new) line (is not None)."""
        changes = [
            Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="f",
                   description="changed", old_value="", new_value="int"),
        ]
        xml = to_junit_xml(_make_result(changes))
        root = _parse(xml)
        fail = root.find(".//failure")
        assert "→" in fail.text

    def test_multiple_changes_same_symbol(self) -> None:
        """Multiple breaking changes on the same symbol produce multiple <failure> children."""
        changes = [
            Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="f",
                   description="return type changed", old_value="int", new_value="long"),
            Change(kind=ChangeKind.FUNC_PARAMS_CHANGED, symbol="f",
                   description="parameter count changed"),
        ]
        xml = to_junit_xml(_make_result(changes))
        root = _parse(xml)
        ts = root.find("testsuite")
        # Only 1 testcase (same symbol)
        assert ts.get("tests") == "1"
        tc = ts.find("testcase")
        failures = tc.findall("failure")
        assert len(failures) == 2


# ---------------------------------------------------------------------------
# Failure attributes
# ---------------------------------------------------------------------------

class TestFailureAttributes:
    def test_failure_message_format(self) -> None:
        changes = [
            Change(
                kind=ChangeKind.FUNC_RETURN_CHANGED,
                symbol="_ZN3foo3bazEv",
                description="Return type changed from int to long",
                old_value="int",
                new_value="long",
            ),
        ]
        xml = to_junit_xml(_make_result(changes))
        root = _parse(xml)
        fail = root.find(".//failure")
        msg = fail.get("message")
        assert msg == "func_return_changed: Return type changed from int to long"

    def test_failure_body_includes_values(self) -> None:
        changes = [
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="MyStruct",
                description="size changed",
                old_value="16",
                new_value="24",
            ),
        ]
        xml = to_junit_xml(_make_result(changes))
        root = _parse(xml)
        fail = root.find(".//failure")
        assert "(16 → 24)" in fail.text


# ---------------------------------------------------------------------------
# Classname grouping
# ---------------------------------------------------------------------------

class TestClassnameGrouping:
    def test_function_classname(self) -> None:
        changes = [Change(kind=ChangeKind.FUNC_REMOVED, symbol="f", description="")]
        xml = to_junit_xml(_make_result(changes))
        root = _parse(xml)
        assert root.find(".//testcase").get("classname") == "functions"

    def test_variable_classname(self) -> None:
        changes = [Change(kind=ChangeKind.VAR_REMOVED, symbol="v", description="")]
        xml = to_junit_xml(_make_result(changes))
        root = _parse(xml)
        assert root.find(".//testcase").get("classname") == "variables"

    def test_type_classname(self) -> None:
        changes = [Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="T", description="")]
        xml = to_junit_xml(_make_result(changes))
        root = _parse(xml)
        assert root.find(".//testcase").get("classname") == "types"

    def test_enum_classname(self) -> None:
        changes = [Change(kind=ChangeKind.ENUM_MEMBER_REMOVED, symbol="E", description="")]
        xml = to_junit_xml(_make_result(changes))
        root = _parse(xml)
        assert root.find(".//testcase").get("classname") == "enums"

    def test_elf_metadata_classname(self) -> None:
        changes = [Change(kind=ChangeKind.SONAME_CHANGED, symbol="soname", description="")]
        xml = to_junit_xml(_make_result(changes))
        root = _parse(xml)
        assert root.find(".//testcase").get("classname") == "metadata"


# ---------------------------------------------------------------------------
# Multi-suite (compare-release)
# ---------------------------------------------------------------------------

class TestMultiSuite:
    def test_multiple_testsuites(self) -> None:
        r1 = _make_result(
            [Change(kind=ChangeKind.FUNC_REMOVED, symbol="f1", description="removed")],
            library="libfoo.so.1",
        )
        r2 = _make_result(
            [],
            library="libbar.so.2",
            verdict=Verdict.NO_CHANGE,
        )
        xml = to_junit_xml_multi([(r1, None), (r2, None)])
        root = _parse(xml)
        suites = root.findall("testsuite")
        assert len(suites) == 2
        assert suites[0].get("name") == "libfoo.so.1"
        assert suites[1].get("name") == "libbar.so.2"

    def test_rollup_counts(self) -> None:
        r1 = _make_result(
            [Change(kind=ChangeKind.FUNC_REMOVED, symbol="f1", description="removed")],
            library="libfoo.so.1",
        )
        r2 = _make_result(
            [Change(kind=ChangeKind.FUNC_ADDED, symbol="f2", description="added")],
            library="libbar.so.2",
            verdict=Verdict.COMPATIBLE,
        )
        xml = to_junit_xml_multi([(r1, None), (r2, None)])
        root = _parse(xml)
        assert root.get("tests") == "2"
        assert root.get("failures") == "1"

    def test_empty_multi(self) -> None:
        xml = to_junit_xml_multi([])
        root = _parse(xml)
        assert root.get("tests") == "0"
        assert root.get("failures") == "0"
        assert root.findall("testsuite") == []


# ---------------------------------------------------------------------------
# With full snapshot — pass rate
# ---------------------------------------------------------------------------

class TestWithSnapshot:
    def test_pass_rate_includes_all_symbols(self) -> None:
        """Total test count includes unchanged symbols from old snapshot."""
        snap = _make_snapshot(
            functions=[
                Function(name="f1", mangled="f1", return_type="void"),
                Function(name="f2", mangled="f2", return_type="void"),
                Function(name="f3", mangled="f3", return_type="void"),
            ],
            types=[
                RecordType(name="MyStruct", kind="struct"),
            ],
        )
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="f1", description="removed"),
        ]
        result = _make_result(changes)
        xml = to_junit_xml(result, snap)
        root = _parse(xml)
        ts = root.find("testsuite")
        # 3 functions + 1 type = 4 total
        assert ts.get("tests") == "4"
        assert ts.get("failures") == "1"

    def test_snapshot_with_variables_and_enums(self) -> None:
        """Snapshot containing variables and enums — all appear as testcases."""
        from abicheck.model import Visibility
        snap = _make_snapshot(
            functions=[
                Function(name="f1", mangled="f1", return_type="void"),
            ],
            variables=[
                Variable(name="g_count", mangled="g_count", type="int",
                         visibility=Visibility.PUBLIC),
            ],
            enums=[
                EnumType(name="Color", members=[]),
            ],
        )
        changes = [
            Change(kind=ChangeKind.VAR_REMOVED, symbol="g_count", description="removed"),
        ]
        result = _make_result(changes)
        xml = to_junit_xml(result, snap)
        root = _parse(xml)
        ts = root.find("testsuite")
        # 1 function + 1 variable + 1 enum = 3 total
        assert ts.get("tests") == "3"
        assert ts.get("failures") == "1"
        # Variable testcase has failure
        var_tc = ts.find(".//testcase[@name='g_count']")
        assert var_tc is not None
        assert var_tc.get("classname") == "variables"
        assert var_tc.find("failure") is not None
        # Enum testcase passes
        enum_tc = ts.find(".//testcase[@name='Color']")
        assert enum_tc is not None
        assert enum_tc.get("classname") == "enums"
        assert enum_tc.find("failure") is None

    def test_additions_included_in_count(self) -> None:
        """New symbols (not in old snapshot) should also be counted."""
        snap = _make_snapshot(
            functions=[
                Function(name="f1", mangled="f1", return_type="void"),
            ],
        )
        changes = [
            Change(kind=ChangeKind.FUNC_ADDED, symbol="f2", description="added"),
        ]
        result = _make_result(changes, verdict=Verdict.COMPATIBLE)
        xml = to_junit_xml(result, snap)
        root = _parse(xml)
        ts = root.find("testsuite")
        # f1 (from snapshot) + f2 (addition) = 2
        assert ts.get("tests") == "2"
        assert ts.get("failures") == "0"


# ---------------------------------------------------------------------------
# Valid XML output
# ---------------------------------------------------------------------------

class TestValidXml:
    def test_output_is_valid_xml(self) -> None:
        """Output must be parseable XML."""
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="sym1", description="desc"),
            Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="T1", description="size",
                   old_value="8", new_value="16"),
            Change(kind=ChangeKind.FUNC_ADDED, symbol="sym2", description="added"),
        ]
        xml = to_junit_xml(_make_result(changes))
        # Should not raise
        root = xml_fromstring(xml)
        assert root.tag == "testsuites"


# ---------------------------------------------------------------------------
# show_only filter
# ---------------------------------------------------------------------------

class TestShowOnly:
    def test_show_only_breaking_filters_compatible(self) -> None:
        """--show-only breaking hides compatible additions."""
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="old_func",
                   description="removed"),
            Change(kind=ChangeKind.FUNC_ADDED, symbol="new_func",
                   description="added"),
        ]
        result = _make_result(changes)
        xml = to_junit_xml(result, show_only="breaking")
        root = _parse(xml)
        ts = root.find("testsuite")
        # Only the breaking change should appear
        assert ts.get("tests") == "1"
        assert ts.get("failures") == "1"
        tc = ts.find("testcase")
        assert tc.get("name") == "old_func"

    def test_show_only_functions_filters_types(self) -> None:
        """--show-only functions hides type changes."""
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="f1",
                   description="removed"),
            Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="MyStruct",
                   description="size changed", old_value="8", new_value="16"),
        ]
        result = _make_result(changes)
        xml = to_junit_xml(result, show_only="functions")
        root = _parse(xml)
        ts = root.find("testsuite")
        assert ts.get("tests") == "1"
        tc = ts.find("testcase")
        assert tc.get("name") == "f1"

    def test_show_only_with_multi(self) -> None:
        """show_only works with to_junit_xml_multi."""
        r1 = _make_result(
            [
                Change(kind=ChangeKind.FUNC_REMOVED, symbol="f1",
                       description="removed"),
                Change(kind=ChangeKind.FUNC_ADDED, symbol="f2",
                       description="added"),
            ],
            library="libfoo.so.1",
        )
        xml = to_junit_xml_multi([(r1, None)], show_only="breaking")
        root = _parse(xml)
        ts = root.find("testsuite")
        assert ts.get("tests") == "1"
        assert ts.get("failures") == "1"


# ---------------------------------------------------------------------------
# failure_count correctness with extra_changes
# ---------------------------------------------------------------------------

class TestFailureCountCorrectness:
    def test_failure_count_when_first_change_is_compatible(self) -> None:
        """failure_count must count symbols with ANY failing change,
        even when the first change per symbol is compatible."""
        changes = [
            # First change for symbol "f" is compatible (addition)
            Change(kind=ChangeKind.FUNC_ADDED, symbol="f",
                   description="added"),
            # Second change for symbol "f" is breaking
            Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="f",
                   description="return type changed"),
        ]
        xml = to_junit_xml(_make_result(changes))
        root = _parse(xml)
        ts = root.find("testsuite")
        # The symbol should be counted as a failure
        assert ts.get("failures") == "1"


# ---------------------------------------------------------------------------
# show_only + snapshot interaction
# ---------------------------------------------------------------------------

class TestShowOnlyWithSnapshot:
    def test_show_only_excludes_unchanged_snapshot_symbols(self) -> None:
        """When show_only is active, unchanged snapshot symbols must NOT
        appear as passing testcases (documented: 'filtered-out changes
        are omitted entirely')."""
        snap = _make_snapshot(
            functions=[
                Function(name="unchanged", mangled="unchanged", return_type="void"),
                Function(name="removed", mangled="removed", return_type="void"),
            ],
        )
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="removed",
                   description="removed"),
        ]
        result = _make_result(changes)
        xml = to_junit_xml(result, snap, show_only="breaking")
        root = _parse(xml)
        ts = root.find("testsuite")
        # Only the breaking change should appear — not the unchanged "unchanged"
        assert ts.get("tests") == "1"
        assert ts.get("failures") == "1"
        tcs = ts.findall("testcase")
        assert len(tcs) == 1
        assert tcs[0].get("name") == "removed"


# ---------------------------------------------------------------------------
# severity_config propagation
# ---------------------------------------------------------------------------

class TestSeverityConfig:
    def test_severity_config_escalates_addition_to_failure(self) -> None:
        """When severity_config marks additions as 'error', they become
        failures in JUnit."""
        from abicheck.severity import SeverityConfig, SeverityLevel

        config = SeverityConfig(
            abi_breaking=SeverityLevel.ERROR,
            potential_breaking=SeverityLevel.ERROR,
            quality_issues=SeverityLevel.WARNING,
            addition=SeverityLevel.ERROR,
        )
        changes = [
            Change(kind=ChangeKind.FUNC_ADDED, symbol="f",
                   description="added"),
        ]
        result = _make_result(changes, verdict=Verdict.COMPATIBLE)
        xml = to_junit_xml(result, severity_config=config)
        root = _parse(xml)
        ts = root.find("testsuite")
        assert ts.get("failures") == "1"
        # Verified defect: the failure `type=` must not still say COMPATIBLE
        # once severity_config is the reason it failed at all — it should
        # name the category (ADDITION) that severity_config promoted.
        fail = root.find(".//failure")
        assert fail.get("type") == "ADDITION"

    def test_demoted_compatible_fails_under_strict_preset(self) -> None:
        """ADR-027 review: a --pattern-verdicts demotion to COMPATIBLE must
        still be a JUnit failure under a strict severity preset, matching the
        severity-aware exit code (classify_change_object → QUALITY_ISSUES →
        error). Otherwise CI consuming the JUnit file misses the failure that
        the exit status reports."""
        from abicheck.severity import PRESET_STRICT

        demoted = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Ctx", description="size"
        )
        demoted.effective_verdict = Verdict.COMPATIBLE
        result = _make_result([demoted], verdict=Verdict.COMPATIBLE)
        # Default preset: quality issues are warnings → testcase passes.
        default_xml = _parse(to_junit_xml(result))
        assert default_xml.find("testsuite").get("failures") == "0"
        # Strict preset: quality issues escalate to error → failure, agreeing
        # with the nonzero severity-aware exit code.
        strict_xml = _parse(to_junit_xml(result, severity_config=PRESET_STRICT))
        assert strict_xml.find("testsuite").get("failures") == "1"
        # The failure type must reflect *why* it failed under the strict
        # preset (a quality issue promoted to error), not the raw COMPATIBLE
        # verdict it was demoted to.
        fail = strict_xml.find(".//failure")
        assert fail.get("type") == "QUALITY_ISSUE"

    def test_severity_config_demotes_breaking_to_pass(self) -> None:
        """When severity_config marks abi_breaking as 'warning', additions
        that were BREAKING still fail (breaking_set takes priority)."""
        # Note: _is_failure always returns True for breaking_set regardless of
        # severity_config. This test verifies that contract.
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="f",
                   description="removed"),
        ]
        result = _make_result(changes)
        xml = to_junit_xml(result)
        root = _parse(xml)
        ts = root.find("testsuite")
        assert ts.get("failures") == "1"

    def test_severity_config_demotes_breaking_verdict_to_pass(self) -> None:
        """Codex review on #549: `_is_failure` used to return True for a
        BREAKING/API_BREAK verdict *before* consulting `severity_config`, so
        `--severity-preset info-only` (which demotes every category to
        'info') could exit 0 via the severity-aware gate while the JUnit XML
        still contained a `<failure>` for the same removal — disagreeing
        with `severity.compute_exit_code`, which treats `severity_config` as
        the sole source of truth once given. Fixed by consulting
        `severity_config` first, unconditionally, matching
        `compute_exit_code`'s own logic."""
        from abicheck.severity import PRESET_INFO_ONLY

        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="f", description="removed"),
        ]
        result = _make_result(changes, verdict=Verdict.BREAKING)
        xml = to_junit_xml(result, severity_config=PRESET_INFO_ONLY)
        root = _parse(xml)
        ts = root.find("testsuite")
        assert ts.get("failures") == "0"

    def test_failure_type_reflects_severity_category_not_raw_verdict(self) -> None:
        """Verified defect: `_failure_type` ignored `severity_config` and
        always derived `type=` from the raw effective *verdict*
        (`_VERDICT_TO_JUNIT_TYPE`, which has no COMPATIBLE entry and falls
        back to `"COMPATIBLE"`), even when `_is_failure` had already decided
        pass/fail from the effective *category* instead. A COMPATIBLE
        addition promoted to `error` therefore both failed and reported
        `type="COMPATIBLE"` — self-contradictory. The type must instead name
        the category (ADDITION) that made it fail."""
        from abicheck.severity import resolve_severity_config

        changes = [
            Change(kind=ChangeKind.FUNC_ADDED, symbol="f", description="added"),
        ]
        result = _make_result(changes, verdict=Verdict.COMPATIBLE)
        cfg = resolve_severity_config("default", addition="error")
        xml = to_junit_xml(result, severity_config=cfg)
        root = _parse(xml)
        fail = root.find(".//failure")
        assert fail.get("type") != "COMPATIBLE"
        assert fail.get("type") == "ADDITION"

    def test_failure_type_distinguishes_api_break_from_risk_under_severity_config(
        self,
    ) -> None:
        """POTENTIAL_BREAKING covers both API_BREAK and RISK kinds; the JUnit
        type= must still distinguish them (matching the legacy, no-severity-
        config type mapping) rather than collapsing both to one generic
        label."""
        from abicheck.severity import resolve_severity_config

        api_break = Change(
            kind=ChangeKind.ENUM_MEMBER_RENAMED, symbol="E", description="renamed",
        )
        risk = Change(
            kind=ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, symbol="f",
            description="version req added",
        )
        cfg = resolve_severity_config("default", potential_breaking="error")

        api_result = _make_result([api_break], verdict=Verdict.API_BREAK)
        api_xml = _parse(to_junit_xml(api_result, severity_config=cfg))
        assert api_xml.find(".//failure").get("type") == "API_BREAK"

        risk_result = _make_result([risk], verdict=Verdict.COMPATIBLE_WITH_RISK)
        risk_xml = _parse(to_junit_xml(risk_result, severity_config=cfg))
        assert risk_xml.find(".//failure").get("type") == "COMPATIBLE_WITH_RISK"

    def test_failure_type_honours_per_change_effective_verdict_override(
        self,
    ) -> None:
        """A per-change `effective_verdict` override (A4 pattern-verdict
        modulation, PolicyFile) can classify a finding as POTENTIAL_BREAKING
        without its *kind* being a member of either api_break_set or
        risk_set (those sets classify by raw kind, unaffected by a
        per-change override) — `_failure_type` must derive the API_BREAK vs.
        COMPATIBLE_WITH_RISK subtype from the finding's actual effective
        verdict, not from raw kind-set membership, so it doesn't fall back
        to a generic label that contradicts the override's own intent
        (CodeRabbit review, PR #557)."""
        from abicheck.severity import resolve_severity_config

        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="f", description="removed")
        c.effective_verdict = Verdict.COMPATIBLE_WITH_RISK
        result = _make_result([c], verdict=Verdict.COMPATIBLE_WITH_RISK)
        cfg = resolve_severity_config("default", potential_breaking="error")
        xml = to_junit_xml(result, severity_config=cfg)
        fail = _parse(xml).find(".//failure")
        assert fail.get("type") == "COMPATIBLE_WITH_RISK"

    def test_failure_type_effective_verdict_wins_over_raw_kind_set(self) -> None:
        """A raw-risk-set kind (SYMBOL_VERSION_REQUIRED_ADDED) whose
        `effective_verdict` is overridden to API_BREAK must report
        type="API_BREAK", not "COMPATIBLE_WITH_RISK" — the type it would get
        from raw kind-set membership alone, which would contradict the
        override (CodeRabbit review, PR #557)."""
        from abicheck.severity import resolve_severity_config

        c = Change(
            kind=ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, symbol="f",
            description="version req added",
        )
        c.effective_verdict = Verdict.API_BREAK
        result = _make_result([c], verdict=Verdict.API_BREAK)
        cfg = resolve_severity_config("default", potential_breaking="error")
        xml = to_junit_xml(result, severity_config=cfg)
        fail = _parse(xml).find(".//failure")
        assert fail.get("type") == "API_BREAK"


# ---------------------------------------------------------------------------
# Error libraries in multi-suite
# ---------------------------------------------------------------------------

class TestErrorLibraries:
    def test_error_library_appears_as_error_testsuite(self) -> None:
        """Failed compare-release pairs should produce <error> testcases."""
        r1 = _make_result(
            [Change(kind=ChangeKind.FUNC_REMOVED, symbol="f1",
                    description="removed")],
            library="libfoo.so.1",
        )
        error_libs = [
            {"library": "libbar.so.1", "error": "missing headers"},
        ]
        xml = to_junit_xml_multi(
            [(r1, None)],
            error_libraries=error_libs,
        )
        root = _parse(xml)
        suites = root.findall("testsuite")
        assert len(suites) == 2
        # First suite is normal
        assert suites[0].get("name") == "libfoo.so.1"
        # Second suite is the error
        err_suite = suites[1]
        assert err_suite.get("name") == "libbar.so.1"
        assert err_suite.get("errors") == "1"
        err_tc = err_suite.find("testcase")
        assert err_tc.find("error") is not None
        assert "missing headers" in err_tc.find("error").get("message")
        # Roll-up counts
        assert root.get("errors") == "1"
        assert root.get("tests") == "2"  # 1 normal + 1 error


# ===========================================================================
# INTEGRATION TESTS — CLI pipeline
# ===========================================================================


class TestJUnitCLICompare:
    """Integration tests that run ``abicheck compare --format junit`` via
    the Click test runner with JSON snapshot files."""

    @staticmethod
    def _snap(version: str, funcs: list[Function]) -> AbiSnapshot:
        return AbiSnapshot(library="libtest.so", version=version, functions=funcs)

    def test_compare_no_changes(self, tmp_path: Path) -> None:
        """No ABI changes → valid JUnit XML with zero failures."""
        from abicheck.cli import main

        funcs = [Function(name="foo", mangled="_Z3foov", return_type="int")]
        old = self._snap("1.0", funcs)
        new = self._snap("2.0", funcs)
        _write_snapshot(tmp_path / "old.json", old)
        _write_snapshot(tmp_path / "new.json", new)

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare",
            str(tmp_path / "old.json"),
            str(tmp_path / "new.json"),
            "--format", "junit",
        ])
        assert result.exit_code == 0, result.output
        root = xml_fromstring(result.output)
        assert root.tag == "testsuites"
        assert root.get("failures") == "0"
        ts = root.find("testsuite")
        assert ts.get("name") == "libtest.so"

    def test_compare_breaking_changes(self, tmp_path: Path) -> None:
        """Removing a function → JUnit XML with failure, exit code 4."""
        from abicheck.cli import main

        old = self._snap("1.0", [
            Function(name="foo", mangled="_Z3foov", return_type="int"),
            Function(name="bar", mangled="_Z3barv", return_type="void"),
        ])
        new = self._snap("2.0", [
            Function(name="foo", mangled="_Z3foov", return_type="int"),
        ])
        _write_snapshot(tmp_path / "old.json", old)
        _write_snapshot(tmp_path / "new.json", new)

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare",
            str(tmp_path / "old.json"),
            str(tmp_path / "new.json"),
            "--format", "junit",
        ])
        assert result.exit_code == 4  # BREAKING
        root = xml_fromstring(result.output)
        assert root.get("failures") == "1"
        fail = root.find(".//failure")
        assert fail is not None
        assert "BREAKING" in fail.get("type")

    def test_compare_compatible_addition(self, tmp_path: Path) -> None:
        """Adding a function → JUnit XML with zero failures, exit code 0."""
        from abicheck.cli import main

        old = self._snap("1.0", [
            Function(name="foo", mangled="_Z3foov", return_type="int"),
        ])
        new = self._snap("2.0", [
            Function(name="foo", mangled="_Z3foov", return_type="int"),
            Function(name="bar", mangled="_Z3barv", return_type="void"),
        ])
        _write_snapshot(tmp_path / "old.json", old)
        _write_snapshot(tmp_path / "new.json", new)

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare",
            str(tmp_path / "old.json"),
            str(tmp_path / "new.json"),
            "--format", "junit",
        ])
        assert result.exit_code == 0
        root = xml_fromstring(result.output)
        assert root.get("failures") == "0"
        # Both kept and added functions should appear as testcases
        tcs = root.findall(".//testcase")
        assert len(tcs) == 2

    def test_compare_output_to_file(self, tmp_path: Path) -> None:
        """--format junit -o file.xml writes valid XML to file."""
        from abicheck.cli import main

        funcs = [Function(name="foo", mangled="_Z3foov", return_type="int")]
        old = self._snap("1.0", funcs)
        new = self._snap("2.0", funcs)
        _write_snapshot(tmp_path / "old.json", old)
        _write_snapshot(tmp_path / "new.json", new)
        out_path = tmp_path / "results.xml"

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare",
            str(tmp_path / "old.json"),
            str(tmp_path / "new.json"),
            "--format", "junit",
            "-o", str(out_path),
        ])
        assert result.exit_code == 0, result.output
        assert out_path.exists()
        content = out_path.read_text(encoding="utf-8")
        root = xml_fromstring(content)
        assert root.tag == "testsuites"

    def test_compare_with_suppression(self, tmp_path: Path) -> None:
        """Suppressed changes should not appear as failures in JUnit output."""
        from abicheck.cli import main

        old = self._snap("1.0", [
            Function(name="foo", mangled="_Z3foov", return_type="int"),
            Function(name="bar", mangled="_Z3barv", return_type="void"),
        ])
        new = self._snap("2.0", [
            Function(name="foo", mangled="_Z3foov", return_type="int"),
        ])
        _write_snapshot(tmp_path / "old.json", old)
        _write_snapshot(tmp_path / "new.json", new)

        # Write a suppression file that suppresses the removed function
        supp_path = tmp_path / "supp.yml"
        supp_path.write_text(
            "version: 1\n"
            "suppressions:\n"
            "  - symbol: _Z3barv\n"
            "    change_kind: func_removed\n"
            "    reason: intentional removal\n",
            encoding="utf-8",
        )

        out_path = tmp_path / "results.xml"
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare",
            str(tmp_path / "old.json"),
            str(tmp_path / "new.json"),
            "--format", "junit",
            "--suppress", str(supp_path),
            "-o", str(out_path),
        ])
        # With suppression, verdict may be NO_CHANGE or COMPATIBLE
        assert result.exit_code == 0, result.output
        content = out_path.read_text(encoding="utf-8")
        root = xml_fromstring(content)
        assert root.get("failures") == "0"

    def test_compare_return_type_changed(self, tmp_path: Path) -> None:
        """Return type change → JUnit failure with old/new values."""
        from abicheck.cli import main

        old = self._snap("1.0", [
            Function(name="getval", mangled="_Z6getvalv", return_type="int"),
        ])
        new = self._snap("2.0", [
            Function(name="getval", mangled="_Z6getvalv", return_type="long"),
        ])
        _write_snapshot(tmp_path / "old.json", old)
        _write_snapshot(tmp_path / "new.json", new)

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare",
            str(tmp_path / "old.json"),
            str(tmp_path / "new.json"),
            "--format", "junit",
        ])
        assert result.exit_code == 4  # BREAKING
        root = xml_fromstring(result.output)
        fail = root.find(".//failure")
        assert fail is not None
        assert "func_return_changed" in fail.get("message")

    def test_compare_multiple_change_types(self, tmp_path: Path) -> None:
        """Mix of additions, removals, and unchanged → correct counts."""
        from abicheck.cli import main

        old = self._snap("1.0", [
            Function(name="keep", mangled="_Z4keepv", return_type="void"),
            Function(name="remove", mangled="_Z6removev", return_type="void"),
        ])
        new = self._snap("2.0", [
            Function(name="keep", mangled="_Z4keepv", return_type="void"),
            Function(name="added", mangled="_Z5addedv", return_type="void"),
        ])
        _write_snapshot(tmp_path / "old.json", old)
        _write_snapshot(tmp_path / "new.json", new)

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare",
            str(tmp_path / "old.json"),
            str(tmp_path / "new.json"),
            "--format", "junit",
        ])
        root = xml_fromstring(result.output)
        ts = root.find("testsuite")
        # Exactly one function was removed
        assert ts.get("failures") == "1"

    def test_compare_policy_sdk_vendor(self, tmp_path: Path) -> None:
        """Different policy can reclassify changes, reflected in JUnit."""
        from abicheck.cli import main

        old = self._snap("1.0", [
            Function(name="foo", mangled="_Z3foov", return_type="int"),
        ])
        new = self._snap("2.0", [
            Function(name="foo", mangled="_Z3foov", return_type="int"),
            Function(name="bar", mangled="_Z3barv", return_type="void"),
        ])
        _write_snapshot(tmp_path / "old.json", old)
        _write_snapshot(tmp_path / "new.json", new)

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare",
            str(tmp_path / "old.json"),
            str(tmp_path / "new.json"),
            "--format", "junit",
            "--policy", "sdk_vendor",
        ])
        assert result.exit_code == 0
        root = xml_fromstring(result.output)
        assert root.get("failures") == "0"

    def test_compare_show_only_breaking(self, tmp_path: Path) -> None:
        """--show-only breaking with --format junit filters to breaking only."""
        from abicheck.cli import main

        old = self._snap("1.0", [
            Function(name="foo", mangled="_Z3foov", return_type="int"),
            Function(name="bar", mangled="_Z3barv", return_type="void"),
        ])
        new = self._snap("2.0", [
            Function(name="foo", mangled="_Z3foov", return_type="int"),
            Function(name="baz", mangled="_Z3bazv", return_type="void"),
        ])
        _write_snapshot(tmp_path / "old.json", old)
        _write_snapshot(tmp_path / "new.json", new)

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare",
            str(tmp_path / "old.json"),
            str(tmp_path / "new.json"),
            "--format", "junit",
            "--show-only", "breaking",
        ])
        assert result.exit_code == 4  # BREAKING
        root = xml_fromstring(result.output)
        ts = root.find("testsuite")
        # Only the breaking removal should be a failure (addition is filtered out)
        assert ts.get("failures") == "1"
        # The removed function should have a <failure> element
        failures = root.findall(".//failure")
        assert len(failures) == 1
        assert "BREAKING" in failures[0].get("type")

    def test_compare_stat_with_junit_produces_xml(self, tmp_path: Path) -> None:
        """--stat --format junit should still produce JUnit XML (stat ignored)."""
        from abicheck.cli import main

        funcs = [Function(name="foo", mangled="_Z3foov", return_type="int")]
        old = self._snap("1.0", funcs)
        new = self._snap("2.0", funcs)
        _write_snapshot(tmp_path / "old.json", old)
        _write_snapshot(tmp_path / "new.json", new)

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare",
            str(tmp_path / "old.json"),
            str(tmp_path / "new.json"),
            "--format", "junit",
            "--stat",
        ])
        assert result.exit_code == 0, result.output
        root = xml_fromstring(result.output)
        assert root.tag == "testsuites"

    def test_format_junit_accepted_by_cli(self) -> None:
        """--format junit is recognized without error (even if inputs are bad)."""
        from abicheck.cli import main

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", "/nonexistent/old.json", "/nonexistent/new.json",
            "--format", "junit",
        ])
        # Should fail on missing file, NOT on unrecognized format
        assert "Unsupported output format" not in (result.output or "")

    def test_xml_output_is_well_formed(self, tmp_path: Path) -> None:
        """Stress test: verify well-formed XML with special characters."""
        from abicheck.cli import main
        from abicheck.model import Param

        old = self._snap("1.0", [
            Function(name="bar<int>", mangled="_Z3barIiEvT_", return_type="void",
                     params=[Param(name="x", type="int")]),
        ])
        new = self._snap("2.0", [
            Function(name="bar<int>", mangled="_Z3barIiEvT_", return_type="void",
                     params=[Param(name="x", type="long")]),
        ])
        _write_snapshot(tmp_path / "old.json", old)
        _write_snapshot(tmp_path / "new.json", new)

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare",
            str(tmp_path / "old.json"),
            str(tmp_path / "new.json"),
            "--format", "junit",
        ])
        # Must parse as valid XML regardless of exit code
        root = xml_fromstring(result.output)
        assert root.tag == "testsuites"


class TestScopedProperties:
    """`--used-by`/`--required-symbol(s)` scoping (ADR-043 + CLI-audit P1).

    The scoped gate is authoritative for this testsuite's own `failures`
    count and each `<testcase>`'s pass/fail status when scoping is active --
    `result.verdict` (the full, unscoped library verdict) is still reported
    as `abicheck.full_library_verdict` for context, but no longer drives
    what a JUnit-consuming CI dashboard treats as failing."""

    def test_no_properties_when_no_scoping(self) -> None:
        r = _make_result([], verdict=Verdict.BREAKING)
        xml_str = to_junit_xml(r)
        root = _parse(xml_str)
        ts = root.find("testsuite")
        assert ts.find("properties") is None

    def test_properties_present_and_can_disagree_with_full_verdict(self) -> None:
        r = _make_result([], verdict=Verdict.BREAKING)
        r.scoped_verdict = Verdict.COMPATIBLE  # type: ignore[attr-defined]
        r.gate_scope = "used_by"  # type: ignore[attr-defined]
        r.used_by = [{"app": "/bin/myapp", "verdict": "COMPATIBLE"}]  # type: ignore[attr-defined]
        xml_str = to_junit_xml(r)
        root = _parse(xml_str)
        ts = root.find("testsuite")
        props = {
            p.get("name"): p.get("value") for p in ts.find("properties")
        }
        assert props["abicheck.gate_scope"] == "used_by"
        assert props["abicheck.gate_verdict"] == "COMPATIBLE"
        assert props["abicheck.full_library_verdict"] == "BREAKING"
        assert props["abicheck.used_by_app_count"] == "1"

    def test_properties_carry_required_symbol_contract_verdict(self) -> None:
        r = _make_result([], verdict=Verdict.BREAKING)
        r.scoped_verdict = Verdict.BREAKING  # type: ignore[attr-defined]
        r.required_symbols = {"verdict": "BREAKING"}  # type: ignore[attr-defined]
        xml_str = to_junit_xml(r)
        root = _parse(xml_str)
        ts = root.find("testsuite")
        props = {
            p.get("name"): p.get("value") for p in ts.find("properties")
        }
        assert props["abicheck.required_symbol_contract_verdict"] == "BREAKING"

    def test_gate_exit_code_follows_severity_scheme(self) -> None:
        # Under a severity scheme the scoped exit code can be floored at 0
        # even for a BREAKING scoped verdict -- the properties must report
        # the actual computed value/scheme.
        r = _make_result([], verdict=Verdict.BREAKING)
        r.scoped_verdict = Verdict.BREAKING  # type: ignore[attr-defined]
        r.scoped_exit_code = 0  # type: ignore[attr-defined]
        r.scoped_exit_code_scheme = "severity"  # type: ignore[attr-defined]
        xml_str = to_junit_xml(r)
        root = _parse(xml_str)
        ts = root.find("testsuite")
        props = {
            p.get("name"): p.get("value") for p in ts.find("properties")
        }
        assert props["abicheck.gate_exit_code"] == "0"
        assert props["abicheck.gate_exit_code_scheme"] == "severity"

    def test_irrelevant_change_does_not_fail_but_relevant_one_does(self) -> None:
        # A change outside the --used-by/--required-symbol gate's relevance
        # must not count as a JUnit failure -- it's out of scope for the
        # gate this testsuite now reports (CLI-audit P1).
        from abicheck.reporter import _finding_id

        relevant = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo")
        irrelevant = Change(ChangeKind.FUNC_REMOVED, "_Z3barv", "removed: bar")
        r = _make_result([relevant, irrelevant], verdict=Verdict.BREAKING)
        r.scoped_verdict = Verdict.BREAKING  # type: ignore[attr-defined]
        r.gate_scope = "used_by"  # type: ignore[attr-defined]
        r.scoped_relevant_finding_ids = frozenset({_finding_id(relevant)})  # type: ignore[attr-defined]
        xml_str = to_junit_xml(r)
        root = _parse(xml_str)
        ts = root.find("testsuite")
        assert ts.get("failures") == "1"
        tcs = {tc.get("name"): tc for tc in ts.findall("testcase")}
        assert tcs["_Z3foov"].find("failure") is not None
        assert tcs["_Z3barv"].find("failure") is None

    def test_missing_contract_emits_a_failing_testcase(self) -> None:
        # A required symbol absent from the new library has no backing diff
        # Change -- without a synthetic testcase the gate's own `failures`
        # count could be nonzero while nothing in the XML explains why.
        r = _make_result([], verdict=Verdict.COMPATIBLE)
        r.scoped_verdict = Verdict.BREAKING  # type: ignore[attr-defined]
        r.gate_scope = "used_by"  # type: ignore[attr-defined]
        r.scoped_relevant_finding_ids = frozenset()  # type: ignore[attr-defined]
        r.scoped_missing_labels = ("_Z6vanishv",)  # type: ignore[attr-defined]
        xml_str = to_junit_xml(r)
        root = _parse(xml_str)
        ts = root.find("testsuite")
        assert ts.get("failures") == "1"
        assert ts.get("tests") == "1"
        tc = ts.find("testcase")
        assert tc.get("name") == "_Z6vanishv"
        assert tc.get("classname") == "used_by_contract"
        assert tc.find("failure") is not None

    def test_missing_contract_demoted_by_severity_config_does_not_fail(self) -> None:
        # Regression (Codex review): under a severity config that demotes
        # abi_breaking, the scoped exit code for a missing contract member
        # is floored at 0 by missing_contract_exit_code -- the synthetic
        # testcase must not fail in that case, or a JUnit-consuming CI would
        # mark the run failed even though the gate itself passed.
        from abicheck.severity import SeverityConfig, SeverityLevel

        demoted = SeverityConfig(abi_breaking=SeverityLevel.WARNING)
        r = _make_result([], verdict=Verdict.COMPATIBLE)
        r.scoped_verdict = Verdict.BREAKING  # type: ignore[attr-defined]
        r.gate_scope = "used_by"  # type: ignore[attr-defined]
        r.scoped_relevant_finding_ids = frozenset()  # type: ignore[attr-defined]
        r.scoped_missing_labels = ("_Z6vanishv",)  # type: ignore[attr-defined]
        xml_str = to_junit_xml(r, severity_config=demoted)
        root = _parse(xml_str)
        ts = root.find("testsuite")
        assert ts.get("failures") == "0"
        tc = ts.find("testcase")
        assert tc.get("name") == "_Z6vanishv"
        assert tc.find("failure") is None
        # A missing-contract member still counts as relevant even when
        # demoted (severity decides blocking, not scope membership --
        # CodeRabbit review).
        props = {p.get("name"): p.get("value") for p in ts.find("properties")}
        assert props["abicheck.relevant_finding_count"] == "1"

    def test_missing_contract_respects_show_only(self) -> None:
        # Regression (Codex review): scoped_missing_labels bypassed
        # --show-only entirely -- a missing required symbol has no backing
        # Change/ChangeKind so it can't run through apply_show_only, but a
        # --show-only run that excludes breaking findings must not still
        # count/emit a failing testcase for it.
        r = _make_result([], verdict=Verdict.COMPATIBLE)
        r.scoped_verdict = Verdict.BREAKING  # type: ignore[attr-defined]
        r.gate_scope = "used_by"  # type: ignore[attr-defined]
        r.scoped_relevant_finding_ids = frozenset()  # type: ignore[attr-defined]
        r.scoped_missing_labels = ("_Z6vanishv",)  # type: ignore[attr-defined]
        xml_str = to_junit_xml(r, show_only="compatible")
        root = _parse(xml_str)
        ts = root.find("testsuite")
        assert ts.get("failures") == "0"
        assert ts.get("tests") == "0"
        assert ts.find("testcase") is None

    def test_missing_contract_shown_when_show_only_includes_breaking(self) -> None:
        r = _make_result([], verdict=Verdict.COMPATIBLE)
        r.scoped_verdict = Verdict.BREAKING  # type: ignore[attr-defined]
        r.gate_scope = "used_by"  # type: ignore[attr-defined]
        r.scoped_relevant_finding_ids = frozenset()  # type: ignore[attr-defined]
        r.scoped_missing_labels = ("_Z6vanishv",)  # type: ignore[attr-defined]
        xml_str = to_junit_xml(r, show_only="breaking")
        root = _parse(xml_str)
        ts = root.find("testsuite")
        assert ts.get("failures") == "1"
        assert ts.find("testcase") is not None

    def test_scoped_only_change_gets_a_testcase(self) -> None:
        # Regression (Codex review): scope_diff_to_app synthesizes a fresh
        # Change (e.g. PE_ORDINAL_RETARGETED) that is relevant to the gate
        # but never added to result.changes -- without a testcase for it, a
        # --used-by run that fails solely because of one of these would have
        # no testcase/failure in the XML to explain it.
        from abicheck.reporter import _finding_id

        scoped_only = Change(
            kind=ChangeKind.PE_ORDINAL_RETARGETED,
            symbol="ordinal:5",
            description="ordinal 5 retargeted",
            old_value="OldFunc", new_value="NewFunc",
        )
        r = _make_result([], verdict=Verdict.COMPATIBLE)
        r.scoped_verdict = Verdict.BREAKING  # type: ignore[attr-defined]
        r.gate_scope = "used_by"  # type: ignore[attr-defined]
        r.scoped_relevant_finding_ids = frozenset({_finding_id(scoped_only)})  # type: ignore[attr-defined]
        r.scoped_only_changes = (scoped_only,)  # type: ignore[attr-defined]
        xml_str = to_junit_xml(r)
        root = _parse(xml_str)
        ts = root.find("testsuite")
        assert ts.get("failures") == "1"
        tc = ts.find("testcase")
        assert tc.get("name") == "ordinal:5"
        assert tc.find("failure") is not None
