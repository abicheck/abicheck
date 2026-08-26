### Fixed

- **G40 bundle archive: three more Codex review findings, all real, all fixed.**
  (1) The central-directory preflight only inspected a ZIP64 EOCD locator
  when the standard EOCD's own `total_entries`/`cd_size` fields overflowed
  to their sentinel values -- but CPython's own `zipfile.ZipFile` inspects
  a preceding locator *unconditionally*, and always prefers its record's
  values when one is found. A hostile archive could therefore pair small,
  sentinel-free standard-EOCD values with a real ZIP64 record naming an
  oversized directory, bypassing the preflight entirely. The locator is
  now inspected the same way, regardless of the sentinel. (2) The
  aggregate decoded-byte cap on load only charged a shared blob's bytes
  once (via `blob_cache`) -- but every duplicate library name still
  materializes its own independent, deep-copied `AbiSnapshot` object
  graph, so a manifest naming many names against one moderately-sized
  blob could amplify far past the promised aggregate limit in live Python
  objects alone (a single 1 MiB snapshot times 20,000 duplicate names is
  ~20 GiB). Each duplicate's own copy is now charged against the same
  budget. (3) The write path enforced the distinct-blob member-count and
  aggregate-byte caps, but never the independent library-*name*-count cap
  the reader enforces -- a `BundleFacts` with more names than
  `DEFAULT_MAX_LIBRARY_COUNT`, all sharing one identical blob, wrote
  successfully and then could never be reopened by its own paired reader.
  Enforced on write now too, before anything is published.
