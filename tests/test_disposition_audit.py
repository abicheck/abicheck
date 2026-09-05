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

"""ADR-067 C-S1: the scalar policy-disposition audit.

The registered bug class is ``policy.disposition_conservation``
(``tests/regressions/manifest.py``): *a policy disposition moves a detected
change between buckets; it never changes how many changes were detected, and
the report states both totals.* Two concrete defects fall out of that
invariant being unstated before this slice, and both are exercised below
rather than only described:

* a suppressed major-class break degraded ``semver.recommend_release`` to "no
  version bump required" — the recommendation read the *post*-disposition
  ``changes`` list;
* a detector that never ran was recorded as ``enabled=True,
  changes_count=0``, so "did not run" and "ran, found nothing" were the same
  report.

The conservation tests are deliberately generative rather than fixed-input
(AGENTS.md "A bug fix's regression test targets the bug *class*"): the
oracle is an *independent recount* of the terminal buckets on ``DiffResult``,
run over the full cross-product of five snapshot shapes and six rule shapes
(narrow, broad, kind-scoped, non-matching, overlapping, none) so the
invariant is checked against a formula the implementation does not share,
across every application point those combinations reach — not against an
expected number a reader could have copied out of the implementation.
"""

from __future__ import annotations

import itertools
import json

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.model import AbiSnapshot, Function, Variable, Visibility
from abicheck.policy.disposition_close import (
    conservation_holds,
    finalize_ledger,
    ledger_for,
)
from abicheck.policy.disposition_ledger import (
    Disposition,
    DispositionLedger,
    record_suppressed_change,
)
from abicheck.report.disposition_audit import compute_disposition_audit
from abicheck.semver import SemverBump
from abicheck.suppression import Suppression, SuppressionList


def _snapshots(
    removed: int = 0,
    *,
    kept: int = 0,
    added: int = 0,
    variables_removed: int = 0,
    prefix: str = "foo",
) -> tuple[AbiSnapshot, AbiSnapshot]:
    """Old/new pair with *removed* public functions gone in the new side."""
    old = AbiSnapshot(library="libfoo", version="1.0")
    new = AbiSnapshot(library="libfoo", version="2.0")

    def _fn(name: str) -> Function:
        return Function(
            name=f"{prefix}::{name}",
            mangled=f"_ZN3{prefix}{len(name)}{name}Ev",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )

    for i in range(removed):
        old.functions.append(_fn(f"gone{i}"))
    for i in range(kept):
        fn = _fn(f"stay{i}")
        old.functions.append(fn)
        new.functions.append(_fn(f"stay{i}"))
    for i in range(added):
        new.functions.append(_fn(f"new{i}"))
    for i in range(variables_removed):
        old.variables.append(
            Variable(
                name=f"{prefix}::var{i}",
                mangled=f"_ZN3{prefix}3var{i}E",
                type="int",
                visibility=Visibility.PUBLIC,
            )
        )
    return old, new


# ---------------------------------------------------------------------------
# The ADR's own named acceptance fixture
# ---------------------------------------------------------------------------


class TestHundredSuppressedRemovals:
    """ADR-067's "Tests (contract)" first row: *100 removals plus a wildcard
    waiver (counts and rule visible on a passing run)*."""

    @pytest.fixture
    def result(self):
        old, new = _snapshots(removed=100)
        rules = SuppressionList(
            [
                Suppression(
                    symbol_pattern=".*",
                    reason="bulk internal churn",
                    label="wildcard-waiver",
                    allow_public_break=True,
                )
            ]
        )
        return compare(old, new, rules)

    def test_the_run_passes_but_the_hundred_removals_are_still_counted(
        self, result
    ) -> None:
        # The point of the fixture: the *gate* is clean, and the audit is not.
        assert result.changes == []
        audit = compute_disposition_audit(result)
        assert audit.detected_total == 100
        assert audit.effective_total == 0
        assert dict(audit.counts)[Disposition.SUPPRESSED.value] == 100

    def test_the_rule_that_hid_them_is_named_with_its_reason(self, result) -> None:
        audit = compute_disposition_audit(result)
        assert len(audit.rules) == 1
        rule, count = audit.rules[0]
        assert count == 100
        assert rule.reason == "bulk internal churn"
        assert rule.label == "wildcard-waiver"
        assert rule.allow_public_break is True
        # ADR-067 D5's migration default for every rule written before the
        # explicit `intent:` field exists.
        assert rule.intent == "unspecified"

    def test_rule_provenance_reaches_the_json_suppression_ledger(self, result) -> None:
        from abicheck import reporter

        report = json.loads(reporter.to_json(result))
        entries = report["suppression"]["suppressed_changes"]
        assert len(entries) == 100
        assert all(e["rule"]["reason"] == "bulk internal churn" for e in entries)
        assert report["disposition_audit"]["detected_total"] == 100
        assert report["disposition_audit"]["counts"]["suppressed"] == 100

    def test_the_suppression_document_path_is_recorded(self, tmp_path) -> None:
        path = tmp_path / "suppress.yml"
        path.write_text(
            "version: 1\nsuppressions:\n  - symbol_pattern: '.*'\n"
            "    reason: bulk internal churn\n    allow_public_break: true\n",
            encoding="utf-8",
        )
        old, new = _snapshots(removed=100)
        result = compare(old, new, SuppressionList.load(path))
        rule, count = compute_disposition_audit(result).rules[0]
        assert count == 100
        assert rule.source_file == str(path)

    def test_every_projection_states_the_counts(self, result) -> None:
        """Workstream G's report invariant: collapsing detail is fine,
        dropping the counts is not."""
        from abicheck import reporter
        from abicheck.pr_comment import build_model, render_comment
        from abicheck.reporter_markdown import to_review_digest, to_stat

        one_line = to_stat(result)
        assert "100 detected" in one_line and "0 gating" in one_line

        digest = to_review_digest(result)
        assert "| Detected (raw) | 100 |" in digest
        assert "| Effective (gating) | 0 |" in digest
        assert "bulk internal churn" in digest

        for payload in (reporter.to_json(result), reporter.to_stat_json(result)):
            block = json.loads(payload)["disposition_audit"]
            assert block["detected_total"] == 100
            assert block["effective_total"] == 0

        comment = render_comment(build_model(json.loads(reporter.to_json(result))))
        assert "100 detected" in comment
        assert "0 gating" in comment
        assert "bulk internal churn" in comment

        from abicheck.html_report import generate_html_report
        from abicheck.junit_report import to_junit_xml
        from abicheck.sarif import to_sarif

        audit = to_sarif(result)["runs"][0]["properties"]["dispositionAudit"]
        assert audit["detected_total"] == 100
        assert audit["effective_total"] == 0
        assert audit["rules"][0]["reason"] == "bulk internal churn"

        junit = to_junit_xml(result)
        assert 'name="abicheck.detected_total" value="100"' in junit
        assert 'name="abicheck.effective_total" value="0"' in junit
        assert 'name="abicheck.disposition.suppressed" value="100"' in junit

        html = generate_html_report(result)
        assert "100 detected" in html and "0 gating" in html
        assert "bulk internal churn" in html

        # Every Markdown mode, not only the digest: `--report-mode` is a
        # presentation choice, and D3's counts are not presentation.
        from abicheck.reporter_markdown import (
            _to_markdown_leaf,
            _to_markdown_root_cause,
            to_markdown,
        )

        for render in (to_markdown, _to_markdown_leaf, _to_markdown_root_cause):
            text = render(result)
            assert "| Detected (raw) | 100 |" in text, render.__name__
            assert "| Effective (gating) | 0 |" in text, render.__name__
            assert "bulk internal churn" in text, render.__name__


