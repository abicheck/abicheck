# ADR-067: Change-Intent Acknowledgment and the Policy-Disposition Audit

**Date:** 2026-09-05
**Status:** Accepted — **S1, S2 and S3 implemented**; S4 not implemented.
Design record for the vision's "intent and configuration accountability"
decisions (`vision.md`). The audit half (D1–D4) extends
ADR-013/024/044/049/063/064 and the report owners and is sequenced *first*,
without waiting for history (ADR-066); the acknowledgment half (D5–D7) is
the one genuinely new mechanism, landed by S3 (see below); S4's base/head
policy-delta and suppression-growth warnings remain unimplemented. What S1
landed, for the scalar single-pair `compare` path: one conserved
disposition ledger (`abicheck/policy/disposition_ledger.py`,
`abicheck/policy/disposition_close.py`) that all five suppression
application points record into
(`abicheck/post_processing.py`, `abicheck/checker.py`,
`abicheck/appcompat.py`); the raw-versus-effective counts and rule
provenance in every scalar projection — JSON, `--stat` JSON, one-line,
review digest, PR comment, SARIF, JUnit and HTML
(`abicheck/report/disposition_audit.py`, `abicheck/reporter.py`,
`abicheck/reporter_markdown.py`, `abicheck/report/render_text.py`,
`abicheck/pr_comment_render.py`, `abicheck/sarif.py`,
`abicheck/junit_report.py`, `abicheck/html_report.py`) under report schema
2.50; a
`not_evaluated` detector state (`abicheck/detector_registry.py`,
`abicheck/detectors.py`); and `abicheck/semver.py`'s release
recommendation reading the conserved delta instead of the
post-suppression change list — the one behavior change, carried with a
migration note. What S2 added on top: the release/bundle fan-out's JSON/
Markdown reports and `--output-dir` `summary.json` sidecar gain a folded
`disposition_audit` block over every library
(`abicheck/report/release_document.py`'s `release_disposition_audit_block`,
`abicheck/report/disposition_audit.py`'s `fold_disposition_audits`);
`abicheck aggregate --format json` gains the same folded block plus a
`disposition_audit_missing_targets` list (aggregate schema 1.9,
`abicheck/workflows/aggregate/disposition_axis.py`); reclassification is
now actually recorded through the ledger
(`DispositionLedger.resolve_reclassifications`, surfaced as
`reclassified_total`/`reclassifications`); an `out_of_contract`/
`unresolved_relevance` scope exclusion gains a `scope_reasons` breakdown by
contract-relevance reason code; and the consumer-scoped path
(`abicheck/appcompat.py`) reaches full parity with the other four
application points — both `scope_diff_to_app` (`--used-by`) and
`scope_diff_to_required_symbols` (`--required-symbol(s)`) now synthesize
the same suppressible, ledger-recorded `CONSUMER_REQUIRED_SYMBOL_REMOVED`
overlay for a missing symbol/entrypoint no diff `Change` names, sharing one
recording primitive
(`abicheck/policy/disposition_close.record_and_maybe_suppress_overlay`).
Report schema 3.0 -> 3.1. **One S2-adjacent gap remains, outside S2's own
scope:** `scan` reports carry no `disposition_audit` block at all (that
command has no scalar `compare`-shaped audit to fold from), so
`aggregate`'s own fold can only distinguish "no block present" (a
`scan`-sourced or pre-schema-2.51 target, listed in
`disposition_audit_missing_targets`) from "an audit exists" — `aggregate`
does not itself supply the missing block for `scan`.

