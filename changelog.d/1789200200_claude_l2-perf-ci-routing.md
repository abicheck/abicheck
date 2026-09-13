### Added

- **Real-integration L2 profiles and CI routing for the full-CLI perf lane.**
  `scripts/l2_real_profiles.py` pins the oneDAL, SVS and PVXS integrations
  declaratively — compared revisions, the libraries and public headers actually
  in L2 scope, required tools, approximate build cost, and reproducible prepare
  commands — for a periodic/manual lane, so an ordinary PR never downloads and
  builds oneDAL. An unavailable profile is reported `BLOCKED`/`NOT_RUN` with a
  concrete reason rather than silently replaced by a synthetic substitute, a
  library with no public API of its own is a recorded non-case rather than an
  inflated L2 case, and a plan that resolves both sides' headers to one revision
  is rejected as not being a temporal comparison at all.

  `performance.yml` gains a `l2-cli-perf` PR gate (the six L2 CLI forms on one
  small fixture, base and head measured by the same harness on the same runner
  with separate cache roots and interleaved ordering) and an `l2-cli-extended`
  periodic job (the axis sweep plus profile availability reporting).

### Changed

- **The perf-sensitive path classifier now covers the code the full-CLI lane
  actually executes.** `PERF_SENSITIVE_PATTERNS` predates a full-CLI
  measurement level existing, so it listed no CLI entry point, report renderer,
  orchestration or storage-codec path — a change to any of them could not
  trigger a performance run. Added per verified dependency rather than by
  directory name.

### Fixed

- **`check_l2_cli_perf.py --no-spy` crashed instead of running.** The scenario
  runner reset the invocation spy's log unconditionally, while `--no-spy` never
  created the shim directory, so the flag raised `FileNotFoundError`
  immediately. Found by the one measurement the flag exists to enable — the
  harness's own instrumentation overhead — which could therefore not be taken at
  all.

- **The full-CLI harness's extraction-count assertion calibrated itself.**
  `one_side` — the cost of extracting a single operand, against which
  "this stored/live comparison did not re-extract the stored side" is checked —
  was seeded from the measured step's *own* observed count, so the comparison
  reduced to `observed > observed` and could never fail. It is now calibrated
  only from an untimed setup dump that really did extract exactly one side;
  where no such calibration exists, the upper bound is explicitly **not**
  checked and is reported as unchecked, rather than checked against a number
  derived from the thing under test.
