<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **Internal: `reporter_markdown.py`'s Markdown/review-digest rendering now
  builds a structured intermediate before formatting** — most `_build_*`/
  `_append_*` helpers behind `to_markdown`'s full-mode report, the
  `--report-mode leaf` view's type-change sections, and `to_review_digest`
  now split into a `compute_*` function (plain-data dataclasses) and a
  `report/render_markdown.py` projection that formats them, matching the
  `ReportDocument`/`render_json.py`/`render_xml.py` pattern ADR-061 Phase 2
  already established for JSON/SARIF/JUnit. No output change: every existing
  public function name and signature is preserved as a thin compatibility
  wrapper, and `tests/golden/*.md` plus the new `tests/golden/review/*.md`
  goldens pin the Markdown/review-digest output byte-for-byte.
