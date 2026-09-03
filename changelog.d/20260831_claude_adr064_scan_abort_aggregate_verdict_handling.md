### Fixed

- **`aggregate` still treated a `scan` abort report as an unavailable/
  verdictless target**, even after the abort JSON envelope shape was fixed
  (PR review finding, following up on the fix above). `workflows/aggregate/
  load._load_report_file` only calls `GateInfo.from_scan_report` after
  `parse_report_verdict` succeeds, and neither `"BUDGET_OVERFLOW"` nor
  `"EVIDENCE_CONTRACT_ERROR"` is a `Verdict` enum member — so a saved
  `scan --format json` abort report still read as "report carried no ABI
  verdict" under a warn/optional/discovered-target policy that could
  silently let it pass, instead of the real failure it is. `_load_report_file`
  now recognizes both abort verdicts before the generic verdict-parsing
  branch and forces a blocking gate (exit 5/1,
  `blocking_categories=("budget_overflow",)`/`("evidence_contract_error",)`),
  the same treatment a compare-release operational `"ERROR"` verdict already
  gets. Verified end-to-end: a real `scan --format json` abort report fed to
  the real `aggregate_reports_dir` now blocks a required target instead of
  reading as unavailable.
