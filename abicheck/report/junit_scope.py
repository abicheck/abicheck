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

"""ADR-065 S2's JUnit projection of the ``comparison_scope`` section.

Mirrors ``junit_report._append_coverage_suite``: one ``<testsuite>`` per
release document, one ``<testcase>`` per member that did not reach a
completed comparison. A member the policy *blocks* on (``--on-incomplete-
scope block``), and a run that completed no comparison at all (D7, under
every policy), are ``<error>``s so a CI dashboard turns red exactly when
the process exit did; a member the default ``warn`` policy accepted is a
``<skipped>`` case -- still visible, never a green pass, never a failure
the resolved policy did not produce (Codex review on the first cut, which
emitted ``errors="1"`` for a warn-accepted ``unsupported`` member).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Mapping
from typing import Any

__all__ = ["append_scope_suite"]

SUITE_NAME = "abicheck.comparison_scope"


def append_scope_suite(
    root: ET.Element, section: Mapping[str, Any] | None
) -> tuple[int, int]:
    """Append the scope suite to *root*; returns ``(tests, errors)`` added.

    Nothing at all when *section* is ``None`` (a scalar comparison, or a
    caller without a record) or when the scope was fully checked -- the
    latter gets an empty suite, so "checked, nothing missing" and "never
    checked" stay distinguishable, as the coverage suite already does.
    """
    if section is None:
        return 0, 0
    blocking = int(section.get("incomplete_scope_exit_contribution") or 0) == 1
    no_comparison = bool(section.get("no_comparison_completed"))
    members = section.get("members")
    unchecked = [
        m
        for m in (members if isinstance(members, list) else [])
        if isinstance(m, Mapping)
        and m.get("state") not in ("available", "out_of_scope")
    ]
    suite = ET.SubElement(root, "testsuite")
    suite.set("name", SUITE_NAME)
    tests = errors = 0
    for m in unchecked:
        case = ET.SubElement(suite, "testcase")
        case.set("classname", SUITE_NAME)
        case.set("name", f"{m.get('name')}:{m.get('state')}")
        text = f"{m.get('state')}: {m.get('reason') or 'not compared'}"
        tests += 1
        if blocking:
            error = ET.SubElement(case, "error")
            error.set("type", str(m.get("state")))
            error.set(
                "message",
                f"comparison scope: {m.get('name')} was not compared -- {text}",
            )
            errors += 1
        else:
            skipped = ET.SubElement(case, "skipped")
            skipped.set(
                "message",
                f"comparison scope: {m.get('name')} was not compared ({text}); "
                f"accepted by --on-incomplete-scope {section.get('policy', 'warn')}",
            )
    if no_comparison:
        case = ET.SubElement(suite, "testcase")
        case.set("classname", SUITE_NAME)
        case.set("name", "no_comparison_completed")
        error = ET.SubElement(case, "error")
        error.set("type", "no_comparison_completed")
        error.set(
            "message",
            "comparison scope: the selected scope produced no valid comparison "
            "(ADR-065 D7) -- never a clean pass under any policy",
        )
        tests += 1
        errors += 1
    suite.set("tests", str(tests))
    suite.set("failures", "0")
    suite.set("errors", str(errors))
    suite.set("skipped", str(tests - errors))
    return tests, errors