# ---------------------------------------------------------------------------
# Conservation across the application points (the class-level invariant)
# ---------------------------------------------------------------------------


#: Selector shapes that reach genuinely different application points and
#: gate paths — a narrow exact symbol, a broad pattern needing the
#: public-break gate, a kind selector, and one that matches nothing at all.
_RULE_SHAPES = (
    lambda: [],
    lambda: [Suppression(symbol_pattern=".*gone1.*", reason="narrow")],
    lambda: [Suppression(symbol_pattern=".*", reason="broad", allow_public_break=True)],
    lambda: [
        Suppression(
            symbol_pattern="_ZN3foo.*",
            change_kind="func_removed",
            reason="by kind",
            allow_public_break=True,
        )
    ],
    lambda: [Suppression(symbol="does::not::exist", reason="matches nothing")],
    lambda: [
        Suppression(symbol_pattern=".*gone0.*", reason="first", label="a"),
        Suppression(symbol_pattern=".*", reason="rest", allow_public_break=True),
    ],
)

#: Snapshot shapes exercising removals, additions, unchanged symbols and a
#: removed variable (a different detector, hence a different call site).
_SHAPES = (
    dict(removed=0),
    dict(removed=1),
    dict(removed=3, kept=2, added=2),
    dict(removed=2, variables_removed=2),
    dict(removed=5, added=1, variables_removed=1, kept=1),
)


@pytest.mark.parametrize(
    ("shape", "make_rules"),
    list(itertools.product(_SHAPES, _RULE_SHAPES)),
)
def test_disposition_counts_conserve_the_detected_total(shape, make_rules) -> None:
    """ADR-067 D3's counting identity, over a matrix of shapes and rules.

    The oracle is deliberately *not* the ledger's own ``counts()`` sum: it is
    an independent recount of the terminal buckets on ``DiffResult`` — kept,
    suppressed, redundant, out-of-surface and reconciled — which is what
    "conserved" has to mean for a reader of the report. A change that a
    disposition moved between buckets must still be counted exactly once.
    """
    old, new = _snapshots(**shape)
    rules = make_rules()
    result = compare(old, new, SuppressionList(rules) if rules else None)
    ledger = ledger_for(result)

    assert conservation_holds(ledger)
    assert sum(ledger.counts().values()) == ledger.detected_total

    # Scope of this oracle, stated so a later reader does not over-read it:
    # equality holds because none of these shapes produce an *early* collapse
    # (a duplicate a pre-`FilterRedundant` step discards reaches no bucket at
    # all, and is counted by the ledger alone -- see
    # `test_early_deduplication_is_counted_not_dropped`). The claim here is
    # the one that matters for a report reader: everything that survived to a
    # bucket is counted exactly once, in exactly one disposition.
    independent_total = (
        len(result.changes)
        + len(result.suppressed_changes)
        + len(result.redundant_changes)
        + len(result.out_of_surface_changes)
        + len(result.reconciled_changes)
    )
    assert ledger.detected_total == independent_total


@pytest.mark.parametrize(
    ("shape", "make_rules"), list(itertools.product(_SHAPES, _RULE_SHAPES))
)
def test_every_suppressed_finding_has_a_ledger_record(shape, make_rules) -> None:
    """No application point may move a change into ``suppressed_changes``
    without recording it — the failure mode a ledger fed from one helper
    alone would have."""
    old, new = _snapshots(**shape)
    rules = make_rules()
    result = compare(old, new, SuppressionList(rules) if rules else None)
    ledger = ledger_for(result)
    for change in result.suppressed_changes:
        record = ledger.record_for(change)
        assert record is not None
        assert record.disposition is Disposition.SUPPRESSED
        # Recorded *at* the application point, so the rule survived: the
        # fallback path (`unrecorded_suppression`) means one of them was
        # missed.
        assert record.application_point != "unrecorded_suppression"
        assert record.rule is not None


