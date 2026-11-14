### Fixed

- **A library no existing consumer can load is now BREAKING** — two new
  `ChangeKind`s replace a COMPATIBLE_WITH_RISK answer for an ELF build that
  fails to load. `sanitizer_runtime_required` covers two cases: the library
  gained a `DT_NEEDED` on an ASan/TSan/HWASan runtime that must load first,
  or it gained strong `__asan_*`/`__ubsan_*`/... imports with no sanitizer
  runtime among its `DT_NEEDED` libraries (clang `-shared -fsanitize=...`).
  `imported_symbol_unprovided` needs `--follow-deps` evidence on both sides.
  It fires when a self-contained library gains a strong import that nothing
  in its dependency closure provides. Weak imports never count, and
  libraries that already relied on their host (plugins) are not judged.
