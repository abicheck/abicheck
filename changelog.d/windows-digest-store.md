### Fixed

- **Windows never serves a stale header digest from the on-disk store** — the
  cross-process file-digest store (ADR-062 D11) reuses a digest while the
  file's stat signature is unchanged, but on Windows `st_ctime` is the file's
  creation time, so a rewrite that kept its size and mtime went unseen and the
  next process could reuse the old digest in a cache key. The store is now
  neither read nor written on Windows; POSIX hosts keep it.
