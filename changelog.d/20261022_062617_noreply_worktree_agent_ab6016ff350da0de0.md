<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **C++ runtime internals no longer gate the contract** — a removed
  function-local static or guard variable of a standard template
  (`std::__detail::__to_chars...::__digits`, `std::use_facet`'s cache, a
  `std::ctype` `id` guard) that no header declares was left
  `UNKNOWN_UNRESOLVED` under a stated contract, which floored the exit to 1.
  The origin table now attributes these statics to libstdc++/libc++ (it
  handled only their `_ZNSt`-prefixed forms, and the hygiene audit disagreed
  with it), and the contract excludes runtime- and toolchain-owned symbols with
  reason `toolchain_runtime_origin` instead of gating. A symbol the library
  declares public, a user `std::hash` specialization and a std template over the
  library's own type still gate. `ElfSymbol.origin_lib` changes for these
  symbols, so the snapshot cache salt moves to v37.
- **Confidence HIGH requires record-layout evidence** — a header-plus-binary
  comparison reported HIGH with no DWARF and a header frontend that computes no
  record layout (the clang frontend parses declarations only), so a struct
  size, alignment or offset change read `NO_CHANGE` at HIGH. HIGH now needs
  layout on every compared side (DWARF, or CastXML / the clang layout
  companion); otherwise it is capped at MEDIUM with a coverage warning that
  says why. Golden `leaked_dependency_symbol` moves from HIGH to MEDIUM.
