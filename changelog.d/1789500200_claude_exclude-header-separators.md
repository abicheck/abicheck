### Fixed

- **`--exclude-header` matches the same headers whatever OS spelled the path.**
  A stored snapshot keeps the dumping host's path separators, while `fnmatch`
  normalizes separators only on Windows, so a snapshot dumped on Windows and
  read on Linux never matched a pattern like `fftw/*` (and a backslash pattern
  never matched a Linux path). The match rule now also compares the
  `/`-spelled form of both the path and the pattern. It only adds matches
  where a backslash is present; every POSIX path and pattern is decided
  exactly as before.
