<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`abi_compare`'s embedded `report` JSON now agrees with the top-level
  `changes` array on missing-symbol contract evaluation** —
  `_fold_scoped_compat_into_text` (shared with the CLI) builds its own,
  separate missing-contract-label dicts for `response["report"]["changes"]`,
  independent of the top-level array's already-stamped copy; a caller
  reading only the embedded report previously saw no `contract_relevance`
  on this finding even with `contract_evaluation=True`.
