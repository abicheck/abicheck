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

  Containers are encoded structurally under a private tag, so a list and a
  tuple of the same items stay distinct keys, mapping order never reaches
  the key, and no detector value can forge a converted one. A value with no
  structure to encode keys by identity rather than by `repr`, which cannot
  merge two unequal values that happen to print the same.
