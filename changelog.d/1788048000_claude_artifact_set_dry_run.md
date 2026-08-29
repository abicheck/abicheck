### Added

- **CLI cleanup phase two, PR 5**: `scan --artifact-set --dry-run` now
  previews the run -- resolved member list, shared header/build/source
  inputs, tool/frontend status, and a genuinely per-member-scaled cost
  projection -- instead of being hard-rejected. The projection sums an
  independent `estimate_scan()` result per discovered member rather than
  reusing the shared estimator's single-request shape (whose L1-L5 rows
  don't scale with the number of binaries), so it reflects the real
  per-member work `run_scan_set` performs.
