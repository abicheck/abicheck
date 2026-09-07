<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **The default (full) HTML view now builds through the same shared
  report-document choke point JSON/Markdown/`review` already use** —
  `service_render.render_output`'s `html` branch calls `report/build.py`'s
  `build_report_document` once and threads the result into
  `html_report.generate_html_report`/`build_html_document`, which reuse its
  `disposition_audit` block instead of independently resolving a second one
  (ADR-061 Phase 2 "gap C"); no user-visible output changed. HTML's own
  bucketing (removed/changed/added), per-section rows, and the `compat_html`
  ABICC-clone layout remain HTML-specific computation for now — see the
  ADR's own status note for the remaining per-format scope (SARIF, JUnit).
- Added a byte-exact golden fixture for the previously-unpinned
  `compat_html=True` (ABICC-clone) HTML layout
  (`tests/golden/html_template/main_report_compat.html`,
  `tests/test_html_template_golden.py`).
