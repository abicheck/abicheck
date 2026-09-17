### Fixed

- **`actions/aggregate`'s analysis-context transport could be aggregated as a
  target.** The two-step handoff file was written beside `reports-dir` using
  `dirname`, which for a bare relative `reports-dir` — or for `.` — resolves
  right back inside it, where `abicheck aggregate` globs `*.json` and reads
  every match as a target. A leading dot did not exempt it, which was
  verified rather than assumed: a `.abicheck-analysis-context.json` dropped
  in a reports directory aggregates as a target named
  `.abicheck-analysis-context`. It now goes in `RUNNER_TEMP`, which cannot
  collide for any input, with an explicit refusal if it somehow still would.
