### Fixed

- **`ReportEnvelope`-driven Markdown and the envelope's own display-copy
  fallback no longer re-derive a per-finding verdict against a different
  "today" than the envelope was built with** — `ReportEnvelope._resolve`
  (used only for a display-only `Change` copy
  `_suppress_dangling_correlation_notes` hands a renderer under
  `--show-only`) now threads `resolved_today`, frozen once at
  `build_report_envelope` construction, through to its
  `build_report_findings` fallback call instead of implicitly reading a
  fresh `date.today()` at render time. `report_model.ReportModel.classify`/
  `from_result` gained a `verdict_overrides`/`effective_verdicts` parameter
  so Markdown's severity-group split and headline totals both reuse the
  envelope's own already-resolved verdicts instead of independently calling
  `result._effective_verdict_for_change` again. Without either fix, a dated
  `PolicyFile.reclassify` rule expiring between envelope construction and
  render could make one section of a report disagree with another built
  from the same envelope.
