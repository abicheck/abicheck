### Fixed

- `gate-mode: advisory` now neutralizes a `scan --against` report's *nested*
  contract-coverage contribution. The fields live under `diff` for a scan
  report, so zeroing only the document root left the aggregate — which reads
  the nested block — folding it back into the CI exit, and an advisory scan
  still gated. The neutralizer and the aggregate now share one block
  path definition (`aggregate.contract_coverage_block_paths`, which
  `contract_coverage_blocks` is the reader's own wrapper over) rather than two
  that agree until one changes.
- `scripts/measure_contract_shadow.py` no longer pins the accepted `public`
  unresolved-loss findings by corpus case name alone: that allowed one
  accepted gap to be fixed while a different finding *in the same case*
  regressed, leaving both the count and the case set unchanged. (The key was
  tightened once more after this — see the entry below on
  `report_finding_id`, which is the identity it ended on.)
