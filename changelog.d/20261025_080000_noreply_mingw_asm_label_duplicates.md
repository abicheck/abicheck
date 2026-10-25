### Fixed

- **mingw-w64 dumps no longer warn "Duplicate mangled symbols skipped
  (first-wins)" for `_swprintf`/`_vswprintf`.** mingw-w64's `<wchar.h>`
  declares `_swprintf`, and its `swprintf.inl` declares `swprintf`, which an
  asm label binds to the same symbol. The benign-duplicate collapse compared
  the source names, so it treated the pair as a conflict. It now compares only
  the signature under the shared key. A pair whose signatures conflict, or
  that has a member from the library's own headers, still warns.
