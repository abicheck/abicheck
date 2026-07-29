### Fixed

- **`scan --against`'s suppression ledger now attributes each silenced
  finding to the `--suppress` rule that matched it** — `diff.suppressed[]`
  entries gained a `suppression_rule` field (`Change.suppression_rule`),
  matching `compare`'s own suppression audit trail
  (`reporter._suppressed_change_entry`'s
  `impact_assessment.decision.suppression_rule`). Without it, a suppression
  file with multiple overlapping rules gave no way to tell which rule
  silenced a given finding via `scan --against` (Codex review, PR #657).
