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

Also owns :func:`append_env_matrix_suite` (Codex review, P2 follow-up,
round-8), moved here from ``junit_report.py`` (at its own
``architecture/debt.yaml`` ``no_growth`` baseline with no headroom) to keep
this file's own established role: release-level JUnit projections of
metadata that doesn't belong to any one per-library ``<testsuite>``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Mapping
from typing import Any

__all__ = ["append_env_matrix_suite", "append_scope_suite"]

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
    # Exactly the section's own resolved `unchecked` names: a `not_supplied`
    # member the lacking side's proof turned into a proven removal is not
    # unchecked, and must not render as a skipped/error case (CodeRabbit).
    names = section.get("unchecked")
    unchecked_names = set(names) if isinstance(names, list) else set()
    members = section.get("members")
    unchecked = [
        m
        for m in (members if isinstance(members, list) else [])
        if isinstance(m, Mapping) and m.get("name") in unchecked_names
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
                f"accepted under scope.on_incomplete: {section.get('policy', 'warn')}",
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


def append_env_matrix_suite(root: ET.Element, digest: str | None) -> None:
    """Append a release-level ``<testsuite name="abicheck.deployment">``
    carrying the release-wide deployment-floor digest as a property.

    A release-scoped value -- computed once by the caller directly from the
    resolved ``EnvironmentMatrix``, the same way the JSON envelope's
    ``env_matrix_source_sha256`` field and the Markdown "Deployment floor
    digest" bullet are -- so it must not depend on any one per-library
    comparison having completed. Rendered as its own dedicated, zero-test/
    zero-error ``<testsuite>`` (so a CI dashboard's pass/fail counts are
    unaffected) with a single ``<properties>``/``<property>`` pair, rather
    than folded into an existing per-library testsuite's ``<properties>``
    (``junit_report._add_env_matrix_property``), which only ever fires on a
    per-library testsuite that a completed comparison actually produced --
    exactly the gap this function exists to close for a release with zero
    matched/completed pairs, where no per-library testsuite exists at all
    to carry the property.

    Omitted entirely (no empty suite) when *digest* is ``None`` -- a
    release whose candidate declared no ``deployment:`` contract at all,
    matching every other ``env_matrix_source_sha256`` projection's
    "omitted, never null" convention.
    """
    if digest is None:
        return
    suite = ET.SubElement(root, "testsuite")
    suite.set("name", "abicheck.deployment")
    suite.set("tests", "0")
    suite.set("failures", "0")
    suite.set("errors", "0")
    props = ET.SubElement(suite, "properties")
    p = ET.SubElement(props, "property")
    p.set("name", "env_matrix_source_sha256")
    p.set("value", digest)
