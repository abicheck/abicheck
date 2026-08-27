### Changed

- **ADR-061 adoption ledger**: `architecture/debt.yaml` baselines are tightened
  again for 13 files whose recorded baseline had drifted from their actual
  size since the last ratchet (`74cee4d`) — 145 lines of already-won ground
  (84 across the 12 production files, plus 61 on the one test file) across
  `checker_types.py`, `cli_compare_helpers.py`, `cli_compare_release.py`,
  `cli_resolve.py`, `cli_scan_baseline.py`, `diff_filtering.py`,
  `diff_platform.py`, `diff_types.py`, `html_report.py`, `junit_report.py`,
  `reporter_markdown.py`, `serialization.py`, and
  `tests/test_bugfix_test_contract.py`. The `no_growth` rule caps a file at
  `max(baseline, PR base)`, so as with the earlier ratchet, a file that had
  shrunk since its baseline was measured was still carrying a standing
  licence to grow back to the old, larger figure. `python
  scripts/check_architecture.py` stays at 0 errors before and after — this is
  pure ledger tightening, no production code moved.
