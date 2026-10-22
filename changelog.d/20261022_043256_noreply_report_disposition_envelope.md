<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **Every one-line and review render reads the one completed envelope** — the `--stat`/`oneline` line and the review digest no longer recompute the ADR-067 disposition audit, the severity gate, or the per-finding verdicts on their own. They read them from the `ReportEnvelope` `render_output` already builds once per evaluation, and the severity summary's per-category counts come from that envelope's findings instead of a second policy classification inside `reporter_markdown`. The rendered output is unchanged: the CLI and `render_output`/`render_envelope` produce byte-identical `review`, `markdown`, `json`, `sarif`, `junit`, `html` and `oneline` output across the golden corpus. **Python API break:** `reporter_markdown.to_stat`, `reporter.to_stat`, `reporter_markdown.compute_review_digest`, `dispatch_markdown.to_review_digest` and `report.render_markdown_document.build_review_digest_document` now take a required `envelope` (built with `report.document_builder.build_report_envelope`) in place of `severity_config`; a direct caller with no envelope must build one first.