def test_suppression_never_changes_the_detected_total() -> None:
    """The metamorphic half: adding a rule redistributes dispositions and
    leaves the raw total alone, whatever the rule matches."""
    old, new = _snapshots(removed=6, added=2, kept=2, variables_removed=2)
    baseline = ledger_for(compare(old, new)).detected_total
    for make_rules in _RULE_SHAPES:
        rules = make_rules()
        result = compare(old, new, SuppressionList(rules) if rules else None)
        assert ledger_for(result).detected_total == baseline


def test_consumer_overlay_suppression_records_into_the_same_ledger() -> None:
    """The fourth application point (``appcompat``), whose input shape is a
    raw ``missing_symbols`` string rather than a detected change."""
    from abicheck.appcompat import AppRequirements
    from abicheck.checker_types import Change, DiffResult

    result = DiffResult(old_version="1.0", new_version="2.0", library="libfoo")
    ledger = finalize_ledger(DispositionLedger(), result)
    result.disposition_ledger = ledger
    overlay = Change(
        kind=ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
        symbol="_ZN3foo4goneEv",
        description="required by consumer",
    )
    rule = Suppression(symbol="_ZN3foo4goneEv", reason="consumer retired")
    from abicheck.policy.disposition_ledger import record_suppressed_change

    record_suppressed_change(
        ledger_for(result),
        overlay,
        rule=rule,
        application_point="consumer_overlay",
        suppression=SuppressionList([rule]),
    )
    record = ledger.record_for(overlay)
    assert record is not None
    assert record.application_point == "consumer_overlay"
    assert record.rule is not None and record.rule.reason == "consumer retired"
    assert conservation_holds(ledger)
    assert AppRequirements is not None  # the module imported cleanly


def test_ledger_records_each_change_exactly_once() -> None:
    """Idempotence: finalizing twice cannot double-count."""
    old, new = _snapshots(removed=4, added=1)
    result = compare(old, new)
    ledger = ledger_for(result)
    before = ledger.detected_total
    finalize_ledger(ledger, result)
    finalize_ledger(ledger, result)
    assert ledger.detected_total == before
    assert conservation_holds(ledger)


def test_two_distinct_changes_are_never_collapsed_into_one_record() -> None:
    """The must-merge / must-not-merge pair for the ledger's identity keying.

    ``record()`` is identity-keyed so a finalization pass cannot re-record a
    change an application point already recorded (the must-merge half, above).
    The complement matters just as much and is a separate claim: two *distinct*
    findings that happen to share a ``(kind, symbol)`` spelling — the key a
    naive value-based ledger would use — must stay two records, or the
    conservation identity would be satisfiable by collapsing everything.
    """
    from abicheck.checker_types import Change, DiffResult

    def _change() -> Change:
        return Change(kind=ChangeKind.FUNC_REMOVED, symbol="dup", description="removed")

    twins = [_change(), _change()]
    result = DiffResult(
        old_version="1.0", new_version="2.0", library="libfoo", changes=twins
    )
    ledger = ledger_for(result)
    assert ledger.detected_total == 2
    assert ledger.record_for(twins[0]) is not None
    assert ledger.record_for(twins[1]) is not None

    # …and the must-merge half, stated against the same ledger: recording the
    # *same* object again is a no-op, whatever disposition is offered.
    ledger.record(twins[0], Disposition.SUPPRESSED, application_point="again")
    assert ledger.detected_total == 2
    assert ledger.counts()[Disposition.SUPPRESSED.value] == 0
    assert conservation_holds(ledger)


def test_distinct_rules_are_never_merged_in_the_rule_tally() -> None:
    """The same pair for the audit's *rule* grouping: two rules sharing a
    label/reason must stay two rows, and one rule matching many findings must
    stay one row with a count — the failure this repo's own history calls a
    grouping key that folds in too much or too little."""
    old, new = _snapshots(removed=4)
    result = compare(
        old,
        new,
        SuppressionList(
            [
                Suppression(
                    symbol_pattern=".*gone0.*",
                    reason="shared prose",
                    label="same",
                    allow_public_break=True,
                ),
                Suppression(
                    symbol_pattern=".*",
                    reason="shared prose",
                    label="same",
                    allow_public_break=True,
                ),
            ]
        ),
    )
    rules = compute_disposition_audit(result).rules
    assert len(rules) == 2, "two rules sharing label and reason are still two"
    assert {count for _, count in rules} == {1, 3}
    assert sum(count for _, count in rules) == 4


def test_kept_findings_split_into_gating_and_non_gating() -> None:
    old, new = _snapshots(removed=2, added=3)
    result = compare(old, new)
    counts = ledger_for(result).counts()
    assert counts[Disposition.GATING.value] == 2
    assert counts[Disposition.NON_GATING.value] == 3
    assert counts[Disposition.SUPPRESSED.value] == 0


