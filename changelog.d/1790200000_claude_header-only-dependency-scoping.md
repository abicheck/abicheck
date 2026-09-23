### Fixed

- A binary-less, header-only `dump` (`-H` with no `SO_PATH`) now applies the
  default toolchain/system-declaration exclusion. The header-only executor
  never ran provenance, so every declaration's `source_header` was unset and
  the exclusion kept the whole transitive libstdc++/libc surface: on Intel SVS
  (5 headers) 32,643 functions were written, 304 of them the library's own
  (236 MB -> 15 MB snapshot, 126 s -> 50 s). Header-only snapshots now also
  carry the same declaration origins as a binary+headers dump of the same
  headers, so a declaration in a `-H` header is classified `public_header`.
- Dependency scoping no longer treats a system header as the library's own
  merely because it shares a basename with a `-H` root (libstdc++'s
  `bits/allocator.h` and fmt's `core.h` against SVS's `core/allocator.h` and
  `saveload/core.h` kept 3,164 toolchain functions). castxml's bundled
  builtin headers (`share/castxml/clang/include`) are now recognised as
  toolchain headers too.
- The clang header backend no longer misattributes a declaration's source
  file. clang omits a location's file when it equals the last one written,
  and the parser tracked that only across the nodes it visits, which skip
  function bodies. SVS's `CACHE_LINE_BYTES` (`threadlocal.h:55`) was recorded
  at `/usr/include/c++/13/concepts:55`. Every clang AST now has its locations
  made explicit once, when it is loaded. On one SVS root, a default
  header-only dump now keeps 445 functions instead of 245. A small header
  using `<concepts>` previously produced an empty snapshot.
