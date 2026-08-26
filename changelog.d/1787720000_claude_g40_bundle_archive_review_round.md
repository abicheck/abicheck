### Fixed

- **G40 bundle archive: four more Codex review findings, all real, all fixed.**
  (1) `save_bundle_facts(..., format="archive")` could write an archive
  the reader's own `MAX_ARCHIVE_MEMBERS` safety cap would then refuse to
  reopen — nothing on the write path checked member count at all. The
  writer now rejects a write that would exceed the reader's cap up front,
  before producing an unreadable archive. (2) `SnapshotWriteResult`'s
  `stored_sha256`/`stored_size_bytes` were computed by reopening the
  *destination* path after publication — a concurrent writer replacing the
  same destination in between could make the reported digest describe a
  different generation, or a different writer's content entirely.
  `BundleArchiveWriter.close()` now computes both from the still-private
  temp file immediately before `os.replace()`, exposed as
  `stored_sha256`/`stored_size_bytes` attributes the caller reads after
  publish. (3) A zip member with the "encrypted" general-purpose bit set
  made `ZipFile.open()` raise a bare `RuntimeError` instead of the
  `SnapshotError` this module promises for every other read failure — now
  checked and rejected explicitly before opening. (4) Added a
  production-scale (`slow`-marked) `save_bundle_facts`/`load_bundle_facts`
  round-trip test, mirroring ADR-059 §12's postmortem test for the
  plain-JSON path: every prior archive-format test used ~5 KiB blobs, too
  small to exercise the reader's real 128 MiB `max_window_size` contract
  or production-level-19 compression behavior.
