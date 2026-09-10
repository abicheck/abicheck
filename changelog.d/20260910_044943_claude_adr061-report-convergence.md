<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **One completed evaluation now produces one report document for every
  output format** (ADR-061 gap C, closure package 3). `service_render.
  render_output` builds a single `ReportEnvelope` — the shared
  `ReportDocument`, the severity `GateDecision`, and one resolved
  verdict/issue-category per finding — *before* a format is selected, and
  each of JSON, Markdown, `review`, HTML, SARIF and JUnit is now a pure
  projection of it via the new `service_render.render_envelope(fmt,
  envelope)`. No renderer re-runs policy evaluation or gate resolution: SARIF
  and HTML read the envelope's gate instead of resolving their own, JUnit and
  the review digest read its findings, and SARIF's published invocation
  `exitCode` moved to `report/sarif_invocation.py` (a renderer does not own
  exit behaviour). Every format's bytes are unchanged.