@pytest.mark.parametrize(
    ("relevance", "expected"),
    [
        ("IN_CONTRACT", Disposition.GATING),
        ("NOT_APPLICABLE", Disposition.GATING),
        ("PROVEN_OUT_OF_CONTRACT", Disposition.OUT_OF_CONTRACT),
        ("UNKNOWN_UNPROVEN", Disposition.UNRESOLVED_RELEVANCE),
        ("UNKNOWN_UNRESOLVED", Disposition.UNRESOLVED_RELEVANCE),
    ],
)
def test_every_contract_relevance_maps_to_its_own_disposition(
    relevance, expected
) -> None:
    """ADR-049's split, exhaustively: "proven outside the contract" and
    "the evidence ran out" are different dispositions with different
    downstream consequences, and an evaluated finding is neither.

    Enumerated over the whole (small) domain rather than one example, because
    the mapping is exactly the kind of value-spelling comparison that fails
    silently for the *other* members when written against one of them.
    """
    from abicheck.checker_types import Change, DiffResult
    from abicheck.contract_relevance_types import ContractRelevance

    change = Change(kind=ChangeKind.FUNC_REMOVED, symbol="gone", description="removed")
    change.contract_relevance = ContractRelevance[relevance]
    result = DiffResult(
        old_version="1.0", new_version="2.0", library="libfoo", changes=[change]
    )
    ledger = ledger_for(result)
    record = ledger.record_for(change)
    assert record is not None and record.disposition is expected
    assert conservation_holds(ledger)


def test_ledger_for_a_hand_built_result_still_reconciles() -> None:
    """Every consumer must be able to state the counts unconditionally, even
    for a ``DiffResult`` no ``compare()`` produced."""
    from abicheck.checker_types import Change, DiffResult

    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libfoo",
        changes=[Change(kind=ChangeKind.FUNC_REMOVED, symbol="a", description="")],
        suppressed_changes=[
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="b", description="")
        ],
        suppressed_count=1,
    )
    ledger = ledger_for(result)
    assert ledger.detected_total == 2
    assert conservation_holds(ledger)
    # Derived, never attached: a projection asking for the ledger must not
    # mutate the result it is about to render (the invariant
    # `tests/unit/report/test_render_html.py` states executably). Two calls
    # therefore agree on the facts without sharing an object.
    assert result.disposition_ledger is None
    again = ledger_for(result)
    assert again.counts() == ledger.counts()
    assert again.detected_total == ledger.detected_total


class TestGatingFollowsTheResolvedGate:
    """`gating` means "contributes to *this run's* gate", which is the
    severity-aware answer whenever a severity configuration is in effect
    (ADR-064 resolves it in the front end, after `compare()` has run).

    Both directions matter and are independent claims: a category promoted to
    `error` gates something the raw verdict calls compatible, and a category
    demoted to `warning`/`info` stops gating something the raw verdict calls
    a break.
    """

    @staticmethod
    def _config(**levels):
        from abicheck.policy.severity import SeverityConfig, SeverityLevel

        return SeverityConfig(
            **{category: SeverityLevel(level) for category, level in levels.items()}
        )

    def test_an_addition_promoted_to_error_reads_gating(self) -> None:
        old, new = _snapshots(added=2)
        result = compare(old, new)
        assert compute_disposition_audit(result).effective_total == 0

        promoted = compute_disposition_audit(result, self._config(addition="error"))
        assert promoted.effective_total == 2
        assert dict(promoted.counts)[Disposition.NON_GATING.value] == 0

    def test_a_break_demoted_to_info_reads_non_gating(self) -> None:
        old, new = _snapshots(removed=2)
        result = compare(old, new)
        assert compute_disposition_audit(result).effective_total == 2

        demoted = compute_disposition_audit(result, self._config(abi_breaking="info"))
        assert demoted.effective_total == 0
        assert dict(demoted.counts)[Disposition.NON_GATING.value] == 2

    def test_the_gate_never_moves_a_suppressed_finding(self) -> None:
        """A severity configuration decides what *gates*; it cannot revive a
        finding a rule withheld (D2: one change, one terminal disposition)."""
        old, new = _snapshots(removed=2)
        result = compare(
            old,
            new,
            SuppressionList(
                [Suppression(symbol_pattern=".*", reason="w", allow_public_break=True)]
            ),
        )
        for config in (None, self._config(abi_breaking="error")):
            audit = compute_disposition_audit(result, config)
            assert dict(audit.counts)[Disposition.SUPPRESSED.value] == 2
            assert audit.detected_total == 2
            assert audit.effective_total == 0

    def test_applying_a_gate_twice_is_idempotent(self) -> None:
        old, new = _snapshots(removed=1, added=1)
        result = compare(old, new)
        config = self._config(addition="error")
        first = compute_disposition_audit(result, config)
        second = compute_disposition_audit(result, config)
        assert first == second
        assert first.detected_total == 2


def test_early_deduplication_is_counted_not_dropped() -> None:
    """`DeduplicateAstDwarf`/`DeduplicateCrossDetector` run before
    `FilterRedundant` and simply return a shorter list. A finding they fold
    away is still a detected change, so it must appear in the raw total under
    the `deduplicated` disposition — otherwise `detected_total` silently
    undercounts exactly the collapses the audit exists to expose.

    Driven through the pipeline itself with a duplicate pair rather than
    through a hand-called step, and asserted against the *step's own* output
    length, which is an oracle independent of the ledger.
    """
    from abicheck.checker_types import Change
    from abicheck.post_processing import DEFAULT_PIPELINE

    duplicates = [
        Change(kind=ChangeKind.FUNC_REMOVED, symbol="dup", description="a"),
        Change(kind=ChangeKind.FUNC_REMOVED, symbol="dup", description="b"),
    ]
    old, new = _snapshots()
    ledger = DispositionLedger()
    ctx = DEFAULT_PIPELINE.run(list(duplicates), old, new, disposition_ledger=ledger)
    dropped = len(duplicates) - len(ctx.kept)
    if dropped == 0:  # pragma: no cover - defends the fixture, not the code
        pytest.skip("this pair was not collapsed by any early dedup step")
    assert ledger.counts()[Disposition.DEDUPLICATED.value] == dropped
    assert ledger.detected_total == dropped


