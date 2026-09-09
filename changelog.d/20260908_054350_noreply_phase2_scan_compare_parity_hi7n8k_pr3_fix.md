### Fixed

- **`compare`'s pattern/preprocessor pre-scan report sections (Phase 2b)
  now version, cover, and render honestly across every mode.**
  `REPORT_SCHEMA_VERSION` bumped 3.12 → 3.13 (both `compare_report.
  schema.json` copies now describe the per-side `coverage` block the
  previous round added); a `preprocessor_prescan` side whose `clang -E`
  coverage is `partial` (some probes failed, or the scan's own probe cap
  truncated it) now renders a visible partial-coverage warning instead of
  reading identically to a clean, fully-scanned side; `--report-mode
  leaf`/`root-cause` now render both pre-scan sections too (previously
  full-mode-only); and a `pattern_prescan` side now distinguishes, in its
  rendered message, three previously-collapsed reasons nothing was
  scanned: no headers/`--sources` were supplied, a `--since`/
  `--changed-path` seed resolved to a real, valid, empty scope by design,
  or the supplied inputs were unreadable/unscannable. Two follow-up rounds
  (Codex review, PR #1169): first, a seeded `--since`/`--changed-path` run
  that selected candidate files which were all unreadable
  (`files_skipped > 0`) was misclassified as `empty_seed` (a real, valid,
  empty-by-design scope) rather than `unreadable_inputs` (a genuine
  acquisition failure) -- `_pattern_scan_scope_reason` now consults the
  scan's own `files_skipped` count instead of trusting `seeded` alone.
  Second, `--format review`'s digest (`build_review_digest_document`) never
  called either pre-scan renderer at all -- both are leaf/root-cause/full-
  mode-only -- so an `unreadable_inputs` pattern-scan side or a `partial`
  preprocessor-scan side could vanish entirely from the one GitHub-facing
  summary a reviewer approves a merge from. `reporter_markdown.
  compute_review_digest` now folds a dedicated warning for each into
  `coverage_warnings` (silent for the ordinary `no_inputs`/`empty_seed`
  cases, which every other report view already covers via each side's own
  `coverage` block). Third follow-up round (Codex review, PR #1169, second
  round, fresh evidence): the second round's own review-digest fix was
  itself incomplete on two axes. The pattern-scan warning silently dropped
  `empty_seed` (a real, valid, by-design 0-file scope -- still worth a
  reviewer knowing the diff's own lexical surface wasn't covered) and never
  examined `coverage.status == "partial"` at all (a side with
  `files_scanned > 0` AND `files_skipped > 0` reads `scope_reason: null`,
  since real coverage exists, but is still not fully covered); both now
  surface a warning, and only the structural `no_inputs` case (no headers/
  `--sources` supplied at all) stays silent. The preprocessor-scan warning
  only recognized `coverage.status == "partial"`, missing the case where
  clang and build evidence are both available but *every* `clang -E`
  invocation failed (`PreprocessorScanResult.all_failed`, `coverage()`
  returns `not_collected` with `ran: True`) -- now also surfaced,
  distinguished from the ordinary never-attempted `ran: False` skip (which
  stays silent). The `pattern_prescan` schema description's own closing
  sentence ("An all-zero side means nothing was in scope to scan, not a
  failure") directly contradicted `unreadable_inputs`'s own documented
  meaning a few sentences earlier -- qualified to state both directions
  (both schema copies).
