### Added

- **`compare --no-baseline`'s audit document now carries a `disposition_audit`
  block (schema 1.4)**, at the same root key every two-sided `compare`
  report already carries it under. The audit's own `suppressed_findings`
  list (with full rule provenance) was already real, but nothing fed the
  generic root-level `disposition_audit` block `abicheck aggregate`'s
  fan-in reads for every report shape — so a project audit that suppressed
  every one of its findings still folded into the aggregate as
  `detected_total: 0`/`suppressed: 0`, losing the rule provenance and
  counts the source report actually recorded.
