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

Suppression already has one application point
(`abicheck/checker.py`, `_filter_suppressed_changes`), one selector
grammar shared with reclassification (`abicheck/policy/selectors.py`,
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
- `allow_public_break` stays; its documented meaning becomes "acknowledged
  break, still reported and still counted", which is a behavior change to
  the release recommendation for runs that use it (today: silently "no
  bump"). Sequenced with a migration note.
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
