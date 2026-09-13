"""Tests for ABICC format compliance fixes.

Validates that abicheck compat mode produces output compatible with
existing ABICC report parsing harnesses.
"""

from __future__ import annotations

import re

from click.testing import CliRunner

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.html_report import generate_html_report


class TestReportFormatChoices:
    """Verify -report-format accepts ABICC format names."""

    def test_htm_is_valid_format(self):
        """ABICC uses 'htm' not 'html' — we must accept both."""
        from abicheck.cli import main

        runner = CliRunner()
        # Just test that 'htm' is accepted (will fail on missing descriptor, not on format)
        result = runner.invoke(
            main,
            [
                "compat",
                "check",
                "-lib",
                "test",
                "-old",
                "/nonexistent",
                "-new",
                "/nonexistent",
                "-report-format",
                "htm",
            ],
        )
        # Should NOT fail with "invalid choice: htm"
        assert "Invalid value for '-report-format'" not in (result.output or "")

    def test_xml_is_valid_format(self):
        """XML format must be accepted for abi-tracker compatibility."""
        from abicheck.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "compat",
                "check",
                "-lib",
                "test",
                "-old",
                "/nonexistent",
                "-new",
                "/nonexistent",
                "-report-format",
                "xml",
            ],
        )
        assert "Invalid value for '-report-format'" not in (result.output or "")

    def test_html_is_still_valid(self):
        from abicheck.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "compat",
                "check",
                "-lib",
                "test",
                "-old",
                "/nonexistent",
                "-new",
                "/nonexistent",
                "-report-format",
                "html",
            ],
        )
        assert "Invalid value for '-report-format'" not in (result.output or "")


class TestDefaultReportPath:
    """Verify default report filename matches ABICC convention."""

    def test_default_filename_is_compat_report(self):
        """ABICC default filename is compat_report.html, not report.html."""
        from pathlib import Path

        source = Path(__file__).parent.parent / "abicheck" / "compat" / "cli.py"
        content = source.read_text()
        assert "compat_report." in content, (
            "Default report filename should be 'compat_report.*' to match ABICC convention"
        )


class TestConsoleOutputFormat:
    """Verify console output includes ABICC-compatible BC% line."""

    def test_bc_percentage_line_in_source(self):
        """Console output should include 'Binary compatibility: XX.X%' like ABICC."""
        from pathlib import Path

        source = Path(__file__).parent.parent / "abicheck" / "compat" / "cli.py"
        content = source.read_text()
        assert "Binary compatibility:" in content, (
            "Console output should include ABICC-format 'Binary compatibility: XX.X%'"
        )

    def test_total_problems_line_in_source(self):
        """Console output should include ABICC-format problem count."""
        from pathlib import Path

        source = Path(__file__).parent.parent / "abicheck" / "compat" / "cli.py"
        content = source.read_text()
        assert "Total binary compatibility problems:" in content, (
            "Console output should include ABICC-format problem count"
        )


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


class TestCompatHtmlFlag:
    """Verify -compat-html / -old-style flag is accepted."""

    def test_compat_html_flag_accepted(self):
        from abicheck.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "compat",
                "check",
                "-lib",
                "test",
                "-old",
                "/nonexistent",
                "-new",
                "/nonexistent",
                "-compat-html",
            ],
        )
        # Should not fail with "no such option"
        assert "no such option" not in (result.output or "").lower()

    def test_old_style_flag_still_accepted(self):
        """Backward compat: -old-style is an alias for -compat-html."""
        from abicheck.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "compat",
                "check",
                "-lib",
                "test",
                "-old",
                "/nonexistent",
                "-new",
                "/nonexistent",
                "-old-style",
            ],
        )
        assert "no such option" not in (result.output or "").lower()


