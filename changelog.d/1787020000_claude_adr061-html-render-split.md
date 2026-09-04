### Changed

- **Internal (ADR-061 Phase 2): the HTML report is now built as structured data and projected, not emitted as prose.**
  `abicheck/report/render_html.py` is the HTML counterpart to the existing
  `render_json.py`/`render_xml.py`/`render_markdown.py` projections, closing the
  last format that still interleaved formatting decisions with the logic
  deciding *what* a section contains. (Like Markdown, HTML projects its own
  per-section structs rather than a `ReportDocument`; converging both prose
  formats onto that document remains open.) Each of `html_report.py`'s section
  builders split into a `compute_*` function returning a small frozen struct of
  plain values and a `render_*` function that turns that struct into markup and
  makes no decision of its own. Every previous spelling
  (`_file_metadata_html`, `_summary_table`, `_nav_bar`, `_confidence_html`,
  `_build_impact_html`, `_gate_card_html`, `_build_sections_html`,
  `_changes_table`, `_abbr_symbol_text`, `_symbol_cell`, `_compat_changes_table`,
  `_verdict_icon`) still resolves from `abicheck.html_report`, and the rendered
  HTML is byte-for-byte unchanged.
