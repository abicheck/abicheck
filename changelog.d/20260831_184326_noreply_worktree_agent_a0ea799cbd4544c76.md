### Changed

- **ADR-061 Phase 2 item 5 (post-render mutation), partial**: `compare
  --format json`'s `old_evidence_depth`/`new_evidence_depth` and
  `suppression_audit` fields are now resolved once, before rendering, and
  attached directly onto the `DiffResult` (a new `ReportSideFacts` mixin,
  `abicheck.report_side_facts`) instead of `cli_compare_helpers.
  _fold_evidence_depth_into_json`/`cli_compare_fold.
  _fold_suppression_audit_into_text`'s JSON branch re-parsing the already-
  rendered JSON text to splice the fields in afterwards. `reporter.to_json`'s
  three JSON builders (full, leaf, root-cause) now read them directly
  through one shared tail, `reporter_contract_blocks.
  render_json_with_side_facts`. No output changes for any existing
  invocation without `--used-by`/`--required-symbol(s)` scoping — verified
  byte-identical via a direct parity test
  (`tests/test_reporter_side_facts.py`) that reconstructs the removed
  fold-ins' exact behavior and asserts equality with the new pre-render
  path. The markdown/text/review rendering of `suppression_audit`, and all
  of `--used-by`/`--required-symbol(s)` scoped-gate JSON folding and
  `--use-cases` markdown/text/review folding, are unchanged and remain
  post-render for now — see ADR-061 Phase 2's own item 5 note for why those
  three are a materially different, larger follow-up.
