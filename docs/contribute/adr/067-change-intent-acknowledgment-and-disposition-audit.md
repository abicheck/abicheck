# ADR-067: Change-Intent Acknowledgment and the Policy-Disposition Audit

**Date:** 2026-09-05
**Status:** Proposed — not implemented. Design record for the vision's
"intent and configuration accountability" decisions (`vision.md`). The
audit half (D1–D4) extends ADR-013/024/044/049/063/064 and the report
owners and is sequenced *first*, without waiting for history (ADR-066);
the acknowledgment half (D5–D7) is the one genuinely new mechanism. No
code, schema, or default changes with this document. Implementation is
sequenced in
[`plans/vision-api-abi-evolution.md`](../plans/vision-api-abi-evolution.md)
(workstream "Policy-disposition audit and change acknowledgment").
**Decision maker:** maintainer (product decision recorded in `vision.md`);
technical sign-off pending review of this document.

## Context

Suppression has one selector grammar but **four application points** —
`post_processing.ApplySuppression.apply()` over the main change list,
`checker._filter_suppressed_changes()` and `checker._filter_pattern_synthetic()`
over separately produced changes, and `appcompat.py`'s consumer-overlay
pass — sharing one selector grammar with reclassification (`abicheck/policy/selectors.py`,
`SelectorSet`, ADR-063 Phase 9), per-rule `reason`/`label`/`expires`/
`reachability`/`allow_public_break`/`finding_id` fields, an in-memory
ledger (`DiffResult.suppressed_changes`), and a `SuppressionAudit` of
stale, expired, and high-risk rules. Reclassification stamps
`Change.reclassified_by`; out-of-surface changes have their own ledger
under the JSON `scope` block (ADR-024); redundancy/dedup has
`redundant_count`; contract coverage failures are structurally
unsuppressible (ADR-049 Phase 5). The effective configuration digest
records the policy and suppression content hashes.

What is missing is the *audit contract* on top of these mechanisms:

- The JSON suppression ledger drops the rule that matched and its reason
  (`reporter._suppressed_change_entry` emits kind/symbol/description
  only), so "which rule hid this and why" is computed and then lost.
- Reclassified and reconciled changes have no disposition-keyed ledger;
  the one-line format and the review digest carry no suppression totals;
  the PR comment's suppression note is a trailing blockquote.
- There is no first-class *acknowledged* state. The nearest is
  `allow_public_break`, after which the break disappears from `changes`
  and the release recommendation silently degrades to "no bump needed"
  with no trace that a major-worthy break was accepted.
- A disabled detector is recorded as `enabled=True, changes_count=0`
  (`DetectorRegistry.run_all` does not distinguish "ran, empty" from
  "did not run"), so the headline reads "0 changes" where it should read
  "not evaluated".
- Neither finding id is a sound acknowledgment key on its own:
  `report_finding_id` folds raw `source_location`/`description` (not
  backend-stable), and `report_canonical_finding_id` is backend-stable
  but explicitly not unique per finding.
- The suppression file's path is not recorded (only a content hash), and
  nothing compares a base revision's policy with a head revision's.

## Decision

### D1 — Immutable normalized changes precede every disposition

