### Fixed

- **`--bundle-facts-out` (G38 Phase 2) no longer silently drops an
  old-only library, and now captures real filesystem soname aliases so a
  later stored-baseline comparison can resolve them (Codex review).**
  `write_bundle_facts_out()` previously built its persisted
  `per_library_snapshots` map exclusively from `_compare_release_libraries()`'s
  own `diff_pairs` — which only ever holds an entry for a library present
  in *both* releases, so a library removed in the new release had no
  entry at all in a `--bundle-facts-out` baseline, even though a live
  `compare_bundle()` run against the same old release would still emit
  `bundle_library_removed`/dependency-removal/version-resolution findings
  for it. `write_bundle_facts_out()` now also captures every unmatched old
  library directly (via `parse_elf_metadata()`, mirroring what a live
  `build_bundle_snapshot()` does for exactly this case), and threads every
  `old_map` path (matched and removed alike) to `capture_bundle_facts()`'s
  new `library_paths` parameter, which probes and persists real
  filesystem soname aliases (a resolved symlink target's basename,
  hard-linked sibling basenames) — closing a second gap where a stored
  baseline could disagree with an equivalent live comparison for a
  provider without a usable `DT_SONAME`. `abicheck.bundle`'s
  `build_bundle_snapshot_from_metadata()`/`_compute_resolution_graph()`
  gained a matching `extra_aliases` parameter to replay those persisted
  aliases without touching the filesystem; the underlying basename-probing
  helpers moved to the existing `abicheck.bundle_soname` leaf module
  (`hard_link_alias_basenames`/`filesystem_alias_basenames`) to keep
  `bundle.py` under the AI-readiness file-size hard cap.
- **A `BundleFacts` file with a newer `schema_version` than this reader
  supports is now rejected outright, rather than silently interpreted
  under the current field set (Codex review).** `bundle_facts_from_dict()`
  now raises `IncompatibleSnapshotSchemaError` for a `schema_version`
  above `BUNDLE_FACTS_SCHEMA_VERSION`, mirroring `snapshot_from_dict()`'s
  existing hard rejection of a too-new `AbiSnapshot`.
