### Changed

- **`dump`/`compare`/`scan`'s snapshot JSON is now sectioned and
  independently versioned by default** (ADR-062/ADR-063 Phase 8): every
  `-o`/`--output`/stdout write splits the document across D8's named,
  independently-versioned sections (`binary`, `declarations`, `types`,
  `layout`, `debug`, `build`, `graph`, `provenance`), each structurally
  validated on read, packaged as a single JSON document — no new file, no
  new flag, no directory. `snapshot_from_dict`/`load_snapshot` still read
  an older flat `.abi.json` a prior build wrote, unchanged. `compare` and
  `scan --against` also still accept a directory-backed `ProjectSnapshot`
  package as an input path (produced via the typed
  `project_snapshot_legacy.write_legacy_snapshot_package` API), resolved
  into the identical in-memory snapshot every other input shape resolves
  to. The sectioned document also records which sections it was written
  with, so a document missing an entire section (not just a field within
  one) is rejected loudly on read instead of silently reading back as
  empty/removed content — the directory-backed package's own reader now
  rejects the inverse case (an unadvertised extra section) too; `scan
  --against`'s `dependency_scope` peek now reads the sectioned shape
  correctly too. `SCHEMA_VERSION` is bumped (41 → 42) to cover this: the
  wire format itself changed, not just a field, so a pre-Phase-8 abicheck
  build now cleanly refuses a sectioned snapshot instead of silently
  reading it as an empty one. `actions/baseline/build_manifest.py` (the
  baseline-set publishing Action) also unwraps the sectioned shape before
  reading a snapshot's metadata and hashing its stable content, and
  `buildsource/baseline_set.py`'s resolver does the same before verifying
  a resolved snapshot's digest — translating a malformed/unrecognized
  sectioned envelope into the documented `AMBIGUOUS`/`STALE_SCHEMA`
  resolve outcomes rather than letting the unwrap's own exception escape.
  The sectioned-shape classifier fingerprint (`abicheck/classify.py`) now
  recognizes a valid document regardless of its top-level key order (e.g.
  `json.dumps(..., sort_keys=True)`), rather than requiring the exact
  adjacency `to_sectioned_document()` happens to write.
