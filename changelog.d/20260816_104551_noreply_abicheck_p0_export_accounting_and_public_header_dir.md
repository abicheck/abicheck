### Fixed

- **`analysis_assurance.export_accounting` double-counted a classified
  non-public export as both `internal` and `unaccounted`, keeping
  `--require-complete-analysis` stuck at `status="partial"` for a
  comparison with no real accounting gap.** Reported against a real Bazel
  lab project (`napetrov/abicheck-bazel-lab`): 6 exports per side, all
  classified into `non_public_symbol_to_reason` (dependency/internal/own),
  still read `internal=6, unaccounted=6` for a 12-export total — an
  arithmetic impossibility (the categories overlapped instead of summing
  to the total). Root cause: `buildsource.source_link.link_source_abi`
  (and its `relink_surface_exports` counterpart) computed `non_public =
  _classify_non_public_exports(...)` but never folded those symbols into
  `all_matched`, so every classified symbol still landed in
  `symbols_without_decl` too — even though `_classify_non_public_exports`'s
  own docstring is explicit that classification exists precisely so a
  dependency/internal/own export "is not double-counted... as a genuinely
  unclassified gap." This also corrupted `SourceAbiSurface.coverage
  ["unmatched_symbols"]`, not just `ExportAccounting`'s rollup — any
  consumer reading `symbols_without_decl` inherited the same overlap.
  Fixed by folding `non_public`'s keys into `all_matched` in both
  functions, so a classified export is genuinely accounted for and no
  longer independently counted as unaccounted.

- **Action `public-header-dir` input produced genuinely different header
  extraction (and therefore `include_sequence`) between `dump` and `scan`
  mode for the same logical inputs, so `scan --against` a fresh `dump`
  baseline spuriously read `NOT_COMPARABLE` (`profile_fingerprint`
  mismatch on `include_sequence`) with no real recipe difference.**
  `dump` mode has no dedicated `--public-header-dir` flag at all — it
  folds the input into `-H`, which `dump` reads BOTH declaration
  provenance AND header-extraction scope from (a directory entry expands
  to every header inside, ADR-015). `scan` mode's own `--public-header-dir`
  CLI flag is scope-only — extraction only ever comes from `-H`/`--header`
  — so the identical Action input left `scan`'s own extraction narrowed to
  whatever explicit header was also given, never expanding to the whole
  directory the way `dump`'s did. Fixed in `action/run.sh`: `scan` mode now
  also forwards `public-header-dir` as a bare `-H` root (in addition to the
  existing `--public-header-dir` scope forward) — `scan`'s own `-H <dir>`
  expansion (`service_scan.expand_header_inputs`) recursively extracts
  every header under a directory identically to `dump`'s
  (`header_utils.iter_directory_headers`), so this closes the gap with no
  CLI change needed. `dump` mode's own forwarding is unchanged.
