<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`--report-mode root-cause` for `--format junit`** (ADR-052 Slice 6, G29
  Phase 3 follow-up): each `<failure>` element gains additive
  `rootCauseId`/`rootCause` attributes, computed via the same grouping
  JSON/markdown/SARIF already share, so no format can disagree about which
  findings correlate. JUnit's per-symbol `<testcase>` tree is unchanged — a
  symbol with multiple failing changes gets multiple `<failure>` children,
  each carrying only its own change's root cause, independently. See
  `docs/learn/impact-analysis.md`.

### Fixed

- **`--format junit --report-mode root-cause` silently rendered as `full`**
  with no error: `service_render.render_output`'s `"junit"` branch never
  forwarded its own `report_mode` argument to `to_junit_xml` at all, for
  every caller (CLI, MCP, Python API) that went through `render_output`.
  Fixed alongside the JUnit root-cause rendering above.
