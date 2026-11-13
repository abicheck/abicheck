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

"""``coverage_warnings`` rendering for :mod:`abicheck.junit_report`.

Split out as its own leaf module rather than grown inline in
``junit_report.py`` (ADR-061 debt-no-growth baseline) -- see
:func:`append_coverage_warnings_suite`'s own docstring for what this closes.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from .checker_types import DiffResult
from .confidence import SAME_BINARY_WARNING_MARKER
from .report.analysis_caveats import CAVEAT_HEADING, analysis_caveats_for

if TYPE_CHECKING:
    from .report.envelope import ReportEnvelope


def append_coverage_warnings_suite(
    root: ET.Element, result: DiffResult, envelope: ReportEnvelope | None = None
) -> int:
    """Append the same-binary ``coverage_warnings`` entry as a passing testcase, returning the count.

    Nothing at all when there is no such warning, so an ordinary report is
    unchanged. A same-binary comparison otherwise rendered as an empty
    passing suite with no indication the comparison's evidence was limited
    (Codex review: "an identical-input comparison produces an empty passing
    suite with no indication the artifacts were duplicates"). Scoped to the
    same-binary marker specifically, not every `coverage_warnings` entry --
    an ordinary comparison already carries a dozen routine detector-disabled
    notices there, and surfacing all of them here would flood every JUnit
    document with boilerplate testcases instead of the one signal this
    finding is actually about (matches `cli_compare_options.
    echo_coverage_warnings`'s identical `-o oneline=...` scoping). Rendered
    as a passing ``<testcase>`` with a ``<system-out>`` message -- not
    ``<error>``/``<failure>`` -- since this is advisory and must not turn a
    green JUnit-consuming build red.
    """
    caveats = _append_analysis_caveats_suite(root, result, envelope)
    warnings = [w for w in result.coverage_warnings if SAME_BINARY_WARNING_MARKER in w]
    if not warnings:
        return caveats
    library = getattr(result, "library", "") or "unknown"
    suite = ET.SubElement(root, "testsuite")
    suite.set("name", f"abicheck.coverage_warnings.{library}")
    suite.set("tests", str(len(warnings)))
    suite.set("failures", "0")
    suite.set("errors", "0")
    for i, warning in enumerate(warnings):
        case = ET.SubElement(suite, "testcase")
        case.set("classname", f"abicheck.coverage_warnings.{library}")
        case.set("name", f"warning[{i}]")
        out = ET.SubElement(case, "system-out")
        out.text = warning
    return caveats + len(warnings)


def _append_analysis_caveats_suite(
    root: ET.Element, result: DiffResult, envelope: ReportEnvelope | None
) -> int:
    """One ``<skipped>`` testcase per ``report/analysis_caveats.py`` fact,
    returning the count.

    A failed snapshot section, a forced not-comparable comparison or a
    ``NOT_EVALUATED`` finding means part of the comparison never ran --
    JUnit's ``skipped`` ("not executed") is exactly that, where a passing
    testcase would read as checked-and-compatible and a failure would claim
    a break nobody observed. Skipped, so it neither turns a JUnit-consuming
    build red nor disappears from the dashboard. Nothing for a complete run.
    """
    caveats = analysis_caveats_for(result, envelope)
    if not caveats:
        return 0
    library = getattr(result, "library", "") or "unknown"
    suite = ET.SubElement(root, "testsuite")
    suite.set("name", f"abicheck.analysis_incomplete.{library}")
    suite.set("tests", str(len(caveats)))
    suite.set("failures", "0")
    suite.set("errors", "0")
    suite.set("skipped", str(len(caveats)))
    for caveat in caveats:
        case = ET.SubElement(suite, "testcase")
        case.set("classname", f"abicheck.analysis_incomplete.{library}")
        case.set("name", caveat.key)
        skipped = ET.SubElement(case, "skipped")
        skipped.set("message", f"{CAVEAT_HEADING}: {caveat.message}")
    return len(caveats)
