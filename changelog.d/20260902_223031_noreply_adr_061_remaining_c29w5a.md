### Changed

- **ADR-061 Phase 2 closed in full: Markdown's leaf/root-cause modes now
  cross the canonical `ReportDocument` boundary too.** `--report-mode leaf`
  and `--report-mode root-cause` markdown (`reporter_markdown._to_markdown_leaf`/
  `_to_markdown_root_cause`) previously built their sections directly from a
  `DiffResult`, unlike every other output format (JSON, SARIF, JUnit,
  `--stat`, HTML, and Markdown's own default/review-digest views). They now
  construct a `ReportDocument` (`report/render_markdown_alternate.py`'s new
  `build_leaf_document`/`build_root_cause_document`) and project it purely
  (`render_leaf_document`/`render_root_cause_document`), the same
  fact/formatting split every other format already uses — closing the one
  remaining gap `docs/contribute/plans/duplication-and-convergence-assessment.md`'s
  Phase 4 tracked. Byte-for-byte output is unchanged: verified against
  `tests/test_golden_root_cause.py`'s golden suite and
  `tests/test_checker_reporter_branches.py`'s leaf-mode assertions. The two
  views share one opening-block compute/render pair
  (`_view_preamble_mapping`/`_render_view_preamble`), retiring
  `reporter_markdown._view_preamble`. `render_markdown_document.py` split
  into a new sibling, `render_markdown_alternate.py`, once the combined
  module passed the architecture check's 800-line new-file ceiling — the
  same reason `render_html.py`/`render_html_document.py` are two files.
