"""Tests for ABICC-compatible XML report generation.

Validates that our XML output matches the real ABICC XML schema so that
abi-tracker, lvc-monitor, and distro infrastructure can parse it.

Real ABICC XML structure:
    <reports>
      <report kind="binary" version="1.2">
        <test_info><library>...</library>...</test_info>
        <test_results><verdict>...</verdict>...</test_results>
        <problem_summary>...</problem_summary>
        <added_symbols>...</added_symbols>
        <removed_symbols>...</removed_symbols>
        <problems_with_types severity="High">...</problems_with_types>
        ...
      </report>
      <report kind="source" version="1.2">...</report>
    </reports>
"""

from __future__ import annotations

from pathlib import Path

from defusedxml.ElementTree import fromstring as xml_fromstring

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.compat.xml_report import generate_xml_report, write_xml_report


def _make_result(
    changes: list[Change] | None = None,
    verdict: Verdict = Verdict.NO_CHANGE,
) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest",
        changes=changes or [],
        verdict=verdict,
    )


class TestXmlReportStructure:
    """Verify the XML report matches the real ABICC schema."""

    def test_root_element_is_reports(self):
        result = _make_result()
        xml = generate_xml_report(result, lib_name="libfoo")
        root = xml_fromstring(xml)
        assert root.tag == "reports"

    def test_has_binary_and_source_report_elements(self):
        result = _make_result()
        xml = generate_xml_report(result, lib_name="libfoo")
        root = xml_fromstring(xml)
        reports = root.findall("report")
        assert len(reports) == 2
        kinds = {r.get("kind") for r in reports}
        assert kinds == {"binary", "source"}

    def test_report_version_attribute(self):
        result = _make_result()
        xml = generate_xml_report(result, lib_name="libfoo")
        root = xml_fromstring(xml)
        for report in root.findall("report"):
            assert report.get("version") == "1.2"

    def test_test_info_section(self):
        result = _make_result()
        xml = generate_xml_report(
            result, lib_name="libfoo", old_version="1.0", new_version="2.0"
        )
        root = xml_fromstring(xml)
        binary = root.find("report[@kind='binary']")
        test_info = binary.find("test_info")
        assert test_info is not None
        assert test_info.find("library").text == "libfoo"
        assert test_info.find("version1/number").text == "1.0"
        assert test_info.find("version2/number").text == "2.0"

    def test_test_results_section(self):
        result = _make_result()
        xml = generate_xml_report(result, lib_name="libfoo")
        root = xml_fromstring(xml)
        binary = root.find("report[@kind='binary']")
        test_results = binary.find("test_results")
        assert test_results is not None
        assert test_results.find("verdict") is not None
        assert test_results.find("affected") is not None
        assert test_results.find("symbols") is not None

    def test_problem_summary_section(self):
        result = _make_result()
        xml = generate_xml_report(result, lib_name="libfoo")
        root = xml_fromstring(xml)
        binary = root.find("report[@kind='binary']")
        summary = binary.find("problem_summary")
        assert summary is not None
        assert summary.find("added_symbols") is not None
        assert summary.find("removed_symbols") is not None
        assert summary.find("problems_with_types") is not None
        assert summary.find("problems_with_symbols") is not None

    def test_problem_summary_severity_tiers(self):
        """ABICC classifies problems into High/Medium/Low/Safe tiers."""
        result = _make_result()
        xml = generate_xml_report(result, lib_name="libfoo")
        root = xml_fromstring(xml)
        summary = root.find("report[@kind='binary']/problem_summary")
        types = summary.find("problems_with_types")
        for sev in ("high", "medium", "low", "safe"):
            el = types.find(sev)
            assert el is not None, f"Missing <problems_with_types>/<{sev}>"
            assert el.text == "0"

    def test_xml_declaration_present(self):
        result = _make_result()
        xml = generate_xml_report(result, lib_name="libfoo")
        assert xml.startswith("<?xml ")

    def test_arch_in_test_info(self):
        result = _make_result()
        xml = generate_xml_report(result, lib_name="libfoo", arch="x86_64")
        root = xml_fromstring(xml)
        binary = root.find("report[@kind='binary']")
        assert binary.find("test_info/version1/arch").text == "x86_64"
        assert binary.find("test_info/version2/arch").text == "x86_64"

    def test_gcc_in_test_info(self):
        result = _make_result()
        xml = generate_xml_report(result, lib_name="libfoo", compiler="12.2.0")
        root = xml_fromstring(xml)
        binary = root.find("report[@kind='binary']")
        assert binary.find("test_info/version1/gcc").text == "12.2.0"

    def test_no_arch_when_empty(self):
        result = _make_result()
        xml = generate_xml_report(result, lib_name="libfoo")
        root = xml_fromstring(xml)
        binary = root.find("report[@kind='binary']")
        assert binary.find("test_info/version1/arch") is None


