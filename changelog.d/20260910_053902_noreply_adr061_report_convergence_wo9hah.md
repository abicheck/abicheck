### Fixed

- **`ReportEnvelope` no longer holds a live, mutable `DiffResult` reference**
  — `report.build.build_report_envelope` now snapshots every list-valued
  attribute of the `DiffResult` it is given before resolving the shared
  document, gate, and findings, so a caller mutating (or reassigning) the
  original object's `changes` after the envelope was built can no longer
  desynchronize a format that reads `envelope.result` directly for
  presentation (HTML's/JUnit's bucketing, SARIF's rule catalog) from the
  document/findings/gate the envelope already froze.
