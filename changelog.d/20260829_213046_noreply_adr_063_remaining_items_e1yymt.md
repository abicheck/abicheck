### Fixed

- **Two real Windows-CI-only failures in the `EntityId` carrier test
  suite.** `_resolver_call_sites`/its mangled-rewrite-scanning sibling
  read every `abicheck/**/*.py` file via `Path.read_text()` with no
  explicit encoding, which fails with `UnicodeDecodeError` on Windows'
  default (non-UTF-8) codepage as soon as any source file contains a
  byte sequence outside it; both call sites now pass
  `encoding="utf-8"` explicitly. Separately, the live-castxml probe
  helper had no pinned compilation target (unlike its live-clang
  sibling, which already pins one for exactly this reason), so on
  Windows CI its underlying castxml install targeted the host platform
  by default and mangled a namespaced variable with MSVC's own scheme
  instead of the Itanium spelling every assertion in that test module is
  written against; now pinned to the same fixed target the clang probe
  already uses.
