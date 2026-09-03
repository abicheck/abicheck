### Changed

- **Internal (ADR-061 Phase 2 item 1): Markdown's full mode and `--format
  review` digest now cross the same canonical `ReportDocument` boundary as
  JSON, SARIF, JUnit, `--stat`, and HTML.** New
  `abicheck/report/render_markdown_document.py` module: `to_review_digest`
  is now `render_review_digest_document(build_review_digest_document(...))`
  (a direct fold — `ReviewDigest` was already fully JSON-safe), and
  `to_markdown`'s default view is now
  `render_markdown_document(build_markdown_document(...))`. The builder
  resolves every remaining business decision (show_only filtering, which
  sections exist, severity-bucket grouping, the `impact_for(kind)` registry
  lookup) into one JSON-shaped mapping; the renderer projects it with no
  `DiffResult`/`Change` access, reusing every existing
  `report/render_markdown.py` section renderer unchanged. Markdown's
  per-`Change` sections needed a JSON-safe row type mirroring HTML's
  `ChangeRow` — `_change_row`/`_render_change_row`/
  `_render_change_row_oneline`, resolving `impact_for` compute-side.
  `to_markdown`'s leaf mode, root-cause mode, and `--stat`'s
  markdown-adjacent paths are **not yet** converted (they still use
  `reporter_markdown.py`'s pre-existing `compute_*`/`_build_*`/`_append_*`
  helpers unchanged) — tracked as the next slice in
  `duplication-and-convergence-assessment.md`'s Phase 4. Every full-mode and
  review-digest output is byte-for-byte unchanged — verified against the
  full `test_golden_output.py`/`test_golden_review_digest.py` suites plus
  2000+ markdown-touching tests repo-wide.
