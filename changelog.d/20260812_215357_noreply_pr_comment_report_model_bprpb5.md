<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Sticky PR comment no longer folds analysis-quality findings into "Needs
  review"** — `layer_coverage_asymmetric` (degraded evidence coverage) and
  `evidence_required_missing` (a required evidence layer absent, ADR-033 D7)
  are comparison-quality signals, not compatibility findings, and previously
  drove the same generic `⚠️ Review recommended` headline as a genuine source
  API change. They now render in their own **🛑 Analysis incomplete** section
  with a distinct headline (`🛑 Source analysis incomplete` /
  `⚠️ Analysis coverage reduced`) and are excluded from the Breaking/Needs
  review/Safe counts, so a clean, implementation-only PR with a coverage gap
  no longer reads as "this PR made a risky API change."
