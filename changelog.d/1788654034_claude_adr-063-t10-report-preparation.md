### Changed

- **ADR-063 track T10 ("Shared report preparation")**: internal-only
  cleanup with no behavioral or output change. The Markdown report's
  `--show-only`-filtered/dangling-correlation-suppressed display list is
  now computed once (`report/render_markdown_document.py`'s
  `_resolve_displayed_changes`) and shared by full, leaf, and root-cause
  mode, instead of each independently re-deriving it. Both runtime import
  cycles the `report/` package used to bridge with `importlib`
  (`render_markdown_document.py`'s reach into `reporter_markdown.py`, and
  `report/scoped_gate.py`'s reach into `reporter.py`) are retired in favor
  of ordinary static imports: `reporter_markdown.py`'s Markdown
  report-mode dispatch (`to_markdown`/`to_review_digest`) moved to
  `report/dispatch_markdown.py`, and `report/scoped_gate.py` now receives
  its six needed `reporter`/`reporter_markdown` helper functions as a
  passed-in `ScopedGateChangeHelpers` bundle instead of resolving
  `abicheck.reporter` dynamically. `appcompat.py`'s `scope_diff_to_app`
  now applies its late mutation of an already-finalized `DiffResult` (the
  disposition-ledger attachment and the consumer-scoped contract
  promotion/verdict recompute) through one named `_finalize_consumer_scope_diff`
  boundary instead of two free-standing statements.
