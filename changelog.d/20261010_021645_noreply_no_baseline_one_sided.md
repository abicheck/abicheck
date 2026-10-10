### Fixed

- **`compare --no-baseline` now reports single-snapshot hygiene findings** —
  a detector that audits one snapshot and never reads the other (today
  `visibility_leak`) is registered `one_sided=True` and runs against the
  candidate when there is no baseline, its findings marked
  `candidate_side_enrichment`. Previously no detector ran at all without a
  baseline, so a library exporting `internal_helper`/`detail_impl` audited
  clean while `compare lib.so lib.so` reported the leak. Two-sided `compare`
  output is unchanged. A new test fails when a detector that ignores `new`
  is not declared `one_sided`.
