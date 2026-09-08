### Fixed

- **`compare`'s pattern/preprocessor pre-scan fold (Phase 2b) now agrees
  between the native CLI and the typed API, and honors the same
  seeded-empty-diff and depth-scoping rules the rest of the pipeline
  already does.** A native `compare --old-sources/--new-sources` run now
  actually scans the given source tree (an earlier revision silently read
  the CLI's own already-consumed, reset-to-`None` locals instead); a typed
  `CompareRequest(depth="binary")` now correctly clears both sides'
  headers before the lexical scan runs, matching the CLI; a
  successfully-resolved but empty `--since`/`--changed-path` diff now
  correctly scans zero files instead of falling back to an unseeded
  whole-tree scan; each side's pre-scan block now carries an explicit
  `coverage` status; and the Markdown report now surfaces both sections
  (previously JSON-only).
