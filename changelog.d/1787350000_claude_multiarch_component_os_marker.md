### Fixed

- **The Debian/Ubuntu multiarch libstdc++ fix earlier in this PR was itself
  too permissive: it accepted any triple-*shaped* directory name, not only
  a real multiarch tuple.** `_TARGET_TRIPLE_RE` alone (built to validate a
  GCC/Clang toolchain-controlled triple) matches an ordinary two-word
  project directory too (e.g. `my-lib`), so a project explicitly installed
  under a system prefix (`-I /usr/include/my-lib/c++`) had that directory
  silently reclassified as a system path, discarding the user's own
  public-scoping declaration and letting a real project header's ABI
  changes go undetected. Fixed by additionally requiring the component to
  name a real OS/libc-environment family (`linux`, `gnu`/`gnueabihf`/
  `musl`, ...) as one of its words before treating it as a multiarch tuple.
