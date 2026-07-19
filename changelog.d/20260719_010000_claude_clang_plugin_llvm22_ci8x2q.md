### Fixed

- **Clang facts plugin builds against LLVM/Clang 19-22** — `clang::FileEntry::getName()`
  was removed upstream (the `SourceManager::fileinfo_*` iteration key moved to
  `FileEntryRef`, which keeps its own `getName()`, back in LLVM 18), so
  `contrib/abicheck-clang-plugin/AbicheckFactsPlugin.cpp` no longer compiled
  against current LLVM majors. The now-unreachable `const FileEntry *` overload
  is guarded `#if CLANG_VERSION_MAJOR < 18` instead of relying on overload
  resolution alone to keep it uncompiled dead code. The `clang-plugin` CI
  workflow's matrix now covers LLVM 16 through 22 (previously 16-18); the C.6
  differential-conformance test, the end-to-end scan-flow test, and the
  public-roots diagnostic test all pass unmodified on every major.