class TestXmlReportCounts:
    """Verify counts and verdicts in the XML report."""

    def test_no_changes_compatible_verdict(self):
        result = _make_result(verdict=Verdict.NO_CHANGE)
        xml = generate_xml_report(result, old_symbol_count=10)
        root = xml_fromstring(xml)
        binary = root.find("report[@kind='binary']")
        assert binary.find("test_results/verdict").text == "compatible"
        summary = binary.find("problem_summary")
        assert summary.find("added_symbols").text == "0"
        assert summary.find("removed_symbols").text == "0"

    def test_func_removed_counts_as_removed(self):
        changes = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="_Z3foov",
                description="foo() removed",
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        xml = generate_xml_report(result, old_symbol_count=10)
        root = xml_fromstring(xml)
        binary = root.find("report[@kind='binary']")
        assert binary.find("test_results/verdict").text == "incompatible"
        assert binary.find("problem_summary/removed_symbols").text == "1"

    def test_func_added_counts_as_added(self):
        changes = [
            Change(
                kind=ChangeKind.FUNC_ADDED, symbol="_Z3barv", description="bar() added"
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.COMPATIBLE)
        xml = generate_xml_report(result)
        root = xml_fromstring(xml)
        binary = root.find("report[@kind='binary']")
        assert binary.find("test_results/verdict").text == "compatible"
        assert binary.find("problem_summary/added_symbols").text == "1"

    def test_type_problem_classified_by_severity(self):
        changes = [
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="MyStruct",
                description="size changed",
                old_value="8",
                new_value="16",
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        xml = generate_xml_report(result, old_symbol_count=5)
        root = xml_fromstring(xml)
        summary = root.find("report[@kind='binary']/problem_summary")
        types = summary.find("problems_with_types")
        # TYPE_SIZE_CHANGED is High severity
        assert types.find("high").text == "1"
        assert types.find("medium").text == "0"

    def test_func_return_changed_is_medium_severity(self):
        changes = [
            Change(
                kind=ChangeKind.FUNC_RETURN_CHANGED,
                symbol="_Z3foov",
                description="return type changed",
                old_value="int",
                new_value="long",
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        xml = generate_xml_report(result, old_symbol_count=10)
        root = xml_fromstring(xml)
        summary = root.find("report[@kind='binary']/problem_summary")
        syms = summary.find("problems_with_symbols")
        assert syms.find("medium").text == "1"

    def test_source_section_excludes_binary_only_kinds(self):
        changes = [
            Change(
                kind=ChangeKind.SONAME_CHANGED,
                symbol="libfoo.so",
                description="soname changed",
            ),
            Change(
                kind=ChangeKind.FUNC_RETURN_CHANGED,
                symbol="_Z3foov",
                description="return type changed",
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        xml = generate_xml_report(result, old_symbol_count=10)
        root = xml_fromstring(xml)
        # Source section excludes SONAME_CHANGED (binary-only)
        source = root.find("report[@kind='source']")
        source_summary = source.find("problem_summary")
        # Only func_return_changed should be present (medium severity symbol problem)
        syms = source_summary.find("problems_with_symbols")
        assert syms.find("medium").text == "1"


class TestXmlReportDetailSections:
    """Verify detail sections contain per-change data."""

    def test_added_symbols_detail(self):
        changes = [
            Change(
                kind=ChangeKind.FUNC_ADDED, symbol="_Z3barv", description="bar() added"
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.COMPATIBLE)
        xml = generate_xml_report(result)
        root = xml_fromstring(xml)
        binary = root.find("report[@kind='binary']")
        added = binary.find("added_symbols")
        assert added is not None
        names = [n.text for n in added.findall("name")]
        assert "_Z3barv" in names

    def test_removed_symbols_detail(self):
        changes = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="_Z3foov",
                description="foo() removed",
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        xml = generate_xml_report(result)
        root = xml_fromstring(xml)
        binary = root.find("report[@kind='binary']")
        removed = binary.find("removed_symbols")
        assert removed is not None
        names = [n.text for n in removed.findall("name")]
        assert "_Z3foov" in names

    def test_type_problem_detail_section(self):
        changes = [
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="MyStruct",
                description="size changed from 8 to 16",
                old_value="8",
                new_value="16",
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        xml = generate_xml_report(result, old_symbol_count=5)
        root = xml_fromstring(xml)
        binary = root.find("report[@kind='binary']")
        # Should have <problems_with_types severity="High"> detail section
        type_probs = binary.findall("problems_with_types[@severity='High']")
        assert len(type_probs) == 1
        type_el = type_probs[0].find("type")
        assert type_el is not None
        assert type_el.get("name") == "MyStruct"
        prob = type_el.find("problem")
        assert prob.get("id") == "type_size_changed"
        change_el = prob.find("change")
        assert change_el.get("old_value") == "8"
        assert change_el.get("new_value") == "16"

    def test_symbol_problem_detail_section(self):
        changes = [
            Change(
                kind=ChangeKind.FUNC_RETURN_CHANGED,
                symbol="_Z3foov",
                description="return type changed",
                old_value="int",
                new_value="long",
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        xml = generate_xml_report(result, old_symbol_count=10)
        root = xml_fromstring(xml)
        binary = root.find("report[@kind='binary']")
        sym_probs = binary.findall("problems_with_symbols[@severity='Medium']")
        assert len(sym_probs) == 1
        sym_el = sym_probs[0].find("symbol")
        assert sym_el.get("name") == "_Z3foov"

    def test_effect_element_in_type_problem(self):
        changes = [
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="MyStruct",
                description="size changed",
                old_value="8",
                new_value="16",
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        xml = generate_xml_report(result, old_symbol_count=5)
        root = xml_fromstring(xml)
        binary = root.find("report[@kind='binary']")
        prob = binary.find(".//problem[@id='type_size_changed']")
        assert prob is not None
        effect = prob.find("effect")
        assert effect is not None
        assert "break binary compatibility" in effect.text

    def test_overcome_element_for_removal(self):
        """func_removed goes into removed_symbols, not problems_with_symbols.
        This is correct — ABICC lists removals separately from problems."""
        changes = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="_Z3foov",
                description="foo() removed",
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        xml = generate_xml_report(result, old_symbol_count=5)
        root = xml_fromstring(xml)  # noqa: S314
        binary = root.find("report[@kind='binary']")
        # Symbol should be in <removed_symbols> detail section
        removed_detail = binary.find("removed_symbols")
        assert removed_detail is not None
        names = [n.text for n in removed_detail.findall("name")]
        assert "_Z3foov" in names
        # Should NOT appear in problems_with_symbols (removals are separate)
        assert binary.find("problems_with_symbols") is None

    def test_no_overcome_for_non_removal(self):
        changes = [
            Change(
                kind=ChangeKind.FUNC_RETURN_CHANGED,
                symbol="_Z3foov",
                description="return type changed",
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        xml = generate_xml_report(result, old_symbol_count=5)
        root = xml_fromstring(xml)
        prob = root.find(".//problem[@id='func_return_changed']")
        assert prob is not None
        assert prob.find("overcome") is None

    def test_no_detail_sections_when_no_changes(self):
        result = _make_result()
        xml = generate_xml_report(result)
        root = xml_fromstring(xml)
        binary = root.find("report[@kind='binary']")
        # No detail sections should be present
        assert binary.find("added_symbols") is None
        assert binary.find("removed_symbols") is None
        assert binary.find("problems_with_types[@severity]") is None

    def test_added_kind_not_in_problem_details(self):
        """Regression: TYPE_FIELD_ADDED is in both _BREAKING_KINDS and _ADDED_KINDS.
        It must appear only in <added_symbols>, NOT in <problems_with_types/symbols>.
        PR#110 fix: _build_problem_details() must filter _ADDED_KINDS explicitly."""
        changes = [
            Change(
                kind=ChangeKind.TYPE_FIELD_ADDED,
                symbol="MyClass",
                description="field 'x' added to polymorphic class",
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        xml = generate_xml_report(result)
        root = xml_fromstring(xml)
        binary = root.find("report[@kind='binary']")
        # Must appear in <added_symbols>
        added = binary.find("added_symbols")
        assert added is not None, "TYPE_FIELD_ADDED should produce <added_symbols>"
        names = [n.text for n in added.findall("name")]
        assert "MyClass" in names, (
            "TYPE_FIELD_ADDED symbol must be listed in <added_symbols>"
        )
        # Must NOT appear in <problems_with_types> or <problems_with_symbols>
        assert binary.find("problems_with_types") is None, (
            "TYPE_FIELD_ADDED must NOT appear in <problems_with_types> (it belongs in added_symbols)"
        )
        assert binary.find("problems_with_symbols") is None, (
            "TYPE_FIELD_ADDED must NOT appear in <problems_with_symbols>"
        )


class TestXmlReportParsability:
    """Simulate how abi-tracker / lvc-monitor would parse the XML report."""

    def test_abi_tracker_bc_percentage_extraction(self):
        """abi-tracker computes BC from affected/symbols counts."""
        changes = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="_Z3foov",
                description="foo() removed",
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        xml = generate_xml_report(result, lib_name="libfoo", old_symbol_count=10)
        root = xml_fromstring(xml)
        # Navigate like abi-tracker: report[@kind='binary']/test_results/verdict
        binary = root.find("report[@kind='binary']")
        verdict = binary.find("test_results/verdict").text
        assert verdict == "incompatible"

    def test_abi_tracker_problem_summary_extraction(self):
        """abi-tracker reads problem_summary/removed_symbols and severity counts."""
        changes = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="_Z3foov",
                description="foo() removed",
            ),
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="MyStruct",
                description="size changed",
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        xml = generate_xml_report(result, old_symbol_count=10)
        root = xml_fromstring(xml)
        summary = root.find("report[@kind='binary']/problem_summary")
        removed = int(summary.find("removed_symbols").text)
        assert removed == 1
        high_types = int(summary.find("problems_with_types/high").text)
        assert high_types == 1

    def test_mixed_changes_full_extraction(self):
        changes = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="_Z3foov",
                description="foo() removed",
            ),
            Change(
                kind=ChangeKind.FUNC_ADDED, symbol="_Z3barv", description="bar() added"
            ),
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="MyStruct",
                description="size changed",
            ),
            Change(
                kind=ChangeKind.FUNC_RETURN_CHANGED,
                symbol="_Z3bazv",
                description="return type changed",
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        xml = generate_xml_report(result, lib_name="libfoo", old_symbol_count=20)
        root = xml_fromstring(xml)
        binary = root.find("report[@kind='binary']")
        summary = binary.find("problem_summary")

        assert summary.find("removed_symbols").text == "1"
        assert summary.find("added_symbols").text == "1"
        # type_size_changed → High type problem
        assert summary.find("problems_with_types/high").text == "1"
        # func_return_changed → Medium symbol problem
        assert summary.find("problems_with_symbols/medium").text == "1"


class TestWriteXmlReport:
    def test_writes_valid_xml_file(self, tmp_path: Path):
        result = _make_result()
        out = tmp_path / "sub" / "report.xml"
        write_xml_report(result, out, lib_name="libfoo")
        assert out.exists()
        content = out.read_text()
        root = xml_fromstring(content)
        assert root.tag == "reports"

    def test_creates_parent_dirs(self, tmp_path: Path):
        result = _make_result()
        out = tmp_path / "a" / "b" / "c" / "report.xml"
        write_xml_report(result, out, lib_name="libfoo")
        assert out.exists()


class TestXmlEscaping:
    """Regression tests for PR#106 — && characters in type names must be XML-safe.

    Python's ET automatically escapes & as &amp; in text content.
    We verify the serialized output is well-formed and parseable when
    type names contain C++ rvalue reference (&&) characters.
    """

    def test_rvalue_ref_in_description_is_escaped(self):
        """Type names with && must produce valid XML (PR#106 regression)."""
        changes = [
            Change(
                kind=ChangeKind.FUNC_RETURN_CHANGED,
                symbol="_Z7get_refv",
                description="Return type changed: int& → int&&",
                old_value="int&",
                new_value="int&&",
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        xml_str = generate_xml_report(result, lib_name="libfoo")
        # Must not contain raw & outside of &amp; entities
        # Python ET ensures this; we just verify the output is parseable
        root = xml_fromstring(xml_str)
        assert root is not None, "XML with && in type names must be parseable"
        # Also confirm &amp; appears in the serialized form (not raw &)
        assert "&amp;" in xml_str or "&&" not in xml_str, (
            "Raw && in XML output would produce invalid XML"
        )

    def test_ampersand_in_library_name_is_escaped(self):
        """& in library names must be escaped in XML output."""
        result = _make_result()
        xml_str = generate_xml_report(result, lib_name="lib&special")
        root = xml_fromstring(xml_str)
        assert root is not None
        binary = root.find("report[@kind='binary']")
        lib_el = binary.find("test_info/library")
        assert lib_el is not None
        assert lib_el.text == "lib&special"


# ---------------------------------------------------------------------------
# File metadata in XML report (traceability)
# ---------------------------------------------------------------------------


class TestXmlFileMetadata:
    """XML report includes file metadata inside <test_info>."""

    def test_file_info_present_in_test_info(self):
        from abicheck.checker_types import LibraryMetadata

        result = _make_result()
        result.old_metadata = LibraryMetadata(
            path="/old/lib.so",
            sha256="aa" * 32,
            size_bytes=4096,
        )
        result.new_metadata = LibraryMetadata(
            path="/new/lib.so",
            sha256="bb" * 32,
            size_bytes=8192,
        )
        xml_str = generate_xml_report(result, lib_name="libtest")
        root = xml_fromstring(xml_str)
        binary = root.find("report[@kind='binary']")
        file_info = binary.find("test_info/file_info")
        assert file_info is not None
        old_file = file_info.find("old_file")
        assert old_file is not None
        assert old_file.find("path").text == "/old/lib.so"
        assert old_file.find("sha256").text == "aa" * 32
        assert old_file.find("size_bytes").text == "4096"
        new_file = file_info.find("new_file")
        assert new_file is not None
        assert new_file.find("path").text == "/new/lib.so"
        assert new_file.find("sha256").text == "bb" * 32
        assert new_file.find("size_bytes").text == "8192"

    def test_file_info_absent_when_no_metadata(self):
        result = _make_result()
        xml_str = generate_xml_report(result, lib_name="libtest")
        root = xml_fromstring(xml_str)
        binary = root.find("report[@kind='binary']")
        file_info = binary.find("test_info/file_info")
        assert file_info is None

    def test_file_info_in_source_section_too(self):
        from abicheck.checker_types import LibraryMetadata

        result = _make_result()
        result.old_metadata = LibraryMetadata(
            path="/old/lib.so",
            sha256="cc" * 32,
            size_bytes=1024,
        )
        result.new_metadata = LibraryMetadata(
            path="/new/lib.so",
            sha256="dd" * 32,
            size_bytes=2048,
        )
        xml_str = generate_xml_report(result, lib_name="libtest")
        root = xml_fromstring(xml_str)
        source = root.find("report[@kind='source']")
        file_info = source.find("test_info/file_info")
        assert file_info is not None

    def test_no_file_info_as_reports_child(self):
        """file_info must NOT appear as direct child of <reports>."""
        from abicheck.checker_types import LibraryMetadata

        result = _make_result()
        result.old_metadata = LibraryMetadata(
            path="/old/lib.so",
            sha256="ee" * 32,
            size_bytes=512,
        )
        xml_str = generate_xml_report(result, lib_name="libtest")
        root = xml_fromstring(xml_str)
        # file_info should NOT be a direct child of <reports>
        direct_file_info = root.find("file_info")
        assert direct_file_info is None
