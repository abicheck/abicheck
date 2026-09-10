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

"""The ``compare --no-baseline`` audit document, and what it may be rendered as.

A leaf holding only the *contract* the audit's compute half
(:mod:`abicheck.report.no_baseline`) produces and its render halves consume
-- the same shape this package's own ``document.py`` has relative to its
``render_*`` siblings.

It exists as a third module rather than living beside the compute half
because the machine-format renderers moved out to
:mod:`abicheck.report.no_baseline_render` when that file crossed the
800-line cap, and a renderer needs the document's type. Importing it back
from ``no_baseline`` formed a real cycle
(``no_baseline -> no_baseline_render -> no_baseline``) that the
``import-cycle-growth`` gate rejects -- correctly, since the dependency
direction is what tells a reader which half owns the shape. Both halves now
depend on this leaf, and nothing depends on both of them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .finding import ReportFinding

if TYPE_CHECKING:
    from .cross_source_evolution import CrossSourceEvolutionSummary

__all__ = [
    "AUDIT_REPORT_SCHEMA_VERSION",
    "NO_BASELINE_EXIT_AXIS_LABELS",
    "NO_BASELINE_EXIT_AXIS_NOTICES",
    "NO_BASELINE_REPORT_SCHEMA_VERSION",
    "NO_BASELINE_SUPPORTED_FORMATS",
    "NO_BASELINE_UNSUPPORTED_FORMATS",
    "NoBaselineDocument",
    "SuppressedFinding",
    "suppression_provenance_of",
    "suppression_rule_label",
]


#: Independent of ``reporter.REPORT_SCHEMA_VERSION`` (the two-sided report's
#: own schema) -- this report carries a different shape (no ``old_version``,
#: no ``verdict``, no addition/removal summary), so it gets its own counter
#: rather than borrowing one that promises a shape this report doesn't have.
#:
#: ``2.0``: the audit's cross-source/candidate-side findings reach the
#: document (``findings``, ``suppressed_findings``, ``suppressed_count``,
#: ``cross_source_evolution``, ``pattern_preprocessor_scan``). ``1.0``
#: always emitted ``"changes": []``, which was the gap, not the schema's
#: intent. ``suppressed_findings`` landed in the same unreleased ``2.0``
#: rather than as a ``2.1``: a suppressed finding disappearing entirely was
#: a defect in this shape, not a later addition to a shipped one, and

#: The audit report's **own** schema version, in its **own** namespace.
#:
#: Emitted as ``audit_report_schema_version``, not ``report_schema_version``.
#: That distinction is the whole point: the packaged ``compare_report.schema.
#: json`` tells consumers to "accept any version with the same MAJOR
#: component", so stamping an audit into that field offered a *different
#: document* under the compare report's identity. Verified against the real
#: schema, not inferred: an audit validates with two errors -- a null
#: ``verdict`` there means ADR-050 D2's "the comparability gate rejected this
#: pair" and so requires ``reason``, which an audit has no business claiming,
#: and ``no_baseline`` is not in the enum its ``selection`` field allows
#: (Codex review, P1).
#:
#: Starts at ``1.0``: this is a new schema's first published version. It was
#: briefly numbered ``2.0``/``2.1`` while it shared the compare report's
#: field, which implied a version history in a namespace it never had.
#:
#: Bump MINOR for an additive field, MAJOR for a removal or a changed
#: meaning -- the same policy ``REPORT_SCHEMA_VERSION`` follows.
#:
#: ``1.1`` adds ``suppression_provenance`` to a suppressed finding (ADR-067
#: D3's full rule record beside the existing display label) and moves
#: ``old_acquisition_state`` into the root ``required`` list, where it
#: always belonged -- it is emitted unconditionally. Both are additive: a
#: ``1.0`` consumer reading the fields it already knows is unaffected, which
#: is exactly what MINOR promises. Bumped because a consumer that selects or
#: caches a schema by this string could otherwise not tell the two contracts
#: apart (Codex review, P2).
AUDIT_REPORT_SCHEMA_VERSION = "1.1"

#: Deprecated alias kept for one release so an in-flight import does not
#: break; it names the same string. Prefer the name above.
NO_BASELINE_REPORT_SCHEMA_VERSION = AUDIT_REPORT_SCHEMA_VERSION


#: Formats a ``--no-baseline`` audit renders one-sided.
#:
#: ``json``/``markdown`` are the audit's own native shapes. ``sarif`` and
#: ``junit`` were added once the audit had a real finding set to carry:
#: both are *findings* formats with no verdict slot to leave empty (a SARIF
#: run is a list of results with rule ids and levels; a JUnit suite is a
#: list of test cases), which is exactly what a single-build audit
#: produces, and both are how a CI job consumes one -- code scanning upload
#: and test-report annotation respectively. ``oneline`` is the "just tell
#: me" flow and needs one sentence.
NO_BASELINE_SUPPORTED_FORMATS = frozenset(
    {"json", "markdown", "sarif", "junit", "oneline"}
)

#: Formats that stay a declared usage error, and why (ADR-068 D2 ruling,
#: 2026-09-09 -- recorded here rather than left undated in a plan).
#:
#: Both are *narrative* renderings built around a compatibility comparison,
#: not projections of a finding list:
#:
#: * ``html`` -- the HTML report's whole information architecture is a
#:   verdict badge, an OLD -> NEW version headline, and
#:   addition/removal/modification tables. A single-build audit has no
#:   verdict (D2 forbids one), no OLD version, and no additions or
#:   removals, so a one-sided HTML page is a *new page design* rather than
#:   a projection of the document below -- genuinely different work from
#:   the four formats above, none of which needed a layout decision.
#: * ``review`` -- a compact GitHub-facing digest whose content is
#:   literally "what changed between these two releases, and should you
#:   ship it": verdict, counts by direction, release recommendation,
#:   manual-review banner. With no baseline there is no change to review
#:   and no release to recommend; the honest digest is ``oneline``, which
#:   is supported.
#:
#: Tracked in ``docs/contribute/plans/one-comparison-product.md`` (Phase 2e
#: follow-up) and ``docs/contribute/known-gaps.md``. Not a silent omission:
#: the CLI's usage error names this ruling.
NO_BASELINE_UNSUPPORTED_FORMATS = frozenset({"html", "review"})


#: The same axes, as short phrases for the one-line view. Kept beside the
#: long notices above and keyed identically, so an axis cannot be explained
#: in one projection and silently dropped by the other -- which is exactly
#: what happened: the Markdown fix left `oneline` still printing a bare
#: `[exit 7]` with no word about the missed evidence contract (Codex review,
#: P2). :func:`render_no_baseline_oneline` asserts the two tables agree.
NO_BASELINE_EXIT_AXIS_LABELS: dict[str, str] = {
    "contract_coverage": "contract coverage incomplete",
    "analysis_assurance": "analysis assurance incomplete",
    "evidence_contract": "evidence contract not met",
    "incomplete_scope": "comparison scope incomplete",
    "no_comparison_completed": "no audit completed",
}

NO_BASELINE_EXIT_AXIS_NOTICES: dict[str, str] = {
    "contract_coverage": (
        "**Contract coverage incomplete** -- the selected `--contract` domain's "
        "required evidence was not fully available on this candidate "
        "(ADR-049 Phase 7)."
    ),
    "analysis_assurance": (
        "**Analysis assurance incomplete** -- the evidence behind this audit was "
        "not complete enough to be relied on, and `--require-complete-analysis` "
        "makes that a failure rather than a note."
    ),
    "evidence_contract": (
        "**Evidence contract not met** -- a pinned `--depth build`/`--depth "
        "source` requested evidence this run did not reach; it did not silently "
        "degrade to shallower evidence (ADR-064)."
    ),
    "incomplete_scope": (
        "**Comparison scope incomplete** -- a selected, expected member never "
        "reached a completed audit (ADR-065 D6/D7)."
    ),
    "no_comparison_completed": (
        "**No audit completed** -- this run examined nothing, which never reads "
        "as a clean pass (ADR-065)."
    ),
}


def suppression_rule_label(
    change: Any, provenance: Mapping[str, Any] | None
) -> str | None:
    """The suppressing rule's *label*, or ``None`` when it stated none.

    The one owner of this question, because getting it wrong is easy and has
    now been gotten wrong three times in this package alone.
    ``Change.suppression_rule`` is ``SuppressionOutcome.rule_label()``'s
    ``label or reason`` collapse -- a single string that does not say which
    of the two it holds -- so reading it as a label is a coin flip. When the
    run recorded provenance, the real ``label`` is knowable and the collapsed
    field must not be consulted at all; only a run with no ledger entry falls
    back to it, and there nothing better is knowable.

    Every projection that shows a label separately from a reason routes
    through here (the Markdown table's "Suppressed by" column, and
    ``no_baseline_render._suppression_justification`` for SARIF and JUnit),
    so a fourth call site cannot quietly form its own opinion. The failure
    this prevents is a reason printed twice, once under a heading claiming
    it is a rule label (Codex review, P2, twice).
    """
    if provenance:
        label = provenance.get("label")
        return str(label) if label else None
    collapsed = getattr(change, "suppression_rule", None)
    return str(collapsed) if collapsed else None


def suppression_provenance_of(
    entry: ReportFinding,
) -> Mapping[str, Any] | None:
    """The suppressing rule's record for *entry*, or ``None`` when it has none.

    The one place any projection asks. ``NoBaselineDocument.suppressed`` is
    typed as :class:`SuppressedFinding`, but the document is an ordinary
    frozen dataclass a caller can build or ``dataclasses.replace`` by hand,
    and a caller written against the pre-pairing shape passes plain
    :class:`~abicheck.report.finding.ReportFinding` entries -- which made
    every detailed renderer raise ``AttributeError`` on ``entry.provenance``
    (Codex review, P2).

    ``None`` for such an entry is the *truthful* answer, not a papered-over
    one: a document carrying plain findings genuinely holds no ledger
    record, so "no provenance recorded" is what it has to say. That is the
    same distinction the renderers already draw for a run whose
    ``DiffResult`` kept no ledger -- and the opposite of fabricating a
    record, which is what this file's other rules forbid.
    """
    return getattr(entry, "provenance", None)


@dataclass(frozen=True, slots=True)
class SuppressedFinding(ReportFinding):
    """One suppressed finding and the rule that actually hid it.

    **A** :class:`~abicheck.report.finding.ReportFinding`, not a wrapper
    around one: a suppressed entry *is* a finding, and the rule that hid it
    is one more resolved fact about it. So ``entry.change``/``.verdict``/
    ``.category`` work exactly as they do on any other finding, and
    ``isinstance(entry, ReportFinding)`` holds -- which is what keeps every
    consumer of ``NoBaselineDocument.suppressed`` working unchanged, rather
    than trading an `AttributeError` for a provenance field (Codex review,
    P2). A first version paired the two side by side and did force that
    change on callers.

    *provenance* is the already-serialized
    :class:`~abicheck.policy.disposition_ledger.RuleProvenance` -- ADR-067
    D3's full record (rule id, source file, reason, label, expiry), not a
    display label. The audit carried ``Change.suppression_rule`` instead,
    which is ``SuppressionOutcome.rule_label()``'s deliberate ``label or
    reason`` collapse, so a rule stating both lost its reason and its source
    file in every projection (Codex review, P1).

    Resolved through the run's own disposition ledger by object identity --
    the same ``rule_for`` join ``reporter.py``'s two-sided suppression block
    uses -- never by re-evaluating the rule set, which could name a
    different rule than the one that fired. ``None`` when the run kept no
    ledger entry for this finding (a ``DiffResult`` rebuilt from JSON keeps
    none); inventing a row from the display label would look like a real
    ADR-067 record while carrying strictly less.
    """

    provenance: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class NoBaselineDocument:
    """The one frozen audit document every format projects.

    Holds resolved values only -- no ``DiffResult``, no live policy
    objects -- so a renderer cannot re-derive a verdict or reach past what
    the compute half decided.

    **What "frozen" does and does not buy** (CodeRabbit review). ``frozen=
    True`` and the tuple-typed collections stop a renderer from rebinding a
    field or reordering a finding list. They do not deep-freeze the
    ``Change`` each :class:`~abicheck.report.finding.ReportFinding` carries,
    which is an ordinary mutable dataclass -- the same one every other
    ``compute_*``/``render_*`` pair in this package hands to its renderers.
    Deep-copying it here would fork that shared shape for one command and
    silently double a large report's allocation, so the barrier this class
    actually enforces is *structural* (no verdict, no policy object, nothing
    to re-derive from) rather than a memory-level guarantee.
    """

    library: str
    new_version: str
    #: OLD's acquisition state, always ``"declared_absent"`` today.
    old_acquisition_state: str
    evidence_tiers: tuple[str, ...]
    #: One entry per candidate-side finding, in emission order.
    findings: tuple[ReportFinding, ...]
    #: The findings a ``--suppress`` rule matched, same resolution, kept
    #: alongside rather than dropped: ``vision.md``'s "Record before
    #: disposing" rule requires "detected, then suppressed by rule X" to
    #: stay visible on a passing run, never to read as "nothing found".
    #:
    #: Each carries its own rule provenance rather than the document holding
    #: a second, positionally-aligned tuple: a pairing invariant a renderer
    #: has to honor is one a renderer can break, and the finding and the
    #: rule that hid it are one fact.
    suppressed: tuple[SuppressedFinding, ...]
    evolution: CrossSourceEvolutionSummary | None
    pattern_preprocessor_scan: dict[str, Any] | None
    run_outcome: dict[str, Any]
    comparison_scope: dict[str, Any]
    #: ADR-049 Phase 7's orthogonal coverage contribution, and the total
    #: exit code this run reports. Both resolved compute-side so no
    #: renderer computes an exit code of its own.
    coverage_exit_contribution: int
    exit_code: int
    #: ADR-049 Phase 5's unsuppressible sibling ledger, already serialized.
    #: The contribution above is a *number*; this is what a reader has to act
    #: on -- which provider, on which side, fell short and why. Carrying only
    #: the number meant even ``--format json`` exited 1 with no way to tell
    #: (Codex review, P2). ``()`` when a domain closed cleanly is a real
    #: answer and distinct from "no contract was selected"; the projections
    #: keep that distinction by emitting ``[]`` only under a contract.
    coverage_failures: tuple[Mapping[str, Any], ...] = ()
    #: Whether this run selected a ``--contract`` domain at all. The ledger's
    #: own emptiness cannot answer that -- a closed domain and no domain both
    #: produce no failures -- and the two must not read the same to a
    #: consumer.
    contract_selected: bool = False
    #: Every orthogonal axis's own contribution, keyed as in
    #: ``no_baseline.NO_BASELINE_EXIT_AXIS_NOTICES`` and resolved from the
    #: same function ``exit_code`` above is folded from. Carried so a
    #: renderer can *explain* a nonzero exit instead of only reporting one:
    #: the Markdown projection stated the coverage axis alone, so a missed
    #: evidence contract exited 7 beside a report that said nothing about
    #: it (Codex review, P2). Default ``()``-equivalent empty mapping keeps
    #: a hand-constructed document (tests) valid.
    exit_axes: Mapping[str, int] = field(default_factory=dict)