class TestCompatHtmlStructure:
    """Verify compat_html mode produces ABICC-compatible DOM structure."""

    def test_title_matches_abicc_format(self):
        result = _make_result()
        html = generate_html_report(
            result,
            lib_name="libfoo",
            old_version="1.0",
            new_version="2.0",
            compat_html=True,
        )
        assert "Binary compatibility report for" in html
        assert "libfoo" in html
        assert "1.0" in html and "2.0" in html

    def test_source_kind_title(self):
        result = _make_result()
        html = generate_html_report(
            result,
            lib_name="libfoo",
            old_version="1.0",
            new_version="2.0",
            compat_html=True,
            report_kind="source",
        )
        assert "Source compatibility report for" in html

    def test_has_title_div(self):
        result = _make_result()
        html = generate_html_report(result, lib_name="libfoo", compat_html=True)
        assert "id='Title'" in html

    def test_has_summary_div(self):
        result = _make_result()
        html = generate_html_report(result, lib_name="libfoo", compat_html=True)
        assert "id='Summary'" in html

    def test_has_test_info_section(self):
        result = _make_result()
        html = generate_html_report(
            result,
            lib_name="libfoo",
            old_version="1.0",
            new_version="2.0",
            compat_html=True,
        )
        assert "Test Info" in html
        assert "Library Name" in html

    def test_has_test_results_section(self):
        result = _make_result()
        html = generate_html_report(
            result, lib_name="libfoo", old_symbol_count=10, compat_html=True
        )
        assert "Test Results" in html
        assert "Binary Compatibility" in html

    def test_has_problem_summary_section(self):
        result = _make_result()
        html = generate_html_report(result, lib_name="libfoo", compat_html=True)
        assert "Problem Summary" in html

    def test_bc_compatible_css_class(self):
        result = _make_result(verdict=Verdict.NO_CHANGE)
        html = generate_html_report(
            result, lib_name="libfoo", old_symbol_count=10, compat_html=True
        )
        assert "class='compatible'" in html

    def test_bc_incompatible_css_class(self):
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="removed"),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        html = generate_html_report(
            result, lib_name="libfoo", old_symbol_count=10, compat_html=True
        )
        assert "class='incompatible'" in html or "class='warning'" in html


class TestCompatHtmlMetaData:
    """Verify compat_html mode embeds META_DATA comment like ABICC."""

    def test_meta_data_comment_present(self):
        result = _make_result()
        html = generate_html_report(result, lib_name="libfoo", compat_html=True)
        assert "verdict:" in html
        assert "kind:binary" in html

    def test_meta_data_parseable(self):
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="removed"),
            Change(kind=ChangeKind.FUNC_ADDED, symbol="bar", description="added"),
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="MyStruct",
                description="size changed",
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        html = generate_html_report(
            result, lib_name="libfoo", old_symbol_count=10, compat_html=True
        )
        # Extract META_DATA from comment
        m = re.search(r"<!-- ([\s\S]+?) -->", html)
        assert m is not None, "META_DATA comment not found"
        meta = m.group(1)
        assert "verdict:incompatible" in meta
        assert "added:1" in meta
        assert "removed:1" in meta
        assert "type_problems_high:1" in meta

    def test_meta_data_source_kind(self):
        result = _make_result()
        html = generate_html_report(
            result, lib_name="libfoo", compat_html=True, report_kind="source"
        )
        m = re.search(r"<!-- ([\s\S]+?) -->", html)
        assert m is not None
        assert "kind:source" in m.group(1)

    def test_meta_data_compatible_verdict(self):
        result = _make_result(verdict=Verdict.COMPATIBLE)
        html = generate_html_report(result, lib_name="libfoo", compat_html=True)
        m = re.search(r"<!-- ([\s\S]+?) -->", html)
        assert m is not None
        assert "verdict:compatible" in m.group(1)


class TestCompatHtmlSeveritySections:
    """Verify compat_html mode groups problems by severity like ABICC."""

    def test_added_section_has_abicc_id(self):
        changes = [
            Change(kind=ChangeKind.FUNC_ADDED, symbol="bar", description="added"),
        ]
        result = _make_result(changes=changes, verdict=Verdict.COMPATIBLE)
        html = generate_html_report(result, lib_name="libfoo", compat_html=True)
        assert "id='Added'" in html

    def test_removed_section_has_abicc_id(self):
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="removed"),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        html = generate_html_report(result, lib_name="libfoo", compat_html=True)
        assert "id='Removed'" in html

    def test_type_problems_high_section(self):
        changes = [
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="MyStruct",
                description="size changed",
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        html = generate_html_report(
            result, lib_name="libfoo", old_symbol_count=10, compat_html=True
        )
        assert "id='TypeProblems_High'" in html

    def test_interface_problems_medium_section(self):
        changes = [
            Change(
                kind=ChangeKind.FUNC_RETURN_CHANGED,
                symbol="foo",
                description="return changed",
            ),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        html = generate_html_report(
            result, lib_name="libfoo", old_symbol_count=10, compat_html=True
        )
        assert "id='InterfaceProblems_Medium'" in html

    def test_no_empty_severity_sections(self):
        """When there are no problems of a severity, don't emit that section."""
        result = _make_result(verdict=Verdict.NO_CHANGE)
        html = generate_html_report(result, lib_name="libfoo", compat_html=True)
        assert "id='TypeProblems_High'" not in html
        assert "id='InterfaceProblems_High'" not in html
        assert "id='Added'" not in html
        assert "id='Removed'" not in html
