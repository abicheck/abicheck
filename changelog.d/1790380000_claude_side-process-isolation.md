### Added

- `performance.profile` in `.abicheck.yml` (`balanced` | `low-memory`), with
  the per-run override `compare --performance-profile` and the typed-API
  field `CompareRequest.performance_profile`: one memory/speed trade-off
  setting that maps onto internal execution knobs. `low-memory` resolves each
  side of a comparison in its own forked child process on Linux, one side at
  a time, and returns only the finished snapshot, so allocator fragmentation
  left by a side's header-AST parse no longer sets the comparison's peak RSS.
  It also compares a release's libraries one at a time. Measured on a oneDAL
  comparison: peak 1.338 -> 0.888 GiB with identical findings, at +17% wall
  time on that small input. Default `balanced` is unchanged behavior. See
  `docs/reference/config-file.md#performance`.
