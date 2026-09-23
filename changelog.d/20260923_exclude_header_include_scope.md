### Changed

- `--exclude-header` now also scopes out a matching header that is only
  reached through another header's `#include`, the same way a toolchain
  header is: declarations only it provides are no longer observed or
  reported, while types the library's own public API uses are still checked.
  Previously a pattern matching no `-H` root had no effect and findings were
  still attributed to the excluded header. A pattern that matches only such a
  header is now recorded as achieved on the snapshot. Not applied under
  `--include-system-declarations` or to stored snapshots, which keep their
  recorded surface.
