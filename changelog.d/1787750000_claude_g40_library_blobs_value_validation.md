### Fixed

- **G40 bundle archive: `library_blobs` values weren't type-checked on load.**
  `manifest.json` is untrusted input; a mapping-shaped `library_blobs` whose
  *values* weren't strings (a list or dict) reached `snapshot_cache.get(h)`/
  `blob_cache.get(h)` -- both keyed dicts -- and raised a raw, unhashable-type
  `TypeError` instead of this module's normal `ValueError`/`SnapshotError`
  vocabulary. Now validated up front, alongside the existing mapping-shape
  check (CodeRabbit review).
