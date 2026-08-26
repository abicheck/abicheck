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

from .checker_types import DiffResult
from .confidence import SAME_BINARY_WARNING_MARKER


def append_coverage_warnings_suite(root: ET.Element, result: DiffResult) -> int:
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
    echo_coverage_warnings`'s identical `--profile quick` scoping). Rendered
    as a passing ``<testcase>`` with a ``<system-out>`` message -- not
    ``<error>``/``<failure>`` -- since this is advisory and must not turn a
    green JUnit-consuming build red.
    """
    warnings = [w for w in result.coverage_warnings if SAME_BINARY_WARNING_MARKER in w]
    if not warnings:
        return 0
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
    return len(warnings)
