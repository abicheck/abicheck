### Fixed

- **A stored-baseline comparison could report `bundle_soname_skew` for a
  symlinked provider that a live comparison of the identical on-disk
  layout would not (Codex review, fresh evidence).** The previous
  symlink-resolution fix for `BundleFacts.library_filenames` made the
  *stored-facts* reconstruction correctly resolve a dev symlink
  (`libfoo_core.so -> libfoo_core.so.1`) to its real, versioned target —
  but `bundle._detect_soname_skew()`'s own live fallback (used to derive a
  SONAME major when `DT_SONAME` is absent) still read the bare,
  unversioned representative path's `.name` directly, without resolving
  it. `_detect_soname_skew()`'s fallback now resolves through the symlink
  too (the same shared `bundle_soname.resolved_basename()` helper), so
  live and stored-facts analysis agree again.
- **`save_bundle_facts()` could silently corrupt a manifest's C++ template
  argument order (Codex review, fresh evidence).** It serialized
  `BundleFacts` with `json.dumps(..., sort_keys=True)` — unlike every
  other snapshot writer in this module — which recursively re-sorts every
  dict's keys alphabetically, including each `ManifestEntry`'s own
  `instantiations` dict, whose iteration order *is* the template argument
  order (`_expand_instantiations()`'s documented contract). A manifest
  declaring parameters in a non-alphabetical order (e.g. `Precision`
  before `Method`) would reload with its arguments reordered, producing an
  expanded pattern that matches no real exported symbol and a false
  `bundle_manifest_instantiation_removed`. Dropped `sort_keys=True`.
