### Fixed

- Forked release-member fan-out now returns a member's memory-admission
  reservation even when its `on_error` handler raises, so a reused
  `MemoryAdmission` no longer reports a phantom in-flight member after an
  aborted run.
