### Fixed

- **A pure template-parameter rename affecting an ordinary parameter type
  no longer changes a function template's `EntityId`.**
  `template<class T> void f(T);` and `template<class U> void f(U);` are
  the identical declaration, but an ordinary parameter's raw spelling
  names the template parameter literally (`"T"`/`"U"`), which was already
  guarded against for a non-type template parameter's own declared type
  but not for the ordinary parameter list. Fixed by canonicalizing
  `param_types` against the enclosing function template's own type
  parameter names too.
- **castxml's C-linkage recovery for a bogus pseudo-Itanium mangled name
  now also honors static-archive export evidence.** The override that
  recovers a plain C function's real, unmangled identity checked only
  `exported_dynamic`; a C API observed exclusively through a static
  archive's own export set (`exported_static`) left the bogus guessed
  mangling standing, disagreeing with both the archive's own observed
  symbol and the clang producer's `extern_c` identity for the same
  declaration. Fixed by checking both sets, mirroring the identical union
  the sibling variable-level override already uses.
