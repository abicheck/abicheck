### Fixed

- The JSON report's `finding_evolution` block now states its axis
  (`basis: "comparison_chain"`), whether a comparison chain was evaluated at
  all (`chain_evaluated`), the population it classified (`total`, equal to
  `summary.total_changes`), and its within-comparison counterpart
  (`summary.change_inventory`). A single comparison previously read
  `not_evaluated: 462` beside `hygiene_persistent: 414` with nothing saying
  the two answer different questions over the same findings. Report schema 5.8.
- `finding_evolution.chain_evaluated` now reads the fact that a previous
  comparison was supplied (`DiffResult.finding_evolution_evaluated`, recorded
  by `apply_finding_evolution`) instead of inferring it from non-zero counts,
  so an evaluated chain of two empty results no longer reads `false`.
- Both published compare-report schemas now define the `finding_evolution`
  block (`basis`, `chain_evaluated`, `total`, `counts`, `resolved`,
  `within_comparison_counterpart`); the per-finding `operation` description
  attributes `unchanged` to schema 5.9, not 5.7.
- `--view show=` action tokens now filter on the per-finding operation the
  JSON report serializes (a cross-source finding stated introduced/resolved/
  persistent reads added/removed/unchanged), and a new `unchanged` token
  selects findings present identically on both sides.
