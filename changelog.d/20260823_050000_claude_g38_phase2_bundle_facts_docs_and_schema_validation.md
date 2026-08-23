### Fixed

- **`bundle_facts_from_dict()` no longer silently loads a malformed input
  as a valid, current-schema empty bundle (Codex review, fresh evidence).**
  A malformed or unrelated JSON object omitting the mandatory
  `per_library_snapshots` key — e.g. `{}` — previously defaulted it to `{}`
  and constructed an ordinary-looking, empty `BundleFacts`; a subsequent
  `compare_bundle_from_facts()` call would then score every new library
  against an invented empty baseline instead of the caller ever finding
  out the input was invalid. The key is now required to be present and to
  be a mapping, raising `ValueError` otherwise.
- **The G38 Phase 2 stored-baseline doc example now discovers and
  canonicalizes NEW-side libraries the same way the OLD-side facts were
  keyed (Codex review, fresh evidence).** A plain `{p.name: p for p in
  Path(...).glob("*.so")}` comprehension keys a versioned runtime DSO
  (`libfoo.so.1`, no unversioned dev symlink) by its raw, versioned
  basename — a different key from the canonical `libfoo.so`
  `write_bundle_facts_out()` persisted the OLD side under — so
  `compare_bundle_from_facts()` would misreport the library as both
  removed and added. The example now builds the NEW-side map via
  `abicheck.bundle.discover_artifact_set()`, the same canonicalizing
  discovery path the directory/package release CLI itself uses.
