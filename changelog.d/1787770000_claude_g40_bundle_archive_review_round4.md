### Fixed

- **G40 bundle archive: three more Codex review findings, all real, all fixed.**
  (1) `manifest_blob` (the second blob-reference field, alongside
  `library_blobs`) was never validated as a content-hash string --
  a malformed `manifest.json` carrying a list/dict for it reached
  `_cached_blob()`'s own `blob_cache.get(h)`, a keyed dict, raising a raw
  `TypeError` instead of this module's own error vocabulary. Now
  validated the same way `library_blobs`' values already are.
  (2) `load_bundle_facts(format="auto")` sniffed *path*'s format via one
  open, then reopened *path* separately for the actual archive parse --
  a concurrent atomic replacement of *path* between the two opens could
  swap in a different, individually-valid generation the sniff result no
  longer describes, causing a spurious rejection. The sniff and the
  archive parse now share one fd
  (`storage.bundle_archive.open_regular_file_for_format_sniff` +
  `BundleArchiveReader.from_open_file`), the same fix already applied to
  the central-directory preflight vs. `zipfile.ZipFile` construction.
  (3) `BundleArchiveWriter.close()`'s own failure-cleanup path closed the
  temp file wrapper as a plain sibling statement before unlinking it --
  if that close itself raised (e.g. ENOSPC/EIO flushing buffered bytes
  during an exception-driven abort), the unlink was never reached,
  leaving the temp file behind. The unlink now runs from a nested
  `finally`, so it fires regardless of whether the close raises.
- **G40 bundle archive: the write-side aggregate cap didn't mirror the
  reader's duplicate-copy accounting.** The write path's aggregate
  decoded-byte cap only charged each unique blob's bytes once -- but the
  reader (per the previous review round's fix) charges every duplicate
  library name's own deep-copied `AbiSnapshot` too. Many names sharing
  one moderately-sized blob could pass the write-side unique-bytes check
  while exceeding the reader's real per-copy total (e.g. 1,025 names
  sharing one 1 MiB blob), publishing an archive its own paired reader
  would then refuse to reopen. The write path now computes the identical
  duplicate-charged total the reader will enforce, before publishing.
