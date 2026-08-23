### Fixed

- **`--bundle-facts-out` (G38 Phase 2) no longer misidentifies a versioned
  library, and the new persisted format is now registered in the docs
  topic-ownership registry (Codex review).** `write_bundle_facts_out()`
  previously keyed each persisted library by `Path(DiffResult.library).name`
  — the real, on-disk basename (e.g. `libfoo.so.1.2`) — instead of the
  canonical release-matching key (`libfoo.so`, `_canonical_library_key()`)
  a live `build_bundle_snapshot()` actually uses. For a versioned DSO this
  made a reconstructed old bundle disagree with a live new bundle on the
  library's very identity, reading as a false `bundle_library_removed`/
  `bundle_library_added` pair for a library that never changed. Both the
  matched and removed-library code paths now key by the canonical
  `old_map` key throughout. Also registered a `bundle-analysis` topic in
  `docs/_meta/topics.yaml` (canonical page, fact sources) for the new
  persisted format, Python API, and `--bundle-facts-out` flag.
