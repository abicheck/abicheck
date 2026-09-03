### Fixed

- **A `scan` abort report's forced `aggregate` gate leaked scan's own raw
  exit code instead of the aggregate's normalized one** (PR review finding,
  immediately following the aggregate-verdict-handling fix above).
  `workflows/aggregate/load._load_report_file`'s new forced-blocking branch
  for `"BUDGET_OVERFLOW"`/`"EVIDENCE_CONTRACT_ERROR"` hardcoded scan's own
  private exit code (5 for budget overflow) into the constructed `GateInfo`,
  bypassing `GateInfo.from_scan_report`'s existing normalization — every
  scan exit outside `{0, 2, 4}` folds to `1`
  (`COVERAGE_INCOMPLETE_EXIT`), and the aggregate's own published contract
  has no exit 5. A saved `BUDGET_OVERFLOW` abort therefore made `aggregate`
  return an undocumented `5`, while the equivalent legacy scan payload
  already correctly returned `1`. Both abort verdicts' forced gate now uses
  `COVERAGE_INCOMPLETE_EXIT`, matching the existing normalization rule; the
  target still blocks, and the `budget_overflow`/`evidence_contract_error`
  categories are unchanged.
