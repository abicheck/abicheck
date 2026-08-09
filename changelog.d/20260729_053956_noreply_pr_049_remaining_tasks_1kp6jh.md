<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`used_by`/`required_symbols`-scoped-only findings now get evaluated
  too** — a finding synthesized by app/host scoping *after*
  `compare_snapshots` already ran (e.g. a synthetic
  `consumer_required_symbol_removed`) previously stayed permanently
  unstamped even with `contract_evaluation=True`, since it was never part
  of the collections `compare()`'s own shadow evaluator stamps internally.
