### Changed

- **ADR-065 S2 — comparison scope and input completeness.** `RunOutcome`
  gains a `scope` axis (`complete`/`incomplete`) and `ExitDecision` two
  `0`/`1` fold participants, `incomplete_scope_contribution` and
  `no_comparison_completed_contribution` (report schema 2.50, scan schema
  1.28; the nested `run_outcome.schema_version` is `1.1`, a `1.0` block
  without `scope` reads as `complete`). A directory/package `compare` now
  records every expected member's
  acquisition state (`available`/`not_supplied`/`unsupported`/`failed`/
  `out_of_scope`) in a new `comparison_scope` JSON block, rendered in the
  Markdown report and the PR comment too, and takes
  `--on-incomplete-scope warn|block` (default `warn`: an incompletely checked
  scope is reported and contributes `0`; `block`: it contributes `1`, folded
  with `max` like the contract-coverage axis). A release whose selected scope
  completed **no** comparison exits `1` with a `no_comparison_completed`
  operational outcome under either setting instead of the previous silent
  `NO_CHANGE`/exit `0`. One candidate *named as a single file* against a
  many-member baseline is a current-artifact comparison: the other baseline
  members are `out_of_scope`, not "removed". A one-member NEW *directory* is
  not narrowed (its unmatched baseline members stay unchecked), so
  discovered cardinality can never bypass `--on-incomplete-scope block`. A stored bundle-facts baseline now records a stranded
  library whose dump failed as a degraded member (`degraded_members`), and a
  stored/stored or stored/live comparison skips such a member, says so, and
  gates on it through the completeness axis instead of diffing an ELF-only
  stand-in. A bundle-facts document carrying a degraded member declares
  `schema_version: 3` (and its `ProjectSnapshot` composition section
  version 2), so a pre-S2 reader rejects it rather than misreads it; a
  document with no degraded member keeps declaring the previous version and
  stays readable by older readers. Every reader (JSON, archive,
  `ProjectSnapshot` import, and the composition section itself) refuses a
  non-empty marker declared under a pre-marker version. A stored
  `ProjectSnapshot` package's own marker is read back by the directory/
  package `compare` fan-out: a marked member is recorded `failed` (JSON
  verdict `failed`) and skipped, never diffed as an ELF-only stand-in. JUnit output carries the scope decision
  as an `abicheck.comparison_scope` suite (skipped cases under `warn`,
  errors under `block` or when no comparison completed). An artifact this build cannot analyze is
  recorded `unsupported` (an incompleteness signal) rather than an `ERROR`
  floored to exit `4`, in the stored/live driver too. Bundle-level
  (cross-library) analysis sees matched members and *proven*
  removals/additions only: an unchecked member is absent from the bundle
  graph, never a `BUNDLE_LIBRARY_REMOVED` provider, so a partial local
  build under `warn` cannot exit `4` on a library that was merely not
  supplied.
- **`--fail-on-removed-library` exit `8` migration.** Exit `8` now requires the
  removal to be *proven* — the NEW side's inventory must be proven complete
  (a stored `ProjectSnapshot` package's declared composition). A partial
  release directory no longer exits `8`: its unmatched libraries are reported
  as an incomplete scope (exit `0` under `warn`, `1` under `block`), and the
  JSON key `unmatched_old` keeps listing them. See
  `docs/reference/exit-codes.md`.
