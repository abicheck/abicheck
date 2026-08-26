### Fixed

- Cross-tier finding deduplication no longer aborts a comparison with
  `TypeError: unhashable type: 'list'` when a detector stores a list in a
  finding's `old_value`/`new_value` slot, as the Python-extension detectors
  do and as the JSON report serializes. The dedup key now accepts any value
  a detector can produce, without merging two values that differ.

### Changed

- Added the `abicheck.compare` responsibility package (ADR-061), which owns
  matching old/new entities and identifying a raw change. The flat `diff_*`
  modules remain its declared legacy paths.