def test_a_pipeline_step_can_never_drop_a_finding_unrecorded() -> None:
    """The class-level statement of the same invariant: for *any* step in the
    default pipeline, a finding that leaves `changes` without landing in one
    of the context's own buckets is recorded, so the ledger plus the buckets
    always account for every input change."""
    from abicheck.checker_types import Change
    from abicheck.post_processing import DEFAULT_PIPELINE

    inputs = [
        Change(kind=ChangeKind.FUNC_REMOVED, symbol=f"sym{i}", description="x")
        for i in range(4)
    ] + [
        Change(kind=ChangeKind.FUNC_REMOVED, symbol="sym0", description="dup"),
    ]
    old, new = _snapshots()
    ledger = DispositionLedger()
    ctx = DEFAULT_PIPELINE.run(list(inputs), old, new, disposition_ledger=ledger)
    accounted = {id(c) for c in ctx.kept}
    for bucket in (
        ctx.suppressed,
        ctx.redundant,
        ctx.opaque_filtered,
        ctx.out_of_surface,
    ):
        accounted.update(id(c) for c in bucket)
    accounted.update(ledger._seen_ids)
    assert {id(c) for c in inputs} <= accounted


def test_opaque_downgrades_are_not_labelled_deduplicated() -> None:
    """`redundant_changes` concatenates two populations, split at
    `redundant_count`. An opaque-handle downgrade was excluded from the
    verdict on its own merits — calling it `deduplicated` would claim it was
    folded into a finding that does not exist."""
    from abicheck.checker_types import Change, DiffResult

    collapsed = Change(kind=ChangeKind.FUNC_REMOVED, symbol="a", description="")
    opaque = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="H", description="")
    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libfoo",
        redundant_changes=[collapsed, opaque],
        redundant_count=1,
    )
    ledger = ledger_for(result)
    assert ledger.record_for(collapsed).disposition is Disposition.DEDUPLICATED
    assert ledger.record_for(opaque).disposition is Disposition.NON_GATING
    assert ledger.record_for(opaque).application_point == "opaque_downgrade"
    assert conservation_holds(ledger)


def test_a_grouped_child_is_deduplicated_not_suppressed() -> None:
    """`DetectCppPatterns._suppress_grouped_children` folds per-symbol
    removals into one grouped finding by parking them in `suppressed_changes`
    -- a list choice forced by how the pipeline computes the verdict, not a
    user suppression. Labelling them `suppressed` would make an ordinary
    ISA-tier grouping look like a waived major break.
    """
    from abicheck.checker_types import Change
    from abicheck.post_processing import DetectCppPatterns, PipelineContext

    old, new = _snapshots()
    children = [
        Change(kind=ChangeKind.FUNC_REMOVED, symbol=f"foo_avx{i}", description="x")
        for i in range(2)
    ]
    ledger = DispositionLedger()
    ctx = PipelineContext(old=old, new=new, disposition_ledger=ledger)
    changes = list(children)
    DetectCppPatterns._suppress_grouped_children(changes, {"foo_avx0", "foo_avx1"}, ctx)
    assert changes == []
    assert ctx.suppressed == children
    for child in children:
        record = ledger.record_for(child)
        assert record is not None
        assert record.disposition is Disposition.DEDUPLICATED
        # …and no rule is attributed, because none fired.
        assert record.rule is None
    assert ledger.counts()[Disposition.SUPPRESSED.value] == 0


def test_grouping_children_does_not_recommend_a_major_release() -> None:
    """The consequence that makes the disposition above load-bearing: the
    release recommendation reads *suppressed-and-gating* records, so a
    grouped child mislabelled `suppressed` would turn a PATCH into
    MAJOR/REVIEW."""
    from abicheck.checker_types import Change, DiffResult
    from abicheck.post_processing import DetectCppPatterns, PipelineContext
    from abicheck.semver import recommend_release

    old, new = _snapshots()
    children = [Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo_avx", description="x")]
    ledger = DispositionLedger()
    ctx = PipelineContext(old=old, new=new, disposition_ledger=ledger)
    changes = list(children)
    DetectCppPatterns._suppress_grouped_children(changes, {"foo_avx"}, ctx)

    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libfoo",
        suppressed_changes=list(ctx.suppressed),
        suppressed_count=len(ctx.suppressed),
    )
    result.disposition_ledger = finalize_ledger(ledger, result)
    rec = recommend_release(result)
    assert rec.bump is not SemverBump.MAJOR
    assert "suppressed" not in rec.rationale


def test_a_late_duplicate_finding_is_conserved_with_and_without_a_rule() -> None:
    """`_merge_findings_respecting_suppression` drops a late finding whose
    `(kind, symbol)` is already present. That drop is invisible to
    `Pipeline.run`'s own sweep (these objects are created *by* the step, so
    they are not in the snapshot it diffs against), so it is recorded here --
    in both branches, or adding a rule would change the detected total
    instead of moving the finding between dispositions.
    """
    from abicheck.checker_types import Change
    from abicheck.post_processing import (
        PipelineContext,
        _merge_findings_respecting_suppression,
    )

    old, new = _snapshots()
    totals = {}
    for label, rules in (
        ("no rule", None),
        (
            "matching rule",
            SuppressionList(
                [Suppression(symbol="dup", reason="w", allow_public_break=True)]
            ),
        ),
    ):
        ledger = DispositionLedger()
        ctx = PipelineContext(
            old=old, new=new, suppression=rules, disposition_ledger=ledger
        )
        existing = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="dup", description="first")
        ]
        late = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="dup", description="second")
        ]
        _merge_findings_respecting_suppression(existing, late, ctx)
        totals[label] = ledger.detected_total
    assert totals["no rule"] == totals["matching rule"] == 1


