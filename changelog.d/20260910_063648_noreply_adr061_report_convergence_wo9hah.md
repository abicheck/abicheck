### Fixed

- **`ReportEnvelope` no longer shares the caller's `PolicyFile`** —
  `build_report_envelope`'s snapshot now deep-copies `result.policy_file`
  too (a custom mutable object the container-copy loop couldn't catch).
  HTML's own `compatibility_metrics` call classifies straight from
  `envelope.result.policy_file`, so a `PolicyFile.overrides` mutation
  after construction could previously move the rendered
  binary-compatibility percentage even though every other decision the
  envelope already froze stayed put.
