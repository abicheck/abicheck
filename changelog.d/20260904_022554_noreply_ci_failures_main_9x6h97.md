### Fixed

- **`tu_merge` no longer wrongly collapses TU-local functions/variables on
  Darwin targets.** `_function_key`/`_variable_key`'s "no C++ mangling
  happened" detection relied on `mangled == name`, which never holds on a
  Darwin target (macOS's linker convention prepends a leading underscore to
  every global symbol clang emits, mangled or not) — so a plain-C or
  `static`/`extern "C"` declaration's TU-locality was silently misread on
  that platform, either collapsing distinct per-TU declarations into one or
  raising a spurious `TuMergeError` between unrelated declarations. Now
  reads each backend's already Darwin-aware `is_extern_c` signal (`Function.
  is_extern_c` directly; `Variable`'s `entity_id.extra` sidecar, since
  `Variable` carries no such field of its own) instead of re-deriving the
  same signal from a platform-fragile string comparison.

