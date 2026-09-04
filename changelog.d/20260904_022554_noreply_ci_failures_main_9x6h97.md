### Fixed

- **`tu_merge`/`extract.manifest_semantic_ir` no longer wrongly collapse or
  split TU-local functions/variables on Darwin targets.** Three related
  gaps, all rooted in the same underlying quirk: macOS's linker convention
  prepends a leading underscore to *every* global symbol clang emits,
  mangled or not.
  - `_function_key`/`_variable_key`'s "no C++ mangling happened" detection
    relied on `mangled == name`, which never holds on Darwin — so a plain-C
    or `static`/`extern "C"` declaration's TU-locality was silently
    misread, either collapsing distinct per-TU declarations into one or
    raising a spurious `TuMergeError` between unrelated declarations. Now
    reads each backend's already Darwin-aware `is_extern_c` signal
    (`Function.is_extern_c` directly; `Variable`'s `entity_id.extra`
    sidecar, since `Variable` carries no such field of its own) instead of
    re-deriving the same signal from a platform-fragile string comparison.
  - `_has_local_linkage_mangling`'s Itanium-marker check
    (`mangled.startswith("_Z")`) also rejected a *genuinely* Itanium-mangled
    Darwin symbol (`"__ZL6helperi"`, not the plain Itanium `"_ZL6helperi"`)
    — fixed by stripping the extra leading underscore first, mirroring
    `model/mangled_name.py`'s own `_itanium_strip_prefix`.
  - A static member function/variable nested inside an `extern "C" { ... }`
    block wrongly inherits `is_extern_c=True` from clang's AST walk despite
    being genuinely Itanium-mangled with ordinary external linkage — the
    `is_extern_c` fallback above is now gated on the mangled name *not*
    itself looking Itanium-mangled, so such a member routes to the direct
    mangled-name check instead and merges correctly across TUs.

  Applied identically to `extract/manifest_semantic_ir.py`'s mirror
  classifiers (`extract/` may not import the root-level `tu_merge` module,
  ADR-061).

- **`write_snapshot` through a FIFO no longer risks hanging on a platform
  with a smaller pipe buffer than the write.** The FIFO write-through tests
  assumed a write always fits in the platform's pipe kernel buffer unread —
  not a portable guarantee, and the likely cause of an earlier ~20-minute
  macOS CI hang in this test file. Replaced the non-blocking-open trick
  with a concurrently-draining reader thread, the standard
  capacity-independent way to exercise a pipe/FIFO write.
