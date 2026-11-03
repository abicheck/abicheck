---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# On Windows an in-process file digest can miss a rewrite that kept size and mtime (2026-11-03)

Index: [Known gaps](../known-gaps.md)

**Status (2026-11-03): OPEN, narrow.**

## What happens

`extract/cache_input_digest.py` reuses a memoized SHA-256 of a file while
its stat signature `(st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)` is
unchanged. On POSIX `st_ctime_ns` is the inode change time, so any rewrite
moves it. On Windows it is the file's creation time (CPython 3.12+ deprecates
it as such), so a rewrite that restores both size and mtime leaves the whole
signature unchanged.

## Landed (#1908)

- The cross-process store (`extract/cache_digest_store.py`) is neither read
  nor written on Windows (`_STORE_SIGNATURES_TRUSTED`), so a later process
  never reuses a digest for different bytes. Before this, the
  `test_an_edited_file_is_re_read_in_a_new_process[same_size_same_mtime]`
  case failed on `windows-latest`.

## Still open

- The in-process memo still applies on Windows. It is guarded only by the
  racy-mtime window (`_RACY_WINDOW_NS`), which does not cover a file whose
  old mtime is restored on purpose within the same process.

## What would close it

A change time on Windows. `os.stat` does not expose one today (CPython plans
to make `st_ctime` the change time on Windows in a later release); reading
`FILE_BASIC_INFO.ChangeTime` through `ctypes` would be a Windows-only
extraction path to maintain.
