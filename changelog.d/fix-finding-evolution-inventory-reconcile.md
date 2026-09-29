### Fixed

- The JSON report's `finding_evolution` block now states its axis
  (`basis: "comparison_chain"`), whether a comparison chain was evaluated at
  all (`chain_evaluated`), the population it classified (`total`, equal to
  `summary.total_changes`), and its within-comparison counterpart
  (`summary.change_inventory`). A single comparison previously read
  `not_evaluated: 462` beside `hygiene_persistent: 414` with nothing saying
  the two answer different questions over the same findings. Report schema 5.7.
