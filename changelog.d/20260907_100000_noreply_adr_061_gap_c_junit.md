<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **JUnit output now builds through the same shared report-document choke
  point JSON/Markdown/`review`/HTML/SARIF already use** — `service_render.
  render_output`'s `junit` branch calls `report/build.py`'s
  `build_report_document` once and forwards the result into
  `junit_report.to_junit_xml`/`_build_testsuite`, which reuse its
  `disposition_audit` block instead of independently resolving a second one
  (ADR-061 Phase 2 "gap C"); no user-visible JUnit output changed. This is
  the last of the five named formats — see the ADR's own status note for the
  overall closure summary. JUnit's per-finding verdict/category resolution
  (already routed through `report.finding`'s `ReportFinding`, item 4b), its
  symbol/testcase tree, and its root-cause grouping remain JUnit-specific
  computation, since the shared document builds no `Change`-keyed
  `ReportFinding` set and JUnit's own change sequence differs from
  `result.changes` (it folds in `scoped_only_changes`/`show_only`).
