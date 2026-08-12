### Fixed

- **`recompute_content_digest_from_disk()`** now validates each existing
  artifact's recomputed sha256/binary_sha256 against the manifest's own
  declared values, not just against the fresh run's digest — an
  already-published asset whose manifest.json declares a stale or
  corrupted digest (even when its real bytes happen to match the current
  build) is now rejected as self-inconsistent rather than silently
  accepted as a safe retry, which would otherwise leave a manifest
  published that a real consumer's `resolve_target()`/`resolve_bundle()`
  would later reject.
- **`actions/stage-baseline`'s zstd fallback** now copies the archive
  payload in bounded 1 MiB chunks instead of buffering the whole
  uncompressed baseline-set in memory, avoiding an OOM risk on a
  memory-constrained or self-hosted runner for a large baseline-set.
