### Fixed

- **DWARF advanced-channel packed-typedef check now propagates a swallowed
  type-resolution failure** — a malformed `DW_AT_type` on an anonymous
  struct typedef (`typedef struct __attribute__((packed)) {...} Name`)
  was caught and swallowed inside `_check_packed_typedef` without any
  completeness signal, since `_walk_cu` only threaded its `incomplete`
  out-param into the calling-convention path, not this separate walk — so
  both `parse_advanced_dwarf` and `dwarf_unified.parse_dwarf_from_session`
  could report advanced evidence `parsed` while silently omitting that
  typedef's packing facts. Fixed by threading the same signal through.
- **`from abicheck.dwarf_advanced import *` now surfaces the module's
  lazily-resolved compatibility re-exports again** — `diff_advanced_dwarf`
  and its diff-only siblings (moved to `compare/dwarf_advanced_diff.py`
  and re-exported lazily via a module-level `__getattr__`) silently
  dropped out of wildcard imports, since Python's `import *` reads
  `__all__` directly and never consults `__getattr__` for a name absent
  from it. `dwarf_advanced.py` now declares an explicit `__all__`
  covering its real public API plus every re-exported name, each still
  resolved lazily via `getattr()`'s own fallback to `__getattr__`.