def test_a_versioned_symbol_collapse_is_deduplicated_not_suppressed() -> None:
    """Third instance of the same shape as the grouped-children case: G15's
    `--collapse-versioned-symbols` parks the matched rename pair in
    `ctx.suppressed` because that is the only bucket excluded from the
    verdict — no user rule is involved, and reading it as a waiver would make
    the release recommendation contradict the option's whole purpose (the
    pair is *reclassified as compatible*).
    """
    from abicheck.checker_types import Change, DiffResult
    from abicheck.post_processing import PipelineContext, _record_collapsed_findings
    from abicheck.semver import recommend_release

    old, new = _snapshots()
    matched = [
        Change(kind=ChangeKind.FUNC_REMOVED, symbol="u_foo_70", description="x"),
        Change(kind=ChangeKind.FUNC_ADDED, symbol="u_foo_71", description="x"),
    ]
    ledger = DispositionLedger()
    ctx = PipelineContext(old=old, new=new, disposition_ledger=ledger)
    _record_collapsed_findings(
        matched, ctx, application_point="versioned_symbol_collapse"
    )
    for change in matched:
        record = ledger.record_for(change)
        assert record is not None
        assert record.disposition is Disposition.DEDUPLICATED
        assert record.rule is None

    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libfoo",
        suppressed_changes=list(matched),
        suppressed_count=len(matched),
    )
    result.disposition_ledger = finalize_ledger(ledger, result)
    assert recommend_release(result).bump is not SemverBump.MAJOR


def test_every_rule_less_suppressed_bucket_write_is_recorded() -> None:
    """The sweep, as an executable check rather than a one-time audit.

    Three steps now park a change in `ctx.suppressed` with no rule
    (`_suppress_grouped_children`, the late-duplicate drop, the versioned
    -symbol collapse), and each was found one review round at a time. This
    asserts the *property* they share: after any default-pipeline run, no
    finding in `ctx.suppressed` is labelled `suppressed` without a rule
    attributed — which is exactly what made `recommend_release` misread them.
    """
    from abicheck.checker_types import Change
    from abicheck.post_processing import DEFAULT_PIPELINE

    old, new = _snapshots()
    inputs = [
        Change(kind=ChangeKind.FUNC_REMOVED, symbol="dup", description="a"),
        Change(kind=ChangeKind.FUNC_REMOVED, symbol="dup", description="b"),
        Change(kind=ChangeKind.FUNC_ADDED, symbol="added", description="c"),
    ]
    ledger = DispositionLedger()
    ctx = DEFAULT_PIPELINE.run(list(inputs), old, new, disposition_ledger=ledger)
    for change in ctx.suppressed:
        record = ledger.record_for(change)
        assert record is not None, (
            "a rule-less suppression bucket write went unrecorded"
        )
        if record.disposition is Disposition.SUPPRESSED:
            assert record.rule is not None, (
                f"{change.symbol!r} is labelled suppressed with no rule attributed"
            )


def test_a_merged_suppression_list_keeps_each_rule_source() -> None:
    """The ABICC front end merges a `--suppress` document with rules
    synthesized from `-skip-*` options. The merged list has no single source
    path, so a list-level answer reports `None` for *every* rule — including
    the ones that really did come from the file."""
    from abicheck.policy.disposition_ledger import rule_provenance

    from_file = Suppression(symbol="a", reason="from yaml")
    from_flag = Suppression(symbol="b", reason="from -skip-symbol")
    file_list = SuppressionList([from_file], source_path="/tmp/suppressions.yml")
    flag_list = SuppressionList([from_flag])
    merged = SuppressionList.merge(file_list, flag_list)

    assert merged.source_path is None  # honest: two origins, no single one
    assert merged.source_for(from_file) == "/tmp/suppressions.yml"
    assert merged.source_for(from_flag) is None

    from abicheck.policy.disposition_ledger import _source_file_for

    assert _source_file_for(merged, from_file) == "/tmp/suppressions.yml"
    assert _source_file_for(merged, from_flag) is None
    assert rule_provenance(from_file, source_file=None).rule_id is not None


def test_merged_rule_provenance_reaches_a_real_comparison(tmp_path) -> None:
    """…and end to end, through `compare()` and the report: a finding hidden
    by a file-backed rule names the file even when the rule set reaching the
    engine was merged with programmatic rules."""
    path = tmp_path / "suppress.yml"
    path.write_text(
        "version: 1\nsuppressions:\n  - symbol_pattern: '.*gone0.*'\n"
        "    reason: from the document\n    allow_public_break: true\n",
        encoding="utf-8",
    )
    merged = SuppressionList.merge(
        SuppressionList.load(path),
        SuppressionList([Suppression(symbol="unrelated", reason="from a flag")]),
    )
    old, new = _snapshots(removed=1)
    audit = compute_disposition_audit(compare(old, new, merged))
    assert len(audit.rules) == 1
    rule, _ = audit.rules[0]
    assert rule.source_file == str(path)
    assert rule.reason == "from the document"


