### Added

- **Scalar policy-disposition audit (ADR-067 C-S1).** A single-pair `compare`
  now records every atomically detected change together with the one terminal
  disposition it received — `gating`, `non_gating`, `suppressed`,
  `out_of_contract`, `unresolved_relevance` or `deduplicated` — in a conserved
  ledger (`abicheck.policy.disposition_ledger`) that all five suppression
  application points route through. Every report projection carries the
  resulting raw-versus-effective counts: the JSON report gains an additive
  top-level `disposition_audit` block, `--stat`'s one-line summary gains an
  `[audit: N detected, M gating, …]` suffix (including a "N detector(s) not
  evaluated" count, which on a zero-change run is the only place that
  assurance gap is stated), the review digest and sticky PR
  comment gain a disposition table/row, SARIF gains a run-level
  `properties.dispositionAudit`, JUnit gains testsuite-level
  `abicheck.detected_total`/`abicheck.effective_total`/
  `abicheck.disposition.*` properties, the HTML change-summary table gains a
  raw-versus-effective footer row, and every Markdown mode (default, leaf,
  root-cause, review digest) gains a "Disposition audit" section. A fully suppressed comparison can no
  longer read as "no changes". `gating` means the gate the run was actually
  scored on: with a `--severity-preset` (or a config `severity:` block) in
  effect, the split follows `severity.gate_contribution_for_change` — the same
  per-finding function the exit code folds — so a promoted addition reads
  `gating` and a demoted break reads `non_gating`.
- **Per-rule suppression provenance survives a merge.** `SuppressionList`
  now tracks each rule's originating document, so the ABICC front end's
  merged rule set (a `--suppress` file plus rules synthesized from `-skip-*`
  options) still reports the real source file for a file-backed rule instead
  of `null`.
- **Rule provenance in the suppression ledger.** Each
  `suppression.suppressed_changes[]` entry now records *which* rule hid the
  finding — its selector identity, the `--suppress` document's path, reason,
  label and expiry — instead of computing that and dropping it. Report schema
  bumped to `2.50` (additive only).
- **`not_evaluated` detector state.** A detector whose support gate refused it
  is now recorded as `not_evaluated` with the gate's reason rather than as
  `changes_count: 0`, so "did not run" and "ran, found nothing" are no longer
  the same report. The `dwarf` detector's own "neither side has debug info"
  early return moved onto that gate.

### Fixed

- **`recommend_release` no longer reports "no version bump required" for a
  suppressed break.** It read the *post*-suppression change list, so a rule
  with `allow_public_break: true` covering a removed public symbol silently
  degraded the release advice. It now reads the conserved disposition ledger:
  a suppressed major-class finding is reported as `major` with state `review`
  and a rationale naming the rule, the finding kinds, and
  `intent: unspecified`. **Migration note:** a run whose breaks are entirely
  suppressed previously received `bump: none` / `soname_action:
  no_bump_needed`; it now receives `bump: major` / `soname_action:
  not_determined` / `state: review`. Automation keyed on the old value should
  either drop the suppression rule or record an explicit acknowledgment once
  ADR-067 D5's `intent:` field lands. No verdict, gate decision or exit code
  changes.
