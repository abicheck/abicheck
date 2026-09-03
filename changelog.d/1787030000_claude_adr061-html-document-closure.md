### Changed

- **Internal (ADR-061 Phase 2): the HTML report now crosses the same
  canonical `ReportDocument` boundary as JSON, SARIF, JUnit, and `--stat`.**
  `html_report.build_html_document` resolves every remaining business
  decision (show_only filtering, verdict bucketing, compatibility metrics,
  which sections exist, and the ABICC-compatible `compat_html=True` layout's
  severity-band classification) into one JSON-shaped `ReportDocument`, and
  the new `abicheck/report/render_html_document.py` module's
  `render_html_document` projects it into the final HTML string with no
  `DiffResult`/`Change` access and no decision-making import.
  `generate_html_report` is now `render_html_document(build_html_document(...))`
  — a two-line composition rather than a third code path. `ChangeRow`
  (`report/render_html.py`) replaces the previous `id(change)`-keyed
  `ChangeRowFactsById` lookup table with a plain JSON-safe value carrying
  every table-row field a change needs. Markdown is now the only remaining
  format whose renderer does not yet cross this boundary (tracked separately
  in `duplication-and-convergence-assessment.md`'s Phase 4). Every HTML
  output — native and ABICC-compatible alike — is byte-for-byte unchanged.
