### Added

- **ADR-067 workstream C, slice S2**: the disposition ledger's
  raw-versus-effective reconciliation (C-S1) now extends beyond scalar
  `compare`. The release/bundle fan-out's JSON summary (`compare-release`'s
  primary report, its `--output-dir` `summary.json` sidecar, and its
  Markdown report) gains a top-level `disposition_audit` block folding every
  library's own per-comparison audit
  (`report.disposition_audit.fold_disposition_audits`); each library entry
  already carries the identical scalar-`compare` shape. `abicheck aggregate
  --format json` gains the same folded block across every analyzed/
  unexpected target that reports one (aggregate schema 1.9), plus a
  `disposition_audit_missing_targets` list naming a target whose report
  predates report schema 2.51 or came from `scan` (which carries no such
  block).
- Reclassification is now actually recorded through the ledger: a
  `reclassify:` policy rule that moves a finding's verdict class is recorded
  as an overlay attribute (`DispositionLedger.resolve_reclassifications`),
  surfaced as `disposition_audit.reclassified_total`/`.reclassifications` --
  independent of the finding's own terminal disposition, the same way a
  suppression rule's provenance already was. A contract/scope exclusion
  (`out_of_contract`/`unresolved_relevance`) similarly gains a
  `scope_reasons` breakdown by contract-relevance reason code, the scope
  counterpart of the existing suppression `rules` breakdown.

### Changed

- Report schema `3.0` -> `3.1` (additive): `disposition_audit` gains
  `reclassified_total`/`reclassifications`/`scope_reasons`.
- Aggregate schema `1.8` -> `1.9` (additive): top-level `disposition_audit`/
  `disposition_audit_missing_targets`, and a per-target `disposition_audit`
  field.
