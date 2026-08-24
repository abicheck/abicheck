<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **G38 bundle analysis: a `compare_bundle()` or Phase 4 signature-evidence
  failure is now recorded structurally, not only echoed to stderr.**
  `BundleDiffResult` gained `analysis_errors: list[str]`; a `compare
  --release` run whose bundle-analysis step raised previously returned an
  empty `bundle_findings` list with the failure visible only in the CLI's
  own stderr, indistinguishable in the JSON/Markdown report from "bundle
  analysis ran cleanly and found nothing." The JSON summary now carries
  `bundle_analysis_errors` (present only when non-empty) and the Markdown
  report gains a "⚠️ Bundle Analysis Warnings" section, rendered even when
  there are no bundle findings to show. A snapshot-construction failure
  (before any `BundleDiffResult` exists) still degrades to a bare `None`
  return with only a stderr warning — tracked as a follow-up in the G38
  plan doc's Phase 11 section.
