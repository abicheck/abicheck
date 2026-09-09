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
  or the supplied inputs were unreadable/unscannable.
