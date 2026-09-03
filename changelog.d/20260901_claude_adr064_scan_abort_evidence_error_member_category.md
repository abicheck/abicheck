### Fixed

- **A `scan --artifact-set` member's `EVIDENCE_CONTRACT_ERROR` abort could
  lose its own gate category when a sibling member's real `API_BREAK`/
  `BREAKING` verdict won the set-level rollup** (PR review finding).
  `_aggregate_scan_set_verdict` deliberately keeps the stronger real
  compatibility verdict at a set's own root even when another member
  aborted with `EVIDENCE_CONTRACT_ERROR` alongside it — a real break must
  never be hidden behind an evidence-completeness verdict — but that left
  `aggregate`'s loader with no way to tell, from the root `verdict` string
  alone, that a member had aborted at all: the target's gate silently
  dropped the `evidence_contract_error` (or `budget_overflow`) category
  despite that member never completing a comparison. `aggregate`'s loader
  now also reads each `per_artifact` member's own bare `verdict` field and
  folds any abort category it names into the gate's `blocking_categories`,
  regardless of which verdict won at the root.
