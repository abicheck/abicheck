<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`--contract-evaluation`/`--contract` now work on a directory/package
  `compare`** (CLI-audit P1, release/package contract parity): the
  per-library release fan-out previously rejected both flags outright
  ("not supported for directory/package comparisons yet"). It now threads
  them straight into each library pair's own `service.run_compare()` call —
  the identical Tier-2 chokepoint a single-pair `compare` uses — so a
  library compared through a release/package fan-out gets the same ADR-049
  contract decision it would from comparing it individually. Each
  library's own `--output-dir` report already carries the full per-finding
  contract shape for free (it's the same `to_json()` a single-pair compare
  produces). ADR-049 Phase 7's orthogonal contract-coverage floor is
  `max()`-aggregated across every library into the release's own exit
  code, surfaced in the release JSON summary as
  `contract_coverage_exit_contribution` — the same field name and fold
  rule (raises a clean `0` to `1`, never lowers a real `2`/`4`) a
  single-pair `compare` already uses. `--fail-on-removed-library`'s exit
  `8` is checked ahead of this coverage-only fallback, so a removed
  library's own signal is never masked by an unrelated coverage gap.
  `--pack` remains rejected
  for directory/package comparisons — applying a pack's policy/contract/
  gate overrides per library still needs its own resolve-once-apply-
  per-pair design.