**What S3 landed:** D5's acknowledgment records — a distinct disposition
from suppression (an acknowledgment is intentional acceptance; a
suppression is a claimed false positive or scope exclusion), sharing the
same `SelectorSet` grammar and YAML loader shape suppression already uses
(`abicheck/policy/acknowledgment.py`'s `Acknowledgment`/
`AcknowledgmentList`), but restricted to a *bounded* selector subset
(`finding_id`/exact `symbol`, optionally narrowed by `change_kind`, plus a
`component`/`baseline`/`candidate` release scope) — every suppression-only
broad selector (`symbol_pattern`, `namespace`, `source_location`, ...) is a
hard load error, and an ambiguous match across two loaded records is a hard
error too, never a silent nearest-match (both directly enforce D5 and
`vision.md`'s "a baseline refresh or a broad ignore rule is not an
acknowledgment" invariant). Acknowledgment is recorded through the
identical ledger primitive suppression already uses
(`DispositionLedger.resolve_acknowledgments`, `DispositionRecord.
acknowledged_by` — a D2 overlay attribute, exactly like `reclassified_by`,
never a seventh terminal disposition; the resolver is
`abicheck/policy/disposition_acknowledgment.py`'s `resolve_acknowledgments`,
called by the ledger close in `abicheck/policy/disposition_close.py`'s
`finalize_ledger`), surfaced in the `disposition_audit`
report block (schema 3.10: `acknowledged_total`/`acknowledgments`) the same
way reclassification already is. D6's additions review gate
(`abicheck/policy/acknowledgment_gate.py`) is a policy file `acknowledgment:`
block (`unacknowledged_additions: allow|warn|block`, default `allow` — no
existing run changes; `abicheck/policy_file_acknowledgment.py`), evaluated
as a fourth orthogonal `0`/`1` exit axis folded with `max()` beside contract
coverage and analysis assurance
(an `ExitDecision` axis since 2026-10: `exit.additions_review_contribution`,
reason `additions_review`, report schema 5.13) — it never
reclassifies an addition's `ChangeKind` or verdict, per D6's own "policy
acceptance, never reclassification" requirement. Record ids share B's
component/release-label scheme (`Acknowledgment.record_id`, built from the
same `component`/`baseline`/`candidate` strings
`workflows.history.HistoryEntry.version` already carries), without waiting
on B-S2's own versioning-policy resolution. **Engine-level wiring only in
this slice:** `checker.compare(acknowledgments=...)` (the typed Python API)
and the CLI's own exit-code fold (`frontends.cli.runtime.
_exit_with_severity_or_verdict`) both honor a supplied
`AcknowledgmentList`; no native `compare`/`scan` CLI flag to *load* an
acknowledgment document from a path exists yet — that front-end wiring is
left to a follow-up, the same staged "engine primitive, then CLI surface"
sequencing ADR-049's own contract-coverage axis used (Phase 5 landed the
ledger; Phase 7 wired the exit). **That follow-up landed (2026-10) for a
single-pair `compare`, then for the directory/package fan-out and the typed
API's `CompareRequest`:** `.abicheck.yml`'s `acknowledgment:` block names the
records `file` (loaded only from an explicit `--config`, the trust boundary
`contract.overlays` has) and `unacknowledged_additions`; a `--policy`
document's own `acknowledgment:` block outranks it (ADR-049 D7); the
Action publishes `ADDITIONS_UNACKNOWLEDGED`; the release fan-out folds each
member's contribution with `max`; `CompareRequest.acknowledgments_path`/
`acknowledgment_unacknowledged_additions` carry both settings. See
[Change acknowledgment](../../use/acknowledgments.md) for the user-facing
format and gate contract.

**Break gate, matching strictness, and the remaining surfaces (2026-10-20,
the ADR-067 P8 slice).** An acknowledged *break* now has an explicit gate
decision, the one D5 left to policy: `acknowledgment.breaks: gate|accept`
(`abicheck/model/acknowledgment_policy.py`, carried on `AcknowledgmentPolicy`,
resolved once by `workflows/acknowledgment_inputs.py`). `gate` (default) keeps
today's behaviour: the record is an overlay and the exit code is what the break
alone decides. `accept` records an acknowledged gating break as `non_gating`
under the `acknowledgment` application point
(`abicheck/policy/acknowledgment_breaks.py`) and drops it from the population
the compatibility exit is decided over; the verdict, the changes list and the
release recommendation are unchanged, and only that finding moves, so a sibling
break on the same run still gates. Matching is strict: a `symbol` record must
name a `change_kind` (`PolicyError` otherwise, G2); a record that matches more
than one change in a run is `AmbiguousAcknowledgmentError` (G3); an unscoped
record and an expired record each produce a warning, and an expired record
never matches. The surfaces are `compare --acknowledgments FILE` (overrides
`acknowledgment.file`) and the Action's `acknowledgments` input, both over the
same resolver, with `CompareRequest.acknowledgment_breaks` and
`checker.compare(acknowledgment_breaks=...)` for the typed API. Trust: a
discovered `.abicheck.yml` cannot supply `breaks`, and the Action's overlay
builder strips `acknowledgment.file` and `breaks` from a discovered document
(G5: a pull-request-planted records file had been reaching the explicit
`--config` overlay). Provenance on `pull_request` is the existing
`protect-committed-baseline` reusable check, documented in the user guide. The
capability reachability invariant (`tests/test_policy_capability_reachability.py`)
keeps each policy capability reachable from every front end that claims it.
Not in this slice: unused/expired acknowledgment rows in `disposition_audit`
(owned by the audit workstream), and the base/head policy delta (S4).

**Owner (2026-10, behavior-preserving refactor).** D1's "record before
disposing" is now owned by one module rather than by caller discipline:
`abicheck/policy/finding_disposition/` runs the disposition steps in their
default order (`dispose_findings`, `default_pipeline.py`), always with a
disposition ledger (one is created when the caller supplies none), and every
suppression application point named above shares one primitive,
`dispose_by_suppression`. Root-type redundancy and deduplication live in
`abicheck/compare/finding_redundancy.py`/`finding_dedup.py`, finding identity
in `abicheck/compare/finding_identity/`. References above to
`post_processing.ApplySuppression`, `checker._filter_suppressed_changes` and
`finding_identity.py` describe where those mechanisms lived when each slice
landed.

Implementation is sequenced in
[`plans/vision-api-abi-evolution.md`](../plans/vision-api-abi-evolution.md)
(workstream "Policy-disposition audit and change acknowledgment").
**Decision maker:** maintainer (product decision recorded in `vision.md`);
accepted as recorded above — the "technical sign-off pending" note in an
earlier draft of this line referred to review of this ADR document itself,
which review and merge have since completed (S1/S2 landing implies sign-off
occurred).

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
reclassification, scoping, and upstream-disabled detectors. **S3 landed:**
explicit acknowledgment records and the additions review gate; share
records with history (see "What S3 landed" above). S4: base/head
policy-delta analysis and suppression-growth warnings.

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

## Addendum (2026-10-20): configured-rule inventory and the unused-rules gate

*Amends D3 and adds a gate. The decisions above are not edited in place.*

**Problem.** Every audit aggregate was a projection of the disposition
*records*, so a rule that disposed of no finding was absent from
`disposition_audit`: a zero-match suppression, a `reclassify:` rule that
matched nothing, an acknowledgment that covered nothing. A rule whose selectors
matched a finding an earlier rule had already claimed was also absent. "Record
before disposing" held for findings but not for the rules configured to act on
them.

**Rule id (D3).** A rule's `rule_id` is its optional `id:` when the document
states one, and otherwise its canonical rule identity
(`abicheck/policy/rule_identity.py`). `label` is a grouping tag and `reason` is
prose: both are reported as their own fields and are never the identifier.
`id:` is accepted in both the suppression and the `reclassify:` grammars, must
be a non-empty string, and must be unique within its document; a repeat is a
`PolicyError`. `id:` never affects matching. Two byte-identical entries are two
rules, and each row carries `entry_index`, its zero-based position in the file.
A `reclassify:` rule's identity no longer embeds an enum repr (its `to_verdict`
is covered by `to`).

**Configured rules are recorded (D3's "may" becomes "must" for configured
rules).** Every configured suppression, reclassify and acknowledgment rule has
exactly one audit row, zero-count rows included, in the JSON
`disposition_audit.rules`, `reclassifications` and `acknowledgments`
(`abicheck/policy/rule_inventory.py`, `abicheck/policy/disposition_rules.py`).
The rows are the one source for the human views, the `suppression_audit`
stale list and the gate, so they cannot disagree. Human views collapse
zero-count rows to one line; the JSON keeps every row.

**Counting.** `matched_count` keeps its meaning: findings the rule *disposed
of*. For a suppression, first-match credit. For a reclassify rule, the
verdict-changing decider, which the JSON also names `changed_count`. For an
acknowledgment, the findings it covered. `shadowed_count` is every other finding
the rule's selectors matched: one an earlier rule claimed, or a no-op
reclassification. `selector_matched_count` is their sum.

**Status (per rule, 2026-10-21).** Each row carries one of: `active`
(disposed of at least one finding); `shadowed_only` (matched, disposed of
none); `expired` (past its `expires` date, with nothing disposed of); `unused`
(matched nothing, and at least one detector that can produce one of the rule's
change kinds ran); `not_evaluated` (matched nothing, and none of those detectors
ran). The check runs in that order. A rule's candidate detectors are those that
can produce its `kind`/`change_kind` selector; a rule with no kind selector can
match any kind, so every detector that produces a kind is a candidate. A kind
that no registered detector produces leaves its rule `not_evaluated`. This keeps
D3's "absent is not removed": a rule is never `unused` unless a check that could
have matched it ran. Acknowledgment rows take the same statuses; ambiguous-match
handling is unchanged.

**Which detectors can produce a rule's kind (resolved 2026-10-21).** The status
above was first decided globally: any refused detector made every unmatched
rule `not_evaluated`, so `unused` was reported only when every detector ran.
Detectors do not declare their kinds in the registry, so the mapping is derived
once, from the source, into `abicheck/policy/detector_kind_map.py`
(`scripts/gen_detector_kind_map.py`). For each registered detector it lists the
`ChangeKind` values its function reaches through first-party calls and
references, and it lists the kinds no detector reaches. `tests/test_detector_kind_map.py`
fails on drift, on a missing detector or kind, and on any kind a detector emits
on the controlled mutation catalogue that its entry does not credit. The map is
an over-approximation where the walk cannot resolve a call, so it errs toward
`unused`. A refused detector no longer affects a rule it cannot have checked.
The ELF-only comparison that prompted this now reads a stale symbol-kind rule
as `unused`, and a DWARF-only kind as `not_evaluated`.

**The `unused_rules` gate (D6's pattern).** A top-level policy key
`unused_rules: allow|warn|block` (default `allow`, so no existing run changes)
and no command-line flag (the former `--fail-on-unused-rules` was removed; the
policy key is the only setting). `block` contributes `1` when at least one
configured rule has status `unused`. It is a fourth `0`/`1` exit axis folded with `max`
(`ExitDecision.unused_rules_contribution`, reason `unused_rules`), so it never
lowers a `2`, `4`, `7`, `8` or `16`, and it never changes what was observed
(`abicheck/policy/unused_rules_gate.py`). Only the `unused` status triggers it.
A scalar `compare` evaluates its own rules. For a directory or package release
the gate is evaluated over the union of members: a member could evaluate a rule
when its status is anything but `not_evaluated`. A rule is unused for the release
only when every member that could evaluate it left it unused, and at least one
could. A member that could not evaluate it is ignored. The release audit row
folds with the same rule (`fold_rule_status`), so the gate and the row agree. The
strictest member mode applies.

**Schema.** Report schema 6.3 (`abicheck/schemas/__init__.py`): the row fields
above, `disposition_audit.unused_rules_review` and
`exit.unused_rules_contribution`. `suppression_audit.stale_rules` is now a
projection of the same inventory and lists rule ids. Two changes are not purely
additive and are in the changelog: a configured rule with no finding now appears
in `rules`, `reclassifications` and `acknowledgments`, so a consumer that assumed
non-empty rows must tolerate zero-count rows; and a reclassify rule's `rule_id`
is its `id:` or canonical identity instead of its label or reason.

**Reclassification's per-finding display label is unchanged.** The
`reclassified_by` field on a finding keeps its label-or-reason-or-verdict value.
The audit keys reclassify rows on the configured rule, not on that display
string.

**Still open.** A kind that a pipeline step (not a registered detector) emits,
such as `inline_body_changed`, reads `not_evaluated` even when that step ran,
because no detector run can be credited with it. Recording pipeline steps as
producers would need the same per-step evaluation record, and is not decided
here. The row's optional list of candidate detectors that did not run is not
added to the JSON report. The acknowledgment `breaks: gate|accept` policy is
owned by its own change and is not decided here. *(Partly superseded by
addendum 2 below.)*

## Addendum 2 (2026-10-21): observed kinds, and `not_evaluated` as a warning

*Amends the per-rule status and the `unused_rules` gate above.*

**Investigation.** Real gcc-built C comparisons were run ELF-only,
ELF+headers, ELF+DWARF and ELF+DWARF+headers, and every rule shape was
classified against the detectors that ran. No detector that ran was missing
from `DETECTOR_KINDS`; the map was not too narrow for detectors. The gaps
were elsewhere. (1) The kinds no registered detector emits (`NON_DETECTOR_KINDS`,
about 130 of 417, among them `public_surface_shrank`, emitted on every run
with a removal) could never read `unused`: a stale rule on them was always
`not_evaluated`, so `block` never fired for them. (2) Kinds only the DWARF
detector emits (`struct_size_changed`, `struct_field_type_changed`) are
correctly `not_evaluated` without DWARF, but nothing told the user so: under
`warn`/`block` such a rule was silent.

**Observed kinds are proof of evaluation.** A zero-match rule is also `unused`
when the run *recorded* a change of one of its kinds, in any disposition
(`changes`, `suppressed_changes`, `redundant_changes`,
`out_of_surface_changes`, `reconciled_changes`;
`policy/disposition_rules.observed_kinds`). The producer of that kind
demonstrably ran, and the rule was checked against real findings of its kind
and matched none. This is sound for detector and pipeline kinds alike and
needs no per-step evaluation record. A pipeline kind that the run did *not*
record still reads `not_evaluated`: a step that ran and found nothing is
still indistinguishable from one that did not run, which stays open.

**`missing_evidence`.** Every `not_evaluated` row carries `missing_evidence`
(JSON rows and the release fold): the candidate detectors that did not run,
each with its evidence gate's `coverage_gap`, or, for a pipeline kind, that no
registered detector produces it and its producer recorded no change of it.

**`not_evaluated` is a warning, not a gate (minimal design).** Under
`unused_rules: warn` and `block`, `unused_rules_review.not_evaluated` lists
every `not_evaluated` rule with its `missing_evidence`, and the Markdown audit
renders it as a warning. Under `allow` (the default) nothing changes. It never
contributes to `gate_contribution`: missing evidence narrows a conclusion and
never manufactures a failure (ADR-063), and a user who needs the evidence
present already has `require-complete-analysis` / the analysis-assurance
gate. A separate `unused_rules_not_evaluated: block` opt-in was considered and
not added: it would add a policy key, a digest field and a release-fold rule
for a case the assurance gate already owns. It stays a maintainer decision.

**Schema.** Additive inside `disposition_audit` (both objects allow
additional properties): `missing_evidence` on `not_evaluated` rows and
`unused_rules_review.not_evaluated` when the mode is not `allow`. No exit code
changes: a rule that moves from `not_evaluated` to `unused` under `block` can
newly raise a clean `0` to `1`, which is the gate's documented meaning.
