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
#: meaning -- the same policy ``REPORT_SCHEMA_VERSION`` follows. A third
#: case the policy did not name, and should: **moving a field into
#: ``required`` is a tightening, not an addition.** It does not change what
#: a producer emits, but it changes what *validates* -- a document that was
#: schema-valid without the field is rejected afterwards. On a published
#: version that is a MAJOR change, or grounds for leaving the field
#: optional; MINOR is not available for it.
#:
#: ``1.1`` makes both kinds of change, and they are not the same kind.
#: ``suppression_provenance`` on a suppressed finding (ADR-067 D3's full
#: rule record beside the existing display label) is genuinely additive,
#: which is what earns the MINOR bump: a consumer that selects or caches a
#: schema by this string could otherwise not tell the two contracts apart.
#: Moving ``old_acquisition_state`` into the root ``required`` list is the
#: tightening. An earlier revision of this comment called both "additive"
#: on the grounds that a ``1.0`` consumer reading known fields is
#: unaffected -- that is the *producer's* view, and a schema's job is
#: validation, where the change is strictly narrowing (Codex review, P2,
#: correcting an earlier reply of mine that made the same conflation).
#:
#: It is accepted here for one reason, checked rather than assumed: version
#: ``1.0`` was never released. This schema was introduced on 2026-09-10 in
#: ``10c4de15``, after the last release (0.5.0, 2026-07-16), with every
#: changelog fragment since still unreleased in ``changelog.d/`` -- so no
#: published build has ever emitted a ``1.0`` audit document, and there is
#: no such document anywhere to invalidate. ``required`` is also the
#: truthful model, since the field is emitted unconditionally; a schema
#: marking an always-present field optional describes the format less
#: accurately. Once a release ships an audit document, the rule above
#: applies with no such escape.
#:
#: ``1.2`` adds the top-level ``policy`` key (the resolved policy name that
#: classified this audit's findings, read off ``diff.policy``) -- purely
#: additive, a straightforward MINOR bump under the policy stated above
#: (Codex review, PR #1210, round 5: the field was entirely absent before,
#: so a JSON consumer had no way to tell which policy actually classified
#: a run's findings).
#:
#: ``1.3`` -- mirrors ``REPORT_SCHEMA_VERSION``'s ``4.1``/``SCAN_SCHEMA_
#: VERSION``'s ``1.33`` entries (Codex review, PR #1209): ``run_outcome``
#: nests the same ``AnalysisAssurance.to_dict()`` compare's report does, so
#: this always-present block also gains the additive ``schema_staleness_
#: status`` key.
#:
#: ``1.4`` -- two independent additive fields landed together, both MINOR
#: bumps under the policy stated above:
#:
#: * mirrors ``REPORT_SCHEMA_VERSION``'s ``4.2`` (Codex review, P2): an
#:   additive, top-level ``env_matrix_source_sha256`` key, present only
#:   when this audit's candidate was actually run under a declared
#:   ``deployment.runtime_floors``/``EnvironmentMatrix`` contract -- the
#:   identical digest a two-sided ``compare`` report of the same matrix
#:   carries under this same name. Absent (not ``null``) when the run
#:   declared none, matching the compare-side convention.
#: * adds the top-level ``disposition_audit`` block (ADR-067 C-S2). Closes
#:   the gap where an audit that suppressed every one of its findings still
#:   read, in an ``abicheck aggregate`` fan-in, as ``detected_total: 0``/
#:   ``suppressed: 0``: the rule-attributed ``suppressed`` list was already
#:   on this document, but nothing folded it into the one block every other
#:   report shape's own fan-in reads (Codex review, fresh evidence).
#: ``1.5``: the ``pattern_preprocessor_scan`` block gains the additive
#:   ``coverage`` object (per-check, per-side sufficiency) and each pattern
#:   side gains ``sufficient``/``inputs``, mirroring the compare report's own
#:   ``4.4``. Additive only here: an audit's candidate side is a live
#:   extraction, so it keeps its source-read licence and no existing value
#:   moves -- unlike a stored-snapshot ``compare``, which now honestly
#:   declines to re-derive (see ``buildsource/source_inputs.py``).
AUDIT_REPORT_SCHEMA_VERSION = "1.5"

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
    "audit_gate": "audit-gate finding",
    "contract_coverage": "contract coverage incomplete",
    "analysis_assurance": "analysis assurance incomplete",
    "evidence_contract": "evidence contract not met",
    "incomplete_scope": "comparison scope incomplete",
    "no_comparison_completed": "no audit completed",
}

