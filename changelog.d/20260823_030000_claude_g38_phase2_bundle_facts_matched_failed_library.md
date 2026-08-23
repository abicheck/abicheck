### Fixed

- **`--bundle-facts-out` (G38 Phase 2) no longer drops a matched library
  whose per-library compare errored (Codex review).** `write_bundle_facts_out()`'s
  fallback for a real old-release library missing from `diff_pairs`
  previously only covered `removed_keys` (a library absent from the new
  release) — a library present in *both* releases but whose per-library
  compare returned `ERROR`/`not_comparable` had no `diff_pairs` entry
  either, and was silently dropped from the persisted baseline the same
  way. The fallback now covers *every* `old_map` key not already captured
  by a successful `diff_pairs` entry, regardless of why it's missing.
