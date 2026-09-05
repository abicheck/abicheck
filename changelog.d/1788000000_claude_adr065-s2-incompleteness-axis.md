### Changed

- **ADR-065 S2 — comparison scope and input completeness.** `RunOutcome`
  gains a `scope` axis (`complete`/`incomplete`) and `ExitDecision` two
  `0`/`1` fold participants, `incomplete_scope_contribution` and
  `no_comparison_completed_contribution` (report schema 2.50, scan schema
  1.28). A directory/package `compare` now records every expected member's
  acquisition state (`available`/`not_supplied`/`unsupported`/`failed`/
  `out_of_scope`) in a new `comparison_scope` JSON block, rendered in the
  Markdown report and the PR comment too, and takes
  `--on-incomplete-scope warn|block` (default `warn`: an incompletely checked
  scope is reported and contributes `0`; `block`: it contributes `1`, folded
  with `max` like the contract-coverage axis). A release whose selected scope
  completed **no** comparison exits `1` with a `no_comparison_completed`
  operational outcome under either setting instead of the previous silent
  `NO_CHANGE`/exit `0`. One candidate against a many-member baseline is a
  current-artifact comparison: the other baseline members are `out_of_scope`,
  not "removed". A stored bundle-facts baseline now records a stranded
  library whose dump failed as a degraded member (`degraded_members`), and a
  stored/stored comparison skips such a member and says so, instead of
  diffing an ELF-only stand-in. An artifact this build cannot analyze is
  recorded `unsupported` (an incompleteness signal) rather than an `ERROR`
  floored to exit `4`.
- **`--fail-on-removed-library` exit `8` migration.** Exit `8` now requires the
  removal to be *proven* — the NEW side's inventory must be proven complete
  (a stored `ProjectSnapshot` package's declared composition). A partial
  release directory no longer exits `8`: its unmatched libraries are reported
  as an incomplete scope (exit `0` under `warn`, `1` under `block`), and the
  JSON key `unmatched_old` keeps listing them. See
  `docs/reference/exit-codes.md`.