The observed change set — each change with its evidence, selected scope,
and contract relevance — is recorded once, before suppression,
reclassification, scope exclusion, acknowledgment, deduplication, or
display filtering acts on it, and is never mutated by any of them. Every
downstream consumer that needs raw inputs (bundle-internal detectors,
consumer impact, history) reads this set, so a suppression can never
starve a sibling or consumer finding by deleting its input (this
generalizes G38 Phase 14's fix for public-surface scoping).

**Clarification (2026-09): the observation and its evaluated relevance are
two objects, not one immutable record.** A documentation review found a
real tension between this decision's own wording ("the observed change
set... with its evidence, selected scope, **and contract relevance**... is
recorded once... and never mutated") and D4's requirement to show how a
*different* resolved policy/suppression context changes dispositions over
the **same** detected facts (a PR's base-vs-head policy diff) — contract
relevance is itself the output of evaluating a detected fact against a
*particular* resolved contract mode (ADR-049's `CompatibilityEvaluationConfig`),
so if it were baked into the one immutable record, evaluating a second
resolved context (D4's base/head comparison, or ADR-066's later
re-evaluation of old history under a revised policy) would have nowhere to
put its answer without violating "never mutated." The correct model,
already implicit in ADR-049's own replay/re-evaluation design
(`contract_replay.py`'s `replay_original_decisions()`/
`reevaluate_from_evidence()`): an **immutable observation** (the detected
change, its evidence, and its selected scope — never contract relevance)
plus, separately, an **immutable evaluation result for one resolved
context** (contract relevance, compatibility decision, gate contribution —
everything this decision's own "contract relevance" phrase was describing).
Re-evaluating under a different resolved context (a different policy
revision, a different contract mode, a later ADR-066 history re-read)
produces **another** evaluation-result object keyed by its own context, and
never mutates the observation or rewrites an earlier evaluation-result's own
receipt — the same "replay vs. reevaluate, never in place" split ADR-049
Phase 4 already established for its own persisted context. D1/D2's
"immutable" language throughout this ADR should be read with this split
already applied.

### D2 — One change, one terminal disposition, many matches

Each atomic detected change carries exactly one terminal **effective gate
disposition** for counting: `gating`, `non_gating` (evaluated by policy,
contributes nothing to the gate), `suppressed`, `out_of_contract`,
`unresolved_relevance`, or `deduplicated`. Two *transformations* are
recorded as independent attributes on the same change, never as
alternatives to that disposition: `reclassified` (from → to, with the
rule) and `acknowledged` (with the record). A change reclassified from
`COMPATIBLE` to `BREAKING` is `reclassified` **and** `gating`; the reverse
is `reclassified` and `non_gating`; an acknowledged break is `acknowledged`
and whatever the policy makes its gate contribution (D5). Disposition
counts therefore sum to the detected total, while reclassified and
acknowledged counts are reported as overlays on that total, not added to
it. The change may additionally record every rule that matched, the
winning rule and why it won (precedence), and every consumer it affects.
Presentation is a separate, per-view attribute — `shown`, `collapsed`, or
`filtered` by `--show-only`, a report mode, or truncation — recorded
alongside the disposition, never in place of it: a change that is
`suppressed` and then omitted from a filtered view keeps its `suppressed`
disposition and still counts in that view's suppression total (which is
what D3 requires). Derived impact findings and grouped presentation rows
never inflate the raw totals. Totals reconcile across scalar, bundle,
aggregate, and compact reports, and across every presentation state — an
executable invariant.

These stay distinct and are named as such in the audit: technical
compatibility (the verdict class); policy acceptance (the gate); an
acknowledgment of intent; a suppression as a claimed false positive or a
waiver (the rule's declared kind); proven out-of-contract; unresolved
relevance; reclassification; deduplication/root-cause grouping;
display-only filtering. An acknowledgment is not proof of compatibility,
and a suppression is not proof the tool was wrong.

**Clarification (2026-09): counting units stay separate across
aggregation.** D2's "disposition counts sum to the detected total" invariant
needs one further precision a documentation review found this decision does
not yet state explicitly: a *logical* change can be observed more than once
along more than one independent axis — e.g. the same underlying signature
change detected in 3 compiler profiles of a declared build matrix, and
separately shown (via ADR-057's consumer graph / `--use-cases` attribution)
to affect 2 named consumers. That single scenario must never collapse
ambiguously into "1 change," "3 changes," or "6 changes" reported
interchangeably depending on which view happens to be read. Instead, four
counting domains stay separate, each internally consistent and reconciling
only within itself (D2's reconciliation invariant applies *per domain*, not
across domains): the **logical finding** count (one, keyed by canonical
identity — ADR-045/049's `finding_identity` — regardless of how many
profiles or consumers observed it); the **profile-specific observation**
count (three, one per build-matrix member that actually detected it,
per ADR-065's per-member acquisition/selection model); the **affected
consumer** count (two, from the consumer/use-case join, orthogonal to how
many profiles detected the underlying change); and the **presentation
group** count (however many rows a report's own grouping/root-cause
collapsing renders it as — D2's own "derived impact findings and grouped
presentation rows never inflate the raw totals" already governs this one).
A report may show any of these four numbers, but must always label *which*
one it is showing, and must never sum across domains (a report claiming
"6 changes" by multiplying profiles by consumers states nothing that answers
a real question).

**Clarification (2026-09): technical classification survives policy
reclassification as a separately exposed value.** D2 already *records*
`reclassified` as a from→to attribute distinct from the terminal
disposition; this clarification states the report-facing consequence
explicitly, since a documentation review found no requirement anywhere in
this ADR that a rendered report actually *show* the distinction rather than
only retain it internally. A report must be able to expose, for any
reclassified change, all three of: the **original technical
classification** (the raw detector `ChangeKind`'s `default_verdict` — what
the underlying binary/source fact *is*, unaffected by any policy), the
**effective classification** under the resolved policy (D2's `reclassified`
`to` value, or the unreclassified original when no rule matched), and the
**gate contribution** (D5's acknowledgment-adjusted, D2's disposition-driven
number that actually reaches the exit code) — as three separately labeled
values, never collapsed into one "severity" field that silently reports
only the effective or only the gate-facing number. A reader auditing "did
policy hide a real technical break" needs the first value available next to
the third; a report that only ever shows the effective/gate value cannot
answer that question even when the underlying data (D2's `reclassified`
attribute) already has the answer.

### D3 — The raw-versus-effective summary is unsuppressible and survives every view

Every report projection, including the one-line, review-digest, PR-comment,
SARIF/JUnit, and aggregate views, carries: the detected total, the
effective (gating) total, the per-disposition counts with rule provenance
(rule id, source file, reason, expiry), and the analysis coverage. A view
may collapse the detail; it cannot omit these counts. An ordinary
per-finding suppression cannot suppress its own audit record or a coverage
failure (the ADR-049 structural pattern, reused). The audit-warning
severity may be configured separately, and that setting is itself recorded
in the effective configuration.

When configuration disabled extraction or a detector *before* detection,
the audit reports the disabled scope or capability and the loss of
assurance — a `not_evaluated` detector state, never a fabricated
suppressed-change count and never "0 changes".

### D4 — Policy provenance and widening are visible

The report records the resolved policy and suppression sources (paths and
content hashes, extending the existing effective-config digest). Where a
trustworthy base and head revision of the policy are available (a PR), the
audit shows how the widened rules changed dispositions over the **same**
detected facts. Suspicious suppression volume or rule expansion is
reported against configurable thresholds with the denominator stated; a
rule is never asserted erroneous merely because it matches many changes;
with no prior audit available the report says so rather than inventing
growth.

### D5 — Acknowledgments are explicit, bounded, and reviewable

An acknowledgment is a record — in the suppression file's own YAML
(reusing `SelectorSet`, never a second grammar) or a sibling file the same
loader owns — with: the exact finding(s) or transition, or a tightly
constrained set; a component/variant/domain scope; a baseline/candidate
pair or release range; a reason; an optional expiry and reference. It is
matched by the canonical finding identity plus the entity/transition
discriminators needed to make that match unique for the run; an
ambiguous or unknown identity requires review and is never resolved to the
nearest old acknowledgment. A broad regex is a suppression, not an
acknowledgment.

An acknowledged change keeps its verdict class, stays in the report and in
the release recommendation ("a known, accepted break" is still a MAJOR-
class break), and contributes to the gate according to policy — so a
project can accept an intentional break without the report pretending it
is compatible.

### D6 — Unacknowledged public additions are a configurable review gate

A project may configure `allow`, `warn`, or `block` for public additions
that carry no acknowledgment. The default is `allow`, so no existing run
changes. This folds through the existing gate/exit precedence (ADR-064)
as policy acceptance, never as a reclassification of the addition into a
break.

### D7 — A baseline refresh is not an acknowledgment

Updating a baseline is storage activity. It does not acknowledge the delta
it absorbs. A project that opts into a reviewed-baseline protocol scopes
the approval to exact content digests and retains the prior delta and its
audit trail; the plan owns that protocol's shape.

## Consequences

- The suppression JSON ledger gains rule provenance (additive schema
  bump); reclassified/reconciled changes gain a disposition ledger; the
  compact views gain the raw-versus-effective row. Golden reports change
  additively.
- `DetectorRegistry` learns a `not_evaluated` result distinct from an empty
  one; headline counts stop reading disabled detectors as zero.
- `allow_public_break` stays **a suppression control**, not an
  acknowledgment: it is precisely the gate that lets a *broad*
  namespace/source-location rule suppress a public-reachable break
  (`Suppression._passes_public_break_gate`), and D5 says a broad rule is a
  suppression. Its disposition is `suppressed`, counted and rule-attributed
  in the audit like any other suppression, with the supplied `reason`
  preserved verbatim. The flag itself only bypasses the public-break gate;
  it does **not** say whether the author meant an intentional waiver or a
  claimed detector false positive, so the audit never infers `waiver` from
  it. A new optional `intent:` field on a suppression rule (`waiver` |
  `false_positive`) records that distinction explicitly; a rule without it
  is reported as `intent: unspecified`, which is the migration default for
  every existing rule and changes nothing about matching. The release
  recommendation reads the audit rather than the post-suppression
  `changes` list, so a suppressed major-class break is surfaced as
  "suppressed (intent: waiver / false_positive / unspecified), not
  compatible" instead of today's silent "no bump" — a behavior change for
  runs using the flag, sequenced with a migration note. A project that wants a bounded *acknowledgment*
  writes an acknowledgment record (D5); an existing narrow
  `allow_public_break` rule that already names one exact finding may be
  migrated to one explicitly by the S3 migration, never implicitly.
- The finding-identity workstream (ADR-063 Phase 2B) gains a concrete
  consumer: an acknowledgment key that must be unique per run and stable
  across backends.

## Relationship to existing decisions

Extends ADR-013 (suppression), ADR-024 (surface traceability), ADR-044
(reachability-aware suppression), ADR-049 (contract relevance, coverage
ledger, config precedence), ADR-063 Phase 9 (one selector grammar), ADR-064
(gate/exit precedence), ADR-036/061 (report document). Integrates with
ADR-066 through shared record ids. Introduces no second selector grammar,
no second gate algorithm, and no new report framework.

## Implementation slices

S0: inspect ledgers and ordering; agree the counting/identity/outcome
contract; amend the owning ADRs' status notes. S1: conserve changes and
expose raw-versus-effective counts with rule provenance on the native
`compare` path, including a 100-suppressed-removals fixture. S2: carry the
audit through bundle/consumer/aggregate and every projection; cover
reclassification, scoping, and upstream-disabled detectors. S3: explicit
acknowledgment records and the additions review gate; share records with
history. S4: base/head policy-delta analysis and suppression-growth
warnings.

## Tests (contract)

100 removals plus a wildcard waiver (counts and rule visible on a passing
run); all additions acknowledged versus unacknowledged under each of
`allow/warn/block`; expired and stale acknowledgments; overlapping rules
with a stated winner; a finding reappearing in a later unrelated
transition; source-path and backend spelling changes that must not break
the acknowledgment key; scope exclusion with unknown reachability;
disabled detection with no raw facts (reads `not_evaluated`); a suppression
that would starve a sibling or consumer finding; report truncation
preserving the audit row; a policy widened in a PR; a baseline overwritten;
report/CLI/API/Action parity. Properties: policy and view changes never
mutate the raw fact/change set; audit counts conserve normalized changes
and never count matched rules as separate removals.
