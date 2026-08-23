### Fixed

- **`BundleFacts.library_filenames` recorded a dev symlink's own name, not
  its versioned target, defeating the SONAME-skew fix it exists for
  (CodeRabbit review, fresh evidence).** `capture_bundle_facts()` stored
  `path.name` verbatim — for the common `libfoo_core.so -> libfoo_core.so.1`
  layout (`library_paths` commonly names the unversioned dev symlink, the
  same representative path `discover_artifact_set()` keeps), that captured
  the symlink's own unversioned name, from which no SONAME major is
  derivable — the identical failure mode the previous `library_filenames`
  fix was written to close. `capture_bundle_facts()` now resolves through
  the symlink (`bundle_soname.resolved_basename()`, shared with the
  existing filesystem-alias probe, falling back to the symlink's own name
  on an unresolvable/broken link) before recording it.
- **A persisted filesystem alias for a library with no ELF metadata could
  misclassify a real "extra" (external/unresolved) `DT_NEEDED` edge as
  intra-bundle (CodeRabbit review, fresh evidence).** `bundle_snapshot_
  from_facts()` drops a per-library entry whose `AbiSnapshot.elf` is
  `None` before ever calling `build_bundle_snapshot_from_metadata()`, but
  that dropped library's captured `filesystem_aliases` entry (recorded at
  capture time, before any such drop) still reached
  `_compute_resolution_graph()`'s `extra_aliases` handling unconditionally
  — indexing an alias for a bundle member that doesn't actually exist in
  the reconstructed snapshot. A live `build_bundle_snapshot()` never has
  this problem: it never has an alias for a provider it didn't parse in
  the first place. `extra_aliases` entries are now skipped for any name
  absent from the survived `metadata` dict.
