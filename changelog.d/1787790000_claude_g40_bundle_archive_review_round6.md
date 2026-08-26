### Fixed

- **G40 bundle archive: decouple `read_blob`'s stored-read cap from
  `max_decoded_bytes` (Codex review).** `_read_stored_member`'s
  still-compressed size cap was `max_decoded_bytes + 1 MiB slack` -- so a
  small `max_decoded_bytes` (a low remaining aggregate bundle-read budget,
  say) rejected a *valid* blob carrying more than ~1 MiB of leading zstd
  skippable-frame metadata ahead of a real frame decoding to only a
  handful of bytes, before decompression ever ran. Reported repro: a 2 MiB
  skippable frame followed by a frame decoding to `{}` failed
  `read_blob(..., max_decoded_bytes=100)`. Fixed by introducing
  `DEFAULT_MAX_STORED_BLOB_BYTES` (2 GiB, independent of
  `max_decoded_bytes`, mirroring `snapshot_io.py`'s own
  `DEFAULT_MAX_STORED_BYTES` and its documented reasoning) as the stored-
  member cap, while the incremental decoded-size check (the real
  decompression-bomb defense, enforced against the growing decoded output
  chunk by chunk) stays tight against `max_decoded_bytes` exactly as
  before. `read_manifest()` was checked for the identical coupling and
  found already independent -- it has no `max_decoded_bytes` parameter at
  all, always checking against the fixed `DEFAULT_MAX_MANIFEST_BYTES`.

  `tests/test_bundle_archive_stored_cap.py` (new): a real archive with a
  2 MiB leading skippable frame and a tiny decoded payload now succeeds
  even with a `max_decoded_bytes` as low as 1 byte; the incremental
  decoded-size check is confirmed still enforced (no skippable padding, a
  genuinely oversized decoded payload still rejected); and a genuinely
  oversized *stored* member (past `DEFAULT_MAX_STORED_BLOB_BYTES`) is
  still rejected before being buffered into memory -- split into its own
  file (not added to `tests/test_bundle_archive.py`, already at its
  ADR-061 800/1200-line caps) the same way the round-5 writer-hardening
  tests were. All new cases confirmed to fail against the pre-fix code.
