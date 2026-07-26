<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **Resource-aware per-TU scheduling and cache-key extension (ADR-050 D6,
  G32 Phase E)**: a manifest-driven `dump`/`compare` (ADR-050 D3, G32 Phase
  B) now runs its per-translation-unit castxml/clang invocations under a
  RAM-aware thread pool instead of a fully sequential loop, sized the same
  way `buildsource/source_replay.py`'s L4 source-replay pool already is
  (`ABICHECK_TU_JOBS`/`ABICHECK_TU_JOB_MEM_GIB`, mirroring `ABICHECK_L4_*`).
  The shared memory-probing/pool-sizing logic (host `/proc/meminfo`,
  cgroup v1/v2 headroom, the oversubscription ceiling) moved to a new leaf
  module, `abicheck/process_resources.py`, so the two pools share one
  implementation instead of two independently-maintained copies;
  `source_replay.py`'s own `ABICHECK_L4_*`-named wrapper functions are
  unchanged. A required TU's failure (including a killed/timed-out
  castxml/clang invocation) still propagates and fails the whole manifest
  dump; an optional TU's failure still degrades to a logged diagnostic —
  fragments are always collected in the manifest's own declared TU order,
  never completion order, since the merge step treats TU order as
  significant.
- The whole-snapshot dump cache (`snapshot_cache.py`/`service_dump_cache.py`)
  now covers a manifest-driven dump instead of unconditionally excluding it:
  `comparability.compute_extraction_contract`'s `scope_fingerprint` gained a
  `translation_units` field (each TU's name, its own ordered
  `includes`/`forced_includes` including `project_owned`, `required`, and
  `contributes_to_abi`) — previously `scope_fingerprint` only tracked a
  manifest's flat `roots`/public-header fields, so two manifests differing
  only in TU structure (a TU renamed, its includes reordered, or a flag
  flipped) could silently fingerprint identically, missing exactly the class
  of extraction-contract drift ADR-050 exists to catch. The cache key folds
  in this same structural identity alongside a content-hash walk over every
  file a manifest-driven dump could actually read, so a manifest edit
  invalidates the cache correctly instead of always forcing a live re-dump.

### Fixed

- A manifest-driven dump served from a cache *miss* inside
  `service_dump_cache.cached_run_dump` used to omit `dump_manifest` from its
  live `run_dump(...)` call entirely — dormant while `dump_manifest` forced
  every call uncacheable, but would have silently fallen back to a legacy
  single-TU dump on every cold cache the moment manifest caching was
  enabled. Fixed alongside enabling that caching, not left latent.
