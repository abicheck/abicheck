### Fixed

- **A merge of `origin/main`'s ADR-061 "report convergence" rewrite of
  `report_summary.build_summary` (PR #1193) would have silently reverted the
  `compatible_additions` schema-4.0 correction** (additions only, excluding
  `quality_issues`) back to the pre-4.0 whole-compatible-bucket total, since
  PR #1193 rewrote the function's return statement independently of that
  fix. Resolved by combining both changes: the envelope-reuse structure
  (`findings=` parameter) from `origin/main`, with the `compatible_additions`
  exclusion preserved on top of it. Both of `build_summary`'s computation
  paths (independently resolved, and reusing an envelope's already-resolved
  `findings`) are now covered by a dedicated regression test
  (`tests/test_report_summary.py`) verified to fail on the reverted formula
  and pass on the fix, so a future merge of either side cannot silently drop
  the other's correction again.
