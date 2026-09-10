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

"""Bug classes about how a finding reaches a *report*.

A themed sibling of :mod:`manifest`, following the same split
``manifest_tool_surface`` established: these are defects in the projection
layer -- a value that restructures the document rendering it, an audit field
resolved one way by one entry-builder and another way by its sibling -- not
in detection or in the CLI surface. Keeping them here is also what lets the
registry keep growing one entry per closed class without ``manifest.py``
outrunning its recorded size baseline.

Query the registry through ``manifest.get``/``manifest.BUG_CLASSES``;
nothing should import this module directly.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["REPORT_BUG_CLASSES"]

REPORT_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="report.untrusted_value_escapes_its_cell",
        invariant=(
            "A value the report does not control — a detector's description, "
            "a demangled symbol, an extractor's error text, a file path — is "
            "data, never structure. Rendering it into a structured format "
            "(a Markdown table cell or code span today) must not let any "
            "character in it end a row, split the table, or close a span. "
            "The escaping rule has exactly one owner per format, shared by "
            "every renderer, and its contract is stated over generated "
            "adversarial values: a row assembled from n escaped cells always "
            "parses back to n cells, for any n and any values. The oracle "
            "counts separators off the rendered text and never re-runs the "
            "escaper, so a bug in the escaper cannot make the check agree "
            "with it."
        ),
        # #1185-era: `report/no_baseline.py`'s finding tables interpolated
        # `change.description`/`change.symbol` raw, and the only escaping
        # helper in the package was private to `report/comparison_scope.py`.
        # Sharing it exposed a hole that helper had shipped with for its
        # whole life: an odd-length run of backslashes immediately before a
        # pipe pairs off against the escape emitted for that pipe, leaving a
        # live cell separator. The generated-input property tests falsified
        # both the original rule and the first, too-narrow fix for it.
        fixed_by=(1185,),
        seed_tests=(
            "tests/test_markdown_cell.py",
            "tests/test_no_baseline_report_formats.py",
        ),
        # `()` and not `("cli",)`: both seed tests call the renderers and the
        # escaper directly, never through Click or `abicheck.service`. A
        # claimed surface a seed test does not reach conceals exactly the
        # missing cross-surface coverage this registry exists to surface
        # (CodeRabbit review; the same rule Codex established in PR #885).
        public_surfaces=(),
        axes={
            "hostile_character": (
                "pipe",
                "backslash",
                "backtick",
                "newline",
                "control",
            ),
            # Only what a seed test really renders. The scope table shares
            # the escaper but no seed test drives a hostile value through
            # it, so listing it here would overstate coverage -- recorded as
            # a known gap below instead (CodeRabbit review).
            "renderer": ("audit-findings-table",),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Only the Markdown renderers route through the shared "
                    "escaper. The HTML, SARIF, JUnit and text renderers each "
                    "carry their own quoting (or rely on a serializer's), and "
                    "no gate asserts that a renderer interpolating an "
                    "untrusted value uses an escaper at all — a new renderer "
                    "can still interpolate one raw."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
            KnownGap(
                description=(
                    "`report/comparison_scope.py`'s scope tables route "
                    "through the same shared escaper, but no seed test "
                    "renders a hostile value through that table -- the "
                    "escaper's own contract is covered, the scope table's "
                    "use of it is covered only by construction."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
        ),
    ),
    BugClass(
        id="report.finding_entry_builder_parity",
        invariant=(
            "Every per-finding entry-builder for a shared `Change` "
            "(`compare`'s `changes[]`, `scan --against`'s baseline dicts, "
            "the release fan-out's capped `findings`, `compare "
            "--no-baseline`'s audit rows) must resolve an audit field (e.g. "
            "`reclassified_by`, a suppression's rule provenance) via one "
            "canonical helper -- never a sibling silently omitting a field "
            "another computes. The same rule holds *between formats* of one "
            "report: a fact one projection publishes and its siblings drop "
            "leaves a consumer of the quiet format unable to act on the run "
            "it was handed."
        ),
        # #1185-era follow-up, two instances of the same shape. The audit's
        # suppressed rows read `Change.suppression_rule` -- the `label or
        # reason` display collapse -- while `reporter.py`'s sibling already
        # resolved the full ADR-067 record through
        # `DispositionLedger.rule_for`, so a waiver stating both lost its
        # reason and source file. And the JUnit projection published only
        # the total exit code where JSON/Markdown/oneline/SARIF all named
        # the orthogonal axis that fired.
        fixed_by=(1176, 1185),
        seed_tests=(
            "tests/test_disposition_reclassification.py",
            "tests/test_no_baseline_report_formats.py",
        ),
        axes={
            # Only what a seed test really drives.
            "renderer": ("json", "markdown", "junit"),
            "record": ("suppression-provenance", "exit-axis"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The between-formats half is asserted for the audit "
                    "document's suppression provenance and exit axes only. "
                    "No gate enumerates a report's semantic fields and "
                    "checks every projection carries each one, so a new "
                    "field added to one renderer alone is still possible."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
        ),
    ),
)
