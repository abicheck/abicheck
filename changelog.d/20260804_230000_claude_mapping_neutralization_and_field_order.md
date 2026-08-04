### Fixed

- `gate-mode: advisory` now neutralizes a nested contract-coverage block held
  in any `Mapping`, not only a `dict`. The aggregate reads any `Mapping`, so
  skipping an immutable one left its contribution intact and an advisory
  check still gated CI. The block is copied into a real `dict` and rebound,
  which also keeps the caller's own container untouched.
- `TargetReport`'s contract-coverage fields moved after `findings`, so adding
  them cannot shift an existing positional construction of a public
  dataclass. A caller passing its `ReportFindings` positionally would
  otherwise have bound it to `contract_coverage_exit`, and the aggregate's
  `max()` would compare it against an int.
