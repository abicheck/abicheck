### Changed

- **Markdown report projections no longer decide anything** — the compute
  halves of the full-mode, digest, leaf and root-cause Markdown views move
  out of `render_markdown_document.py` and `render_markdown_alternate.py` into
  `report/markdown_compute.py`, so those two modules are pure functions of a
  finished `ReportDocument`. Their policy reads (kind sets, impact text,
  evidence status, exit code, reclassify rules, environment-drift kinds,
  classification) resolve through `report/policy_facts.py`, and `html_report.py`
  takes its registry lookups and `compatibility_metrics` from the same owner.
  Rendered output is byte-identical; the golden suites are unchanged.
