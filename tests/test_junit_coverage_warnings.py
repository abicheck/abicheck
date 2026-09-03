"""Tests for `abicheck.junit_coverage_warnings`.

`coverage_warnings` (e.g. a same-binary comparison) must not vanish into an
empty passing JUnit suite -- see `junit_coverage_warnings.py`'s own
docstring. Split into its own test file (rather than grown inline in
`test_junit_report.py`) to stay under that file's ADR-061 debt-no-growth
baseline.
"""

from __future__ import annotations

from defusedxml.ElementTree import fromstring as xml_fromstring

from abicheck.checker_policy import Verdict
from abicheck.checker_types import DiffResult
from abicheck.junit_report import to_junit_xml, to_junit_xml_multi


def _result_with_warning(
    warning: str = "old and new binaries are byte-identical",
) -> DiffResult:
    result = DiffResult(
        old_version="1.0",
        new_version="1.0",
        library="libfoo.so.1",
        changes=[],
        verdict=Verdict.COMPATIBLE,
    )
    result.coverage_warnings = [warning]
    return result


class TestCoverageWarnings:
    def test_warning_appears_as_a_passing_testcase(self) -> None:
        root = xml_fromstring(to_junit_xml(_result_with_warning()))
        (suite,) = [
            s
            for s in root.findall("testsuite")
            if (s.get("name") or "").startswith("abicheck.coverage_warnings")
        ]
        assert suite.get("name") == "abicheck.coverage_warnings.libfoo.so.1"
        assert suite.get("tests") == "1"
        assert suite.get("failures") == "0"
        assert suite.get("errors") == "0"
        (case,) = suite.findall("testcase")
        assert case.find("error") is None
        assert case.find("failure") is None
        out = case.find("system-out")
        assert out is not None
        assert "byte-identical" in out.text

    def test_warning_rolls_into_document_tests_but_not_errors(self) -> None:
        root = xml_fromstring(to_junit_xml(_result_with_warning()))
        assert root.get("errors") == "0"
        assert int(root.get("tests")) >= 1

    def test_no_warnings_means_no_suite(self) -> None:
        result = DiffResult(
            old_version="1.0",
            new_version="1.0",
            library="libfoo.so.1",
            changes=[],
            verdict=Verdict.COMPATIBLE,
        )
        root = xml_fromstring(to_junit_xml(result))
        assert not [
            s
            for s in root.findall("testsuite")
            if (s.get("name") or "").startswith("abicheck.coverage_warnings")
        ]

    def test_unrelated_coverage_warnings_are_not_surfaced(self) -> None:
        """Only the same-binary warning renders here -- an ordinary
        comparison's routine detector-disabled/missing-metadata notices
        must not flood every JUnit document with boilerplate testcases."""
        result = _result_with_warning("Detector 'sycl' disabled: missing SYCL metadata")
        root = xml_fromstring(to_junit_xml(result))
        assert not [
            s
            for s in root.findall("testsuite")
            if (s.get("name") or "").startswith("abicheck.coverage_warnings")
        ]

    def test_multi_document_qualifies_the_suite_by_library(self) -> None:
        r1 = _result_with_warning("binaries are byte-identical (first)")
        r1.library = "liba.so"
        r2 = _result_with_warning("binaries are byte-identical (second)")
        r2.library = "libb.so"
        root = xml_fromstring(to_junit_xml_multi([(r1, None), (r2, None)]))
        names = {
            s.get("name")
            for s in root.findall("testsuite")
            if (s.get("name") or "").startswith("abicheck.coverage_warnings")
        }
        assert names == {
            "abicheck.coverage_warnings.liba.so",
            "abicheck.coverage_warnings.libb.so",
        }
