### Fixed

- **G40 bundle archive: four more Codex review findings, all real, all fixed.**
  (1) A ZIP64 EOCD sentinel (`total_entries == 0xFFFF` or `cd_size ==
  0xFFFFFFFF`) whose locator/record couldn't be found or validated was
  silently allowed through to `zipfile.ZipFile`'s own parse, which falls
  back to the *standard* EOCD's raw, un-overflowed fields in that case --
  a crafted archive pairing the sentinel with a missing/malformed ZIP64
  record could carry an oversized, never-validated central-directory size
  straight past the preflight. Now rejected outright. (2) `write_bundle_
  facts_archive`'s write-side member-count and aggregate-byte caps
  (added in the previous round) charged raw *name* count rather than
  *distinct blob content* -- over-rejecting a manifest naming many names
  that share one identical snapshot object, the common "one snapshot, many
  aliases" shape. Rewritten as a two-pass write: compute every blob's
  content hash first, check both caps against the resulting unique-hash
  map, then write -- nothing touches disk until every cap the reader will
  enforce has already passed. (3) Neither cap ever charged the container
  `manifest.json` member's own serialized bytes -- only blob content -- so
  a `BundleFacts` with a large `filesystem_aliases`/`library_filenames`
  mapping could write a `manifest.json` the reader's own
  `DEFAULT_MAX_MANIFEST_BYTES` cap would always refuse. Now checked
  against the exact bytes about to be written, before writing them. (4)
  `BundleArchiveReader.__init__` reopened *path* a second time for
  `zipfile.ZipFile` after its own central-directory preflight -- a
  concurrent atomic replacement of *path* in between could swap in a
  different generation, bypassing every preflight guard entirely. Both now
  read through one shared, already-open file descriptor.

  `abicheck/storage/bundle_archive_cd_guard.py` (new): the central-
  directory bomb guard (EOCD/ZIP64 preflight) split out of
  `bundle_archive.py` into its own sibling module, purely to stay under
  the ADR-061 800-line production cap after these fixes.
