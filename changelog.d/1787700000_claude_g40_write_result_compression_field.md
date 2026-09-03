### Fixed

- **`save_bundle_facts(..., format="archive")`'s returned `SnapshotWriteResult.compression`
  no longer claims `ZSTD`.** The saved file is a ZIP envelope (an
  uncompressed `manifest.json` plus zstd-compressed, content-addressed blob
  members) — `SnapshotCompression.ZSTD` described an internal per-member
  codec, not the outer file's own compression, and disagreed with what
  `detect_snapshot_compression()`/`read_snapshot_storage_info()` would
  independently discover by sniffing the same file's magic bytes (they'd
  report `NONE`, since a ZIP's `PK\x03\x04` header isn't a zstd frame). The
  field now reports `SnapshotCompression.NONE`, matching an independent sniff
  of the written file (Codex review).
