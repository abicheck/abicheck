### Fixed

- **Default `dump` dependency scoping (`dump --include-dependencies`
  opt-out, PR #649) no longer silently drops a dependency-header type
  directly named in the library's own kept surface.** A dependency type
  such as `std::string` used as a public function's parameter type, or a
  platform type like `struct tm` used the same way, is now retained even
  though its own defining header is a toolchain/system header — the
  library's ABI genuinely depends on that type's layout, so scoping must
  not throw the fact away before `compare` ever sees it. Retention is
  single-hop only: a directly-referenced dependency type's own internals
  (e.g. `std::string::_Alloc_hider`) are still excluded, so the snapshot-size
  motivation for this scoping is preserved. See
  `abicheck.dumper_scoping._directly_referenced_dependency_names`.

