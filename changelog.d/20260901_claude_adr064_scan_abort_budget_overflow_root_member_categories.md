### Fixed

- **A `scan --artifact-set` member's `EVIDENCE_CONTRACT_ERROR` category could
  still be dropped when a *different* member's `BUDGET_OVERFLOW` won the
  set-level rollup** (PR review finding, immediately following the previous
  member-abort-category fix). `_aggregate_scan_set_verdict` unconditionally
  reports the whole set as `BUDGET_OVERFLOW` whenever any member overflows,
  even when a sibling member aborted with `EVIDENCE_CONTRACT_ERROR` for an
  unrelated reason. `aggregate`'s own forced-blocking branch for that root
  verdict hardcoded only the one category matching the root string, so it
  returned before the member-category union introduced for the normal-verdict
  path was ever consulted, silently dropping the sibling's category. Both
  branches now union `_member_abort_categories` into the gate.
