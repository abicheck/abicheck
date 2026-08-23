### Fixed

- **`--bundle-facts-out` (G38 Phase 2) no longer persists a lossy,
  functions/types-free stand-in for an old-release library that has no
  `diff_pairs` entry (Codex review, fresh evidence).** `write_bundle_facts_out()`'s
  fallback — covering both a library removed in the new release and a
  matched library whose per-library compare errored — previously captured
  only bare `ElfMetadata` for that library, discarding functions, types,
  headers, and every other ABI fact a live bundle analysis would have
  captured for it. That's sufficient for bundle-level dependency-graph
  resolution, but not for the documented
  `old_facts.per_library_snapshots[name]` → `compare_snapshots()` workflow:
  comparing that bare stand-in against a real future dump would read every
  declaration as a compatible addition, hiding a genuine breaking change.
  The fallback now resolves a **real** `AbiSnapshot` (through
  `cli_resolve._resolve_input`, the approved CLI-side wrapper over
  `service.resolve_input` per ADR-037's Tier-1/Tier-2 boundary) with the
  exact same extraction context — headers, includes, version, language,
  debug info, compile context — every other library in the release was
  dumped with, and degrades to the old bare-`ElfMetadata` stand-in only if
  that full resolve itself raises (no header covers the library, or it
  isn't a real ELF binary).
