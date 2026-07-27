### Fixed

- **The stdlib-owned local-static exemption for `EXPORTED_OBJECT_ALIGNMENT_REDUCED`
  missed the companion guard-variable symbol.** A dynamically-initialized
  function-local `static` in a std::/`__gnu_cxx::`/`__cxxabiv1::` function
  gets a one-time-init guard variable exported alongside it, mangled as
  `_ZGVZ...` (Itanium `GV` + the same local-name object) rather than the
  plain `_ZZ...` form `is_stdlib_local_name_symbol()` recognized. The guard
  variable carries the same address-placement-only alignment signal as the
  local static it guards — no header declares it either — so an alignment
  shift on it still produced the same false positive the exemption exists
  to suppress. `_STDLIB_LOCAL_NAME_RE` and
  `_USER_SPECIALIZABLE_STD_TEMPLATE_RE` now accept an optional `GV` wrapper
  right after the leading `_Z`, so a guard variable classifies identically
  to its guarded local static (including the user-specialized-template
  exclusion still applying to its guard).
