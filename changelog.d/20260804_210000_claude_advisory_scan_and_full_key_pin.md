### Fixed

- `gate-mode: advisory` now neutralizes a `scan --against` report's *nested*
  contract-coverage contribution. The fields live under `diff` for a scan
  report, so zeroing only the document root left the aggregate — which reads
  the nested block — folding it back into the CI exit, and an advisory scan
  still gated. The neutralizer and the aggregate now share one block
  traversal (`aggregate.contract_coverage_blocks`) rather than keeping two
  that agree until one changes.
- `scripts/measure_contract_shadow.py` pins the accepted `public`
  unresolved-loss findings by their full `case:mode:kind:symbol` key. Pinning
  only the case name still allowed one accepted gap to be fixed while a
  different finding *in the same case* regressed, leaving both the count and
  the case set unchanged.
