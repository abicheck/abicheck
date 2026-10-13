<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **Report sections move to their owner** — the Markdown `compute_*` notes (redundancy, out-of-surface, suppression, RTTI) now live in `abicheck/report/section_notes.py`, and the legacy `reporter_markdown` no longer re-exports `to_markdown`/`to_review_digest` through a lazy `__getattr__`; import them from `abicheck.report.dispatch_markdown`. Output is byte-identical.
