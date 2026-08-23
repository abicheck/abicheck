### Fixed

- **`--bundle-facts-out` could collide with `--output`/`-o` or `--write`,
  silently overwriting the requested baseline (Codex review, fresh
  evidence).** `--bundle-facts-out result.json --output result.json` wrote
  the requested bundle-facts baseline and then had the primary render
  overwrite it, reporting success either way. Rejected up front with a new
  `reject_bundle_facts_out_collision()` check, mirroring the existing
  `--write`/`--output` collision check.
- **A stored-baseline SONAME-skew comparison could depend on the replay
  process's current working directory (Codex review, fresh evidence).**
  `bundle_snapshot_from_facts()` reconstructs each library's path as a
  synthetic, bare `Path(persisted_filename)` — no real file behind it. But
  `bundle._detect_soname_skew()`'s SONAME-major fallback unconditionally
  re-resolved every member's path against the filesystem
  (`resolved_basename()`), and `Path.resolve()` on such a synthetic path
  still succeeds by walking the *current working directory* — so an
  unrelated real file or symlink happening to share the persisted basename
  in CWD would silently substitute its own target's major for the one
  actually captured, making stored replay environment-dependent and able
  to add or suppress `bundle_soname_skew` findings. `BundleSnapshot` gained
  a `filesystem_backed` field (mirroring `build_bundle_snapshot_from_
  metadata()`'s existing `probe_filesystem` parameter): `_detect_soname_
  skew()` now only re-resolves a snapshot's paths when they are real,
  live filesystem paths, and uses a facts-reconstructed snapshot's
  persisted basename as-is.