NO_BASELINE_EXIT_AXIS_NOTICES: dict[str, str] = {
    "audit_gate": (
        "**Audit-gate finding** -- `--severity-preset` opted this audit into "
        "gating, and at least one candidate-side finding is classified "
        "`BREAKING`/`API_BREAK` (ADR-068 2026-09-10 amendment). This is "
        "orthogonal to the compatibility family: an audit never emits `2`/`4`."
    ),
    "contract_coverage": (
        "**Contract coverage incomplete** -- the selected `--contract` domain's "
        "required evidence was not fully available on this candidate "
        "(ADR-049 Phase 7)."
    ),
    "analysis_assurance": (
        "**Analysis assurance incomplete** -- the evidence behind this audit was "
        "not complete enough to be relied on, and `.abicheck.yml`'s "
        "`assurance.require_complete: true` makes that a failure rather than a "
        "note."
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

    **Equality is per-class, deliberately.** The generated ``__eq__``
    requires the same runtime class, so a ``SuppressedFinding`` never
    compares equal to a bare ``ReportFinding`` holding the same three
    fields, in either direction, and provenance participates in equality
    between two suppressed entries. A review asked for the former
    cross-class equality to be preserved (Codex, P2); it was measured
    rather than argued, and declined. ``field(compare=False)`` does not
    achieve it -- the class check blocks cross-class equality regardless --
    so the only route is a hand-written ``__eq__`` ignoring both the class
    and provenance. That works, and symmetry and transitivity do hold, but
    the price is that two suppressions under *different waiver rules*
    compare equal: provenance stops counting in equality at all. Dropping a
    recorded field on the way to a consumer is the exact defect this class
    exists to fix, and equality is a consumer. No caller compares these
    entries either -- every use of ``NoBaselineDocument.suppressed`` in this
    repository iterates or projects. Pinned by
    ``test_suppressed_finding_equality_is_per_class``.

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
    #: Typed as :class:`SuppressedFinding`, deliberately, even though
    #: :func:`suppression_provenance_of` lets every renderer tolerate a plain
    #: :class:`~abicheck.report.finding.ReportFinding` at runtime. Those two
    #: facts are not in conflict: the tolerance is defensive robustness for a
    #: hand-built document (an ``AttributeError`` from inside a renderer is a
    #: terrible failure), while the annotation states what this package
    #: *produces* and what a consumer may therefore rely on.
    #:
    #: Widening it to ``ReportFinding`` was considered and rejected on
    #: measurement, not taste (Codex review, P2): under that annotation mypy
    #: reports ``"ReportFinding" has no attribute "provenance"`` for the one
    #: consumer this whole feature exists to serve -- code reading a
    #: suppression's rule record off a computed document -- and breaks this
    #: package's own ``mypy abicheck/`` cleanliness at ``_suppressed_json``.
    #: A union does not help either: ``ReportFinding | SuppressedFinding``
    #: collapses to ``ReportFinding``, since the latter is a subclass. So the
    #: choice is binary, and it favours the real consumer over a hypothetical
    #: caller hand-constructing a fourteen-field document.
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
    #: The resolved policy name (``--policy``'s own value, or the
    #: ``"strict_abi"`` default) that classified every finding above --
    #: read straight off ``diff.policy`` (`DiffResult.policy`), the same
    #: attribute `_finding_resolver` already reads to build those findings,
    #: rather than re-derived or left for a renderer to guess (Codex
    #: review, PR #1210, round 5: the JSON projection previously carried no
    #: ``policy`` key at all, so a consumer building a ``CommentModel`` off
    #: it fell back to a hard-coded ``"strict_abi"`` default even when a
    #: non-default policy actually classified the run). ADR-049's own
    #: contract/pack-resolved policy name lives here unchanged -- this
    #: field states what *did* classify the findings, not what a caller
    #: asked for.
    policy: str = "strict_abi"
    #: The declared-deployment-floor contract's content digest (Codex
    #: review, P2), read straight off ``DiffResult.env_matrix_source_sha256``
    #: -- the same field ``run_no_baseline_compare`` stamps onto its
    #: ``DiffResult`` via ``dataclasses.replace`` for exactly this reason
    #: (see that function's own docstring). ``None`` when this audit's
    #: candidate declared no ``deployment.runtime_floors``/
    #: ``EnvironmentMatrix`` contract at all -- without this field, a
    #: candidate that stays within its declared floor (and so produces no
    #: finding) was indistinguishable, in every rendered report, from one
    #: run with no deployment contract in effect at all, even though the
    #: matrix genuinely governed this run.
    env_matrix_source_sha256: str | None = None
    #: ADR-067 C-S2's raw-versus-effective disposition ledger
    #: (``report.disposition_audit.compute_disposition_audit``), already
    #: serialized -- the same block every two-sided ``compare`` report
    #: carries at its root. Previously absent from this shape entirely: a
    #: ``--no-baseline`` audit that suppressed every one of its findings
    #: emitted a real, rule-attributed ``suppressed`` list of its own, but
    #: an aggregate fan-in reading a target's generic root
    #: ``disposition_audit`` block (``workflows.aggregate.disposition_axis.
    #: disposition_audit_block``) found none on this shape, and folded a
    #: clean-looking ``detected_total: 0``/``suppressed: 0`` in its place --
    #: losing the very rule provenance and count `vision.md`'s "Record
    #: before disposing" rule exists to keep visible (Codex review, fresh
    #: evidence). ``None`` only for a hand-constructed document (a test
    #: fixture) that never called :func:`compute_no_baseline_document`; the
    #: real compute half always attaches one, since ``result.diff`` is a
    #: genuine self-compared ``DiffResult`` carrying its own ledger.
    disposition_audit: Mapping[str, Any] | None = None
