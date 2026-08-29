### Added

- **CLI cleanup phase two, PR 5**: `scan --artifact-set --dry-run` now
  previews the run -- resolved member list, shared header/build/source
  inputs, tool/frontend status, and a genuinely per-member-scaled cost
  projection -- instead of being hard-rejected. The projection sums an
  independent `estimate_scan()` result per discovered member rather than
  reusing the shared estimator's single-request shape (whose L1-L5 rows
  don't scale with the number of binaries), so it reflects the real
  per-member work `run_scan_set` performs. The projection also honors a
  caller's `--risk-rules` when resolving the risk-driven depth (rather than
  silently falling back to the shipped defaults) and preserves each
  estimate's own caveats (e.g. an `--build-target` unscoped-TU-count
  warning) instead of dropping them for bare totals, and now also prices
  the one cross-library bundle-audit pass `run_scan_set` performs over the
  whole set rather than excluding it from the projected total. The
  risk-driven depth is resolved through the same function the real
  per-member scan uses (`service_scan._resolve_member_scan_level`), not an
  independent copy of that precedence. The composite GitHub Action's
  `new-library-set` + `dry-run`/`estimate: true` inputs now reach this
  preview too -- `validate-inputs.sh`'s preflight rejection (written while
  the CLI itself still rejected the combination) is removed.
