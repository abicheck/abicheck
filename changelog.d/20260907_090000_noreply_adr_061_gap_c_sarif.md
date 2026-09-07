<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **SARIF output now builds through the same shared report-document choke
  point JSON/Markdown/`review`/HTML already use** — `service_render.
  render_output`'s `sarif` branch calls `report/build.py`'s
  `build_report_document` once and forwards the result into `sarif.to_sarif`/
  `to_sarif_str`, which reuse its `disposition_audit` block instead of
  independently resolving a second one (ADR-061 Phase 2 "gap C"); no
  user-visible SARIF output changed. SARIF's own rule catalog, per-result
  `level`/location derivation, root-cause grouping, and the `scopedGate`/
  `severityGate`/coverage-notification blocks remain SARIF-specific
  computation for now — see the ADR's own status note for the remaining
  per-format scope (JUnit).
