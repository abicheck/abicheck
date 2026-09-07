<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **JSON's full-mode report now builds through one shared, internal report-
  document choke point** — `report/build.py`'s new `build_report_document`
  performs `reporter.to_json`'s `report_mode="full"` build exactly once per
  render (ADR-061 Phase 2 "gap C"); no user-visible output changed. Markdown,
  HTML, SARIF, and JUnit are not yet routed through it — see the ADR's own
  status note for the remaining scope.
