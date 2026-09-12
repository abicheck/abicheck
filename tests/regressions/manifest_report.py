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
            # The audit's own suppression-provenance suite moved here when
            # `test_no_baseline_report_formats.py` crossed its module-size
            # cap. That module still carries the exit-axis half of this
            # class; naming only it left the `suppression-provenance` record
            # and renderer axes declared below unbacked by any seed test
            # (Codex review, P2).
            "tests/test_no_baseline_suppression_provenance.py",
            "tests/test_no_baseline_report_formats.py",
        ),
        axes={
            # Only what a seed test really drives -- and *all* of it. This
            # tuple omitted `sarif` while
            # `test_every_format_carries_the_reason_and_source_not_just_the_label`
            # parametrizes over `NO_BASELINE_SUPPORTED_FORMATS` minus
            # `oneline`, which includes it. Understating coverage sends a
            # contributor to write a test that already exists, the mirror of
            # the overstating case the known gap below describes (Codex
            # review, P2).
            "renderer": ("json", "markdown", "sarif", "junit"),
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
            KnownGap(
                description=(
                    "Nothing enforces that an axis declared on a BugClass "
                    "matches what its `seed_tests` actually exercise, in "
                    "either direction -- this entry has now been wrong both "
                    "ways: a `seed_tests` list pointing at the file the "
                    "assertions had moved out of, and a `renderer` tuple "
                    "omitting `sarif` while the seed test parametrized over "
                    "it. Overstating sends a reader to a test that does not "
                    "exist; understating sends them to write one that does. "
                    "Splitting a test module for a size "
                    "violation left this entry naming only the file the "
                    "suppression-provenance assertions had moved *out* of, "
                    "and no gate noticed: the declared record and renderer "
                    "axes stood with nothing behind them, and a contributor "
                    "following AGENTS.md's 'check BUG_CLASSES first' advice "
                    "would have been sent to the wrong file (Codex review, "
                    "P2). Fixed by hand here; the class of error is open."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
            KnownGap(
                description=(
                    "Two open instances of this class outside the audit "
                    "path, found by grepping every `suppression_rule` reader "
                    "after the audit's own two were fixed -- recorded rather "
                    "than fixed here, since neither is in the scope the PR "
                    "that found them was opened for. (1) `sarif.py`'s "
                    "two-sided suppression `justification` reads only "
                    "`Change.suppression_rule`, the `label or reason` "
                    "display collapse, while the same run's JSON already "
                    "publishes the full ADR-067 record via "
                    "`reporter.py`'s `_suppressed_change_entry` -- the same "
                    "between-formats split the audit's SARIF had. (2) "
                    "`cli_scan_baseline.py`'s baseline `findings[]` entries "
                    "carry the display label only, with no provenance field "
                    "at all. Fixing either means routing "
                    "`DispositionLedger.rule_for` to that builder, exactly "
                    "as the audit now does."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="report.unestablished_result_reads_as_success",
        invariant=(
            "A consumer that publishes a verdict from a report it did not "
            "read must publish 'no result established', never a passing one. "
            "Stated over the whole class of unusable documents rather than "
            "the one shape that prompted it: no file, zero bytes, truncated "
            "JSON, non-JSON text, valid JSON that is not an object, an empty "
            "object, and undecodable bytes are seven different ways to carry "
            "no result, and every one of them must fail the same way. The "
            "same rule governs a field the report was required to carry and "
            "did not -- 'I cannot establish that this axis failed' is not "
            "'this axis passed' -- but only where the report's own schema "
            "version and companion keys establish that it was owed: an "
            "absence that is legitimate under the emitting version must stay "
            "accepted, since failing those fails working runs rather than "
            "broken ones. Deliberately NOT extended to reconstructing an "
            "axis from rendered prose (a CLI notice, a summary line): that "
            "channel is forgeable by anything a run's own build step can "
            "print, and ADR-063 Track T8 retired it. The axis predicates "
            "therefore keep answering 'cannot tell' for an unreadable "
            "report; what must not happen is a consumer reading that as a "
            "pass."
        ),
        # Two prior escapes of this exact class, both recorded in
        # `action/run.sh`'s own comments: #1016 (a COMPATIBLE_WITH_RISK report
        # laundered into plain COMPATIBLE at exit 0) and #1210 (an audit-only
        # run, then a fully-suppressed one, each defaulting to COMPATIBLE
        # because the exit-0 path could not read its answer from the report).
        # Each was fixed for the shape it was found in; the mechanism --
        # `VERDICT="COMPATIBLE"` as the fallthrough for "the report did not
        # say" -- survived both, which is what makes this a class rather than
        # three incidents.
        # #1246 added two more members, both found by review rather than in
        # production, each reaching the fallthrough by a route the earlier
        # fixes did not model: the validation resolved the report through a
        # *fallback chain*, so a destination that did arrive answered for a
        # requested one that did not; and it judged a document by content
        # alone, so one left behind by an earlier run (or committed by a PR
        # author) read as this run's own output.
        fixed_by=(1016, 1210, 1246),
        seed_tests=(
            "tests/test_action_report_query.py",
            "tests/test_action_unreadable_report_verdict.py",
            # The generalized statement of the class, over a generated
            # cross-product rather than the reported inputs: an admitted
            # document must be answerable
            # (`TestAdmissionImpliesAnswerability`). Verified to catch three of
            # the six historical instances when each is reintroduced.
            "tests/test_action_report_verdict_vocabulary.py",
            "tests/test_action_report_destinations.py",
        ),
        public_surfaces=("github-action",),
        axes={
            "unusable_document": (
                "absent",
                "zero_bytes",
                "truncated_json",
                "not_json",
                "json_array",
                "bare_scalar",
                "empty_object",
                "undecodable_bytes",
            ),
            "report_schema_version": ("absent", "pre_2_40", "2_40", "post_2_40"),
            "report_shape": ("compare_root", "nested_diff", "audit_exit_axes"),
            # Where a report was asked for, and whether *this* run produced it.
            # A reader that resolves one destination for the whole request is
            # the same defect wearing a different hat: one artifact arriving
            # answers for another that never did.
            "requested_destination": (
                "primary_output_file",
                "stdout",
                "extra_args_write_json",
                "several_write_json",
            ),
            "authorship": ("written_this_run", "pre_existing_and_rewritten", "stale"),
            # Whether a document the reader ADMITS is one any consumer can act
            # on. Six findings on #1246 were this one shape, each fixed for
            # itself; the axis exists so the next admission rule without a
            # consumer fails a test instead of publishing COMPATIBLE.
            #
            # Only these two states are *reachable*, which is the point rather
            # than a gap (CodeRabbit review): on correct code no document is
            # admitted-and-unanswerable, so naming that as a third state to
            # exercise would describe a seed the matrix cannot contain. It is
            # the state the invariant forbids, and it is reached only under
            # mutation -- re-introducing three of the six historical findings
            # produces it in 46, 48 and 8 generated documents respectively,
            # which is how `TestAdmissionImpliesAnswerability` was verified to
            # generalize rather than merely to pass.
            "answerability": ("admitted_and_answered", "rejected"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Freshness is established from a (mtime, size) "
                    "fingerprint taken before the run, which cannot "
                    "distinguish 'not rewritten' from 'rewritten with "
                    "byte-identical content in the same nanosecond'. The "
                    "second is not reachable by an attacker who must also "
                    "make the run write that content, and the alternative "
                    "(unlinking a caller-supplied path before Click has "
                    "validated the invocation) was tried and reverted for "
                    "destroying real inputs -- see `action/run.sh`'s own "
                    "note. Recorded because it is a real limit of the "
                    "authorship axis above, not because a fix is pending."
                ),
                reference="action/run.sh",
                canary_test=None,
            ),
            KnownGap(
                description=(
                    "Scoped to a JSON report the caller explicitly requested "
                    "(`format: json` plus `output-file`). When the primary "
                    "format is not json, the Action injects an internal "
                    "`--write json=` sidecar for its own rendering; if that "
                    "one is missing, exit 0 still publishes `COMPATIBLE`, "
                    "which can understate a `BREAKING`/`API_BREAK` the "
                    "severity policy demoted to exit 0. Not closed by "
                    "widening the predicate: exit 0 is real evidence that the "
                    "tool's own gate passed, so failing the step there would "
                    "be wrong. It needs a verdict value distinguishing "
                    "'accepted, tier unverified' from 'accepted, compatible' "
                    "-- a change to the Action's declared `verdict` output "
                    "contract, so an ADR rather than an incremental fix."
                ),
                reference="action/AGENTS.md",
                canary_test=None,
            ),
            KnownGap(
                description=(
                    "Only the composite Action's own reader is covered. "
                    "`abicheck aggregate` reads the same report fields "
                    "(`_analysis_assurance_exit`) through an independent "
                    "implementation and is not held to this invariant yet; "
                    "neither is `buildsource/check_report.py`, whose "
                    "defaulting of a missing contribution to 0 is a "
                    "deliberate advisory-neutralization rather than an "
                    "instance of this class, but which shares the shape."
                ),
                reference="docs/contribute/known-gaps.md",
                canary_test=None,
            ),
        ),
    ),
)
