# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Run-level JUnit suites: contract coverage and ADR-090 ``UNCONFIRMED``.

Each describes the comparison's evidence, not one finding, so each is its own
``<testsuite>`` beside the per-symbol suite. Moved out of ``junit_report``
(at its ``no_growth`` baseline) when the second one was added.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..checker_types import DiffResult

__all__ = ["append_coverage_suite", "append_unconfirmed_suite"]


def append_coverage_suite(root: ET.Element, result: DiffResult) -> int:
    """Append the contract-coverage suite, returning how many errors it holds.

    Nothing at all when the run computed no contract context (the default),
    so an ordinary report is unchanged. A run whose selected contract domain
    *closed* gets an empty suite rather than no suite: "checked, nothing
    missing" and "never checked" are different states, and a consumer must be
    able to tell them apart.
    """
    from ..policy.coverage_ledger import coverage_failures_for_context

    ctx = getattr(result, "contract_context", None)
    if ctx is None:
        return 0
    failures = coverage_failures_for_context(ctx)
    # Qualified by library: a multi-library document appends one of these per
    # result, and a coverage failure identifies only its provider and side.
    # Two libraries failing the same provider on the same side produced two
    # indistinguishable suites, so a consumer could not attribute either
    # error to a library (CodeRabbit review).
    library = getattr(result, "library", "") or "unknown"
    suite = ET.SubElement(root, "testsuite")
    suite.set("name", f"abicheck.contract_coverage.{library}")
    suite.set("tests", str(len(failures)))
    suite.set("failures", "0")
    suite.set("errors", str(len(failures)))
    for failure in failures:
        case = ET.SubElement(suite, "testcase")
        case.set("classname", f"abicheck.contract_coverage.{library}")
        case.set("name", f"{failure.provider}:{failure.side}")
        error = ET.SubElement(case, "error")
        error.set("type", failure.reason)
        error.set(
            "message",
            f"contract coverage: {failure.provider} ({failure.side}) could not "
            f"close the {failure.mode} domain ({failure.reason}); this finding "
            "cannot be suppressed",
        )
    return len(failures)


def append_unconfirmed_suite(root: ET.Element, result: DiffResult) -> int:
    """Append the ADR-090 suite, returning how many failures it holds.

    Nothing when the run is not ``UNCONFIRMED``. Otherwise one testcase: a
    ``<failure>`` under the default ``assurance.unconfirmed: fail`` (the same
    exit-``1`` the process takes), or a passing case carrying the reason in
    ``<system-out>`` under ``warn``. Never silently absent, so a dashboard
    cannot read "no type-level evidence" as "compatible".
    """
    from ..policy.unconfirmed_compatibility import (
        UNCONFIRMED,
        is_unconfirmed,
        unconfirmed_exit_contribution,
        unconfirmed_summary,
    )

    if not is_unconfirmed(result):
        return 0
    library = getattr(result, "library", "") or "unknown"
    suite = ET.SubElement(root, "testsuite")
    suite.set("name", f"abicheck.unconfirmed_compatibility.{library}")
    suite.set("tests", "1")
    suite.set("errors", "0")
    case = ET.SubElement(suite, "testcase")
    case.set("classname", f"abicheck.unconfirmed_compatibility.{library}")
    case.set("name", "type_level_evidence")
    message = unconfirmed_summary(result)
    failed = unconfirmed_exit_contribution(result)
    if failed:
        failure = ET.SubElement(case, "failure")
        failure.set("type", UNCONFIRMED)
        failure.set("message", message)
    else:
        ET.SubElement(case, "system-out").text = message
    suite.set("failures", str(failed))
    return failed
