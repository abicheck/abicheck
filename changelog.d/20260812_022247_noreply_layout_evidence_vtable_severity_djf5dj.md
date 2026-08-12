### Changed

- **`_suppress_dangling_correlation_notes` moved to a new leaf module,
  `abicheck/report_correlation.py`** — it's shared by every report format
  (markdown, JSON, SARIF, HTML, JUnit), not markdown-specific, and kept
  `reporter_markdown.py` over the AI-readiness file-size hard cap. Purely a
  file-organization change; every import path (`reporter_markdown`,
  `reporter`, and the formats that import it from `reporter`) is unchanged.