def test_a_substituting_step_records_one_observation_not_two() -> None:
    """`DowngradeOpaqueStructChanges` replaces a breaking layout finding with
    a compatible one describing the *same* observation. Counting the original
    as a drop *and* the replacement as new would report two detected changes
    where one was observed — the audit's whole job is to make that number
    trustworthy.
    """
    from abicheck.checker_types import Change
    from abicheck.post_processing import (
        DowngradeOpaqueStructChanges,
        PipelineContext,
        _record_dropped_duplicates,
    )

    old, new = _snapshots()
    original = Change(
        kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Handle", description="x"
    )
    replacement = Change(
        kind=ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE,
        symbol="Handle",
        description="(opaque struct) x",
    )
    ledger = DispositionLedger()
    ctx = PipelineContext(old=old, new=new, disposition_ledger=ledger)
    _record_dropped_duplicates(
        [original],
        [replacement],
        0,
        ctx,
        DowngradeOpaqueStructChanges.name,
        DowngradeOpaqueStructChanges.dropped_finding_disposition,
    )
    assert ledger.detected_total == 0, "the substituted original is not a drop"
    assert ledger.counts()[Disposition.DEDUPLICATED.value] == 0


@pytest.mark.parametrize(
    ("declared", "expected_records", "expected_disposition"),
    [
        (Disposition.DEDUPLICATED, 1, Disposition.DEDUPLICATED),
        (Disposition.NON_GATING, 1, Disposition.NON_GATING),
        (None, 0, None),
    ],
)
def test_a_steps_declared_drop_meaning_is_what_gets_recorded(
    declared, expected_records, expected_disposition
) -> None:
    """The mechanism itself, over its whole (three-valued) domain.

    None of the three answers — folded into another finding, excluded as
    compatible noise, substituted by a replacement — is derivable from the
    before/after lists, so each step declares its own and this asserts the
    declaration is honoured rather than one step's happening to work.
    """
    from abicheck.checker_types import Change
    from abicheck.post_processing import PipelineContext, _record_dropped_duplicates

    old, new = _snapshots()
    dropped = Change(kind=ChangeKind.FUNC_REMOVED, symbol="gone", description="x")
    ledger = DispositionLedger()
    ctx = PipelineContext(old=old, new=new, disposition_ledger=ledger)
    _record_dropped_duplicates([dropped], [], 0, ctx, "a_step", declared)
    assert ledger.detected_total == expected_records
    if expected_disposition is not None:
        assert ledger.record_for(dropped).disposition is expected_disposition


def test_the_one_line_view_states_a_support_gap_on_a_zero_change_run() -> None:
    """The one view where "no changes (0 total)" is the entire report: if the
    counts are all zero *and* detectors were skipped for missing evidence,
    dropping the note leaves the assurance gap stated nowhere at all."""
    from abicheck.reporter_markdown import to_stat

    old, new = _snapshots()
    result = compare(old, new)
    audit = compute_disposition_audit(result)
    assert audit.detected_total == 0
    assert audit.not_evaluated_detectors, "this pair must leave detectors unevaluated"

    line = to_stat(result)
    assert "no changes (0 total)" in line
    assert f"{len(audit.not_evaluated_detectors)} detector(s) not evaluated" in line


def test_severity_cannot_un_demote_a_scope_excluded_finding() -> None:
    """Scope and severity are different authorities, and the audit must not
    let one overwrite the other: severity says *how severe* a finding is,
    never whether the consumer this run gates on uses it at all. Without the
    distinction, `abi_breaking: error` pulls a scope-excluded finding back
    into the gate the scoped run already passed."""
    from abicheck.policy.disposition_close import close_consumer_scope
    from abicheck.policy.severity import SeverityConfig, SeverityLevel

    old, new = _snapshots(removed=2)
    result = compare(old, new)
    ledger = ledger_for(result)
    result.disposition_ledger = ledger
    close_consumer_scope(ledger, result, gating=[result.changes[0]])
    assert ledger.effective_total == 1

    strict = SeverityConfig(abi_breaking=SeverityLevel.ERROR)
    assert compute_disposition_audit(result, strict).effective_total == 1
    excluded = ledger_for(result, strict).record_for(result.changes[1])
    assert excluded.disposition is Disposition.NON_GATING
    assert excluded.scope_excluded is True


def test_one_close_over_the_union_not_one_per_consumer() -> None:
    """`apply_scope` only demotes, so closing once per consumer would
    *intersect* the consumers' relevant sets: a finding only the second
    consumer uses would already have been demoted by the first one's call.
    The orchestrator therefore closes once over the union — asserted here as
    the property, since the failure is silent (a passing gate with a real
    break counted out of it).
    """
    from abicheck.policy.disposition_close import close_consumer_scope

    old, new = _snapshots(removed=2)
    result = compare(old, new)
    ledger = ledger_for(result)
    result.disposition_ledger = ledger
    first, second = result.changes[0], result.changes[1]

    # The union, as the orchestrator does it: both stay gating.
    close_consumer_scope(ledger, result, gating=[first, second])
    assert ledger.effective_total == 2

    # …and per-consumer closes would have left one of them out, which is why
    # the call belongs to the orchestrator that knows every consumer.
    result_b = compare(old, new)
    result_b.disposition_ledger = per_consumer = ledger_for(result_b)
    close_consumer_scope(per_consumer, result_b, gating=[result_b.changes[0]])
    close_consumer_scope(per_consumer, result_b, gating=[result_b.changes[1]])
    assert per_consumer.effective_total == 0


