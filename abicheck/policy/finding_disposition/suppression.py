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

"""Suppression as a disposition: the one place a user rule withholds a finding.

Every suppression application point -- the pipeline's own
``ApplySuppression`` step, late detector findings merged after it
(:func:`~abicheck.policy.finding_disposition.pipeline.merge_late_findings`),
and ``checker.compare()``'s separately produced findings (SONAME advisories,
platform floors, surface metrics, pattern-verdict synthetics) -- calls
:func:`dispose_by_suppression`. Before this module each of those five sites
carried its own copy of the same loop: evaluate the rule set, stamp
``Change.suppression_rule``, record the match into the ADR-067 disposition
ledger, park the change, and build the ADR-044 D4 diagnostics for a rule
whose selectors matched but whose reachability gate withheld it. A missed
step in one copy was a real bug class (ADR-044's P1 item 6, ADR-052's
missed ``suppression_rule`` stamp): the copies drifted.

What the interface guarantees, so callers need not:

* **Record before disposing (ADR-067 D1).** A suppressed finding is recorded
  in the ledger, with its rule and application point, before it leaves the
  visible set. A ``None`` ledger is a caller that did not opt into the audit
  and is skipped, never faked.
* **Nothing silently vanishes (ADR-044 D4).** A withheld rule always yields
  its ``SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`` /
  ``SUPPRESSION_REACHABILITY_UNKNOWN`` diagnostic, returned separately so a
  caller that deduplicates findings can leave diagnostics alone. A suppressed
  change whose reachability graph coverage never established yields a
  ``GRAPH_COVERAGE_INSUFFICIENT_FOR_SUPPRESSION`` diagnostic alongside it, so
  the report names the rule that hid it without coverage.
* **Order is preserved.** ``kept`` and ``suppressed`` keep input order.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..disposition_ledger import record_suppressed_change

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...model.change import Change
    from ..disposition_ledger import DispositionLedger
    from ..suppression.rules import Suppression, SuppressionList
    from .run_state import PipelineContext


@dataclass(frozen=True)
class SuppressionDisposition:
    """What one suppression pass decided for a batch of findings."""

    #: Findings no rule suppressed, in input order.
    kept: list[Change] = field(default_factory=list)
    #: Findings a rule suppressed, in input order, each stamped with
    #: ``suppression_rule`` and already recorded in the ledger.
    suppressed: list[Change] = field(default_factory=list)
    #: Diagnostics about the suppression itself: ADR-044 D4 withheld-rule
    #: diagnostics, and ``GRAPH_COVERAGE_INSUFFICIENT_FOR_SUPPRESSION`` for a
    #: suppressed change that graph coverage never established as unreachable.
    diagnostics: list[Change] = field(default_factory=list)

    @property
    def visible(self) -> list[Change]:
        """The findings that stay in the report: kept, then diagnostics."""
        return [*self.kept, *self.diagnostics]


def dispose_by_suppression(
    changes: Iterable[Change],
    suppression: SuppressionList | None,
    *,
    ledger: DispositionLedger | None,
    application_point: str,
) -> SuppressionDisposition:
    """Apply *suppression* to *changes*, recording every match in *ledger*.

    *application_point* names the caller in the audit trail (the disposition
    record's ``application_point``), so two sites applying the same rule stay
    distinguishable. With no *suppression*, every change is kept.
    """
    if suppression is None:
        return SuppressionDisposition(kept=list(changes))
    kept: list[Change] = []
    suppressed: list[Change] = []
    diagnostics: list[Change] = []
    # One coverage record per rule, not per change: keyed by rule identity,
    # in first-match order, so a broad rule hiding 50 changes reads as one.
    uncovered: dict[int, tuple[Suppression, list[Change]]] = {}
    for change in changes:
        outcome = suppression.evaluate(change)
        if outcome.suppressed:
            change.suppression_rule = outcome.rule_label()
            record_suppressed_change(
                ledger,
                change,
                rule=outcome.matched_rule,
                application_point=application_point,
                suppression=suppression,
            )
            suppressed.append(change)
            rule = outcome.matched_rule
            if rule is not None and rule.suppresses_without_graph_coverage(change):
                uncovered.setdefault(id(rule), (rule, []))[1].append(change)
            continue
        kept.append(change)
        if outcome.withheld_rule is not None:
            diagnostics.append(overreach_diagnostic(change, outcome.withheld_rule))
        if outcome.withheld_unknown_rule is not None:
            diagnostics.append(
                _unknown_reachability_diagnostic(change, outcome.withheld_unknown_rule)
            )
    diagnostics.extend(
        _graph_coverage_diagnostic(rule, hidden) for rule, hidden in uncovered.values()
    )
    return SuppressionDisposition(
        kept=kept, suppressed=suppressed, diagnostics=diagnostics
    )


class ApplySuppression:
    """Apply user-provided suppression rules.

    ADR-044 D2: a rule matches only when ``Suppression.matches()`` also passes
    its reachability/``allow_public_break`` gate — see that method for the
    semantics. A match refused by that gate is recorded as a
    ``SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`` diagnostic (ADR-044 D4) instead of
    being silently dropped, so the change stays visible *and* the suppression
    author sees why their rule did not apply.
    """

    name = "apply_suppression"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        if ctx.suppression is None:
            return changes
        outcome = dispose_by_suppression(
            changes,
            ctx.suppression,
            ledger=ctx.disposition_ledger,
            application_point=self.name,
        )
        ctx.suppressed.extend(outcome.suppressed)
        return outcome.visible


def overreach_diagnostic(change: Change, rule: Suppression) -> Change:
    """Build the ``SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`` diagnostic for *change*.

    ADR-044 D4. *rule* is the suppression whose selectors matched *change* but
    whose reachability/``allow_public_break`` gate withheld the match. Public
    because the consumer-overlay path
    (``disposition_close.record_and_maybe_suppress_overlay``) evaluates one
    synthesized finding at a time and builds the same diagnostic.
    """
    from ...model.change import Change
    from ...model.change_catalog.kinds import ChangeKind

    # would_withhold() only ever returns True for a *broad* rule (namespace/
    # entity_namespace/cause_namespace/source_location, no primary narrow
    # selector — see Suppression._is_broad_selector): a rule with symbol/
    # symbol_pattern/type_pattern set has _is_broad_selector=False, so
    # _passes_public_break_gate short-circuits True and would_withhold can
    # never fire for it. The selector fallback below only needs the three
    # broad-shaped fields; entity_namespace is the canonical spelling of
    # namespace (self-review finding: it was missing here even though the
    # equivalent SuppressionAudit string-building was already fixed).
    selector = (
        rule.namespace
        or rule.entity_namespace
        or rule.cause_namespace
        or rule.source_location
        or "?"
    )
    proof = (
        f" via {change.reachability_proof_path}"
        if change.reachability_proof_path
        else ""
    )
    return Change(
        kind=ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK,
        symbol=change.symbol,
        description=(
            f"Suppression rule {selector!r} matched {change.symbol!r} "
            f"({change.kind.value}) but was not applied: the symbol is "
            f"public-reachable{proof}. Add `allow_public_break: true` to this "
            "rule to suppress it anyway."
        ),
        caused_by_type=change.symbol,
    )


def _unknown_reachability_diagnostic(change: Change, rule: Suppression) -> Change:
    """Build the ``SUPPRESSION_REACHABILITY_UNKNOWN`` diagnostic for *change*.

    impact-analysis-layer P0 slice. *rule* is the suppression whose selectors
    matched *change*, whose resolved ``reachability`` is
    ``"proven-unreachable-only"``, but whose graph coverage could not prove
    *change* unreachable (``Change.reachability_state`` is ``UNKNOWN``).
    """
    from ...model.change import Change
    from ...model.change_catalog.kinds import ChangeKind

    selector = _rule_selector(rule)
    return Change(
        kind=ChangeKind.SUPPRESSION_REACHABILITY_UNKNOWN,
        symbol=change.symbol,
        description=(
            f"Suppression rule {selector!r} matched {change.symbol!r} "
            f"({change.kind.value}) but was not applied: graph coverage was "
            "insufficient to prove the change unreachable from the public ABI "
            "surface (reachability: proven-unreachable-only). Add "
            "`allow_unknown_reachability: true` to this rule to suppress it "
            "anyway once you have manually confirmed it is safe."
        ),
        caused_by_type=change.symbol,
    )


def _rule_selector(rule: Suppression) -> str:
    """The human-facing selector a diagnostic quotes for *rule*."""
    return (
        rule.symbol
        or rule.symbol_pattern
        or rule.type_pattern
        or rule.namespace
        or rule.entity_namespace
        or rule.cause_namespace
        # Canonical (backend-independent) identity selector (Codex review,
        # fresh evidence, PR #753): mirrors the identical fix in
        # cli_compare_fold.py's _suppression_rule_label -- a finding_id-only
        # rule with no other selector previously rendered as the bare
        # fallback "?" here, indistinguishable from every other unlabeled
        # rule's diagnostic.
        or rule.finding_id
        or rule.source_location
        or "?"
    )


def _graph_coverage_diagnostic(rule: Suppression, hidden: list[Change]) -> Change:
    """Build the per-rule ``GRAPH_COVERAGE_INSUFFICIENT_FOR_SUPPRESSION`` record.

    impact-analysis-layer P0 follow-up (the ``unreachable-only`` decision).
    *rule* hid every change in *hidden* through its default ``unreachable-only``
    gate while ``Change.reachability_state`` was ``UNKNOWN``. The record is
    informational: it carries the count and the suppressed change ids, and
    never changes a verdict or exit code.
    """
    from ...compare.finding_identity import report_canonical_finding_id
    from ...model.change import Change
    from ...model.change_catalog.kinds import ChangeKind

    ids = ", ".join(report_canonical_finding_id(c) for c in hidden)
    return Change(
        kind=ChangeKind.GRAPH_COVERAGE_INSUFFICIENT_FOR_SUPPRESSION,
        symbol=_rule_selector(rule),
        description=(
            f"{len(hidden)} suppressed by rule {_rule_selector(rule)!r} without "
            "graph coverage: their reachability was never established (unknown, "
            "not proven unreachable), and reachability: unreachable-only treats "
            "that as not public. Suppressed change ids: "
            f"{ids}. Add `reachability: proven-unreachable-only` to this rule to "
            "withhold matches until graph coverage proves them unreachable."
        ),
    )
