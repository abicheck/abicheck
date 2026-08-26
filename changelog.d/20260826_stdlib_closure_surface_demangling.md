### Fixed

- The public-surface filter (ADR-024) could not see through Itanium
  mangling, so a stdlib/runtime symbol instantiated over a caller-supplied
  lambda closure (e.g. a `std::call_once` guard closing over a lambda
  declared inside the library's own code) was treated as ordinary,
  unresolvable ABI surface — surfacing as a spurious `BREAKING`
  `func_removed`/`func_removed_elf_only` finding even for an unchanged
  library, since a closure's mangled encoding is per-translation-unit and
  compiler-ordering dependent (`change_registry`'s own
  `unnamed_type_in_public_abi` entry) and therefore can never be named by
  any external consumer's source code. `demangle.
  is_stdlib_internal_closure_instantiation` now recognizes this narrow,
  unconditionally-safe shape (demangled, `std::`/`__gnu_cxx::`/etc.-rooted,
  and containing a closure marker) and `surface.classify_change_surface`
  demotes it via a new `REASON_STDLIB_INTERNAL_CLOSURE` ledger reason —
  ahead of, and independent of, whether either side's header surface
  resolves at all, since this is a stronger guarantee than ordinary
  reachability can express. A library's own (non-stdlib-rooted) closure
  symbols are deliberately untouched by this narrow rule, since a consumer
  reaching into a library's own internal namespace could in principle name
  one.
