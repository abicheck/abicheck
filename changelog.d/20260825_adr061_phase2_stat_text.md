### Changed

- Continued ADR-061 Phase 2 by moving the `--stat` one-line text summary
  (`reporter_markdown.to_stat`) onto the same immutable `ReportDocument`
  contract the JSON report formats already use: `abicheck/stat_line.py`
  moved to `abicheck/report/render_text.py`, which now also exposes
  `render_stat_document`, a pure `ReportDocument -> str` projection that
  formats an already-resolved verdict label, summary counts, and (when
  severity configuration is active) a precomputed exit code — it never
  calls `compute_exit_code` or otherwise computes a decision itself.
  `to_stat` still resolves the severity-aware exit code exactly once (as
  before), but now carries that value into the document instead of
  formatting it inline.
