### Fixed

- **No more "Duplicate mangled symbols skipped (first-wins)" warning for
  system-header duplicates** — glibc's C++ `const`/non-`const` overloads of
  `wcschr`/`strchr`/`memchr` (one asm symbol) and libstdc++'s copy/move
  constructor beside a same-signature converting-constructor template (one
  `__abicheck_ctor__std::...` key) are now collapsed to one record when the
  castxml dump is built, so dumping a header that includes `<memory>` or
  `<cstring>` no longer warns. A duplicate from the library's own headers, or
  with conflicting signatures, still warns. A standalone mingw-w64 install's
  CRT headers (`C:/mingw64/x86_64-w64-mingw32/include`, MSYS2, Strawberry) are
  now recognized as system headers, as `/usr/<triple>/include` already was.
  Extractor generation is now 18.
