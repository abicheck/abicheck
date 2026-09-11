### Fixed

- **`abicheck aggregate`'s `compare --no-baseline` loader now validates the
  whole `run_outcome` envelope, not just `run_outcome.operational`** — a
  missing, non-object, or schema-incomplete `run_outcome` block on a
  `no_baseline: true` report previously still read as a completed audit
  (`completed_without_compatibility_verdict: true`), silently treating a
  malformed or hand-authored document as a genuine result. It now fails
  closed the same way every other malformed-gate report in the aggregate
  already does — the target reads `unavailable` with a reason — reusing the
  same `_is_schema_valid_run_outcome` schema check the two-sided-report
  reader already relies on.
- **`abicheck.buildsource.check_report`'s no-baseline `exit_axes`
  neutralization no longer risks a `TypeError`/non-`int` `exit_code`** on a
  malformed or hand-authored report whose `exit_axes` values aren't all
  plain `int`s — the recomputed top-level `exit_code` now filters to real
  (non-`bool`) `int` values before folding them with `max()`.
- **The single-release audit-only Action recipe's pinned-SHA YAML example**
  (`docs/use/github-action-source-scans.md`) no longer contradicts its own
  caveat text — the example itself now shows the placeholder commit-SHA pin
  the surrounding prose already instructs, instead of the released
  `v0.5.0` tag the same paragraph says doesn't carry this shape yet.
