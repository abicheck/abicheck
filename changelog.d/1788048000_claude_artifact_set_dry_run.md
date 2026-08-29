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
  the CLI itself still rejected the combination) is removed, and
  `new-library-set` splitting now also handles a pure newline-separated
  (no comma) YAML block-scalar value, not just the comma-joined form. The
  preview also blocks (via `DryRunResult.block`, exit code 1 -- not a plain
  warning line) when a pinned `--depth` actually needs source/build
  evidence (`collect_mode != "off"`, the same guard the real evidence-
  contract check itself short-circuits on) and no `--sources`/
  `--build-info`/`--build-config` was given at all, matching the real run's
  `EVIDENCE_CONTRACT_ERROR` (exit 1) instead of previewing a run that would
  never actually execute; a shallow pinned depth like `binary`/`headers`
  never trips this, since the real run doesn't reject it either. It also
  rejects an ambiguous duplicate-`DT_SONAME` set the same way `run_scan_set`
  does, rather than previewing a request that was always going to be
  rejected. The Action's single-directory `new-library-set` form (no comma)
  also trims a trailing newline now, matching the multi-member form.