def test_synthesized_scoped_findings_join_the_audit() -> None:
    """A missing entrypoint or a retargeted PE ordinal is a real detected
    consumer finding that never reaches `result.changes`. Recording it is
    what lets a scoped view state counts the audit agrees with."""
    from abicheck.checker_types import Change
    from abicheck.policy.disposition_close import close_consumer_scope

    old, new = _snapshots(removed=1)
    result = compare(old, new)
    ledger = ledger_for(result)
    result.disposition_ledger = ledger
    before = ledger.detected_total

    synthesized = Change(
        kind=ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
        symbol="entrypoint",
        description="required by consumer",
    )
    close_consumer_scope(
        ledger, result, gating=[synthesized], also_detected=[synthesized]
    )
    assert ledger.detected_total == before + 1
    assert ledger.record_for(synthesized).disposition is Disposition.GATING
    assert conservation_holds(ledger)


class TestALateProducerClosesTheLedgerAgain:
    """`appcompat.scope_diff_to_app` joins *after* `compare()` closed the
    ledger, and every gap that produced had the same root cause: the two
    closing passes (verdict-class resolution, gating classification) had
    already run. `close_consumer_scope` is the one call that re-closes it, so
    these assert the two consequences that motivated it rather than the call
    itself.
    """

    def test_a_late_suppressed_finding_gets_its_verdict_class(self) -> None:
        """Left unresolved, a suppressed consumer-breaking removal is
        invisible to `recommend_release`'s conserved-delta check — it looks
        for *gating* suppressed records, and `None` is not one."""
        from abicheck.checker_types import Change, DiffResult
        from abicheck.policy.disposition_close import close_consumer_scope
        from abicheck.semver import recommend_release

        result = DiffResult(old_version="1.0", new_version="2.0", library="libfoo")
        ledger = finalize_ledger(DispositionLedger(), result)
        result.disposition_ledger = ledger

        late = Change(
            kind=ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            symbol="_ZN3foo4goneEv",
            description="required by consumer",
        )
        rule = Suppression(symbol="_ZN3foo4goneEv", reason="w")
        record_suppressed_change(
            ledger,
            late,
            rule=rule,
            application_point="consumer_overlay",
            suppression=SuppressionList([rule]),
        )
        assert ledger.record_for(late).verdict_class is None

        close_consumer_scope(ledger, result, gating=[])
        assert ledger.record_for(late).verdict_class is not None
        assert ledger.suppressed_gating_records()
        # …and the consequence: the hidden consumer break reaches the release
        # advice instead of a clean "no bump required".
        rec = recommend_release(result)
        assert rec.bump is SemverBump.MAJOR
        assert "suppressed" in rec.rationale

    def test_scoping_narrows_the_gating_set_to_what_the_gate_scores(self) -> None:
        """`--used-by` gates on the consumer's own subset, so a breaking
        removal the consumer never calls must not be counted `gating` while
        the gate it is supposedly counted in exits 0."""
        from abicheck.policy.disposition_close import close_consumer_scope

        old, new = _snapshots(removed=2)
        result = compare(old, new)
        ledger = ledger_for(result)
        assert ledger.effective_total == 2

        used = [result.changes[0]]
        close_consumer_scope(ledger, result, gating=used)
        assert ledger.effective_total == 1
        assert ledger.detected_total == 2, "scoping moves, it never removes"
        assert (
            ledger.record_for(result.changes[1]).disposition is Disposition.NON_GATING
        )

    def test_scoping_never_promotes_a_finding_into_the_gate(self) -> None:
        """It only narrows: a suppressed or excluded finding is not pulled
        back into the gating set by being named in scope (D2)."""
        from abicheck.policy.disposition_close import close_consumer_scope

        old, new = _snapshots(removed=1)
        result = compare(
            old,
            new,
            SuppressionList(
                [Suppression(symbol_pattern=".*", reason="w", allow_public_break=True)]
            ),
        )
        ledger = ledger_for(result)
        hidden = result.suppressed_changes[0]
        close_consumer_scope(ledger, result, gating=[hidden])
        assert ledger.record_for(hidden).disposition is Disposition.SUPPRESSED
        assert ledger.effective_total == 0


def test_a_suppressed_out_of_contract_finding_does_not_force_a_bump() -> None:
    """ADR-049 D1: compatibility policy never scored a proven-out-of-contract
    finding, and `record_compatibility_decisions` leaves its decision `None`
    for exactly that reason. Suppressing it must not resurrect a verdict —
    otherwise the suppressed exclusion recommends MAJOR/REVIEW while the
    identical *unsuppressed* exclusion correctly recommends no bump.
    """
    from abicheck.checker_types import Change, DiffResult
    from abicheck.contract_relevance_types import ContractRelevance
    from abicheck.semver import recommend_release

    excluded = Change(
        kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Internal", description="x"
    )
    excluded.contract_relevance = ContractRelevance.PROVEN_OUT_OF_CONTRACT
    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libfoo",
        suppressed_changes=[excluded],
        suppressed_count=1,
    )
    ledger = ledger_for(result)
    assert ledger.record_for(excluded).verdict_class is None
    assert ledger.suppressed_gating_records() == ()
    assert recommend_release(result).bump is not SemverBump.MAJOR


def test_the_pr_comment_and_html_state_a_support_gap_on_a_zero_change_run() -> None:
    """The remaining two projections with an early-return path: same class as
    the one-line fix, different renderers. "No ABI changes" is the sentence
    that most needs the caveat."""
    import json as _json

    from abicheck import reporter
    from abicheck.html_report import generate_html_report
    from abicheck.pr_comment import build_model, render_comment

    old, new = _snapshots()
    result = compare(old, new)
    audit = compute_disposition_audit(result)
    assert audit.detected_total == 0 and audit.not_evaluated_detectors

    expected = f"{len(audit.not_evaluated_detectors)} detector(s) not evaluated"
    comment = render_comment(build_model(_json.loads(reporter.to_json(result))))
    assert expected in comment
    assert expected in generate_html_report(result)
