### Added

- **Suppressed findings now survive reporting instead of leaving zero
  trace.** `scan --against --format text` prints an always-present
  `suppressed=N` count in the baseline-comparison summary line, and the new
  `--show-suppressed` flag itemizes each one (kind/symbol/location/rule) the
  same way gating findings are itemized; `--format json`'s existing
  `diff.suppressed[]` entries now also carry `pre_suppression_bucket` (the
  verdict bucket the finding would have counted as had it not been
  suppressed). SARIF output (`compare --format sarif`) now emits every
  suppressed finding as its own result, marked via the standard SARIF
  `suppressions` array (kind `external`, with the `--suppress` rule as the
  justification) instead of only a bare `properties.suppressedCount`
  integer. The sticky PR comment now reports how many findings were
  suppressed by `--suppress` and how many were reclassified by a
  `--policy-file` override, so a fully-suppressed diff no longer reads as
  "no ABI changes at all".

