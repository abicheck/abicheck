### Fixed

- **A stored baseline compared against a live PE or Mach-O build now finds
  the new side's libraries** (#1715). `compare <bundle facts> <dir>` listed the
  live NEW directory with an ELF-only walk, so a directory of `.dll` or
  `.dylib` libraries supplied no member: every library read "not supplied",
  and the new side's declared-in-a-public-header-but-exported-nowhere check
  was lost. The NEW directory now also yields every PE and Mach-O library the
  live-vs-live comparison discovers, as does the
  `--bundle-facts-library-manifest` validation.
