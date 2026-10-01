### Fixed

- `ABICHECK_MAX_THREADS` now bounds the release fan-out's worker plan itself,
  for an explicit `--jobs` as well as the automatic size. A budget of 1 runs
  release members on the sequential path instead of a pooled dispatch with one
  worker, so the budget documented as the process-wide thread cap is honoured
  by the plan the run reports.
