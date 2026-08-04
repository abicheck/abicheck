### Fixed

- `aggregate` no longer counts a `scan --against` report's contract-coverage
  failure as a compatibility-gate failure. A scan report's top-level
  `exit_code` is already a fold of both axes, so reading it whole put a
  `NO_CHANGE` target into `blocking_targets` and marked its profile
  `affected` when the only failure was incomplete contract evidence —
  where the equivalent `compare` report did not. Scan emits 0/2/4/5/6 and
  has no native 1, so a raw 1 is attributable to the coverage axis; 5
  (budget overflow), 6 (NOT_COMPARABLE), and an unattributed 1 all keep
  blocking.
- `scripts/measure_contract_shadow.py` now pins the accepted `public`
  unresolved-loss cases by identity, not only by count — a fixed gap and a
  newly regressed case leave the total unchanged, so a count-only gate would
  swap an accepted gap for a fresh false negative silently.
