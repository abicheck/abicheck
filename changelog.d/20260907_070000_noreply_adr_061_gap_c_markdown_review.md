<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **Full-mode Markdown and `--format review` now build through the same
  shared report-document choke point JSON's full mode uses** —
  `service_render.render_output`'s markdown/review branches call
  `report/build.py`'s `build_report_document` once and thread the result
  into `to_markdown`/`to_review_digest`, which reuse its `disposition_audit`
  block instead of independently resolving a second one (ADR-061 Phase 2
  "gap C"); no user-visible output changed. `--stat` and the Markdown
  `leaf`/`root-cause` alternate views remain their own separate, legitimate
  documents (unchanged from the prior slice), as do HTML, SARIF, and JUnit —
  see the ADR's own status note for the remaining scope.
