### Fixed

- **A function-pointer-returning ("spiral") declarator's own trailing
  GNU calling-convention attribute (e.g. `stdcall` vs. `cdecl`) is now
  preserved in `return_type` instead of being stripped.** A previous fix
  unconditionally removed any trailing `__attribute__((...))` clause
  from a spiral declarator's return type, on the assumption that GNU
  attributes are never part of a function's type — true for an ordinary
  function, but not for a calling-convention attribute on the pointed-to
  function of a returned function pointer, which is a real, ABI-breaking
  difference (confirmed on a 32-bit x86 target, where `stdcall` and
  `cdecl` disagree on stack-cleanup responsibility). Since clang's own
  printed type cannot distinguish whether such a trailing attribute
  binds to the outer function or the returned one, it is now always
  preserved there, erring toward not silently hiding a genuine ABI
  difference. An ordinary (non-pointer-returning) function's own
  trailing attribute is unaffected and continues to be correctly
  excluded from its return type.
