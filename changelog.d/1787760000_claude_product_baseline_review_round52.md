### Fixed

- **`abicheck.package.TarExtractor`**: on Python 3.10/3.11 (no `filter=
  "data"` support), the safe-extraction fallback validated every archive
  member against a pre-extraction filesystem snapshot and only then
  bulk-`extractall()`ed -- a path-depth-collapse TOCTOU let an earlier
  symlink member (e.g. `a -> .`) make a later member's real nesting depth
  shallower once actually created than what its own validation assumed,
  so a symlink target that validated as contained within the extraction
  root could resolve outside it by the time extraction actually reached
  it. Members are now validated and extracted one at a time, immediately
  after the previous member lands on disk, so validation always runs
  against the real, currently-extracted filesystem state.
- **`abicheck.package.TarExtractor`**: an archive's decompression-bomb
  defense only bounded the decompressed *tar stream* byte count, not the
  logical size tarfile will materialize on extraction -- a GNU/PAX sparse
  member's declared `size` can be arbitrarily large while its on-stream
  data blocks (the only bytes that limit measured) stay tiny, letting a
  ~10 KiB archive declare gigabytes of extracted content and defeat the
  limit entirely. Member sizes are now summed and checked against the
  same limit before any extraction begins.
